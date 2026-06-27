import os
from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(_env_path, override=True)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ON_VERCEL = bool(os.getenv('VERCEL') or os.getenv('VERCEL_ENV'))


def _vercel_tmp_path(name: str) -> str:
    return os.path.join('/tmp', name)


def _env_str(key: str, default: str = '') -> str:
    raw = os.getenv(key)
    if raw is None:
        return default
    value = str(raw).strip()
    return value if value else default


def _env_int(key: str, default: int, *, base: int = 10) -> int:
    raw = os.getenv(key)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip(), base)
    except ValueError:
        return default


_MYSQL_PLACEHOLDER_HOSTS = frozenset({
    'host', 'your-host', 'your_host', 'database-host', 'db-host', 'db_host',
    'mysql', 'changeme', 'example.com', 'hostname',
})


def _mysql_host_is_valid(host: str) -> bool:
    h = (host or '').strip()
    if not h:
        return False
    hl = h.lower()
    if hl in _MYSQL_PLACEHOLDER_HOSTS:
        return False
    # Trên Vercel không thể kết nối MySQL local/XAMPP
    if _ON_VERCEL and hl in ('localhost', '127.0.0.1'):
        return False
    return True


def mysql_configured() -> bool:
    host = _env_str('DB_HOST') or _env_str('MYSQL_HOST')
    user = _env_str('DB_USER') or _env_str('MYSQL_USER')
    database = _env_str('DB_NAME') or _env_str('MYSQL_DATABASE')
    return _mysql_host_is_valid(host) and bool(user) and bool(database)


def _resolve_db_engine() -> str:
    requested = (_env_str('DB_ENGINE') or ('sqlite' if _ON_VERCEL else 'mysql')).lower()
    if requested == 'sqlite':
        return 'sqlite'
    if requested == 'mysql':
        if mysql_configured():
            return 'mysql'
        print(
            '[DB] DB_ENGINE=mysql but DB_HOST/DB_USER/DB_NAME invalid '
            f'(DB_HOST={_env_str("DB_HOST") or _env_str("MYSQL_HOST")!r}) — using SQLite.'
        )
        return 'sqlite'
    return requested


class Config:
    SECRET_KEY = _env_str('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
    UPLOAD_FOLDER = (
        _vercel_tmp_path('uploads')
        if _ON_VERCEL
        else os.path.join(_BASE_DIR, 'uploads')
    )
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20MB tổng request
    MAX_ANALYZE_IMAGES = _env_int('MAX_ANALYZE_IMAGES', 5)  # Tối đa ảnh mỗi lần phân tích
    MAX_ITEMS_PER_IMAGE = _env_int('MAX_ITEMS_PER_IMAGE', 10)  # Tối đa món / ảnh (khớp GT benchmark)
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    OPENROUTER_API_KEY = _env_str('OPENROUTER_API_KEY')
    # Dùng cho API gợi ý mix (không dùng cho phân tích ảnh — ảnh luôn qua ensemble)
    OPENROUTER_MODEL = _env_str('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
    # Model dùng cho API dịch giao diện (/api/translate); mặc định dùng OPENROUTER_MODEL
    OPENROUTER_TRANSLATE_MODEL = _env_str('OPENROUTER_TRANSLATE_MODEL')
    OPENROUTER_TIMEOUT = _env_int('OPENROUTER_TIMEOUT', 90)
    OPENROUTER_MAX_TOKENS = _env_int('OPENROUTER_MAX_TOKENS', 512)  # Giữ ≤ 556 nếu tài khoản free
    # Phân tích ảnh: 3 model vision một lượt (phát hiện món + primary_style mỗi món)
    OPENROUTER_VISION_MODELS = _env_str(
        'OPENROUTER_VISION_MODELS',
        'google/gemini-2.5-flash,anthropic/claude-3-haiku,openai/gpt-5.4-mini',
    )
    # Trọng số bỏ phiếu phong cách theo thứ tự model (cùng số lượng với OPENROUTER_VISION_MODELS; thiếu → 1.0)
    OPENROUTER_VISION_MODEL_WEIGHTS = _env_str('OPENROUTER_VISION_MODEL_WEIGHTS')
    #  bước hợp nhất món hiện thực hiện hoàn toàn bằng code (xem ai_service._code_merge_detection_outputs).
    # Database: mysql (production) | sqlite (local, không cần XAMPP)
    DB_ENGINE = _resolve_db_engine()
    SQLITE_PATH = (
        _env_str('SQLITE_PATH')
        or (_vercel_tmp_path('styleid.db') if _ON_VERCEL else 'data/styleid.db')
    )
    # MySQL — khi DB_ENGINE=mysql (Vercel / production dùng DB_*)
    DB_HOST = _env_str('DB_HOST') or _env_str('MYSQL_HOST', 'localhost')
    DB_PORT = _env_int('DB_PORT', 3306)
    DB_USER = _env_str('DB_USER') or _env_str('MYSQL_USER', 'root')
    DB_PASSWORD = _env_str('DB_PASSWORD') or _env_str('MYSQL_PASSWORD')
    DB_NAME = _env_str('DB_NAME') or _env_str('MYSQL_DATABASE', 'lumistyle_db')
    # Alias cũ (tương thích README / template)
    MYSQL_HOST = DB_HOST
    MYSQL_USER = DB_USER
    MYSQL_PASSWORD = DB_PASSWORD
    MYSQL_DATABASE = DB_NAME
    # Google OAuth (đăng nhập Google)
    GOOGLE_CLIENT_ID = _env_str('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = _env_str('GOOGLE_CLIENT_SECRET')
    # Trống = cùng origin với request (vd http://localhost:5000). Khác port/SPA: vd http://localhost:5173
    FRONTEND_URL = _env_str('FRONTEND_URL').rstrip('/')
    # Phải khớp 100% URI trong Google Cloud Console (trống = tự sinh từ request)
    GOOGLE_REDIRECT_URI = _env_str('GOOGLE_REDIRECT_URI')
    JWT_EXPIRES_DAYS = _env_int('JWT_EXPIRES_DAYS', 7)
    # Lượt phân tích: đăng ký mới & cột mặc định trong DB
    INITIAL_ANALYSIS_CREDITS = _env_int('INITIAL_ANALYSIS_CREDITS', 5)
    # Trang «Gói cước»: số lượt + giá VND tham khảo (chỉ hiển thị, không có cổng thanh toán trong app)
    CREDIT_PACKAGE_ST = _env_int('CREDIT_PACKAGE_ST', 5)
    PRICE_PACKAGE_ST_VND = _env_int('PRICE_PACKAGE_ST_VND', 2000)
    CREDIT_PACKAGE_30 = _env_int('CREDIT_PACKAGE_30', 30)
    CREDIT_PACKAGE_50 = _env_int('CREDIT_PACKAGE_50', 50)
    PRICE_PACKAGE_30_VND = _env_int('PRICE_PACKAGE_30_VND', 2000)
    PRICE_PACKAGE_50_VND = _env_int('PRICE_PACKAGE_50_VND', 10000)
    PACKAGE_NAME_ST = _env_str('PACKAGE_NAME_ST', 'Khởi đầu')
    PACKAGE_NAME_30 = _env_str('PACKAGE_NAME_30', 'Cơ bản')
    PACKAGE_NAME_50 = _env_str('PACKAGE_NAME_50', 'Nâng cao')

    # -------------------- Payments (manual bank transfer + SePay reconciliation) --------------------
    # NAME_WEB: phần "tên web" nhúng vào nội dung chuyển khoản để đối soát.
    NAME_WEB = (_env_str('NAME_WEB', 'LUMISTYLE') or 'LUMISTYLE').upper()

    # Thông tin pháp lý / hỗ trợ (Privacy, Terms, Support — Google Play / App Store)
    APP_DISPLAY_NAME = _env_str('APP_DISPLAY_NAME', 'StyleID')
    COMPANY_NAME_VI = _env_str('COMPANY_NAME_VI', 'CÔNG TY TNHH MỘT THÀNH VIÊN CÔNG NGHỆ KỸ THUẬT TIÊN PHONG')
    COMPANY_NAME_EN = _env_str('COMPANY_NAME_EN', 'TIEN PHONG ENGINEERING TECHNOLOGY CO., LTD')
    COMPANY_TAX_ID = _env_str('COMPANY_TAX_ID', '1801526082')
    COMPANY_REPRESENTATIVE = _env_str('COMPANY_REPRESENTATIVE', 'NGÔ HỒ ANH KHÔI')
    COMPANY_ADDRESS = _env_str('COMPANY_ADDRESS', 'P16, Đường số 8, KDC lô 49, Khu đô thị Nam Cần Thơ, Phường Cái Răng, TP. Cần Thơ')
    SUPPORT_EMAIL = _env_str('SUPPORT_EMAIL', 'levikhangbh2001@gmail.com')
    SUPPORT_PHONE = _env_str('SUPPORT_PHONE', '0916 416 409')

    # Nội dung chuyển khoản bắt buộc bắt đầu bằng: SEVQR{NAME_WEB}NAPTOKEN{HEX_ID}
    PAYMENT_TRANSFER_PREFIX = (_env_str('PAYMENT_TRANSFER_PREFIX', 'SEVQR') or 'SEVQR').upper()

    # XOR obfuscation key: thay đổi key này cho mỗi dự án (đặt dạng số thập phân hoặc hex: 0x5EAFB)
    SECRET_XOR_KEY = _env_int('SECRET_XOR_KEY', 0x5EAFB, base=0)

    # Hóa đơn hết hạn sau N phút (vẫn có thể tự "completed" nếu tiền về sau)
    PAYMENT_EXPIRES_MINUTES = _env_int('PAYMENT_EXPIRES_MINUTES', 60)

    # VietQR (dùng URL ảnh để frontend hiển thị QR quét chuyển khoản)
    VIETQR_BANK_CODE = _env_str('VIETQR_BANK_CODE')          # vd: VCB, ACB, TCB...
    # VietQR image URL dùng acquirer id (BIN 6 số). Nếu để trống, backend sẽ map từ VIETQR_BANK_CODE phổ biến.
    VIETQR_ACQUIRER_ID = _env_str('VIETQR_ACQUIRER_ID')      # vd: 970415 (VietinBank)
    VIETQR_ACCOUNT_NO = _env_str('VIETQR_ACCOUNT_NO')        # số tài khoản
    VIETQR_ACCOUNT_NAME = _env_str('VIETQR_ACCOUNT_NAME')    # tên chủ tài khoản (không bắt buộc)
    VIETQR_TEMPLATE = _env_str('VIETQR_TEMPLATE', 'compact2')    # compact2 | compact | qr_only | ...

    # SePay API (lấy lịch sử giao dịch)
    SEPAY_HISTORY_URL = _env_str('SEPAY_HISTORY_URL')        # vd: https://api.sepay.vn/...
    SEPAY_API_KEY = _env_str('SEPAY_API_KEY')                # token/key
    SEPAY_TIMEOUT = _env_int('SEPAY_TIMEOUT', 20)

    # Gmail SMTP — OTP đăng ký / quên mật khẩu (App Password)
    SMTP_HOST = _env_str('SMTP_HOST', 'smtp.gmail.com')
    SMTP_PORT = _env_int('SMTP_PORT', 587)
    SMTP_USER = _env_str('SMTP_USER')
    SMTP_PASSWORD = _env_str('SMTP_PASSWORD')
    SMTP_FROM = _env_str('SMTP_FROM', SMTP_USER)
    SMTP_FROM_NAME = _env_str('SMTP_FROM_NAME', APP_DISPLAY_NAME or 'StyleID')
    SMTP_USE_TLS = _env_str('SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes')
    SMTP_TIMEOUT = _env_int('SMTP_TIMEOUT', 20)

    # Yêu cầu xóa tài khoản — grace period (ngày) và email thông báo admin
    ACCOUNT_DELETE_GRACE_DAYS = _env_int('ACCOUNT_DELETE_GRACE_DAYS', 30)
    ADMIN_NOTIFY_EMAIL = (
        _env_str('ADMIN_NOTIFY_EMAIL') or _env_str('SUPPORT_EMAIL', 'levikhangbh2001@gmail.com')
    )

