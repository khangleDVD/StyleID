# -*- coding: utf-8 -*-
"""Chuẩn hoá + gọn item_style_rules (3–5 style, chuẩn sát hơn) và format mỗi món 1 dòng.

Chạy: python scripts/compact_item_style_rules.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "fashion_style_dataset.json")

FIRST_KEYS = [
    "version",
    "description",
    "language",
    "categories",
    "items",
    "attributes",
    "styles",
]
TAIL_KEYS = ["style_weights", "occasions_by_style"]


def _dedupe_preserve_order(xs):
    out = []
    seen = set()
    for x in xs or []:
        s = str(x).strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def _build_item_to_category_map(data: dict) -> dict:
    items = data.get("items") or {}
    out = {}
    if not isinstance(items, dict):
        return out
    for cat, arr in items.items():
        if not isinstance(arr, list):
            continue
        for name in arr:
            s = str(name).strip()
            if not s:
                continue
            # Nếu trùng item ở nhiều category, giữ category đầu (đủ tốt cho heuristic)
            out.setdefault(s.lower(), str(cat).strip())
    return out


def _style_ids(data: dict) -> set:
    out = set()
    for s in data.get("styles") or []:
        if isinstance(s, dict) and s.get("id"):
            out.add(str(s["id"]).strip().lower())
    return out


def _suggest_styles(item_name: str, category: str, existing: list, valid_styles: set) -> list:
    """
    Chọn 3–5 phong cách cho 1 món đồ dựa trên:
    - prior từ existing rules (giữ thứ tự ưu tiên cũ)
    - category priors
    - keyword heuristics theo item_name
    """
    name = (item_name or "").strip()
    low = name.lower()
    cat = (category or "").strip().lower()

    scores = {}

    def add(style_id: str, w: float):
        sid = (style_id or "").strip().lower()
        if not sid or (valid_styles and sid not in valid_styles):
            return
        scores[sid] = scores.get(sid, 0.0) + float(w)

    # 1) Prior: existing (giữ thứ tự)
    ex = _dedupe_preserve_order(existing)
    for i, sid in enumerate(ex):
        add(sid, 1.0 - (i * 0.12))  # 1.0, 0.88, 0.76...

    # 2) Category priors (nhẹ)
    if "headwear" in cat:
        for sid in ("streetwear", "casual", "sporty", "y2k", "classic"):
            add(sid, 0.15)
    if "topwear" in cat or cat in ("jackets_coats",):
        for sid in ("casual", "streetwear", "classic", "minimalist"):
            add(sid, 0.10)
    if cat in ("bottomwear", "skirts", "dresses"):
        for sid in ("casual", "classic", "chic", "minimalist"):
            add(sid, 0.10)
    if "footwear" in cat:
        for sid in ("casual", "streetwear", "sporty", "minimalist"):
            add(sid, 0.12)
    if "accessories" in cat:
        for sid in ("casual", "streetwear", "chic", "minimalist"):
            add(sid, 0.10)
    if "jewelry" in cat:
        for sid in ("chic", "minimalist", "classic", "y2k"):
            add(sid, 0.10)

    # 3) Keyword heuristics (mạnh hơn category)
    # Outerwear / tailoring
    if any(k in low for k in ("blazer", "suit", "tuxedo", "dress shirt", "oxford shirt", "tie", "cufflinks", "briefcase")):
        for sid in ("formal", "classic", "business", "smart_casual", "old_money"):
            add(sid, 0.35)
    if any(k in low for k in ("trench", "overcoat", "peacoat", "chesterfield", "crombie")):
        for sid in ("classic", "formal", "minimalist", "old_money", "parisian_style"):
            add(sid, 0.30)

    # Street / hip hop
    if any(k in low for k in ("hoodie", "oversized", "graphic", "snapback", "trucker", "cargo", "baggy", "dad sneakers", "chunky sneakers")):
        for sid in ("streetwear", "hip_hop", "y2k", "hypebeast", "skater_style"):
            add(sid, 0.32)
    if any(k in low for k in ("durag", "nameplate", "chain", "grill", "jersey", "longline", "baggy jeans")):
        for sid in ("hip_hop", "streetwear", "y2k"):
            add(sid, 0.28)

    # Techwear / cyberpunk
    if any(k in low for k in ("tech", "softshell", "shell jacket", "windbreaker", "chest rig", "tactical", "utility")):
        for sid in ("techwear", "streetwear", "cyberpunk", "athleisure"):
            add(sid, 0.30)

    # Sport / active
    if any(k in low for k in ("running", "training", "gym", "sports bra", "track", "jogger", "compression", "tennis")):
        for sid in ("sporty", "activewear", "athleisure", "casual"):
            add(sid, 0.30)

    # Feminine / romantic / coquette
    if any(k in low for k in ("lace", "ruffle", "tulle", "chiffon", "babydoll", "sweetheart", "peplum")):
        for sid in ("romantic", "feminine", "coquette", "chic", "soft_girl"):
            add(sid, 0.34)
    if any(k in low for k in ("mary jane", "bow", "ribbon", "pearl", "puff", "tiered")):
        for sid in ("coquette", "soft_girl", "feminine", "romantic", "light_academia"):
            add(sid, 0.28)

    # Dark / punk / grunge / gothic
    if any(k in low for k in ("leather", "combat", "studded", "spike", "choker", "o-ring", "harness")):
        for sid in ("punk", "gothic", "egirl_eboy", "cyberpunk", "streetwear"):
            add(sid, 0.33)
    if any(k in low for k in ("flannel", "distressed", "ripped", "band t-shirt", "denim jacket")):
        for sid in ("grunge", "streetwear", "vintage", "punk"):
            add(sid, 0.28)

    # Academia / preppy / old money
    if any(k in low for k in ("pleated", "cardigan", "sweater vest", "loafer", "oxford")):
        for sid in ("preppy", "dark_academia", "light_academia", "old_money", "classic"):
            add(sid, 0.26)
    if any(k in low for k in ("tweed", "pea coat", "ivy cap", "cashmere")):
        for sid in ("old_money", "classic", "dark_academia", "parisian_style"):
            add(sid, 0.28)

    # Cultural / uniform
    if any(k in low for k in ("ao dai", "áo dài", "non la", "nón lá", "ba ba", "yem", "khăn")):
        for sid in ("cultural", "elegant", "formal", "feminine", "romantic"):
            add(sid, 0.40)
    if any(k in low for k in ("scrubs", "lab coat", "school uniform", "uniform", "pe gym", "chef", "nurse")):
        for sid in ("uniform", "casual", "minimalist", "comfy", "sporty"):
            add(sid, 0.45)

    # 4) Chọn top 3–5
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    picked = [sid for sid, _ in ranked[:5]]

    # Bảo đảm tối thiểu 3: thêm từ existing nếu cần
    if len(picked) < 3:
        for sid in ex:
            s = sid.strip().lower()
            if s and s not in picked and (not valid_styles or s in valid_styles):
                picked.append(s)
            if len(picked) >= 3:
                break

    # Nếu vẫn thiếu (hiếm), fallback casual/classic/streetwear
    for sid in ("casual", "classic", "streetwear"):
        if len(picked) >= 3:
            break
        if sid in valid_styles and sid not in picked:
            picked.append(sid)

    return picked[:5]


def main():
    with open(PATH, encoding="utf-8") as f:
        data = json.load(f)

    rules = data["item_style_rules"]
    valid = _style_ids(data)
    item2cat = _build_item_to_category_map(data)
    # Bổ sung rule cho các item còn thiếu
    for item_l, item_name in list(item2cat.keys()) if False else []:
        pass
    for item_key, cat in item2cat.items():
        # item2cat: key lowercase -> category (string)
        # Cần lấy lại item_name original từ items list để giữ đúng key trong JSON.
        # Vì item2cat giữ category theo lowercase key, ta sẽ dùng lowercase để tìm trong rules.
        if item_key not in {str(k).strip().lower() for k in rules.keys()}:
            # Tìm lại item name original (case) từ map (item2cat only stored cat), fallback title-case
            original = None
            # item2cat được xây từ list theo thứ tự; ta không giữ original ở đây → dựng lại từ items list nhanh
            # (chi phí nhỏ vì chạy offline)
            pass

    # Xây lại map item_key -> original name (case) một lần
    items = data.get("items") or {}
    orig_by_key = {}
    if isinstance(items, dict):
        for _, arr in items.items():
            if isinstance(arr, list):
                for nm in arr:
                    s = str(nm).strip()
                    if s:
                        orig_by_key.setdefault(s.lower(), s)

    existing_keys = {str(k).strip().lower() for k in rules.keys()}
    for item_key, cat in item2cat.items():
        if item_key in existing_keys:
            continue
        original = orig_by_key.get(item_key) or item_key
        rules[original] = _suggest_styles(str(original), cat, [], valid)
        existing_keys.add(item_key)

    # Chuẩn hoá "chuẩn sát": chấm điểm theo category + keyword + prior hiện tại
    for k, v in list(rules.items()):
        if not isinstance(v, list):
            continue
        cat = item2cat.get(str(k).strip().lower(), "")
        rules[k] = _suggest_styles(str(k), cat, v, valid)

    part1 = json.dumps({k: data[k] for k in FIRST_KEYS}, ensure_ascii=False, indent=2)
    part2 = json.dumps({k: data[k] for k in TAIL_KEYS}, ensure_ascii=False, indent=2)
    if part1[-1] != "}" or part2[0] != "{" or part2[-1] != "}":
        raise SystemExit("Unexpected JSON shape")

    lines = []
    items = list(rules.items())
    for i, (k, v) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append("    " + json.dumps(k, ensure_ascii=False) + ": " + json.dumps(v, ensure_ascii=False) + comma)

    out = (
        part1[:-1].rstrip()
        + ',\n  "item_style_rules": {\n'
        + "\n".join(lines)
        + "\n  },"
        + part2[1:-1]
        + "\n}"
    )

    with open(PATH, "w", encoding="utf-8") as f:
        f.write(out)
        f.write("\n")

    print(f"OK: {len(rules)} rules compacted -> {PATH}")


if __name__ == "__main__":
    main()
