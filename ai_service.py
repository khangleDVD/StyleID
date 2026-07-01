"""
ai_service — Phân tích ảnh thời trang qua OpenRouter + chuẩn hóa theo dataset.

Luồng xử lý một ảnh (có API key):
  (1) 3 model vision (một lượt / model): mỗi model trả danh sách món + thuộc tính + phong cách
      (primary_style + style_confidence hoặc tương đương) + detection_confidence — song song.
  (2) Code merger (rule-based): chuẩn hoá theo danh mục trong dataset; giữ món khi ≥2/3 model cùng phát hiện
      HOẶC chỉ 1 model nhưng detection_confidence > 0.8; hợp nhất attribute theo majority.
  (3) Chốt phong cách từng món: bỏ phiếu có trọng số model trên detected_styles của các model
      trong cluster (không gọi vision lần 2). Phong cách tổng thể (aggregate_styles) giữ nguyên.
  (4) Giới hạn tối đa MAX_ITEMS_PER_IMAGE món / ảnh (mặc định 10, khớp GT) — ưu tiên confidence cao.

Không có key: trả kết quả demo cố định (để UI vẫn chạy được).

Các khối trong file (theo thứ tự):
  §1  Dataset       — đọc JSON, cache, sinh chuỗi ràng buộc cho prompt
  §2  Prompt        — quy tắc liệt kê đủ món; prompt một lượt (món + phong cách)
  §3  Ensemble      — đọc model từ config; vision một lượt + merger + vote style
  §4  Chuẩn hóa     — category/item/style khớp danh mục dataset; item_style_rules
  §5  Từ dataset    — map dịp theo style (occasions)
  §6  Hiển thị VI   — category/style/item → tiếng Việt; hằng số OCCASION_MAP…
  §7  API OpenRouter — đọc ảnh base64; gọi vision / chat
  §8  Phân tích ảnh — analyze_image (retry nếu lỗi mạng)
  §9  Sau phân tích — tổng hợp phong cách nhiều món; gợi ý dịp; mô tả tổng thể; gợi ý mix đồ
"""
import os
import re
import json
import base64
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple

# -----------------------------------------------------------------------------
# §1 — Dataset: file JSON định nghĩa danh mục + cache (tránh đọc lặp mỗi request)
# -----------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATASET_PATH = os.path.join(_SCRIPT_DIR, 'data', 'fashion_style_dataset.json')
_DATASET_CACHE: Optional[Dict] = None
_DATASET_WRITE_LOCK = threading.Lock()


def _env_flag_true(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None or (isinstance(raw, str) and raw.strip() == ''):
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def _dataset_file_path_for_write() -> str:
    if os.path.isfile(_DATASET_PATH):
        return _DATASET_PATH
    alt = os.path.join(_SCRIPT_DIR, '..', 'data', 'fashion_style_dataset.json')
    return alt if os.path.isfile(alt) else _DATASET_PATH


def learn_item_from_analysis(category: str, raw_before: str, normalized_after: str, dataset: Dict) -> bool:
    """
    Bổ sung tên món vào items[category] (memory + file JSON) khi bật DATASET_LEARN_FROM_ANALYSIS.

    Chỉ thêm nếu: sau _normalize_item_type_to_dataset, chuỗi giữ nguyên nội dung so với AI (chỉ khác hoa thường)
    và chưa có trong items[category] — tức là tên mới thật, không phải đã fuzzy map sang loại trong danh mục khác
    (vd. không thêm khi AI gửi "graphic tee" mà đã map thành "Graphic T-shirt").
    """
    if not _env_flag_true('DATASET_LEARN_FROM_ANALYSIS', default=False):
        return False
    cat = (category or '').strip()
    o = (raw_before or '').strip()
    n = (normalized_after or '').strip()
    if not cat or not o or not n or not isinstance(dataset, dict):
        return False
    if o.lower() != n.lower():
        return False
    if len(n) > 160 or '\n' in n or '\r' in n:
        return False
    path = _dataset_file_path_for_write()
    if not os.path.isfile(path):
        return False
    cats = dataset.get('categories') or []
    if cats and cat not in cats:
        return False

    with _DATASET_WRITE_LOCK:
        items = dataset.get('items')
        if not isinstance(items, dict):
            items = {}
            dataset['items'] = items
        lst = items.get(cat)
        if not isinstance(lst, list):
            lst = []
            items[cat] = lst
        have = {str(x).strip().lower() for x in lst}
        if n.lower() in have:
            return False
        lst.append(n)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                disk = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print('[Fashion dataset] Học món: không đọc lại file:', e)
            return True
        ditems = disk.get('items')
        if not isinstance(ditems, dict):
            ditems = {}
            disk['items'] = ditems
        dlst = ditems.get(cat)
        if not isinstance(dlst, list):
            dlst = []
            ditems[cat] = dlst
        dhave = {str(z).strip().lower() for z in dlst}
        if n.lower() not in dhave:
            dlst.append(n)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(disk, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print('[Fashion dataset] Học món: không ghi file:', e)
    return True


def load_fashion_dataset() -> Dict:
    """Load dataset từ fashion_style_dataset.json; cache trong bộ nhớ (chỉ đọc file một lần mỗi lần chạy server)."""
    global _DATASET_CACHE
    if _DATASET_CACHE is not None:
        return _DATASET_CACHE
    path = _DATASET_PATH
    if not os.path.isfile(path):
        path = os.path.join(_SCRIPT_DIR, '..', 'data', 'fashion_style_dataset.json')
    if not os.path.isfile(path):
        _DATASET_CACHE = {}
        return _DATASET_CACHE
    try:
        with open(path, 'r', encoding='utf-8') as f:
            _DATASET_CACHE = json.load(f)
        return _DATASET_CACHE
    except (json.JSONDecodeError, OSError) as e:
        print('[Fashion dataset] Không đọc được file:', e)
        _DATASET_CACHE = {}
        return _DATASET_CACHE


def build_constraints_from_dataset(dataset: Dict) -> Dict[str, Any]:
    """
    Từ JSON định nghĩa danh mục → các chuỗi categories_str, item_types_str, styles_str… nhét vào prompt AI.
    Hỗ trợ cấu trúc items theo category, base_item_types, hoặc item_types_by_category (bản cũ).
    """
    categories = dataset.get('categories') or ['Nón', 'Áo', 'Quần', 'Váy/Đầm', 'Giày/Dép', 'Phụ kiện', 'Trang sức']
    # item types: v2 base_item_types, v1 item_types_by_category, hoặc items (dataset mới)
    base = dataset.get('base_item_types') or dataset.get('item_types_by_category') or dataset.get('items') or {}
    item_types_flat = []
    for v in (base.values() if isinstance(base, dict) else []):
        if isinstance(v, list):
            item_types_flat.extend(str(x) for x in v if x)
        else:
            item_types_flat.append(str(v))
    item_types_str = ', '.join(item_types_flat) if item_types_flat else 'Áo thun, Áo sơ mi, Quần jean, Sneaker, ...'
    # styles
    styles_raw = dataset.get('styles') or []
    style_names = []
    for s in styles_raw:
        if isinstance(s, dict):
            style_names.append(s.get('name_en') or s.get('name_vi') or s.get('id', ''))
        else:
            style_names.append(str(s))
    styles_str = ', '.join(x for x in style_names if x)
    # attributes (v2): fit, material, pattern — chỉ tên, không dump full list vào prompt
    attrs = dataset.get('attributes') or {}
    fit_vals = attrs.get('fit') or ['oversize', 'regular', 'slim']
    mat_vals = attrs.get('material') or ['cotton', 'denim', 'len', 'da']
    pat_vals = attrs.get('pattern') or ['trơn', 'kẻ sọc', 'hoa văn']
    return {
        'categories_str': ', '.join(categories),
        'item_types_str': item_types_str,
        'styles_str': styles_str,
        'fit_str': ', '.join(fit_vals) if isinstance(fit_vals, list) else str(fit_vals),
        'material_str': ', '.join(mat_vals) if isinstance(mat_vals, list) else str(mat_vals),
        'pattern_str': ', '.join(pat_vals) if isinstance(pat_vals, list) else str(pat_vals),
    }


# -----------------------------------------------------------------------------
# §2 — Prompt gửi AI: ràng buộc từ dataset + hướng dẫn liệt kê đủ món + merger ensemble
# -----------------------------------------------------------------------------

def _prompt_exhaustive_detection_rules() -> str:
    """
    Quy tắc chung để tăng recall (liệt kê đủ món). Dùng cho bước phát hiện (vision).
    """
    return (
        "Quy tắc liệt kê đủ: (1) Quét có hệ thống từ trên xuống — mũ/nón, kính, trang sức (khuyên, vòng cổ, vòng tay, nhẫn), "
        "lớp ngoài và lớp trong (áo khoác ≠ áo trong), thắt lưng, quần/váy/đầm, tất/vớ, giày/dép/sandal, túi xách/túi đeo. "
        "(2) Kể cả món chỉ lộ một phần nếu vẫn nhận ra loại đồ. "
        "(3) Hai lớp khác nhau (ví dụ blazer và áo phông) là HAI món — không gộp. "
        "(4) Nếu không có từ khớp chính xác trong danh sách item_type, hãy chọn loại GẦN NHẤT trong danh sách — không được bỏ qua món vì không tìm thấy từ đúng 100%."
    )


def _max_items_per_image_from_config(config=None) -> int:
    """Đọc trần số món / ảnh từ Flask config hoặc MAX_ITEMS_PER_IMAGE trong .env."""
    raw = None
    if config is not None:
        raw = getattr(config, 'MAX_ITEMS_PER_IMAGE', None)
    if raw is None:
        raw = os.environ.get('MAX_ITEMS_PER_IMAGE', '10')
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 10
    return max(1, min(n, 50))


def build_analysis_prompt(dataset: Dict, max_items: int = 10) -> str:
    """
    Prompt một lượt cho từng model vision: phát hiện món + gán phong cách + độ tin cậy món.
    """
    c = build_constraints_from_dataset(dataset)
    cat = c['categories_str']
    itypes = c['item_types_str']
    sty = c['styles_str']
    fit_s = c.get('fit_str', 'oversize, regular, slim')
    mat_s = c.get('material_str', 'cotton, denim, len, da')
    pat_s = c.get('pattern_str', 'trơn, kẻ sọc, hoa văn')
    attrs = dataset.get('attributes') or {}
    sil_s = ', '.join(attrs.get('silhouette') or []) if isinstance(attrs.get('silhouette'), list) else ''
    col_s = ', '.join(attrs.get('color_tone') or []) if isinstance(attrs.get('color_tone'), list) else ''
    det_s = ', '.join(attrs.get('details') or []) if isinstance(attrs.get('details'), list) else ''
    len_s = ', '.join(attrs.get('length') or []) if isinstance(attrs.get('length'), list) else ''
    sea_s = ', '.join(attrs.get('season') or []) if isinstance(attrs.get('season'), list) else ''
    lay_s = ', '.join(attrs.get('layer') or []) if isinstance(attrs.get('layer'), list) else ''
    rules = _prompt_exhaustive_detection_rules()
    return (
        f"Phân tích ảnh thời trang — một lượt: nhận diện món VÀ gán phong cách từng món. "
        f"Liệt kê từng món nhìn thấy (mỗi món 1 lần), TỐI ĐA {max_items} món. {rules} "
        f"Nếu ảnh có nhiều chi tiết: ưu tiên món trang phục chính (áo khoác, áo, quần/váy/đầm, giày) "
        f"và phụ kiện nổi bật; bỏ qua món mơ hồ hoặc phụ kiện nhỏ khuất để không vượt {max_items} món.\n"
        f"{_STYLE_HIPHOP_VS_STREETWEAR_HINT}"
        f"Mỗi món: category CHỈ trong [{cat}]; item_type CHỈ trong [{itypes}] (gần nhất nếu cần); "
        f"fit [{fit_s}], material [{mat_s}], pattern [{pat_s}]; "
        f"silhouette [{sil_s}], color_tone [{col_s}], details 0-3 từ [{det_s}], length [{len_s}], season [{sea_s}], layer [{lay_s}].\n"
        f"detection_confidence: số 0..1 — độ tin CHẮC CHẮN có món đó trong ảnh (không phải điểm style). "
        f"Nếu chỉ đoán món khuất/mơ hồ → dùng ≤0.55; nhìn rõ → 0.75-0.95.\n"
        f"Với MỖI món chỉ chọn ĐÚNG MỘT phong cách bạn khẳng định đúng nhất: primary_style = id hoặc name trong [{sty}] (ưu tiên id snake_case); "
        f"style_confidence = 0..1 (độ tin vào lựa chọn phong cách đó). Không liệt kê thêm phong cách phụ.\n"
        f"reason: 1-3 câu tiếng Việt ngắn, vì sao chọn primary_style này cho món đó.\n"
        f"Trả về ĐÚNG MỘT JSON, KHÔNG markdown:\n"
        '{"items":[{"category":"","item_type":"","fit":"","material":"","pattern":"","silhouette":"","color_tone":"","details":[],"length":"","season":"","layer":"","detection_confidence":0.85,"primary_style":"casual","style_confidence":0.82,"reason":["..."]}]}'
    )


_STYLE_HIPHOP_VS_STREETWEAR_HINT = (
    'Phân biệt hip_hop vs streetwear (hay nhầm — dùng id đúng trong JSON): '
    'hip_hop — ưu tiên khi có dấu hiệu văn hoá rap rõ: dây chuyền/trang sức vàng hoặc chunky nổi bật, mũ bucket/durag/bandana theo phong rap, '
    'áo jersey/graphic mang tên nghệ sĩ-nhóm-crew, silhouette oversize cực đoan kiểu golden age; '
    'streetwear — phong đường phố đương đại rộng hơn: layer hoodie-áo khoác, sneaker street brand, cargo jogger, graphic tee thương hiệu street/skate '
    'mà không gắn rap cụ thể; nếu chỉ outfit “đường phố chung” (áo rộng + sneaker + phụ kiện nhẹ) không đủ tín hiệu rap ở trên thì chọn streetwear. '
    'Chỉ trả MỘT primary_style cho món — chọn thứ phù hợp nhất.\n\n'
)


# §3 — Ensemble: (1) đọc 3 tên model vision từ .env  (2) gọi song song vision + merger

def _vision_models_from_config(config) -> List[str]:
    """Luôn trả đúng 3 model vision (OpenRouter). Thiếu trong env thì lấp từ mặc định, không trùng lặp nếu có thể."""
    defaults = [
        'google/gemini-2.5-flash',
        'anthropic/claude-3-haiku',
        'openai/gpt-5.4-mini',
    ]
    raw = (getattr(config, 'OPENROUTER_VISION_MODELS', None) or os.environ.get('OPENROUTER_VISION_MODELS') or '').strip()
    parts = [p.strip() for p in raw.split(',') if p.strip()]
    if not parts:
        return list(defaults)
    out = parts[:3]
    if len(out) < 3:
        for d in defaults:
            if len(out) >= 3:
                break
            if d not in out:
                out.append(d)
        i = 0
        while len(out) < 3:
            out.append(defaults[i % len(defaults)])
            i += 1
    return out[:3]


def _vision_model_weights_from_config(config, n: int) -> List[float]:
    """Trọng số bỏ phiếu theo thứ tự OPENROUTER_VISION_MODELS; thiếu → 1.0."""
    raw = (
        getattr(config, 'OPENROUTER_VISION_MODEL_WEIGHTS', None)
        or os.environ.get('OPENROUTER_VISION_MODEL_WEIGHTS')
        or ''
    ).strip()
    out: List[float] = []
    for part in raw.split(','):
        p = part.strip()
        if not p:
            continue
        try:
            out.append(float(p))
        except ValueError:
            out.append(1.0)
    while len(out) < n:
        out.append(1.0)
    return out[:n]


def _detection_items_only(parsed: Optional[Dict]) -> List[Dict]:
    """Lấy danh sách items từ JSON bước 1; chỉ giữ các khóa mô tả món."""
    if not isinstance(parsed, dict):
        return []
    items = parsed.get('items')
    if not isinstance(items, list):
        return []
    keys = (
        'category',
        'item_type',
        'fit',
        'material',
        'pattern',
        'silhouette',
        'color_tone',
        'details',
        'length',
        'season',
        'layer',
    )
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        row = {}
        for k in keys:
            if k == 'details':
                v = it.get(k)
                if isinstance(v, list):
                    row[k] = [str(x).strip() for x in v if str(x).strip()][:3]
                elif isinstance(v, str) and v.strip():
                    row[k] = [v.strip()][:3]
                else:
                    row[k] = []
            else:
                row[k] = (it.get(k) or '')
        if not row.get('item_type') and it.get('item'):
            row['item_type'] = str(it.get('item') or '').strip()
        if row.get('category') or row.get('item_type'):
            rows.append(row)
    return rows


def _coerce_unit_interval(val: Any, default: float = 0.72) -> float:
    """Chuẩn hoá số thực về [0,1] (hỗ trợ cả nhập dạng phần trăm 0–100)."""
    if val is None:
        return default
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    if v > 1.0:
        v = v / 100.0
    return max(0.0, min(1.0, v))


def _oneshot_items_from_parsed(parsed: Optional[Dict]) -> List[Dict]:
    """Items từ JSON một lượt: món + đúng 1 phong cách / model (primary_style) + detection_confidence + reason."""
    if not isinstance(parsed, dict):
        return []
    items = parsed.get('items')
    if not isinstance(items, list):
        return []
    keys = (
        'category',
        'item_type',
        'fit',
        'material',
        'pattern',
        'silhouette',
        'color_tone',
        'details',
        'length',
        'season',
        'layer',
    )
    rows: List[Dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        row: Dict[str, Any] = {}
        for k in keys:
            if k == 'details':
                v = it.get(k)
                if isinstance(v, list):
                    row[k] = [str(x).strip() for x in v if str(x).strip()][:3]
                elif isinstance(v, str) and v.strip():
                    row[k] = [v.strip()][:3]
                else:
                    row[k] = []
            else:
                row[k] = (it.get(k) or '')
        if not row.get('item_type') and it.get('item'):
            row['item_type'] = str(it.get('item') or '').strip()
        dc = it.get('detection_confidence', it.get('item_confidence'))
        row['detection_confidence'] = _coerce_unit_interval(dc, default=0.72)
        ps = it.get('primary_style') or it.get('final_style') or it.get('style')
        ds_out: List[Dict[str, Any]] = []
        if isinstance(ps, str) and ps.strip():
            sc_sty = _coerce_unit_interval(it.get('style_confidence'), default=0.75)
            ds_out = [{'style': ps.strip(), 'score': sc_sty}]
        else:
            ds_raw = it.get('detected_styles') or []
            best_name, best_sc = '', -1.0
            if isinstance(ds_raw, list):
                for entry in ds_raw:
                    if not isinstance(entry, dict):
                        continue
                    name = entry.get('style') or entry.get('name') or ''
                    if not name:
                        continue
                    sc0 = entry.get('score') if entry.get('score') is not None else entry.get('confidence')
                    scv = _coerce_unit_interval(sc0, default=0.55)
                    if scv > best_sc:
                        best_sc = scv
                        best_name = str(name).strip()
            if best_name:
                ds_out = [{'style': best_name, 'score': max(0.0, best_sc)}]
        if not ds_out:
            row['detected_styles'] = [{'style': 'casual', 'score': 0.5}]
        else:
            row['detected_styles'] = [ds_out[0]]
        rs = it.get('reason')
        if isinstance(rs, list):
            row['reason'] = [str(x) for x in rs[:3] if str(x).strip()]
        elif isinstance(rs, str) and rs.strip():
            row['reason'] = [rs.strip()]
        else:
            row['reason'] = []
        if row.get('category') or row.get('item_type'):
            rows.append(row)
    return rows


def _norm_key(s: str) -> str:
    return (s or '').strip().lower()


# Gộp các nhóm loại đồ hay bị model gán nhầm — cùng item_type → một cluster khi merge/dedup.
# Đầu (nón, kẹp tóc…) vs phụ kiện Soft/Hard: cùng tên món vẫn một cluster.
_HEAD_OR_ACCESSORY_CATEGORIES = frozenset({'headwear', 'accessories_soft', 'accessories_hard'})
# Quần/váy: Skirt đôi khi rơi Bottomwear đôi khi Skirts.
_BOTTOM_OR_SKIRT_CATEGORIES = frozenset({'bottomwear', 'skirts'})


def _oneshot_merge_group_key(category: str, item_type: str) -> Tuple[str, str]:
    """Khóa gộp ensemble: các nhánh hay nhầm + cùng item_type → một cluster."""
    c = _norm_key(category)
    t = _norm_key(item_type)
    if not t:
        return (c, t)
    if c in _BOTTOM_OR_SKIRT_CATEGORIES:
        return ('__bottom_or_skirt__', t)
    if c in _HEAD_OR_ACCESSORY_CATEGORIES:
        return ('__head_or_accessory__', t)
    return (c, t)


# Phụ kiện nhỏ / khuất → model dễ bỏ sót, hạ ngưỡng đồng thuận xuống 1/3
_SMALL_ACCESSORY_CATEGORIES = (
    'jewelry',
    'trang sức',
)
_SMALL_ITEM_KEYWORDS = (
    'ring', 'earring', 'necklace', 'bracelet', 'anklet', 'pendant',
    'watch', 'sunglasses', 'glasses',
    'sock', 'stocking', 'tights',
    'tie', 'bow tie', 'pocket square', 'cufflink',
    'hair clip', 'hair pin', 'headband', 'hair tie', 'hair scarf',
    'pin', 'brooch',
)

# Ngưỡng tối thiểu số attribute "không rỗng" để xem là model nhìn rõ món
# (dùng cho cơ chế cứu món 1/3 phiếu — Hướng 2).
_RICH_ATTR_THRESHOLD = 3

# Một lượt vision: ≥2 model cùng nhóm (cat,item) HOẶC 1 model với detection_confidence > ngưỡng.
_MIN_ENSEMBLE_DETECT_VOTES = 2
_SINGLE_DETECT_CONF_KEEP = 0.8


def _min_votes_for_item(cat_raw: str, item_raw: str) -> int:
    """
    Ngưỡng phiếu tối thiểu để giữ lại 1 món sau khi hợp nhất.

    - Outerwear (jacket/coat): 3/3 — hay bị nhầm với áo trong, dễ ảo giác.
    - Phụ kiện nhỏ/khuất (trang sức, vớ, kính, đồng hồ...): 1/3 — model dễ bỏ sót.
    - Các nhóm khác: 2/3 (cân bằng precision/recall).
    """
    c = _norm_key(cat_raw)
    t = _norm_key(item_raw)
    if 'jackets' in c or 'coats' in c or 'jacket' in t or 'coat' in t:
        return 3
    if any(k in c for k in _SMALL_ACCESSORY_CATEGORIES):
        return 1
    if any(k in t for k in _SMALL_ITEM_KEYWORDS):
        return 1
    return 2


def _attribute_richness(item: Dict) -> int:
    """
    Đếm số attribute chi tiết model đã trả (không tính category/item_type).

    Càng cao → càng có khả năng model nhìn rõ món thật, ít rủi ro ảo giác
    (model bịa kèm nhiều thuộc tính chi tiết là khó hơn bịa tên đơn thuần).
    Dùng cho cơ chế cứu món 1/3 phiếu trong _code_merge_detection_outputs.
    """
    rich_keys = (
        'fit', 'material', 'pattern', 'silhouette',
        'color_tone', 'length', 'season', 'layer',
    )
    n = sum(1 for k in rich_keys if str(item.get(k) or '').strip())
    details = item.get('details') or []
    if isinstance(details, list) and details:
        n += 1
    return n


def _code_merge_detection_outputs(
    detect_outputs: List[Tuple[str, Dict]],
    dataset: Dict,
) -> List[Dict]:
    """
    Hợp nhất danh sách món từ N model vision bằng code thuần (thay AI merger).

    Quy trình:
      1) Chuẩn hoá từng item theo danh mục dataset (category + item_type) bằng
         _normalize_category_to_dataset / _normalize_item_type_to_dataset.
      2) Group theo (category_normalized, item_type_normalized) — so khớp lower-case.
      3) Lọc theo ngưỡng đồng thuận (_min_votes_for_item):
         outerwear cần ≥3 model nhắc tới, nhóm khác cần ≥2.
      4) Hợp nhất attributes (fit, material, pattern, silhouette, color_tone,
         length, season, layer) theo majority vote; details = union dedup ≤ 3.

    Tất định: cùng input → cùng output. Không gọi AI, không tốn token.
    """
    norm_rows: List[Tuple[str, Dict]] = []
    for mid, data in detect_outputs:
        for it in _detection_items_only(data):
            cat = _normalize_category_to_dataset(str(it.get('category') or ''), dataset)
            item_t = _normalize_item_type_to_dataset(
                str(it.get('item_type') or it.get('item') or ''), cat, dataset
            )
            if not item_t:
                continue
            row = dict(it)
            row['category'] = cat
            row['item_type'] = item_t
            norm_rows.append((mid, row))

    groups: Dict[Tuple[str, str], List[Tuple[str, Dict]]] = {}
    for mid, row in norm_rows:
        key = (row['category'].lower(), row['item_type'].lower())
        groups.setdefault(key, []).append((mid, row))

    attr_keys = (
        'fit', 'material', 'pattern', 'silhouette',
        'color_tone', 'length', 'season', 'layer',
    )

    merged_out: List[Dict] = []
    for (_cat_l, _item_l), members in groups.items():
        models_seen = {mid for mid, _ in members}
        rep_cat = members[0][1]['category']
        rep_item = members[0][1]['item_type']
        need = _min_votes_for_item(rep_cat, rep_item)
        if len(models_seen) < need:
            # H2 RESCUE — Cứu món chỉ 1 model phát hiện nếu nó trả KÈM ≥ _RICH_ATTR_THRESHOLD
            # attribute "không rỗng": coi như bằng chứng "model nhìn rõ", không phải bịa.
            # KHÔNG áp dụng cho outerwear (need=3) — vẫn yêu cầu 3/3 để chống ảo giác đặc thù.
            if (
                need <= 2
                and len(members) == 1
                and _attribute_richness(members[0][1]) >= _RICH_ATTR_THRESHOLD
            ):
                pass  # cho qua: attributes phong phú = bằng chứng mạnh
            else:
                continue

        merged_item: Dict[str, Any] = {
            'category': rep_cat,
            'item_type': rep_item,
        }
        for attr in attr_keys:
            vals = [
                str(m.get(attr) or '').strip()
                for _, m in members
                if str(m.get(attr) or '').strip()
            ]
            if vals:
                cnt: Dict[str, int] = {}
                for v in vals:
                    k = v.lower()
                    cnt[k] = cnt.get(k, 0) + 1
                merged_item[attr] = max(cnt.items(), key=lambda kv: (kv[1], kv[0]))[0]
            else:
                merged_item[attr] = ''

        seen_d, details_out = set(), []
        for _, m in members:
            d_list = m.get('details') or []
            if not isinstance(d_list, list):
                continue
            for d in d_list:
                k = str(d).strip().lower()
                if not k or k in seen_d:
                    continue
                seen_d.add(k)
                details_out.append(str(d).strip())
                if len(details_out) >= 3:
                    break
            if len(details_out) >= 3:
                break
        merged_item['details'] = details_out

        merged_out.append(merged_item)

    return merged_out


def _find_style_row_for_merged(
    merged: Dict,
    index: int,
    model_items: List[Dict],
) -> Optional[Dict]:
    """Khớp món trong output model bước 3 với món đã hợp nhất (ưu tiên index, sau đó category+item_type)."""
    if not model_items:
        return None
    if index < len(model_items) and isinstance(model_items[index], dict):
        return model_items[index]
    mc = _norm_key(str(merged.get('category') or ''))
    mt = _norm_key(str(merged.get('item_type') or merged.get('item') or ''))
    best = None
    for it in model_items:
        if not isinstance(it, dict):
            continue
        c = _norm_key(str(it.get('category') or ''))
        t = _norm_key(str(it.get('item_type') or it.get('item') or ''))
        if mc and mt and c == mc and t == mt:
            return it
        if mc and c == mc and (not mt or not t or t == mt):
            best = it
    return best


def _styles_from_model_item_row(it: Dict, dataset: Dict) -> List[Tuple[str, float]]:
    """Trích (style_id, confidence 0..1) từ một item do model bước 3 trả về."""
    out: List[Tuple[str, float]] = []
    for entry in (it.get('detected_styles') or []):
        if not isinstance(entry, dict):
            continue
        name = entry.get('style') or entry.get('name') or ''
        if not name:
            continue
        sid = _get_style_id(str(name), dataset)
        conf = entry.get('confidence') if entry.get('confidence') is not None else entry.get('score')
        if isinstance(conf, (int, float)):
            v = float(conf)
            score = (v / 100.0) if v > 1.0 else v
        else:
            score = 0.5
        out.append((sid, max(0.0, min(1.0, score))))
    for entry in (it.get('styles') or []):
        if not isinstance(entry, dict):
            continue
        name = entry.get('name') or entry.get('style') or ''
        if not name:
            continue
        sid = _get_style_id(str(name), dataset)
        conf = entry.get('confidence') if entry.get('confidence') is not None else entry.get('score')
        if isinstance(conf, (int, float)):
            v = float(conf)
            score = (v / 100.0) if v > 1.0 else v
        else:
            score = 0.5
        out.append((sid, max(0.0, min(1.0, score))))
    # Gộp trùng style_id: lấy max confidence
    merged: Dict[str, float] = {}
    for sid, sc in out:
        k = (sid or 'casual').lower()
        merged[k] = max(merged.get(k, 0.0), sc)
    return [(k, merged[k]) for k in merged]


def _vote_styles_for_merged_items(
    merged_items: List[Dict],
    style_outputs: List[Tuple[str, Dict]],
    model_weights: List[float],
    dataset: Dict,
) -> List[Dict]:
    """
    Với mỗi món đã hợp nhất: cộng điểm style theo (trọng số model / tổng trọng số) × confidence.
    """
    result: List[Dict] = []
    n = min(len(style_outputs), len(model_weights))
    pairs: List[Tuple[Tuple[str, Dict], float]] = list(
        zip(style_outputs[:n], model_weights[:n])
    )
    w_sum = sum(w for _, w in pairs) or 1.0

    for idx, base in enumerate(merged_items):
        row = dict(base)
        agg: Dict[str, float] = {}
        best_reason_model: Optional[Tuple[float, List[str]]] = None
        per_model_debug: List[Dict[str, Any]] = []
        top_vote_counts: Dict[str, int] = {}
        top_vote_weighted: Dict[str, float] = {}

        for (model_id, data), w_m in pairs:
            m_items = data.get('items') if isinstance(data, dict) else None
            if not isinstance(m_items, list):
                continue
            hit = _find_style_row_for_merged(row, idx, m_items)
            if not hit:
                continue
            w_eff = w_m / w_sum
            styles_from_this_model = _styles_from_model_item_row(hit, dataset)
            for sid, conf in styles_from_this_model:
                key = (sid or 'casual').lower()
                agg[key] = agg.get(key, 0.0) + w_eff * conf
            rs = hit.get('reason')
            if isinstance(rs, list) and rs:
                conf_top = max((x[1] for x in styles_from_this_model), default=0.0)
                if best_reason_model is None or conf_top > best_reason_model[0]:
                    best_reason_model = (conf_top, [str(x) for x in rs[:3]])
            top_style = None
            top_conf = None
            if styles_from_this_model:
                top_style, top_conf = max(styles_from_this_model, key=lambda x: x[1])
                if top_style:
                    sid_l = (top_style or 'casual').lower()
                    top_vote_counts[sid_l] = top_vote_counts.get(sid_l, 0) + 1
                    try:
                        cval = float(top_conf or 0.0)
                    except (TypeError, ValueError):
                        cval = 0.0
                    top_vote_weighted[sid_l] = top_vote_weighted.get(sid_l, 0.0) + w_eff * cval
            per_model_debug.append({
                'model': str(model_id),
                'weight': float(w_m),
                'weight_effective': round(float(w_eff), 4),
                'top_style': (top_style or '').lower() if top_style else None,
                'top_confidence': round(float(top_conf), 4) if isinstance(top_conf, (int, float)) else None,
                'styles': [
                    {'style': (sid or '').lower(), 'confidence': round(float(conf), 4)}
                    for sid, conf in styles_from_this_model[:5]
                ],
            })

        if not agg:
            agg = {'casual': 0.5}
        # Chốt phong cách theo majority vote từ TOP style mỗi model (phong cách được gán nhiều nhất).
        # Tie-break: tổng (w_eff * top_confidence) cao hơn; nếu vẫn tie thì theo agg score.
        if top_vote_counts:
            best_count = max(top_vote_counts.values())
            cands = [k for k, v in top_vote_counts.items() if v == best_count]
            if len(cands) == 1:
                final_sid = cands[0]
            else:
                final_sid = max(
                    cands,
                    key=lambda k: (
                        top_vote_weighted.get(k, 0.0),
                        agg.get(k, 0.0),
                        k,
                    ),
                )
        else:
            final_sid = max(agg, key=lambda k: agg[k])
        max_score = float(agg.get(final_sid, 0.0))
        denom = max_score if max_score > 0 else 1.0
        detected = sorted(
            [{'style': k, 'score': round(min(1.0, v / denom), 2)} for k, v in agg.items()],
            key=lambda e: e['score'],
            reverse=True,
        )[:5]

        row['styles'] = [
            {'name': e['style'], 'confidence': int(round(e['score'] * 100))} for e in detected[:3]
        ]
        row['detected_styles'] = [{'style': e['style'], 'score': e['score']} for e in detected]
        row['final_style'] = final_sid
        row['confidence'] = round(min(1.0, max_score), 2)
        row['reason'] = best_reason_model[1] if best_reason_model else []
        row['vote_debug'] = {
            'per_model': per_model_debug,
            'top_vote_counts': dict(sorted(top_vote_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            'aggregate_scores': {k: round(float(v), 4) for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:8]},
            'chosen_style': final_sid,
        }
        result.append(row)
    return result


def _vote_styles_from_cluster_members(
    base_row: Dict,
    cluster_members: List[Tuple[str, Dict]],
    vision_models: List[str],
    model_weights: List[float],
    dataset: Dict,
) -> Dict:
    """
    Bỏ phiếu phong cách cho một cluster món (cùng category+item_type sau chuẩn hoá),
    từ output một lượt của từng model trong cluster. Luật giống _vote_styles_for_merged_items nhưng không cần alignment index.
    """
    row = dict(base_row)
    pairs: List[Tuple[str, float]] = list(zip(vision_models, model_weights))
    if not pairs:
        pairs = [(vision_models[0], 1.0)] if vision_models else [('default', 1.0)]
    w_sum = sum(float(w) for _, w in pairs) or 1.0
    member_by_model = {str(mid): r for mid, r in cluster_members}

    agg: Dict[str, float] = {}
    best_reason_model: Optional[Tuple[float, List[str]]] = None
    per_model_debug: List[Dict[str, Any]] = []
    top_vote_counts: Dict[str, int] = {}
    top_vote_weighted: Dict[str, float] = {}

    for m, w_m in pairs:
        w_eff = float(w_m) / w_sum
        hit = member_by_model.get(str(m))
        if not hit:
            per_model_debug.append({
                'model': str(m),
                'weight': float(w_m),
                'weight_effective': round(float(w_eff), 4),
                'top_style': None,
                'top_confidence': None,
                'styles': [],
            })
            continue
        styles_from_this_model = _styles_from_model_item_row(hit, dataset)
        if not styles_from_this_model:
            fs = hit.get('final_style')
            if fs:
                sid = _get_style_id(str(fs), dataset)
                styles_from_this_model = [(sid, 0.65)]
        for sid, conf in styles_from_this_model:
            key = (sid or 'casual').lower()
            agg[key] = agg.get(key, 0.0) + w_eff * float(conf)
        rs = hit.get('reason')
        if isinstance(rs, list) and rs:
            conf_top = max((x[1] for x in styles_from_this_model), default=0.0)
            if best_reason_model is None or conf_top > best_reason_model[0]:
                best_reason_model = (conf_top, [str(x) for x in rs[:3]])
        top_style = None
        top_conf = None
        if styles_from_this_model:
            top_style, top_conf = max(styles_from_this_model, key=lambda x: x[1])
            if top_style:
                sid_l = (top_style or 'casual').lower()
                top_vote_counts[sid_l] = top_vote_counts.get(sid_l, 0) + 1
                try:
                    cval = float(top_conf or 0.0)
                except (TypeError, ValueError):
                    cval = 0.0
                top_vote_weighted[sid_l] = top_vote_weighted.get(sid_l, 0.0) + w_eff * cval
        per_model_debug.append({
            'model': str(m),
            'weight': float(w_m),
            'weight_effective': round(float(w_eff), 4),
            'top_style': (top_style or '').lower() if top_style else None,
            'top_confidence': round(float(top_conf), 4) if isinstance(top_conf, (int, float)) else None,
            'styles': [
                {'style': (sid or '').lower(), 'confidence': round(float(conf), 4)}
                for sid, conf in styles_from_this_model[:5]
            ],
        })

    if not agg:
        agg = {'casual': 0.5}
    if top_vote_counts:
        best_count = max(top_vote_counts.values())
        cands = [k for k, v in top_vote_counts.items() if v == best_count]
        if len(cands) == 1:
            final_sid = cands[0]
        else:
            final_sid = max(
                cands,
                key=lambda k: (
                    top_vote_weighted.get(k, 0.0),
                    agg.get(k, 0.0),
                    k,
                ),
            )
    else:
        final_sid = max(agg, key=lambda k: agg[k])
    max_score = float(agg.get(final_sid, 0.0))
    denom = max_score if max_score > 0 else 1.0
    detected = sorted(
        [{'style': k, 'score': round(min(1.0, v / denom), 2)} for k, v in agg.items()],
        key=lambda e: e['score'],
        reverse=True,
    )[:5]

    row['styles'] = [
        {'name': e['style'], 'confidence': int(round(e['score'] * 100))} for e in detected[:3]
    ]
    row['detected_styles'] = [{'style': e['style'], 'score': e['score']} for e in detected]
    row['final_style'] = final_sid
    row['confidence'] = round(min(1.0, max_score), 2)
    row['reason'] = best_reason_model[1] if best_reason_model else []
    row['vote_debug'] = {
        'per_model': per_model_debug,
        'top_vote_counts': dict(sorted(top_vote_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        'aggregate_scores': {k: round(float(v), 4) for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:8]},
        'chosen_style': final_sid,
    }
    row['style'] = (final_sid or 'casual').capitalize()
    return row


def _merge_oneshot_ensemble_outputs(
    detect_outputs: List[Tuple[str, Dict]],
    dataset: Dict,
    vision_models: List[str],
    model_weights: List[float],
    min_votes: int = _MIN_ENSEMBLE_DETECT_VOTES,
    single_conf_keep: float = _SINGLE_DETECT_CONF_KEEP,
) -> List[Dict]:
    """
    Hợp nhất output một lượt từ nhiều model: giữ món khi ≥min_votes model khác nhau cùng nhóm
    (category+item; Bottomwear/Skirts, Headwear/Accessories_Soft/Hard + cùng item_type → một món)
    hoặc 1 model với detection_confidence > single_conf_keep. Sau đó vote style theo cluster.
    """
    norm_rows: List[Tuple[str, Dict]] = []
    for mid, data in detect_outputs:
        for it in _oneshot_items_from_parsed(data):
            cat = _normalize_category_to_dataset(str(it.get('category') or ''), dataset)
            item_t = _normalize_item_type_to_dataset(
                str(it.get('item_type') or it.get('item') or ''), cat, dataset
            )
            if not item_t:
                continue
            cat = _correct_category_for_item_type(cat, item_t, dataset)
            row = dict(it)
            row['category'] = cat
            row['item_type'] = item_t
            norm_rows.append((mid, row))

    dedup_best: Dict[Tuple[str, str, str], Tuple[str, Dict]] = {}
    for mid, row in norm_rows:
        gk = _oneshot_merge_group_key(row['category'], row['item_type'])
        k3 = (str(mid), gk[0], gk[1])
        prev = dedup_best.get(k3)
        if prev is None or float(row.get('detection_confidence') or 0) > float(prev[1].get('detection_confidence') or 0):
            dedup_best[k3] = (mid, row)
    norm_rows = list(dedup_best.values())

    groups: Dict[Tuple[str, str], List[Tuple[str, Dict]]] = {}
    for mid, row in norm_rows:
        gk = _oneshot_merge_group_key(row['category'], row['item_type'])
        groups.setdefault(gk, []).append((mid, row))

    attr_keys = (
        'fit', 'material', 'pattern', 'silhouette',
        'color_tone', 'length', 'season', 'layer',
    )

    merged_out: List[Dict] = []
    for gk, members in sorted(groups.items(), key=lambda kv: kv[0]):
        models_seen = {str(mid) for mid, _ in members}
        max_dc = max(float(r.get('detection_confidence') or 0.0) for _, r in members)
        keep = len(models_seen) >= min_votes or (len(models_seen) == 1 and max_dc > single_conf_keep)
        if not keep:
            continue
        cat_vals = [str(m.get('category') or '').strip() for _, m in members if str(m.get('category') or '').strip()]
        rep_cat = Counter(cat_vals).most_common(1)[0][0] if cat_vals else members[0][1]['category']
        rep_item = members[0][1]['item_type']
        rep_cat = _correct_category_for_item_type(rep_cat, rep_item, dataset)

        merged_item: Dict[str, Any] = {
            'category': rep_cat,
            'item_type': rep_item,
        }
        for attr in attr_keys:
            vals = [
                str(m.get(attr) or '').strip()
                for _, m in members
                if str(m.get(attr) or '').strip()
            ]
            if vals:
                cnt: Dict[str, int] = {}
                for v in vals:
                    lk = v.lower()
                    cnt[lk] = cnt.get(lk, 0) + 1
                merged_item[attr] = max(cnt.items(), key=lambda kv: (kv[1], kv[0]))[0]
            else:
                merged_item[attr] = ''

        seen_d, details_out = set(), []
        for _, m in members:
            d_list = m.get('details') or []
            if not isinstance(d_list, list):
                continue
            for d in d_list:
                lk = str(d).strip().lower()
                if not lk or lk in seen_d:
                    continue
                seen_d.add(lk)
                details_out.append(str(d).strip())
                if len(details_out) >= 3:
                    break
            if len(details_out) >= 3:
                break
        merged_item['details'] = details_out

        attrs_only = {a: merged_item.get(a) or '' for a in attr_keys}
        merged_item['item'] = build_design_name(rep_item, attrs_only, dataset)

        voted = _vote_styles_from_cluster_members(
            merged_item, members, vision_models, model_weights, dataset
        )
        merged_out.append(voted)

    return merged_out


def _fallback_oneshot_first_model(
    ok_detect: List[Tuple[str, Dict]],
    dataset: Dict,
    vision_models: List[str],
    model_weights: List[float],
) -> List[Dict]:
    """Khi merger ensemble rỗng: dùng model đầu tiên, vote style như cluster 1 model."""
    if not ok_detect:
        return []
    mid0, data0 = ok_detect[0]
    out: List[Dict] = []
    attr_keys = (
        'fit', 'material', 'pattern', 'silhouette',
        'color_tone', 'length', 'season', 'layer',
    )
    for row in _oneshot_items_from_parsed(data0):
        cat = _normalize_category_to_dataset(str(row.get('category') or ''), dataset)
        item_t = _normalize_item_type_to_dataset(
            str(row.get('item_type') or row.get('item') or ''), cat, dataset
        )
        if not item_t:
            continue
        cat = _correct_category_for_item_type(cat, item_t, dataset)
        row = dict(row)
        row['category'] = cat
        row['item_type'] = item_t
        attrs_only = {a: row.get(a) or '' for a in attr_keys}
        base = {
            'category': cat,
            'item_type': item_t,
            **{a: row.get(a) or '' for a in attr_keys},
            'details': row.get('details') or [],
            'item': build_design_name(item_t, attrs_only, dataset),
        }
        memb: List[Tuple[str, Dict]] = [(str(mid0), row)]
        out.append(_vote_styles_from_cluster_members(
            base, memb, vision_models, model_weights, dataset
        ))
    return out


def _run_vision_ensemble(
    image_url: str,
    dataset: Dict,
    base_url: str,
    api_key: str,
    vision_models: List[str],
    max_tokens: int,
    timeout: int,
    config,
) -> Dict:
    """
    Pipeline một lượt vision / model: mỗi model trả items + detected_styles + detection_confidence
    → merger code (≥2 model HOẶC 1 model với detection_confidence > 0.8) → vote style theo cluster.
    Không gọi vision lần 2 cho gán phong cách.
    """
    max_items = _max_items_per_image_from_config(config)
    detect_prompt = (
        build_analysis_prompt(dataset, max_items=max_items)
        if dataset else build_analysis_prompt({}, max_items=max_items)
    )
    weights = _vision_model_weights_from_config(config, len(vision_models))
    oneshot_tokens = min(max(max_tokens * 2, 2048), 8192)

    def _one_vision_oneshot(mid: str) -> Tuple[str, Optional[Dict], Optional[str]]:
        try:
            content = _call_vision_api(
                base_url, api_key, mid, image_url, detect_prompt,
                max_tokens=oneshot_tokens, timeout=timeout,
            )
            parsed = extract_json_from_response(content)
            items = _oneshot_items_from_parsed(parsed)
            if items:
                return (mid, {'items': items}, None)
            snippet = (content or '').strip().replace('\n', ' ')[:180]
            return (mid, None, f'empty_items_or_parse_failed: {snippet}')
        except Exception as e:
            return (mid, None, str(e))

    ok_detect: List[Tuple[str, Dict]] = []
    errs: List[str] = []
    detect_err_by_model: Dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(vision_models))) as ex:
        futs = [ex.submit(_one_vision_oneshot, m) for m in vision_models]
        for fut in as_completed(futs):
            mid, parsed, err = fut.result()
            if parsed is not None:
                ok_detect.append((mid, parsed))
            elif err:
                errs.append(f'{mid}: {err}')
                detect_err_by_model[mid] = str(err)

    if not ok_detect:
        raise RuntimeError(
            'Không có model vision nào trả về items hợp lệ (bước một lượt). '
            + ('; '.join(errs) if errs else '')
        )

    voted_items = _merge_oneshot_ensemble_outputs(
        ok_detect, dataset, vision_models, weights,
        min_votes=_MIN_ENSEMBLE_DETECT_VOTES,
        single_conf_keep=_SINGLE_DETECT_CONF_KEEP,
    )
    if not voted_items:
        voted_items = _fallback_oneshot_first_model(ok_detect, dataset, vision_models, weights)

    weights_by_model = {vision_models[i]: float(weights[i]) for i in range(len(vision_models))}
    trace = {
        'vision_models': list(vision_models),
        'vision_model_weights_by_model': weights_by_model,
        'merger_model': 'code (rule-based, one-shot)',
        'merger_method': 'code_oneshot',
        'merge_rules': {
            'min_distinct_models': _MIN_ENSEMBLE_DETECT_VOTES,
            'single_model_detection_confidence_keep_gt': _SINGLE_DETECT_CONF_KEEP,
        },
        'step1_detect_outputs': [{'model': mid, 'data': d} for (mid, d) in ok_detect],
        'step1_detect_errors_by_model': detect_err_by_model,
        'step2_merged_items': list(voted_items),
        'step3_style_outputs': None,
        'step3_style_errors_by_model': {},
        'step3_style_rerun_outputs': None,
        'step3_style_rerun_errors_by_model': None,
        'step3_style_rerun_indices': [],
        'final_items': list(voted_items),
    }
    return {'items': voted_items, 'trace': trace}


# -----------------------------------------------------------------------------
# §4 — Chuẩn hóa output AI về đúng categories / item_types / styles trong dataset
# -----------------------------------------------------------------------------

def _normalize_category_to_dataset(category: str, dataset: Dict) -> str:
    """Chuẩn hóa category từ AI về đúng một giá trị trong dataset (backend)."""
    if not category or not isinstance(category, str):
        return 'Phụ kiện'
    raw = category.strip()
    categories = dataset.get('categories') or []
    if not categories:
        return raw or 'Phụ kiện'
    lower = raw.lower()
    for c in categories:
        if c and c.strip().lower() == lower:
            return c.strip()
    for c in categories:
        if c and lower in c.strip().lower() or c.strip().lower() in lower:
            return c.strip()
    return categories[0] if categories else 'Phụ kiện'


def _get_base_item_types_flat(dataset: Dict) -> List[str]:
    """Lấy danh sách phẳng các item_type hợp lệ từ dataset (base_item_types, item_types_by_category, hoặc items)."""
    base = dataset.get('base_item_types') or dataset.get('item_types_by_category') or dataset.get('items') or {}
    out = []
    for v in (base.values() if isinstance(base, dict) else []):
        if isinstance(v, list):
            out.extend(str(x).strip() for x in v if x)
    return out


def _normalize_item_type_to_dataset(item_type: str, category: str, dataset: Dict) -> str:
    """Chuẩn hóa item_type từ AI về đúng một giá trị trong dataset (backend)."""
    if not item_type or not isinstance(item_type, str):
        return ''
    raw = item_type.strip()
    base = dataset.get('base_item_types') or dataset.get('item_types_by_category') or dataset.get('items') or {}
    # Ưu tiên tìm trong đúng category
    candidates = list(base.get(category, [])) if isinstance(base.get(category), list) else []
    for c in candidates:
        if c and str(c).strip().lower() == raw.lower():
            return str(c).strip()
    for c in candidates:
        if c and (raw.lower() in str(c).lower() or str(c).lower() in raw.lower()):
            return str(c).strip()
    # Fallback: tìm trong toàn bộ
    all_types = _get_base_item_types_flat(dataset)
    for c in all_types:
        if c and c.lower() == raw.lower():
            return c
    for c in all_types:
        if c and (raw.lower() in c.lower() or c.lower() in raw.lower()):
            return c
    return raw


def _hosiery_item_prefers_accessories_soft(item_type: str) -> bool:
    """Tất/vớ/tights không thuộc danh mục giày (trừ 'sock boots')."""
    t = _norm_key(item_type)
    if not t:
        return False
    if 'sock' in t and 'boot' not in t:
        return True
    if any(k in t for k in ('tights', 'stocking', 'stockings')):
        return True
    return False


def _correct_category_hosiery_vs_footwear(category: str, item_type: str, dataset: Dict) -> str:
    """Nếu AI gán vớ vào Footwear_* thì chuyển về Accessories_Soft khi có trong dataset."""
    c = _norm_key(category)
    if c not in ('footwear_shoes', 'footwear_sandals'):
        return category
    if not _hosiery_item_prefers_accessories_soft(item_type):
        return category
    cats = dataset.get('categories') or []
    if any(str(x).strip() == 'Accessories_Soft' for x in cats):
        return 'Accessories_Soft'
    return category


_EYEWEAR_ITEM_KEYS = frozenset({'eyeglasses', 'sunglasses', 'goggles'})


def _is_eyewear_item_type(item_type: str) -> bool:
    t = _norm_key(item_type)
    if not t:
        return False
    if t in _EYEWEAR_ITEM_KEYS:
        return True
    return any(k in t for k in ('eyeglass', 'sunglass', 'goggle'))


def _correct_category_eyewear_vs_headwear(category: str, item_type: str, dataset: Dict) -> str:
    """Kính mắt/kính râm thuộc Accessories_Hard, không phải Headwear."""
    if not _is_eyewear_item_type(item_type):
        return category
    cats = dataset.get('categories') or []
    if any(str(x).strip() == 'Accessories_Hard' for x in cats):
        return 'Accessories_Hard'
    return category


def _correct_category_for_item_type(category: str, item_type: str, dataset: Dict) -> str:
    """Chuẩn hóa category theo loại món (vớ, kính mắt…)."""
    cat = _correct_category_hosiery_vs_footwear(category, item_type, dataset)
    cat = _correct_category_eyewear_vs_headwear(cat, item_type, dataset)
    return cat


def _normalize_style_name_to_dataset(style_name: str, dataset: Dict) -> str:
    """Ánh xạ tên phong cách từ AI về đúng tên trong dataset (backend)."""
    if not style_name or not isinstance(style_name, str):
        return 'Casual'
    raw = style_name.strip().lower()
    if not raw:
        return 'Casual'
    styles = dataset.get('styles') or []
    for s in styles:
        if not isinstance(s, dict):
            continue
        for key in ('id', 'name_en', 'name_vi'):
            val = (s.get(key) or '').strip().lower()
            if val and (val == raw or val in raw or raw in val):
                return (s.get('name_en') or s.get('id') or val).capitalize()
        for a in (s.get('aliases') or []):
            av = str(a).strip().lower()
            if av and (av == raw or av in raw or raw in av):
                return (s.get('name_en') or s.get('id') or av).capitalize()
    return style_name.strip().capitalize() if style_name else 'Casual'


def build_design_name(item_type: str, attrs: Dict[str, str], dataset: Dict) -> str:
    """
    Xây dựng tên thiết kế ở backend từ item_type (đã chuẩn hóa) và thuộc tính thị giác.
    Không dùng AI đặt tên — chỉ tập luật + dataset, đảm bảo nhất quán và khả mở rộng.
    """
    if not item_type or not isinstance(item_type, str):
        return 'Trang phục'
    name = item_type.strip()
    parts = []
    for key in ('material', 'fit', 'pattern'):
        val = (attrs.get(key) or '').strip()
        if not val:
            continue
        # Chuẩn hóa theo từ vựng dataset nếu có
        attr_list = (dataset.get('attributes') or {}).get(key)
        if isinstance(attr_list, list) and val.lower() not in [str(x).lower() for x in attr_list]:
            for opt in attr_list:
                if opt and (val.lower() in str(opt).lower() or str(opt).lower() in val.lower()):
                    val = str(opt)
                    break
        parts.append(val)
    if parts:
        name += ' – ' + ', '.join(parts)
    return name


def _get_style_id(style_name: str, dataset: Dict) -> str:
    """Ánh xạ tên phong cách về id (lowercase) trong dataset."""
    if not style_name or not isinstance(style_name, str):
        return 'casual'
    raw = style_name.strip().lower()
    styles = dataset.get('styles') or []
    for s in styles:
        if not isinstance(s, dict):
            continue
        sid = (s.get('id') or '').strip().lower()
        name_en = (s.get('name_en') or '').strip().lower()
        name_vi = (s.get('name_vi') or '').strip().lower()
        if sid:
            if raw == sid or raw in sid or sid in raw or raw == name_en or raw == name_vi:
                return sid
            for a in (s.get('aliases') or []):
                if str(a).strip().lower() == raw:
                    return sid
    return raw or 'casual'


def _apply_item_style_rules_to_detected(detected_styles: List[Dict], item_name: str, dataset: Dict) -> List[Dict]:
    """
    Nếu món đồ có trong item_style_rules thì chỉ hiển thị các phong cách theo rules:
    dùng score từ AI nếu AI trả về style đó, style trong rules mà AI không trả thì cho score mặc định 0.5.
    """
    rules = dataset.get('item_style_rules') or {}
    if not isinstance(rules, dict):
        return detected_styles
    rules_list = rules.get(item_name) if item_name else []
    if not rules_list or not isinstance(rules_list, list):
        return detected_styles
    # Map AI styles -> score
    ai_scores = {}
    for entry in (detected_styles or []):
        if isinstance(entry, dict):
            s = (entry.get('style') or entry.get('name') or '').strip().lower()
            if s:
                ai_scores[s] = float(entry.get('score') or 0.5)
    # Build list theo thứ tự rules, score từ AI hoặc 0.5
    out = []
    for style_key in rules_list:
        sk = str(style_key).strip().lower()
        if not sk:
            continue
        style_id = _get_style_id(sk, dataset)
        score = ai_scores.get(style_id.lower() if style_id else sk, 0.5)
        # Trùng style_id (nếu nhiều key map cùng id) thì không thêm trùng
        if not any((o.get('style') or '').lower() == (style_id or '').lower() for o in out):
            out.append({'style': style_id or sk, 'score': round(score, 2)})
    return out if out else detected_styles


def _build_reasons_from_item_style_rules(item_name: str, final_style: str, dataset: Dict) -> List[str]:
    """Tạo reason bằng TIẾNG VIỆT từ item_style_rules khi AI không cung cấp đủ."""
    rules = dataset.get('item_style_rules') or {}
    rules_list = rules.get(item_name) if isinstance(rules.get(item_name), list) else []
    if not rules_list or final_style.lower() not in [str(x).lower() for x in rules_list]:
        return []
    style_vi = to_vietnamese_style(final_style)
    item_vi = to_vietnamese_item(item_name)
    reasons = []
    if item_vi:
        reasons.append(f"{item_vi} thường xuất hiện trong phong cách {style_vi}")
    return reasons[:3]


def _reasons_are_vietnamese(reasons: List[str]) -> bool:
    joined = ' '.join(str(r).strip() for r in (reasons or []) if str(r).strip())
    if not joined:
        return False
    return _looks_vietnamese_primary(joined)


def _build_reason_vi_from_item(it: Dict, dataset: Dict) -> List[str]:
    """Sinh lý do tiếng Việt từ attributes / rules khi AI trả tiếng Anh hoặc thiếu."""
    _, attr_reasons = _attribute_style_scores(it, dataset)
    if attr_reasons:
        return attr_reasons[:3]
    item_name = str(it.get('item') or it.get('item_type') or '').strip()
    final_style = str(it.get('final_style') or it.get('style') or 'casual').strip()
    rule_reasons = _build_reasons_from_item_style_rules(item_name, final_style, dataset)
    if rule_reasons:
        return rule_reasons
    item_vi = to_vietnamese_item(item_name)
    style_vi = to_vietnamese_style(final_style)
    if item_vi and style_vi:
        return [f'{item_vi} thể hiện đặc trưng phong cách {style_vi} qua form dáng và chi tiết trong ảnh.']
    return ['Phân tích dựa trên đặc điểm trang phục nhìn thấy trong ảnh.']


def _ensure_item_reasons_vietnamese(it: Dict, dataset: Dict) -> None:
    if not isinstance(it, dict):
        return
    reasons = it.get('reason')
    if not isinstance(reasons, list):
        reasons = []
    cleaned = [str(x).strip() for x in reasons if str(x).strip()]
    if cleaned and _reasons_are_vietnamese(cleaned):
        it['reason'] = cleaned[:3]
        return
    it['reason'] = _build_reason_vi_from_item(it, dataset)


def _ensure_all_items_reasons_vietnamese(out: Dict, dataset: Dict) -> None:
    items = out.get('items') if isinstance(out, dict) else None
    if not isinstance(items, list):
        return
    for it in items:
        _ensure_item_reasons_vietnamese(it, dataset)


def _ensure_final_style_in_detected_list(it: Dict, dataset: Dict) -> None:
    """
    Nếu phong cách chính (final_style, sau bỏ phiếu) không có trong detected_styles
    (ví dụ sau item_style_rules), chèn thêm một dòng để UI «Phong cách phát hiện» khớp «Phong cách chính».
    Điểm score: ưu tiên confidence món; không thấp hơn tối đa các dòng hiện có (ít nhất ngang hạng thắng).
    """
    final = it.get('final_style')
    if not final:
        return
    sid = (_get_style_id(str(final), dataset) or 'casual').strip()
    sid_l = sid.lower()
    detected = it.get('detected_styles')
    if not isinstance(detected, list):
        it['detected_styles'] = []
        detected = it['detected_styles']

    def _row_style_key(row: Any) -> str:
        if not isinstance(row, dict):
            return ''
        return (row.get('style') or row.get('name') or '').strip().lower()

    for row in detected:
        if _row_style_key(row) == sid_l:
            return

    conf = it.get('confidence')
    base_sc = 0.9
    if isinstance(conf, (int, float)):
        v = float(conf)
        base_sc = (v / 100.0) if v > 1.0 else v
        base_sc = min(1.0, max(0.0, base_sc))
    mx = 0.0
    for row in detected:
        if isinstance(row, dict):
            try:
                mx = max(mx, float(row.get('score') or 0))
            except (TypeError, ValueError):
                pass
    sc = max(base_sc, min(1.0, mx + 0.01)) if mx > 0 else max(base_sc, 0.85)
    detected.insert(0, {'style': sid_l, 'score': round(sc, 2)})


def _attribute_style_scores(it: Dict, dataset: Dict) -> Tuple[Dict[str, float], List[str]]:
    """
    Suy luận phong cách từ attributes (fit/material/pattern/silhouette/color_tone/details/length/layer/season).
    Trả về (scores, reasons_vi). Scores nằm trong [0..1] (tương đối), dùng để chọn final_style.
    """
    scores: Dict[str, float] = {}
    reasons: List[str] = []

    def add(style_id: str, w: float, why: str):
        sid = (_get_style_id(style_id, dataset) or 'casual').strip().lower()
        scores[sid] = scores.get(sid, 0.0) + float(w)
        if why and why not in reasons:
            reasons.append(why)

    # Chuẩn hoá input
    fit = str(it.get('fit') or '').strip().lower()
    material = str(it.get('material') or '').strip().lower()
    pattern = str(it.get('pattern') or '').strip().lower()
    silhouette = str(it.get('silhouette') or '').strip().lower()
    color_tone = str(it.get('color_tone') or '').strip().lower()
    length = str(it.get('length') or '').strip().lower()
    layer = str(it.get('layer') or '').strip().lower()
    season = str(it.get('season') or '').strip().lower()
    details = it.get('details') or []
    if isinstance(details, str):
        details = [details]
    if not isinstance(details, list):
        details = []
    details = [str(x).strip().lower() for x in details if str(x).strip()]

    item_name = str(it.get('item') or it.get('item_type') or '').strip().lower()
    category = str(it.get('category') or '').strip().lower()

    # ---- Rules: fit / silhouette ----
    if fit in ('oversize', 'oversized'):
        add('streetwear', 0.35, 'Form oversize thường gặp trong streetwear')
        add('hip_hop', 0.20, 'Form oversize là dấu hiệu phổ biến trong hip hop')
        add('grunge', 0.10, 'Form rộng tạo cảm giác casual/grunge')
    elif fit in ('slim', 'tailored'):
        add('formal', 0.25, 'Phom dáng slim/tailored thường hợp phong cách formal')
        add('classic', 0.20, 'Phom dáng chỉn chu gợi phong cách classic')
        add('business', 0.15, 'Phom dáng gọn gàng hợp môi trường công sở')
    elif fit in ('regular', 'relaxed'):
        add('casual', 0.15, 'Phom dáng cơ bản phù hợp phong cách casual')
        add('minimalist', 0.10, 'Phom đơn giản dễ hướng minimalist')

    if silhouette in ('structured',):
        add('formal', 0.20, 'Silhouette structured thường gắn với đồ form đứng, formal')
        add('classic', 0.15, 'Form đứng tạo cảm giác classic')
    elif silhouette in ('flowy',):
        add('bohemian', 0.20, 'Silhouette bay bổng thường gặp trong bohemian')
        add('romantic', 0.15, 'Chất rũ/flowy tạo cảm giác romantic')
    elif silhouette in ('fitted',):
        add('chic', 0.15, 'Silhouette ôm/fitted thường hướng chic')
        add('sexy', 0.15, 'Form ôm là dấu hiệu thường gặp của sexy')

    # ---- Rules: material ----
    if material in ('tweed',):
        add('old_money', 0.25, 'Chất liệu tweed thường gắn với old money')
        add('classic', 0.20, 'Tweed là chất liệu kinh điển của classic')
        add('preppy', 0.15, 'Tweed cũng thường xuất hiện trong preppy')
    if material in ('wool',):
        add('classic', 0.18, 'Len/wool thường hợp classic')
        add('formal', 0.10, 'Len form đứng có thể hướng formal')
        add('dark_academia', 0.10, 'Wool hay gặp trong dark academia')
    if material in ('denim',):
        add('streetwear', 0.18, 'Denim là chất liệu phổ biến trong streetwear')
        add('vintage', 0.14, 'Denim dễ tạo cảm giác vintage')
        add('casual', 0.12, 'Denim phổ biến trong casual')
    if material in ('leather',):
        add('punk', 0.22, 'Leather thường gắn với punk')
        add('gothic', 0.16, 'Leather phối tông tối dễ hướng gothic')
        add('streetwear', 0.10, 'Leather jacket cũng hay gặp trong streetwear')
    if material in ('silk', 'satin'):
        add('elegant', 0.20, 'Silk/satin thường tạo cảm giác elegant')
        add('romantic', 0.12, 'Chất liệu mềm bóng dễ hướng romantic')
        add('formal', 0.08, 'Có thể dùng trong dịp formal')
    if material in ('mesh',):
        add('egirl_eboy', 0.18, 'Mesh/lưới thường gặp trong egirl/eboy')
        add('cyberpunk', 0.12, 'Mesh dễ tạo cảm giác cyberpunk')
        add('streetwear', 0.08, 'Mesh cũng xuất hiện trong streetwear')

    # ---- Rules: pattern ----
    if pattern in ('plaid',):
        add('preppy', 0.18, 'Hoạ tiết plaid thường gặp trong preppy')
        add('dark_academia', 0.16, 'Plaid là dấu hiệu đặc trưng của dark academia')
        add('vintage', 0.10, 'Plaid cũng hay gặp trong vintage')
    if pattern in ('striped',):
        add('classic', 0.10, 'Kẻ sọc thường hợp classic')
        add('parisian_style', 0.10, 'Kẻ sọc cũng hay gặp trong parisian style')
    if pattern in ('floral',):
        add('romantic', 0.18, 'Hoạ tiết hoa thường hướng romantic')
        add('bohemian', 0.12, 'Hoa/vải mềm thường hướng bohemian')
        add('cottagecore', 0.10, 'Floral cũng hay gặp cottagecore')
    if pattern in ('graphic',):
        add('streetwear', 0.16, 'Graphic thường gặp trong streetwear')
        add('y2k', 0.12, 'Graphic nổi bật có thể hướng Y2K')
        add('hip_hop', 0.10, 'Graphic + phụ kiện nổi có thể hướng hip hop')
    if pattern in ('animal-print', 'animal print'):
        add('sexy', 0.18, 'Animal print thường hướng sexy')
        add('chic', 0.10, 'Animal print cũng có thể hướng chic')
    if pattern in ('solid',):
        add('minimalist', 0.10, 'Đồ trơn dễ hướng minimalist')
        add('classic', 0.06, 'Đồ trơn cũng phù hợp classic')

    # ---- Rules: color_tone ----
    if color_tone in ('dark',):
        add('gothic', 0.18, 'Tông tối thường gặp trong gothic')
        add('dark_academia', 0.14, 'Tông tối dễ hướng dark academia')
    if color_tone in ('neutral', 'monochrome', 'muted'):
        add('minimalist', 0.16, 'Tông neutral/monochrome thường hướng minimalist')
        add('old_money', 0.08, 'Tông trung tính cũng hợp old money')
        add('classic', 0.08, 'Tông trung tính hợp classic')
    if color_tone in ('bright', 'neon', 'metallic'):
        add('y2k', 0.14, 'Màu nổi/neon thường gặp trong Y2K')
        add('cyberpunk', 0.12, 'Neon/metallic dễ hướng cyberpunk')

    # ---- Rules: details ----
    if 'lace' in details:
        add('romantic', 0.18, 'Chi tiết ren thường hướng romantic')
        add('coquette', 0.14, 'Ren cũng hay gặp trong coquette')
        add('feminine', 0.10, 'Ren tạo cảm giác feminine')
    if 'ruffled' in details or 'pleated' in details:
        add('romantic', 0.12, 'Chi tiết bèo/xếp ly thường hướng romantic')
        add('coquette', 0.10, 'Xếp ly/bèo nhún cũng hay gặp trong coquette')
        add('preppy', 0.08, 'Xếp ly có thể hướng preppy')
    if 'distressed' in details:
        add('grunge', 0.20, 'Chi tiết distressed thường gắn với grunge')
        add('streetwear', 0.10, 'Distressed cũng hay gặp streetwear')
    if 'embroidery' in details:
        add('cultural', 0.12, 'Thêu/embroidery có thể hướng cultural')
        add('bohemian', 0.08, 'Embroidery cũng hay gặp bohemian')
    if 'cut-out' in details or 'cutout' in details:
        add('sexy', 0.16, 'Cut-out thường hướng sexy')
        add('y2k', 0.10, 'Cut-out cũng hay gặp Y2K')
    if 'minimal-detail' in details:
        add('minimalist', 0.14, 'Ít chi tiết thường hướng minimalist')

    # ---- Rules: length / layer / season (nhẹ) ----
    if length in ('cropped',):
        add('y2k', 0.10, 'Cropped thường gặp trong Y2K')
        add('chic', 0.06, 'Cropped cũng có thể hướng chic')
    if layer in ('outer',):
        add('streetwear', 0.06, 'Layer outer thường gặp trong streetwear')
        add('classic', 0.04, 'Layer outer cũng thường dùng trong classic')
    if season in ('winter',):
        add('classic', 0.04, 'Trang phục mùa lạnh hay gặp trong classic')
        add('dark_academia', 0.04, 'Mùa lạnh cũng hợp dark academia')

    # ---- Item/category priors (nhẹ, không thay thế attributes) ----
    # Giữ rất nhỏ để tránh "cứng" như item_style_rules
    if 'ao dai' in item_name or 'áo dài' in item_name:
        add('cultural', 0.25, 'Trang phục truyền thống thường thuộc phong cách cultural')
        add('formal', 0.10, 'Áo dài thường dùng trong dịp trang trọng')
        add('elegant', 0.10, 'Áo dài thường mang cảm giác thanh lịch')
    if category in ('uniform',) or 'uniform' in item_name or 'scrubs' in item_name:
        add('uniform', 0.25, 'Đồng phục/scrubs thường thuộc phong cách uniform')

    # Chuẩn hoá scores về 0..1 tương đối
    if not scores:
        return {}, []
    mx = max(scores.values()) or 1.0
    for k in list(scores.keys()):
        scores[k] = max(0.0, min(1.0, scores[k] / mx))
    return scores, reasons[:3]


def _apply_attribute_first_style(it: Dict, dataset: Dict) -> bool:
    """
    Nếu suy luận từ attributes đủ mạnh, ghi đè final_style/detected_styles/reason.
    Trả True nếu đã áp dụng.
    """
    # Nếu item đã có kết quả bỏ phiếu từ ensemble thì KHÔNG được ghi đè final_style nữa.
    if isinstance(it, dict) and it.get('vote_debug') is not None:
        return False
    scores, reasons = _attribute_style_scores(it, dataset)
    if not scores:
        return False
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_sid, top_sc = ranked[0]
    second_sc = ranked[1][1] if len(ranked) > 1 else 0.0

    # Ngưỡng áp dụng: cần đủ "chênh lệch" và có ít nhất một attribute cốt lõi
    has_core_attr = any(
        str(it.get(k) or '').strip()
        for k in ('fit', 'material', 'pattern', 'silhouette', 'color_tone', 'length', 'layer')
    ) or (isinstance(it.get('details'), list) and len(it.get('details')) > 0)
    if not has_core_attr:
        return False
    if top_sc < 0.55 or (top_sc - second_sc) < 0.12:
        return False

    it['final_style'] = top_sid
    it['confidence'] = max(float(it.get('confidence') or 0.0), float(top_sc))
    it['detected_styles'] = [{'style': sid, 'score': round(sc, 2)} for sid, sc in ranked[:5]]
    if reasons:
        it['reason'] = reasons
    # Dùng cho aggregate_styles (expects capitalized label)
    it['style'] = (top_sid or 'casual').capitalize()
    _ensure_final_style_in_detected_list(it, dataset)
    return True


def _dedup_items_postprocess(items: List[Dict], dataset: Dict) -> List[Dict]:
    """
    Gộp các near-duplicate sau khi đã normalize + vote style:
    - Các món trùng (category+item hoặc nhánh Bottomwear/Skirts, Headwear/Acc Soft-Hard + cùng item_type) giữ bản confidence cao hơn.
    - Nhóm túi (bag/handbag/shoulder bag/...) chỉ giữ 1 món có confidence cao nhất.
    - Nhóm vớ (socks/crew socks/...) chỉ giữ 1 món nếu trùng ngữ nghĩa.
    """
    if not isinstance(items, list) or not items:
        return items

    def key_cat_item(it: Dict) -> Tuple[str, str]:
        cat = str(it.get('category') or '')
        raw_t = str(it.get('item') or it.get('item_type') or '')
        return _oneshot_merge_group_key(cat, raw_t)

    def conf(it: Dict) -> float:
        try:
            return float(it.get('confidence') or 0.0)
        except (TypeError, ValueError):
            return 0.0

    # 1) Dedup exact same (category,item)
    best_by_exact: Dict[Tuple[str, str], Dict] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        k = key_cat_item(it)
        if not k[1]:
            continue
        prev = best_by_exact.get(k)
        if not prev or conf(it) > conf(prev):
            best_by_exact[k] = it
    out = list(best_by_exact.values())

    # 2) Bag group: giữ 1 món túi có conf cao nhất
    bag_markers = (
        'bag', 'handbag', 'shoulder bag', 'crossbody', 'tote', 'backpack', 'clutch',
        'satchel', 'hobo bag', 'bucket bag', 'messenger bag', 'briefcase', 'belt bag',
        'waist bag', 'mini bag', 'wristlet', 'evening bag', 'duffel bag', 'weekender bag',
        'laptop bag',
    )

    def is_bag(it: Dict) -> bool:
        t = _norm_key(str(it.get('item') or it.get('item_type') or ''))
        c = _norm_key(str(it.get('category') or ''))
        if 'accessories_hard' in c or 'accessories_soft' in c or 'accessory' in c or 'accessories' in c:
            return any(m in t for m in bag_markers)
        return any(m in t for m in bag_markers)

    bags = [it for it in out if isinstance(it, dict) and is_bag(it)]
    if len(bags) > 1:
        keep = max(bags, key=conf)
        out = [it for it in out if (not is_bag(it)) or it is keep]

    # 3) Socks group: socks vs crew socks… giữ 1 nếu nhiều biến thể được phát hiện
    def is_socks(it: Dict) -> bool:
        t = _norm_key(str(it.get('item') or it.get('item_type') or ''))
        return 'sock' in t or 'socks' in t or 'stocking' in t or 'tights' in t

    socks = [it for it in out if isinstance(it, dict) and is_socks(it)]
    if len(socks) > 1:
        keep = max(socks, key=conf)
        out = [it for it in out if (not is_socks(it)) or it is keep]

    return out


# Mỗi “ô” trang phục (nón, áo trong, lớp ngoài, …) chỉ 1 món; phụ kiện có thể nhiều.
_WARDROBE_SLOT_ACCESSORY_CATS = frozenset({'accessories_soft', 'accessories_hard', 'jewelry'})
_WARDROBE_SLOTS: List[Tuple[frozenset, int]] = [
    (frozenset({'headwear'}), 1),
    (frozenset({'topwear_inner'}), 1),
    (frozenset({'topwear_outer', 'jackets_coats'}), 1),
    (frozenset({'bottomwear'}), 1),
    (frozenset({'skirts'}), 1),
    (frozenset({'dresses'}), 1),
    (frozenset({'footwear_shoes', 'footwear_sandals'}), 1),
]


def _limit_wardrobe_slot_items(items: List[Dict], _dataset: Dict) -> List[Dict]:
    """
    Giữ tối đa 1 item / slot trang phục (ưu tiên confidence), gộp Jackets_Coats + Topwear_Outer
    và gộp giày/dép cùng một slot. Accessories_Soft/Hard và Jewelry không giới hạn.
    Category không khớp slot nào → giữ nguyên.
    """
    if not isinstance(items, list) or not items:
        return items

    def conf(it: Dict) -> float:
        try:
            return float(it.get('confidence') or 0.0)
        except (TypeError, ValueError):
            return 0.0

    by_slot: Dict[int, List[Dict]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        c = _norm_key(str(it.get('category') or ''))
        if c in _WARDROBE_SLOT_ACCESSORY_CATS:
            continue
        for i, (cats, _) in enumerate(_WARDROBE_SLOTS):
            if c in cats:
                by_slot.setdefault(i, []).append(it)
                break

    keep_ids = set()
    for i, (_, max_n) in enumerate(_WARDROBE_SLOTS):
        group = by_slot.get(i, [])
        for it in sorted(group, key=conf, reverse=True)[:max_n]:
            keep_ids.add(id(it))

    out: List[Dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        c = _norm_key(str(it.get('category') or ''))
        if c in _WARDROBE_SLOT_ACCESSORY_CATS:
            out.append(it)
            continue
        matched = False
        for i, (cats, _) in enumerate(_WARDROBE_SLOTS):
            if c in cats:
                matched = True
                if id(it) in keep_ids:
                    out.append(it)
                break
        if not matched:
            out.append(it)
    return out


_ITEM_KEEP_CATEGORY_PRIORITY: Dict[str, float] = {
    'dresses': 3.0,
    'jackets_coats': 2.8,
    'topwear_outer': 2.7,
    'topwear_inner': 2.5,
    'bottomwear': 2.5,
    'skirts': 2.5,
    'footwear_shoes': 2.2,
    'footwear_sandals': 2.0,
    'headwear': 1.8,
    'accessories_hard': 1.5,
    'accessories_soft': 1.4,
    'jewelry': 1.2,
}


def _item_keep_rank(it: Dict) -> Tuple[float, float]:
    """Điểm ưu tiên khi cắt bớt món: confidence cao + category trang phục chính."""
    try:
        conf = float(it.get('confidence') or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    cat = _norm_key(str(it.get('category') or ''))
    pri = _ITEM_KEEP_CATEGORY_PRIORITY.get(cat, 1.0)
    return (conf, pri)


def _limit_max_items(items: List[Dict], max_n: int) -> List[Dict]:
    """Giữ tối đa max_n món / ảnh — ưu tiên confidence và món trang phục chính."""
    if not isinstance(items, list) or not items or max_n <= 0:
        return items
    if len(items) <= max_n:
        return items
    ranked = sorted(
        (it for it in items if isinstance(it, dict)),
        key=_item_keep_rank,
        reverse=True,
    )
    return ranked[:max_n]


# Gộp toàn bộ items[] sau merger: map từng field + áp item_style_rules + reason fallback

def normalize_ai_response_to_dataset(out: Dict, dataset: Dict) -> None:
    """
    Chuẩn hóa kết quả AI theo dataset ở backend (sửa tại chỗ).
    Category, item/item_type và style được map về đúng giá trị trong dataset.
    Hỗ trợ schema mới: item, detected_styles, final_style, confidence, reason.
    """
    if not dataset or 'items' not in out or not isinstance(out['items'], list):
        return
    for it in out['items']:
        cat = it.get('category')
        if cat is not None:
            it['category'] = _normalize_category_to_dataset(cat, dataset)
        # item hoặc item_type
        it_name = it.get('item') or it.get('item_type')
        if it_name is not None and it.get('category'):
            raw_item = it_name if isinstance(it_name, str) else str(it_name)
            cat_now = it.get('category', '')
            normalized = _normalize_item_type_to_dataset(raw_item, cat_now, dataset)
            it['item'] = normalized
            if 'item_type' in it:
                it['item_type'] = normalized
            fixed_cat = _correct_category_for_item_type(it.get('category', ''), normalized, dataset)
            if fixed_cat != it.get('category'):
                it['category'] = _normalize_category_to_dataset(fixed_cat, dataset)
                normalized2 = _normalize_item_type_to_dataset(
                    normalized, it.get('category', ''), dataset
                )
                if normalized2:
                    it['item'] = normalized2
                    if 'item_type' in it:
                        it['item_type'] = normalized2
                    normalized = normalized2
            learn_item_from_analysis(it.get('category', ''), raw_item, normalized, dataset)
        # Chuẩn hóa detected_styles (style id + score)
        detected = it.get('detected_styles')
        if isinstance(detected, list) and detected:
            for entry in detected:
                if isinstance(entry, dict):
                    name = entry.get('style') or entry.get('name') or ''
                    entry['style'] = _get_style_id(str(name), dataset)
                    if 'score' not in entry and 'confidence' in entry:
                        c = entry['confidence']
                        entry['score'] = (c / 100.0) if isinstance(c, (int, float)) and c > 1 else float(c or 0)
        # styles cũ → detected_styles mới
        styles_arr = it.get('styles')
        if isinstance(styles_arr, list) and styles_arr and not (isinstance(detected, list) and detected):
            it['detected_styles'] = []
            for entry in styles_arr:
                if isinstance(entry, dict):
                    name = entry.get('name') or entry.get('style') or ''
                    conf = entry.get('confidence') or entry.get('score') or 0
                    score = (conf / 100.0) if isinstance(conf, (int, float)) and conf > 1 else float(conf or 0)
                    it['detected_styles'].append({
                        'style': _get_style_id(str(name), dataset),
                        'score': round(score, 2)
                    })
        # final_style
        final = it.get('final_style') or it.get('style')
        if final is not None:
            it['final_style'] = _get_style_id(str(final), dataset)
        # [Mức A] Đã bỏ item_style_rules — để AI tự do gán phong cách theo ngữ cảnh ảnh
        # (vd. cho phép "formal hoodie", "elegant sneakers"). Trước đây dùng:
        #     it['detected_styles'] = _apply_item_style_rules_to_detected(detected, item_name, dataset)
        if not isinstance(it.get('detected_styles'), list):
            it['detected_styles'] = []
        _ensure_final_style_in_detected_list(it, dataset)
        # [Mức A] Đã bỏ fallback reason theo item_style_rules — giữ reason nguyên gốc từ AI.
        # Nếu AI không trả reason, để rỗng [] (UI tự động ẩn dòng "Lý do"). Trước đây dùng:
        #     it['reason'] = _build_reasons_from_item_style_rules(item_name, final_style, dataset)
        if not isinstance(it.get('reason'), list):
            it['reason'] = []
        _ensure_item_reasons_vietnamese(it, dataset)


# -----------------------------------------------------------------------------
# §5 — Đọc occasions_by_style từ dataset (hoặc mặc định) cho gợi ý dịp
# -----------------------------------------------------------------------------

def get_occasion_map_from_dataset(dataset: Dict) -> Dict[str, List[str]]:
    """Lấy OCCASION_MAP từ dataset, fallback mặc định nếu thiếu."""
    occ = dataset.get('occasions_by_style')
    if isinstance(occ, dict):
        return occ
    return {
        'casual': ['Đi học', 'Đi chơi', 'Dạo phố', 'Gặp bạn bè'],
        'streetwear': ['Dạo phố', 'Sự kiện trẻ', 'Concert', 'Chụp ảnh'],
        'sporty': ['Tập gym', 'Chạy bộ', 'Thể thao', 'Đi dạo'],
        'formal': ['Hội nghị', 'Công sở', 'Phỏng vấn', 'Sự kiện trang trọng'],
        'classic': ['Công sở', 'Tiệc nhẹ', 'Hẹn hò', 'Du lịch'],
        'bohemian': ['Festival', 'Du lịch', 'Picnic', 'Sự kiện nghệ thuật'],
        'minimalist': ['Công sở', 'Đi chơi', 'Cafe', 'Mua sắm'],
    }


def get_style_en_to_vi_from_dataset(dataset: Dict) -> Dict[str, str]:
    """Lấy mapping phong cách EN → VI từ dataset."""
    styles = dataset.get('styles') or []
    out = {}
    for s in styles:
        if not isinstance(s, dict):
            continue
        sid = (s.get('id') or s.get('name_en') or '').strip().lower()
        name_vi = (s.get('name_vi') or s.get('name_en') or sid or '').strip()
        name_en = (s.get('name_en') or sid or '').strip().lower()
        if sid:
            out[sid] = name_vi
        if name_en and name_en not in out:
            out[name_en] = name_vi
        for a in (s.get('aliases') or []):
            ak = str(a).strip().lower()
            if ak and ak not in out:
                out[ak] = name_vi
    if not out:
        out = {
            'casual': 'Thường ngày', 'formal': 'Trang trọng', 'sporty': 'Thể thao',
            'streetwear': 'Streetwear', 'classic': 'Cổ điển', 'minimalist': 'Tối giản',
            'bohemian': 'Bohemian',
        }
    return out


# -----------------------------------------------------------------------------
# §6 — Load một lần khi import: map dịp + EN→VI style; bảng dịch category/item cho UI
# -----------------------------------------------------------------------------
_dataset = load_fashion_dataset()
OCCASION_MAP = get_occasion_map_from_dataset(_dataset)
STYLE_EN_TO_VI = get_style_en_to_vi_from_dataset(_dataset)

# Category kiểu Headwear / Bottomwear… → nhãn hiển thị (bổ sung cho từ khóa EN cũ)
CATEGORY_EN_TO_VI = {
    'top': 'Áo', 'bottom': 'Quần', 'footwear': 'Giày', 'outerwear': 'Áo khoác',
    'accessory': 'Phụ kiện', 'accessories': 'Phụ kiện', 'jewelry': 'Trang sức',
    'dress': 'Váy', 'skirt': 'Váy', 'shoes': 'Giày', 'bag': 'Phụ kiện',
    'headwear': 'Nón/Mũ', 'topwear_inner': 'Áo trong', 'topwear_outer': 'Áo khoác ngoài',
    'bottomwear': 'Quần', 'skirts': 'Chân váy', 'dresses': 'Đầm',
    'footwear_shoes': 'Giày', 'footwear_sandals': 'Dép', 'accessories_soft': 'Phụ kiện mềm',
    'accessories_hard': 'Phụ kiện cứng',
}


def to_vietnamese_category(cat: str) -> str:
    """Chuẩn hóa category để hiển thị tiếng Việt."""
    if not cat or not isinstance(cat, str):
        return 'Phụ kiện'
    key = cat.strip().lower()
    return CATEGORY_EN_TO_VI.get(key, cat.strip())


def to_vietnamese_style(style: str) -> str:
    """Chuẩn hóa phong cách để hiển thị tiếng Việt."""
    if not style or not isinstance(style, str):
        return 'Thường ngày'
    key = style.strip().lower()
    for en, vi in STYLE_EN_TO_VI.items():
        if en in key or key in en:
            return vi
    return style.strip()


# Mapping tên món đồ EN → VI (một số món phổ biến từ dataset)
ITEM_EN_TO_VI = {
    'button-up shirt': 'Áo sơ mi cài cúc', 't-shirt': 'Áo thun', 'oversized t-shirt': 'Áo thun oversize',
    'graphic t-shirt': 'Áo thun in hình', 'tank top': 'Áo ba lỗ', 'crop top': 'Áo crop',
    'hoodie': 'Áo hoodie', 'sweatshirt': 'Áo nỉ', 'cardigan': 'Áo cardigan',
    'blazer': 'Áo blazer', 'denim jacket': 'Áo khoác denim', 'leather jacket': 'Áo khoác da',
    'bomber jacket': 'Áo khoác bomber', 'trench coat': 'Áo trench',
    'jeans': 'Quần jean', 'straight-leg jeans': 'Quần jean ống thẳng', 'skinny jeans': 'Quần jean bó',
    'wide-leg jeans': 'Quần jean ống rộng', 'trousers': 'Quần âu', 'tailored trousers': 'Quần âu may đo',
    'cargo pants': 'Quần cargo', 'joggers': 'Quần jogger', 'shorts': 'Quần đùi',
    'sneakers': 'Giày sneaker', 'loafers': 'Giày loafer', 'oxford shoes': 'Giày oxford',
    'chelsea boots': 'Ủng chelsea', 'combat boots': 'Ủng combat',
    'eyeglasses': 'Mắt kính', 'sunglasses': 'Kính râm', 'goggles': 'Kính bảo hộ',
    'chinos': 'Quần chinos', 'socks': 'Vớ', 'crew socks': 'Vớ cổ',
    'baseball cap': 'Mũ lưỡi trai', 'bucket hat': 'Mũ bucket', 'beanie': 'Mũ len',
    'durag': 'Khăn Durag', 'flower crown': 'Vòng hoa đội đầu', 'platform sneakers': 'Giày sneaker đế cao',
    'chunky sneakers': 'Giày sneaker chunky', 'chest rig bag': 'Túi chest rig', 'noragi jacket': 'Áo Noragi',
    'mesh overlay dress': 'Đầm lưới phủ ngoài', 'corset dress': 'Đầm corset', 'football scarf': 'Khăn cổ bóng đá',
    'scrubs top': 'Áo scrubs (y tế)', 'lab coat': 'Áo blouse phòng lab / bác sĩ',
    'non la': 'Nón lá', 'khan dong': 'Khăn đóng', 'ba ba shirt': 'Áo bà ba',
    'ao dai tunic': 'Áo dài (thân áo)', 'ao dai shirt (men)': 'Áo dài nam', 'school uniform shirt': 'Áo sơ mi đồng phục học sinh',
    'yem bodice': 'Yếm', 'ao dai trousers': 'Quần áo dài', 'school uniform trousers': 'Quần đồng phục học sinh',
    'scrubs pants': 'Quần scrubs', 'pe gym shorts': 'Quần đùi thể dục (đồng phục)', 'school uniform skirt': 'Váy đồng phục học sinh',
    'ao dai dress': 'Áo dài (cả bộ)', 'ao dai school uniform': 'Áo dài đồng phục học sinh', 'ao tu than dress': 'Áo tứ thân',
    'wooden clogs': 'Guốc mộc', 'white canvas school shoes': 'Giày vải trắng (đồng phục)', 'school red neckerchief': 'Khăn quàng đỏ',
    'ao dai silk sash': 'Dải lụa thắt eo áo dài',
}

# Hậu tố (dài trước) → nhãn tiếng Việt; dùng khi không có trong ITEM_EN_TO_VI / item_i18n
_ITEM_SUFFIX_VI_RAW = [
    ('graphic t-shirt', 'Áo thun in hình'),
    ('oversized t-shirt', 'Áo thun oversize'),
    ('longline t-shirt', 'Áo thun dài'),
    ('long-sleeve t-shirt', 'Áo thun dài tay'),
    ('band t-shirt', 'Áo thun ban nhạc'),
    ('football jersey', 'Áo đấu bóng đá'),
    ('button-up shirt', 'Áo sơ mi cài cúc'),
    ('dress shirt', 'Áo sơ mi dress'),
    ('oxford shirt', 'Áo sơ mi Oxford'),
    ('flannel shirt', 'Áo sơ mi flannel'),
    ('polo shirt', 'Áo polo'),
    ('bowling shirt', 'Áo bowling'),
    ('cuban collar shirt', 'Áo cổ Cuban'),
    ('western shirt', 'Áo kiểu miền Tây'),
    ('rugby shirt', 'Áo rugby'),
    ('peasant blouse', 'Áo blouse nông dân'),
    ('silk blouse', 'Áo blouse lụa'),
    ('tube top', 'Áo ống'),
    ('tank top', 'Áo ba lỗ'),
    ('crop top', 'Áo crop'),
    ('mock neck top', 'Áo cổ lọ mock'),
    ('off-shoulder top', 'Áo trễ vai'),
    ('one-shoulder top', 'Áo một vai'),
    ('denim jacket', 'Áo khoác denim'),
    ('leather jacket', 'Áo khoác da'),
    ('bomber jacket', 'Áo khoác bomber'),
    ('trench coat', 'Áo trench'),
    ('blouson jacket', 'Áo blouson'),
    ('straight-leg jeans', 'Quần jean ống thẳng'),
    ('skinny jeans', 'Quần jean bó'),
    ('wide-leg jeans', 'Quần jean ống rộng'),
    ('baggy jeans', 'Quần jean rộng'),
    ('mom jeans', 'Quần jean mom'),
    ('cargo pants', 'Quần cargo'),
    ('track pants', 'Quần track'),
    ('tailored trousers', 'Quần âu may đo'),
    ('ankle boots', 'Giày/ủng cổ thấp'),
    ('chelsea boots', 'Ủng chelsea'),
    ('combat boots', 'Ủng combat'),
    ('ballet flats', 'Giày bệt ballet'),
    ('baseball cap', 'Mũ lưỡi trai'),
    ('bucket hat', 'Mũ bucket'),
    ('running cap', 'Mũ chạy bộ'),
    ('golf cap', 'Mũ golf'),
    ('military cap', 'Mũ kiểu quân đội'),
    ('beanie', 'Mũ len'),
    ('turtleneck', 'Áo cổ lọ'),
    ('bodysuit', 'Bodysuit'),
    ('backpack', 'Balo'),
    ('ankle socks', 'Vớ cổ chân'),
    ('knee socks', 'Vớ gối'),
    ('thigh highs', 'Tất đùi'),
    ('hair scarf', 'Khăn buộc tóc'),
    ('obi belt', 'Thắt lưng obi'),
    ('base layer top', 'Áo lót trong'),
    ('windbreaker', 'Áo gió'),
    ('down jacket', 'Áo phao'),
    ('puffer jacket', 'Áo phao dày'),
    ('parka', 'Áo parka'),
    ('anorak', 'Áo anorak'),
    ('raincoat', 'Áo mưa'),
    ('three-piece suit', 'Complet ba mảnh'),
    ('two-piece suit', 'Complet hai mảnh'),
    ('evening gown', 'Đầm dạ hội'),
    ('ball gown', 'Đầm dạ hội xòe'),
    ('maxi dress', 'Đầm maxi'),
    ('midi dress', 'Đầm midi'),
    ('mini dress', 'Đầm mini'),
    ('midi skirt', 'Chân váy midi'),
    ('mini skirt', 'Chân váy mini'),
    ('maxi skirt', 'Chân váy maxi'),
    ('a-line dress', 'Đầm chữ A'),
    ('a-line skirt', 'Chân váy chữ A'),
    ('pencil skirt', 'Chân váy bút chì'),
    ('pleated skirt', 'Chân váy xếp ly'),
    ('platform sneakers', 'Giày sneaker đế cao'),
    ('chunky sneakers', 'Giày sneaker chunky'),
    ('running shoes', 'Giày chạy bộ'),
    ('high heels', 'Giày cao gót'),
    ('oxford shoes', 'Giày oxford'),
    ('mule shoes', 'Guốc mules'),
    ('t-shirt', 'Áo thun'),
    ('sweatshirt', 'Áo nỉ'),
    ('cardigan', 'Áo cardigan'),
    ('hoodie', 'Áo hoodie'),
    ('blazer', 'Áo blazer'),
    ('vest', 'Áo gi-lê'),
    ('jumpsuit', 'Jumpsuit'),
    ('romper', 'Romper'),
    ('playsuit', 'Playsuit'),
    ('dungarees', 'Yếm quần'),
    ('overalls', 'Quần yếm'),
    ('jeans', 'Quần jean'),
    ('joggers', 'Quần jogger'),
    ('shorts', 'Quần đùi'),
    ('trousers', 'Quần âu'),
    ('leggings', 'Quần legging'),
    ('sneakers', 'Giày sneaker'),
    ('loafers', 'Giày loafer'),
    ('sandals', 'Dép sandal'),
    ('flip-flops', 'Dép tông'),
    ('mules', 'Guốc mules'),
    ('belt', 'Thắt lưng'),
    ('chinos', 'Quần chinos'),
    ('eyeglasses', 'Mắt kính'),
    ('necklace', 'Vòng cổ'),
    ('bracelet', 'Vòng tay'),
    ('earrings', 'Khuyên tai'),
    ('ring', 'Nhẫn'),
    ('watch', 'Đồng hồ'),
    ('sunglasses', 'Kính râm'),
    ('glasses', 'Kính'),
    ('gloves', 'Găng tay'),
    ('scarf', 'Khăn choàng'),
    ('wallet', 'Ví'),
    ('clutch', 'Clutch'),
    ('tote bag', 'Túi tote'),
    ('crossbody bag', 'Túi đeo chéo'),
    ('handbag', 'Túi xách'),
    ('socks', 'Vớ'),
    ('tights', 'Quần tất'),
    ('stockings', 'Tất'),
    ('dress', 'Đầm / váy đầm'),
    ('skirt', 'Chân váy'),
    ('shirt', 'Áo sơ mi'),
    ('blouse', 'Áo blouse'),
    ('top', 'Áo'),
    ('coat', 'Áo choàng'),
    ('jacket', 'Áo khoác'),
    ('boots', 'Giày/ủng'),
    ('shoes', 'Giày'),
    ('hat', 'Mũ'),
    ('cap', 'Mũ'),
    ('bag', 'Túi'),
]

_ITEM_SUFFIX_VI: List[Tuple[str, str]] = sorted(
    _ITEM_SUFFIX_VI_RAW,
    key=lambda x: len(x[0]),
    reverse=True,
)

_HEAD_PHRASE_VI = [
    ('wide-leg', 'ống rộng'),
    ('straight-leg', 'ống thẳng'),
    ('skinny-fit', 'ôm'),
    ('high-waist', 'eo cao'),
    ('low-rise', 'eo thấp'),
    ('cropped', 'crop'),
    ('longline', 'dài'),
    ('oversized', 'oversize'),
    ('fitted', 'ôm'),
    ('pleated', 'xếp ly'),
    ('distressed', 'rách / wash'),
    ('striped', 'kẻ sọc'),
    ('plaid', 'kẻ ca-rô'),
    ('printed', 'in họa tiết'),
    ('lace', 'ren'),
    ('mesh', 'lưới'),
    ('sheer', 'mỏng / xuyên thấu'),
    ('ribbed', 'dệt gân'),
    ('asymmetrical', 'bất đối xứng'),
    ('backless', 'hở lưng'),
    ('strapless', 'không dây'),
    ('halter', 'cổ yếm'),
    ('wrap', 'quấn'),
    ('belted', 'có thắt lưng'),
]


def _looks_vietnamese_primary(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    return bool(
        re.search(
            r'[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ'
            r'ÀÁẢÃẠĂẰẮẲẴẶÂẦẤẨẪẬÈÉẺẼẸÊỀẾỂỄỆÌÍỈĨỊÒÓỎÕỌÔỒỐỔỖỘƠỜỚỞỠỢÙÚỦŨỤƯỪỨỬỮỰỲÝỶỸỴĐ]',
            s,
        )
    )


def _polish_head_modifiers(head: str) -> str:
    """Thay cụm từ mô tả phổ biến ở phần đầu tên (EN) sang tiếng Việt."""
    if not head or not head.strip():
        return ''
    s = head.strip()
    sl = s.lower()
    for eng, vi in sorted(_HEAD_PHRASE_VI, key=lambda x: len(x[0]), reverse=True):
        e_low = eng.lower()
        idx = sl.find(e_low)
        if idx != -1:
            s = s[:idx] + vi + s[idx + len(eng):]
            sl = s.lower()
    return s.strip()


def _heuristic_item_vi(en: str) -> str:
    """Khớp hậu tố dài trước (tránh 'shirt' nuốt 't-shirt')."""
    low = en.lower().strip()
    for suf_en, label_vi in _ITEM_SUFFIX_VI:
        if not low.endswith(suf_en):
            continue
        if len(low) > len(suf_en):
            prev = low[len(low) - len(suf_en) - 1]
            if prev.isalpha() and prev != '-':
                continue
        n = len(suf_en)
        head_raw = en[: len(en) - n].strip().rstrip('-–, ')
        head_raw = re.sub(r'\s+', ' ', head_raw)
        if not head_raw:
            return label_vi
        head_vi = _polish_head_modifiers(head_raw)
        if not head_vi:
            head_vi = head_raw
        return f'{label_vi} {head_vi}'.strip()
    return en


def get_item_bilingual(item_name: str, dataset: Optional[Dict] = None) -> Tuple[str, str]:
    """
    Trả về (tên hiển thị EN, nhãn VI) cho một item_type/item trong dataset.
    Ưu tiên: item_i18n trong fashion_style_dataset.json → ITEM_EN_TO_VI → heuristic.
    Luôn có nhãn VI (không để trống); nếu chuỗi gốc đã là tiếng Việt thì trả (s, s).
    """
    ds = dataset if isinstance(dataset, dict) else load_fashion_dataset()
    en = (item_name or '').strip() or '—'

    if _looks_vietnamese_primary(en):
        return (en, en)

    key = en.lower()
    vi = ''
    extra = ds.get('item_i18n') if isinstance(ds.get('item_i18n'), dict) else {}
    if extra:
        vi = (extra.get(en) or extra.get(key) or '').strip()

    if not vi:
        vi = ITEM_EN_TO_VI.get(key, '').strip()
    if not vi:
        vi = _heuristic_item_vi(en).strip()

    if not vi:
        vi = f'Món thời trang (tên tiếng Anh): {en}'

    if vi.lower() == key:
        vi = f'Món thời trang (tên tiếng Anh): {en}'

    return (en, vi)


def to_vietnamese_item(item_name: str) -> str:
    """Nhãn tiếng Việt cho món đồ; dùng get_item_bilingual."""
    if not item_name or not isinstance(item_name, str):
        return ''
    return get_item_bilingual(item_name.strip(), None)[1]


def normalize_style(name: str) -> str:
    """Chuẩn hóa tên phong cách về dạng thường dùng."""
    if not name:
        return 'Casual'
    s = name.strip().lower()
    for key in OCCASION_MAP:
        if key in s or s in key:
            return key.capitalize()
    # Giữ nguyên dạng capitalize
    return s.capitalize() if s else 'Casual'


# -----------------------------------------------------------------------------
# §7 — Parse JSON từ text model (bỏ markdown ```); đọc file ảnh → data URL; gọi OpenRouter
# -----------------------------------------------------------------------------

def _salvage_items_from_truncated_json(text: str) -> dict:
    """Khi model cắt JSON giữa chừng, lấy các object trong items đã đóng ngoặc đủ."""
    raw = (text or '').strip()
    if not raw:
        return {'items': []}
    if '```json' in raw:
        raw = raw.split('```json', 1)[1].split('```', 1)[0].strip()
    elif '```' in raw:
        parts = raw.split('```')
        if len(parts) >= 2:
            raw = parts[1].strip()
    items_key = raw.find('"items"')
    if items_key == -1:
        return {'items': []}
    arr_start = raw.find('[', items_key)
    if arr_start == -1:
        return {'items': []}
    salvaged: List[Dict] = []
    i = arr_start + 1
    n = len(raw)
    while i < n:
        if raw[i] == '{':
            depth = 0
            in_str = False
            esc = False
            quote = ''
            closed = False
            for j in range(i, n):
                ch = raw[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == '\\':
                        esc = True
                    elif ch == quote:
                        in_str = False
                    continue
                if ch in ('"', "'"):
                    in_str = True
                    quote = ch
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(raw[i:j + 1])
                            if isinstance(obj, dict):
                                salvaged.append(obj)
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        closed = True
                        break
            if not closed:
                break
        elif raw[i] == ']':
            break
        else:
            i += 1
    return {'items': salvaged} if salvaged else {'items': []}


def extract_json_from_response(text: str) -> dict:
    """Tách JSON từ nội dung trả về (có thể có markdown code block / tiền tố 'json')."""
    import ast

    raw = (text or '').strip()
    if not raw:
        return {'items': []}

    # Bỏ markdown code block nếu có
    if '```json' in raw:
        raw = raw.split('```json', 1)[1].split('```', 1)[0].strip()
    elif '```' in raw:
        raw = raw.split('```', 1)[1].split('```', 1)[0].strip()

    # Một số model trả về dạng: "json { ... }" hoặc "JSON:\n{...}"
    lowered = raw.lstrip().lower()
    if lowered.startswith('json'):
        raw = raw.lstrip()[4:].lstrip(' :\n\r\t')

    # Tìm JSON chunk (có thể root là object `{}` hoặc array `[]`)
    i_obj = raw.find('{')
    i_arr = raw.find('[')
    if i_obj == -1 and i_arr == -1:
        return {'items': []}
    if i_obj == -1:
        start = i_arr
        open_ch, close_ch = '[', ']'
    elif i_arr == -1:
        start = i_obj
        open_ch, close_ch = '{', '}'
    else:
        start = min(i_obj, i_arr)
        open_ch, close_ch = ('{', '}') if start == i_obj else ('[', ']')

    depth = 0
    end = -1
    for i in range(start, len(raw)):
        ch = raw[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end == -1:
        salvaged = _salvage_items_from_truncated_json(raw)
        return salvaged if salvaged.get('items') else {'items': []}

    chunk = raw[start:end].strip()
    # Thử parse JSON chuẩn trước
    try:
        obj = json.loads(chunk)
        # Nếu root là list, wrap về dict schema {"items": [...]}
        if isinstance(obj, list):
            return {'items': obj}
        return obj if isinstance(obj, dict) else {'items': []}
    except json.JSONDecodeError:
        pass

    # Fallback nhẹ: một số model trả kiểu "python dict" (single quotes / True/False/None)
    # Chuyển JSON literals → Python literals rồi literal_eval.
    try:
        pyish = (
            chunk.replace('\r\n', '\n')
            .replace(': null', ': None')
            .replace(': true', ': True')
            .replace(': false', ': False')
        )
        obj = ast.literal_eval(pyish)
        if isinstance(obj, list):
            return {'items': obj}
        return obj if isinstance(obj, dict) else {'items': []}
    except Exception:
        salvaged = _salvage_items_from_truncated_json(raw)
        return salvaged if salvaged.get('items') else {'items': []}


def _openrouter_extra_body_for_model(model: str) -> Optional[Dict]:
    """Tắt reasoning cho Gemini 2.5 — tránh ăn hết max_tokens, JSON bị cắt giữa chừng."""
    if 'gemini' in (model or '').lower():
        return {'reasoning': {'effort': 'none'}}
    return None


def _read_image_base64(image_path: str) -> str:
    """Đọc ảnh và trả về data URL base64."""
    with open(image_path, 'rb') as f:
        data = f.read()
    b64 = base64.b64encode(data).decode('utf-8')
    ext = os.path.splitext(image_path)[1].lower().replace('.', '')
    mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else 'image/png'
    return f'data:{mime};base64,{b64}'


def _call_vision_api(base_url: str, api_key: str, model: str, image_data_url: str, prompt: str, max_tokens: int = 2048, timeout: int = 90) -> str:
    """Một lần gọi Chat Completions có image_url (ensemble: gọi 3 lần song song, khác `model`)."""
    import openai
    client = openai.OpenAI(base_url=base_url, api_key=api_key)
    kwargs = dict(
        model=model,
        messages=[
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': image_data_url}},
                ],
            }
        ],
        max_tokens=max_tokens,
        timeout=timeout,
        temperature=0,
    )
    extra_body = _openrouter_extra_body_for_model(model)
    if extra_body:
        kwargs['extra_body'] = extra_body
    # Ưu tiên ép JSON nếu provider hỗ trợ; nếu không hỗ trợ thì fallback call thường.
    try:
        response = client.chat.completions.create(
            **kwargs,
            response_format={'type': 'json_object'},
        )
    except Exception:
        response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or '{}'


def _call_chat_api(base_url: str, api_key: str, model: str, prompt: str, max_tokens: int = 2048, timeout: int = 90) -> str:
    """Gọi OpenRouter chỉ text (gợi ý mix; trước đây dùng cho merger ensemble — đã thay bằng code)."""
    import openai
    client = openai.OpenAI(base_url=base_url, api_key=api_key)
    kwargs = dict(
        model=model,
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=max_tokens,
        timeout=timeout,
        temperature=0,
    )
    try:
        response = client.chat.completions.create(
            **kwargs,
            response_format={'type': 'json_object'},
        )
    except Exception:
        response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or '{}'


def _is_connection_error(exc: Exception) -> bool:
    """True nếu lỗi kiểu mạng/timeout — analyze_image sẽ retry vài lần."""
    msg = (str(exc) or '').lower()
    if 'connection' in msg or 'connect' in msg or 'timeout' in msg or 'network' in msg:
        return True
    exc_name = type(exc).__name__
    if 'Connection' in exc_name or 'Timeout' in exc_name or 'Connect' in exc_name:
        return True
    return False


# -----------------------------------------------------------------------------
# §7b — Cổng kiểm tra ảnh đầu vào (người mẫu / OOTD / mannequin + trang phục thời trang)
# -----------------------------------------------------------------------------

_FASHION_GATE_PROMPT = (
    'Bạn là bộ lọc ảnh đầu vào cho hệ thống phân tích TRANG PHỤC THỜI TRANG.\n'
    'CHẤP NHẬN (accepted=true) CHỈ KHI ảnh có ĐÚNG MỘT chủ thể: một người HOẶC một ma-nơ-canh (mannequin) '
    'đang MẶC hoặc TRƯNG BÀY trang phục thời trang rõ ràng — gồm: người mẫu lookbook, người thật chụp OOTD/outfit, '
    'một ma-nơ-canh cửa hàng/studio. Chủ thể phụ ở xa/mờ không tính nếu không mặc trang phục nổi bật.\n'
    'TỪ CHỐI (accepted=false) khi: có từ HAI người/người mẫu trở lên (ảnh nhóm, runway nhiều người, street style đông người...); '
    'có từ hai ma-nơ-canh trở lên; vừa người vừa ma-nơ-canh cùng là chủ thể outfit; '
    'không có người/mannequin mặc đồ; chỉ phong cảnh/đồ vật/thức ăn/meme/screenshot; '
    'chỉ sản phẩm trải phẳng hoặc treo móc không có người/mannequin; chỉ cận mặt không thấy trang phục; '
    'ảnh không liên quan thời trang.\n'
    'subject_count: số người + ma-nơ-canh đang mặc/trưng bày trang phục thời trang (phải = 1 để accepted=true).\n'
    'confidence: 0..1 — độ chắc chắn vào quyết định accepted.\n'
    'reason_vi: 1 câu tiếng Việt ngắn giải thích (nếu từ chối, nêu vì sao không phù hợp).\n'
    'Trả ĐÚNG MỘT JSON, KHÔNG markdown:\n'
    '{"accepted":true,"subject_count":1,"confidence":0.92,"reason_vi":"..."}'
)


def _gate_model_from_config(config) -> str:
    explicit = (
        getattr(config, 'OPENROUTER_GATE_MODEL', None)
        or os.environ.get('OPENROUTER_GATE_MODEL')
        or ''
    ).strip()
    if explicit:
        return explicit
    models = _vision_models_from_config(config)
    return models[0] if models else 'google/gemini-2.5-flash'


def _fashion_gate_enabled(config) -> bool:
    if config is not None and hasattr(config, 'ENABLE_FASHION_IMAGE_GATE'):
        return bool(getattr(config, 'ENABLE_FASHION_IMAGE_GATE', True))
    raw = os.environ.get('ENABLE_FASHION_IMAGE_GATE')
    if raw is None or not str(raw).strip():
        return True
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


def validate_fashion_input_image(image_path: str, config) -> dict:
    """
    Kiểm tra ảnh có phù hợp phân tích outfit thời trang không (một chủ thể duy nhất).
    Trả {'accepted': bool, 'reason_vi': str, 'confidence': float|None, 'skipped': bool?}.
    Không có API key hoặc gate tắt → accepted=True (skipped).
    Lỗi mạng/API sau retry → fail-open (accepted=True) để không chặn nhầm user.
    """
    if not _fashion_gate_enabled(config):
        return {'accepted': True, 'reason_vi': '', 'confidence': None, 'skipped': True}

    api_key = (getattr(config, 'OPENROUTER_API_KEY', None) or os.environ.get('OPENROUTER_API_KEY') or '').strip()
    if not api_key:
        return {'accepted': True, 'reason_vi': '', 'confidence': None, 'skipped': True}

    image_url = _read_image_base64(image_path)
    base_url = 'https://openrouter.ai/api/v1'
    timeout = getattr(config, 'OPENROUTER_TIMEOUT', 90) or 90
    model = _gate_model_from_config(config)
    max_retries = 2
    last_error = None

    for attempt in range(max_retries):
        try:
            content = _call_vision_api(
                base_url, api_key, model, image_url, _FASHION_GATE_PROMPT,
                max_tokens=256, timeout=min(timeout, 45),
            )
            parsed = extract_json_from_response(content)
            if not isinstance(parsed, dict):
                last_error = 'parse_failed'
                continue
            accepted = parsed.get('accepted')
            if isinstance(accepted, str):
                accepted = accepted.strip().lower() in ('1', 'true', 'yes', 'on')
            else:
                accepted = bool(accepted)
            reason_vi = (parsed.get('reason_vi') or parsed.get('reason') or '').strip()
            conf_raw = parsed.get('confidence')
            try:
                confidence = float(conf_raw) if conf_raw is not None else None
            except (TypeError, ValueError):
                confidence = None
            if not accepted and not reason_vi:
                reason_vi = (
                    'Ảnh không phù hợp: cần ảnh một người mẫu, một người thật (OOTD) hoặc một ma-nơ-canh '
                    'đang mặc/trưng bày trang phục thời trang (không chấp nhận ảnh nhiều người).'
                )
            # Ép từ chối nếu model báo subject_count > 1 (phòng model trả accepted nhầm)
            try:
                subject_count = int(parsed.get('subject_count'))
            except (TypeError, ValueError):
                subject_count = None
            if subject_count is not None and subject_count > 1:
                accepted = False
                if not reason_vi:
                    reason_vi = (
                        f'Ảnh có {subject_count} người/ma-nơ-canh — chỉ chấp nhận ảnh một chủ thể duy nhất.'
                    )
            return {
                'accepted': accepted,
                'reason_vi': reason_vi,
                'confidence': confidence,
            }
        except Exception as e:
            last_error = str(e)
            if _is_connection_error(e) and attempt < max_retries - 1:
                continue
            break

    print('[Fashion gate] Bỏ qua gate do lỗi API:', last_error)
    return {'accepted': True, 'reason_vi': '', 'confidence': None, 'skipped': True, 'gate_error': last_error}


# -----------------------------------------------------------------------------
# §8 — Phân tích một file ảnh: demo nếu không key; ensemble + normalize + retry lỗi mạng
# -----------------------------------------------------------------------------

def analyze_image(image_path: str, config) -> dict:
    """
    Phân tích 1 ảnh qua OpenRouter — pipeline: 3 vision một lượt (món + detected_styles + detection_confidence)
    → merger code (≥2 model hoặc 1 model với detection_confidence > 0.8) → bỏ phiếu phong cách theo cluster;
    tổng hợp outfit (aggregate_styles) theo trọng số loại × confidence. Không OPENROUTER_API_KEY → kết quả demo.
    """
    image_url = _read_image_base64(image_path)
    # Đọc key từ config (Flask) hoặc từ os.environ (phòng khi config load sai)
    api_key = (getattr(config, 'OPENROUTER_API_KEY', None) or os.environ.get('OPENROUTER_API_KEY') or '').strip()
    if not api_key:
        # Demo: hiển thị nhiều món mẫu với schema mới, reason tiếng Việt
        return {
            'items': [
                {
                    'item': 'Button-up shirt',
                    'category': 'Topwear_Inner',
                    'detected_styles': [{'style': 'classic', 'score': 0.82}],
                    'final_style': 'classic',
                    'confidence': 0.82,
                    'reason': ['Áo sơ mi cài cúc thường dùng trong phong cách cổ điển', 'Đường nét gọn gàng đặc trưng phong cách classic']
                },
                {
                    'item': 'Straight-leg jeans',
                    'category': 'Bottomwear',
                    'detected_styles': [{'style': 'casual', 'score': 0.78}],
                    'final_style': 'casual',
                    'confidence': 0.78,
                    'reason': ['Quần jean ống thẳng linh hoạt cho phong cách casual', 'Quần jean phổ biến trong trang phục thường ngày']
                },
                {
                    'item': 'Sneakers',
                    'category': 'Footwear_Shoes',
                    'detected_styles': [{'style': 'casual', 'score': 0.75}],
                    'final_style': 'casual',
                    'confidence': 0.75,
                    'reason': ['Giày sneaker là giày cơ bản cho phong cách casual', 'Phổ biến trong streetwear và trang phục thường ngày']
                },
            ]
        }
    base_url = 'https://openrouter.ai/api/v1'
    timeout = getattr(config, 'OPENROUTER_TIMEOUT', 90) or 90
    max_tokens = getattr(config, 'OPENROUTER_MAX_TOKENS', 2048) or 2048
    max_tokens = min(max(1, max_tokens), 8192)  # clamp 1–8192 để trả về đủ tất cả item
    max_retries = 3
    last_error = None
    dataset = load_fashion_dataset()
    # Merger ở bước 2 đã chuyển sang code thuần (_code_merge_detection_outputs) — không gọi AI nữa.
    vision_models = _vision_models_from_config(config)

    # Bước cuối trên từng item: chọn final_style theo score, field style (Capitalize) cho aggregate_styles
    def _postprocess_out_items(out: dict, dataset: Dict) -> None:
        """Chung: final_style từ score cao nhất, confidence, reason fallback, gán style cho aggregate."""
        if 'items' not in out or not isinstance(out['items'], list):
            return
        for it in out['items']:
            item_name = it.get('item') or it.get('item_type') or ''
            if not it.get('item'):
                it['item'] = item_name
            detected = it.get('detected_styles')
            if isinstance(detected, list) and detected:
                best = max(detected, key=lambda e: float(e.get('score') or 0))
                best_style = best.get('style') or 'casual'
                best_score = round(float(best.get('score') or 0.5), 2)
                if not it.get('final_style'):
                    it['final_style'] = best_style
                if it.get('confidence') is None:
                    it['confidence'] = best_score
            if not it.get('final_style'):
                it['final_style'] = 'casual'
            if it.get('confidence') is None:
                it['confidence'] = 0.7
            # [Mức A] Đã bỏ fallback reason theo item_style_rules.
            # Nếu AI không trả reason → dùng câu chung; trước đây gọi
            # _build_reasons_from_item_style_rules(...) || ['Phân tích từ hình ảnh'].
            if not isinstance(it.get('reason'), list):
                it['reason'] = ['Phân tích từ hình ảnh']
        for it in out['items']:
            if not it.get('style'):
                it['style'] = (it.get('final_style') or 'casual').capitalize()

    for attempt in range(max_retries):
        try:
            out = _run_vision_ensemble(
                image_url, dataset, base_url, api_key,
                vision_models, max_tokens, timeout,
                config,
            )
            normalize_ai_response_to_dataset(out, dataset)
            if 'detected_items' in out and isinstance(out['detected_items'], list):
                items = []
                for d in out['detected_items']:
                    style_list = d.get('style') or []
                    style_str = style_list[0] if isinstance(style_list, list) and style_list else (style_list if isinstance(style_list, str) else 'casual')
                    items.append({
                        'item': d.get('item') or d.get('category') or '—',
                        'category': d.get('category') or 'Accessories_Hard',
                        'detected_styles': [{'style': _get_style_id(str(style_str), dataset), 'score': 0.6}],
                        'final_style': _get_style_id(str(style_str), dataset),
                        'confidence': 0.7,
                        'reason': ['Phân tích từ hình ảnh']
                    })
                out = {'items': items}
            if 'items' not in out:
                out = {'items': []}
            _postprocess_out_items(out, dataset)
            # Attribute-first: nếu attributes đủ mạnh thì ghi đè style từng item (AI fallback khi yếu)
            for it in out.get('items') or []:
                if isinstance(it, dict):
                    _apply_attribute_first_style(it, dataset)
            # Gộp near-duplicate (vd. Shoulder bag vs Handbag; Socks vs Crew socks)
            if isinstance(out.get('items'), list):
                out['items'] = _dedup_items_postprocess(out['items'], dataset)
                out['items'] = _limit_wardrobe_slot_items(out['items'], dataset)
                out['items'] = _limit_max_items(
                    out['items'], _max_items_per_image_from_config(config),
                )
            _ensure_all_items_reasons_vietnamese(out, dataset)
            tr = out.get('trace')
            if isinstance(tr, dict) and isinstance(out.get('items'), list):
                tr['step2_merged_items'] = list(out['items'])
                tr['final_items'] = list(out['items'])
            return out
        except Exception as e:
            last_error = e
            if _is_connection_error(e) and attempt < max_retries - 1:
                import time
                time.sleep(2 * (attempt + 1))  # 2s, 4s
                continue
            break
    err_msg = str(last_error) if last_error else 'Unknown error'
    if last_error and _is_connection_error(last_error):
        err_msg = (
            'Không kết nối được tới OpenRouter. '
            'Kiểm tra mạng, firewall hoặc proxy. Chi tiết: ' + err_msg
        )
    else:
        # Chuẩn hóa lỗi API (402, credits) thành thông báo thân thiện, tránh hiển thị raw JSON lên giao diện
        err_lower = err_msg.lower()
        if '402' in err_msg or 'credits' in err_lower or 'can only afford' in err_lower or 'requires more credits' in err_lower:
            err_msg = (
                'Tài khoản OpenRouter không đủ credit hoặc vượt giới hạn token. '
                'Vui lòng nạp thêm tại openrouter.ai/settings/credits hoặc giảm OPENROUTER_MAX_TOKENS trong .env.'
            )
        elif '401' in err_msg or 'unauthorized' in err_lower or 'invalid api key' in err_lower:
            err_msg = 'API key OpenRouter không hợp lệ. Kiểm tra OPENROUTER_API_KEY trong .env.'
        elif len(err_msg) > 280:
            err_msg = err_msg[:277] + '...'
    return {'items': [], 'error': err_msg}


# -----------------------------------------------------------------------------
# §9 — Tổng hợp nhiều món/ảnh: phong cách chủ đạo (có trọng số category); dịp; mô tả; mix (API riêng)
# -----------------------------------------------------------------------------

def aggregate_style_scores(items_list: List[Dict]) -> Dict[str, float]:
    """
    Điểm tích lũy theo từng nhãn phong cách (sau normalize_style), có trọng số category × confidence.
    Dùng cho aggregate_styles và aggregate_styles_top_k.
    """
    weights = {
        'áo khoác': 2, 'áo': 2, 'quần': 2, 'váy': 2, 'giày': 1.5, 'phụ kiện': 1, 'trang sức': 0.8,
        'outerwear': 2, 'top': 2, 'bottom': 2, 'footwear': 1.5, 'accessory': 1, 'accessories': 1,
        'jewelry': 0.8, 'dress': 2, 'skirt': 2, 'shoes': 1.5, 'bag': 1,
        'topwear_inner': 2, 'topwear_outer': 2.2, 'bottomwear': 2, 'skirts': 2, 'dresses': 2.2,
        'footwear_shoes': 1.6, 'footwear_sandals': 1.4, 'accessories_soft': 1, 'accessories_hard': 1,
        'headwear': 1.2,
    }
    scores: Dict[str, float] = {}
    for it in items_list or []:
        style = it.get('style') or 'Casual'
        style = normalize_style(style)
        cat = (it.get('category') or 'phụ kiện').strip().lower()
        w = weights.get(cat, 1)
        try:
            conf = float(it.get('confidence') or 1.0)
        except (TypeError, ValueError):
            conf = 1.0
        conf = max(0.0, min(1.0, conf))
        scores[style] = scores.get(style, 0) + w * conf
    return scores


def aggregate_styles(items_list: List[Dict]) -> str:
    """
    Tổng hợp phong cách từ nhiều ảnh / nhiều item.
    Ưu tiên: đếm tần suất + trọng số (áo khoác, áo, quần/ váy, giày quan trọng hơn phụ kiện).
    """
    scores = aggregate_style_scores(items_list)
    if not scores:
        return 'Casual'
    return max(scores, key=scores.get)


def aggregate_styles_top_k(items_list: List[Dict], k: int = 3) -> List[str]:
    """
    Top-k nhãn phong cách outfit theo cùng cơ chế điểm như aggregate_styles (kết quả hiển thị dạng chuỗi như internal style).
    """
    scores = aggregate_style_scores(items_list)
    if not scores:
        return ['Casual']
    ranked = sorted(scores.keys(), key=lambda s: (-scores[s], str(s)))
    k = max(1, min(k, len(ranked)))
    return ranked[:k]


def get_suggested_occasions(overall_style: str) -> List[str]:
    """Gợi ý dịp phù hợp theo phong cách tổng thể."""
    key = overall_style.strip().lower()
    for k, occasions in OCCASION_MAP.items():
        if k in key or key in k:
            return occasions
    return OCCASION_MAP.get('casual', ['Đi chơi', 'Dạo phố'])


# Đoạn văn cố định theo từng style id (UI “Phong cách tổng thể”)
OVERALL_STYLE_DESCRIPTIONS = {
    'casual': 'Phong cách thường ngày, ưu tiên sự thoải mái và tính ứng dụng. Trang phục đơn giản, dễ phối, ít chi tiết cầu kỳ và phù hợp nhiều hoàn cảnh sinh hoạt hàng ngày.',
    'streetwear': 'Phong cách đường phố mang tinh thần urban hiện đại. Thường xuất hiện form oversized, graphic nổi bật, sneaker và các item lấy cảm hứng từ văn hóa hip-hop, skate hoặc giới trẻ.',
    'minimalist': 'Phong cách tối giản tập trung vào form dáng gọn gàng và bảng màu trung tính. Ít họa tiết, ít chi tiết trang trí, đề cao sự tinh tế trong cấu trúc và chất liệu.',
    'classic': 'Phong cách cổ điển thanh lịch với phom dáng chuẩn mực và thiết kế bền vững theo thời gian. Dễ phối, trang nhã và phù hợp nhiều dịp khác nhau.',
    'sporty': 'Phong cách thể thao năng động, ưu tiên tính linh hoạt và thoải mái. Thường gồm tracksuit, jogger, sneaker và các item mang cảm hứng vận động.',
    'formal': 'Phong cách trang trọng và chỉn chu, phù hợp môi trường công sở hoặc sự kiện. Đường may sắc nét, cấu trúc rõ ràng và bảng màu nghiêm túc.',
    'vintage': 'Phong cách lấy cảm hứng từ thời trang quá khứ với phom dáng và chi tiết retro. Có thể xuất hiện họa tiết đặc trưng hoặc kiểu dáng mang dấu ấn thập niên cũ.',
    'y2k': 'Phong cách Y2K mang tinh thần thời trang đầu những năm 2000. Thường có form ôm hoặc crop, màu sắc nổi bật, chi tiết táo bạo và phụ kiện gây chú ý.',
    'preppy': 'Phong cách preppy gọn gàng, chỉn chu và mang hơi hướng học đường cổ điển. Thường gồm áo sơ mi, blazer, chân váy xếp ly hoặc quần chinos thanh lịch.',
    'bohemian': 'Phong cách bohemian tự do và phóng khoáng, ưu tiên chất liệu mềm mại, họa tiết tự nhiên và form bay bổng. Thường xuất hiện trong phong cách dạo phố hoặc lễ hội.',
    'chic': 'Phong cách chic hiện đại và tinh tế, cân bằng giữa tối giản và điểm nhấn thời thượng. Tập trung vào sự sắc sảo trong phom dáng và cách phối.',
    'romantic': 'Phong cách lãng mạn với chi tiết mềm mại như bèo nhún, ren, voan hoặc họa tiết hoa. Tạo cảm giác dịu dàng, nữ tính và thanh thoát.',
    'grunge': 'Phong cách grunge mang tinh thần nổi loạn và bụi bặm của thập niên 90. Thường có denim rách, áo flannel, layer tự nhiên và tông màu trầm.',
    'gothic': 'Phong cách gothic đậm chất cá tính với bảng màu tối, chi tiết sắc nét và phụ kiện nổi bật. Tạo cảm giác bí ẩn và mạnh mẽ.'
}



def get_overall_style_description(overall_style: str) -> str:
    """Trả về câu mô tả tổng quan về phong cách (tiếng Việt) để hiển thị ở phần Phong cách tổng thể."""
    if not overall_style or not isinstance(overall_style, str):
        return OVERALL_STYLE_DESCRIPTIONS.get('casual', '')
    key = overall_style.strip().lower()
    for style_id, desc in OVERALL_STYLE_DESCRIPTIONS.items():
        if style_id in key or key in style_id:
            return desc
    return OVERALL_STYLE_DESCRIPTIONS.get('casual', '')


# Gợi ý mix: dùng OPENROUTER_MODEL (text), không dùng 3 vision — gọi khi user bấm nút trong app
MIX_SUGGESTIONS_PROMPT = """Bạn là chuyên gia thời trang. Dựa trên phong cách và các món đồ đã có, gợi ý 2–3 món nên THÊM vào để mix đồ hoàn chỉnh hơn (ví dụ: áo khoác, giày, túi, phụ kiện). Trả về ĐÚNG MỘT JSON hợp lệ, KHÔNG markdown, tiếng Việt:
{"suggestions": ["Món 1", "Món 2", "Món 3"]}
Chỉ trả về JSON, không giải thích."""


def get_mix_suggestions(overall_style: str, items: List[Dict], config) -> List[str]:
    """
    Gợi ý 2–3 món nên thêm để mix đồ theo phong cách và các món đã có.
    Trả về danh sách chuỗi (tên món gợi ý).
    """
    api_key = (getattr(config, 'OPENROUTER_API_KEY', None) or os.environ.get('OPENROUTER_API_KEY') or '').strip()
    if not api_key:
        return ['Áo khoác denim hoặc blazer', 'Giày sneaker trắng hoặc giày tây', 'Túi crossbody hoặc balo nhỏ']
    summary = f"Phong cách: {overall_style}. Các món đã có: " + ', '.join(
        (it.get('category') or '') + ' – ' + (it.get('item') or it.get('description') or it.get('item_type') or '')[:40] for it in (items or [])[:8]
    )
    try:
        import openai
        client = openai.OpenAI(base_url='https://openrouter.ai/api/v1', api_key=api_key)
        model = getattr(config, 'OPENROUTER_MODEL', 'openai/gpt-4o-mini')
        timeout = getattr(config, 'OPENROUTER_TIMEOUT', 90) or 90
        r = client.chat.completions.create(
            model=model,
            messages=[
                {'role': 'user', 'content': MIX_SUGGESTIONS_PROMPT + '\n\n' + summary}
            ],
            max_tokens=256,
            timeout=timeout,
        )
        text = (r.choices[0].message.content or '').strip()
        obj = extract_json_from_response(text)
        suggestions = obj.get('suggestions') if isinstance(obj.get('suggestions'), list) else []
        return [str(s).strip() for s in suggestions[:5] if s][:3] or ['Áo khoác', 'Giày phù hợp', 'Túi hoặc phụ kiện']
    except Exception as e:
        print('[OpenRouter] Lỗi gợi ý mix:', e)
        return ['Áo khoác', 'Giày phù hợp', 'Túi hoặc phụ kiện']
