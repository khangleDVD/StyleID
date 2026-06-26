"""LLM translation for UI dynamic content (OpenRouter)."""
import hashlib
import os
import re
from typing import Dict, Tuple

_translate_cache: Dict[str, str] = {}

LANG_LABELS = {
    'en': 'English',
    'vi': 'Tiếng Việt',
}

TRANSLATE_PROMPT = """Bạn là hệ thống dịch thuật cho website Nhận dạng phong cách thời trang.

Hãy dịch nội dung sau sang {target_language}.

Yêu cầu:

- Giữ nguyên ý nghĩa gốc.
- Dịch tự nhiên, rõ ràng, phù hợp với giao diện web.
- Không thêm giải thích.
- Không tự ý thêm nội dung mới.
- Không dịch tên route, tên biến, mã lỗi, class CSS, ID HTML hoặc nội dung nằm trong dấu ngoặc nhọn.
- Không bọc kết quả trong thẻ HTML (như h1, p, ol) trừ khi nội dung gốc đã có thẻ HTML.
- Chỉ trả về bản dịch thuần, không giải thích, không thêm tiêu đề hay danh sách nếu đầu vào là một cụm ngắn.
- Không dịch tên model AI, tên file, đường dẫn API hoặc dữ liệu kỹ thuật.
- Giữ nguyên các thuật ngữ kỹ thuật nếu chúng là tên riêng.

Dịch thống nhất các thuật ngữ sau:

Fashion Style Recognition = Nhận dạng phong cách thời trang
Fashion Classification = Phân loại phong cách thời trang
Image Analysis = Phân tích hình ảnh
Prediction = Dự đoán
Classification Result = Kết quả phân loại
Confidence Score = Độ tin cậy
Fashion Style = Phong cách thời trang
Casual = Thường ngày
Streetwear = Đường phố
Vintage = Cổ điển
Formal = Trang trọng
Business Casual = Công sở
Sporty = Thể thao
Bohemian = Boho
Minimalist = Tối giản
User Upload = Ảnh người dùng tải lên
Detected Style = Phong cách được nhận dạng
Model Accuracy = Độ chính xác mô hình
Processing = Đang xử lý
Completed = Hoàn tất
Failed = Thất bại

Nội dung cần dịch:

{text}"""

POLICY_HTML_TRANSLATE_PROMPT = """Bạn là hệ thống dịch trang chính sách / điều khoản website thời trang AI.

Dịch đoạn HTML sau sang {target_language}.

Yêu cầu bắt buộc:
- Giữ nguyên cấu trúc HTML: thẻ, thuộc tính, class.
- KHÔNG dịch placeholder [[JINJA:n]] — giữ nguyên từng ký tự.
- KHÔNG dịch URL trong href/src (ví dụ /privacy, mailto:, tel:).
- KHÔNG dịch nội dung trong thẻ <code>.
- Dịch văn bản hiển thị; giữ phong cách văn bản pháp lý chuyên nghiệp.
- Không thêm giải thích hay comment ngoài HTML.
- Chỉ trả về HTML đã dịch, không bọc trong markdown hay code fence.

HTML cần dịch:

{html}"""


def _normalize_target_language(target_language: str):
    lang = (target_language or '').strip().lower()
    if lang in ('en', 'english', 'tiếng anh', 'tieng anh'):
        return 'en'
    if lang in ('vi', 'vietnamese', 'tiếng việt', 'tieng viet'):
        return 'vi'
    return None


def _cache_key(text: str, target_language: str) -> str:
    raw = (text or '') + '|' + target_language
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _target_language_label(lang: str) -> str:
    return LANG_LABELS.get(lang, lang)


def translate_text(text: str, target_language: str, config) -> Tuple[str, bool]:
    """
    Dịch một đoạn văn bản. Trả về (translated_text, from_cache).
    Khi thất bại hoặc không cần dịch, trả về bản gốc.
    """
    original = (text or '').strip()
    if not original:
        return '', False

    lang = _normalize_target_language(target_language)
    if not lang:
        return original, False

    key = _cache_key(original, lang)
    if key in _translate_cache:
        return _translate_cache[key], True

    api_key = (getattr(config, 'OPENROUTER_API_KEY', None) or os.environ.get('OPENROUTER_API_KEY') or '').strip()
    if not api_key:
        return original, False

    prompt = TRANSLATE_PROMPT.format(
        target_language=_target_language_label(lang),
        text=original,
    )
    model = (
        (getattr(config, 'OPENROUTER_TRANSLATE_MODEL', None) or '').strip()
        or getattr(config, 'OPENROUTER_MODEL', 'openai/gpt-4o-mini')
    )
    timeout = getattr(config, 'OPENROUTER_TIMEOUT', 90) or 90
    max_tokens = min(getattr(config, 'OPENROUTER_MAX_TOKENS', 512) or 512, 2048)

    try:
        import openai

        client = openai.OpenAI(base_url='https://openrouter.ai/api/v1', api_key=api_key)
        r = client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=max_tokens,
            timeout=timeout,
        )
        translated = (r.choices[0].message.content or '').strip()
        if not translated:
            return original, False
        # LLM đôi khi bọc trong dấu ngoặc kép
        if len(translated) >= 2 and translated[0] == translated[-1] and translated[0] in ('"', "'"):
            translated = translated[1:-1].strip()
        _translate_cache[key] = translated
        return translated, False
    except Exception as e:
        print('[Translate] LLM error:', e)
        return original, False


def translate_policy_html(html: str, target_language: str, config) -> Tuple[str, bool]:
    """Dịch một đoạn HTML chính sách. Trả về (translated_html, from_cache)."""
    original = (html or '').strip()
    if not original:
        return '', False

    lang = _normalize_target_language(target_language)
    if not lang:
        return original, False

    key = _cache_key('policy|' + original, lang)
    if key in _translate_cache:
        return _translate_cache[key], True

    api_key = (getattr(config, 'OPENROUTER_API_KEY', None) or os.environ.get('OPENROUTER_API_KEY') or '').strip()
    if not api_key:
        return original, False

    prompt = POLICY_HTML_TRANSLATE_PROMPT.format(
        target_language=_target_language_label(lang),
        html=original,
    )
    model = (
        (getattr(config, 'OPENROUTER_TRANSLATE_MODEL', None) or '').strip()
        or getattr(config, 'OPENROUTER_MODEL', 'openai/gpt-4o-mini')
    )
    timeout = getattr(config, 'OPENROUTER_TIMEOUT', 90) or 90
    max_tokens = min(getattr(config, 'OPENROUTER_MAX_TOKENS', 512) or 512, 4096)

    try:
        import openai

        client = openai.OpenAI(base_url='https://openrouter.ai/api/v1', api_key=api_key)
        r = client.chat.completions.create(
            model=model,
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=max_tokens,
            timeout=timeout,
        )
        translated = (r.choices[0].message.content or '').strip()
        if not translated:
            return original, False
        if translated.startswith('```'):
            translated = re.sub(r'^```[a-zA-Z]*\s*', '', translated)
            translated = re.sub(r'\s*```$', '', translated).strip()
        if len(translated) >= 2 and translated[0] == translated[-1] and translated[0] in ('"', "'"):
            translated = translated[1:-1].strip()
        _translate_cache[key] = translated
        return translated, False
    except Exception as e:
        print('[Translate] Policy HTML error:', e)
        return original, False
