import os
from dotenv import load_dotenv

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(_env_path, override=True)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ON_VERCEL = bool(os.getenv('VERCEL') or os.getenv('VERCEL_ENV'))


def _vercel_tmp_path(name: str) -> str:
    return os.path.join('/tmp', name)


class Config:
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
    UPLOAD_FOLDER = (
        _vercel_tmp_path('uploads')
        if _ON_VERCEL
        else os.path.join(_BASE_DIR, 'uploads')
    )
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024  # 20MB tổng request
    MAX_ANALYZE_IMAGES = int(os.getenv('MAX_ANALYZE_IMAGES', '5'))  # Tối đa ảnh mỗi lần phân tích
    MAX_ITEMS_PER_IMAGE = int(os.getenv('MAX_ITEMS_PER_IMAGE', '10'))  # Tối đa món / ảnh (khớp GT benchmark)
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
    # Dùng cho API gợi ý mix (không dùng cho phân tích ảnh — ảnh luôn qua ensemble)
    OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
    # Model dùng cho API dịch giao diện (/api/translate); mặc định dùng OPENROUTER_MODEL
    OPENROUTER_TRANSLATE_MODEL = (os.getenv('OPENROUTER_TRANSLATE_MODEL') or '').strip()
    OPENROUTER_TIMEOUT = int(os.getenv('OPENROUTER_TIMEOUT', '90'))
    OPENROUTER_MAX_TOKENS = int(os.getenv('OPENROUTER_MAX_TOKENS', '512'))  # Giữ ≤ 556 nếu tài khoản free
    # Phân tích ảnh: 3 model vision một lượt (phát hiện món + primary_style mỗi món)
    OPENROUTER_VISION_MODELS = os.getenv(
        'OPENROUTER_VISION_MODELS',
        'google/gemini-2.5-flash,anthropic/claude-3-haiku,openai/gpt-5.4-mini',
    )
    # Trọng số bỏ phiếu phong cách theo thứ tự model (cùng số lượng với OPENROUTER_VISION_MODELS; thiếu → 1.0)
    OPENROUTER_VISION_MODEL_WEIGHTS = os.getenv('OPENROUTER_VISION_MODEL_WEIGHTS', '')
    #  bước hợp nhất món hiện thực hiện hoàn toàn bằng code (xem ai_service._code_merge_detection_outputs).
    # Database: mysql (production) | sqlite (local, không cần XAMPP)
    DB_ENGINE = (os.getenv('DB_ENGINE') or ('sqlite' if _ON_VERCEL else 'mysql')).strip().lower()
    SQLITE_PATH = (
        (os.getenv('SQLITE_PATH') or '').strip()
        or (_vercel_tmp_path('styleid.db') if _ON_VERCEL else 'data/styleid.db')
    )
    # MySQL (lumistyle_db) — khi DB_ENGINE=mysql
    MYSQL_HOST = os.getenv('MYSQL_HOST', 'localhost')
    MYSQL_USER = os.getenv('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
    MYSQL_DATABASE = os.getenv('MYSQL_DATABASE', 'lumistyle_db')
    # Google OAuth (đăng nhập Google)
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
    # Trống = cùng origin với request (vd http://localhost:5000). Khác port/SPA: vd http://localhost:5173
    FRONTEND_URL = (os.getenv('FRONTEND_URL') or '').strip().rstrip('/')
    # Phải khớp 100% URI trong Google Cloud Console (trống = tự sinh từ request)
    GOOGLE_REDIRECT_URI = (os.getenv('GOOGLE_REDIRECT_URI') or '').strip()
    JWT_EXPIRES_DAYS = int(os.getenv('JWT_EXPIRES_DAYS', '7'))
    # Lượt phân tích: đăng ký mới & cột mặc định trong DB
    INITIAL_ANALYSIS_CREDITS = int(os.getenv('INITIAL_ANALYSIS_CREDITS', '5'))
    # Trang «Gói cước»: số lượt + giá VND tham khảo (chỉ hiển thị, không có cổng thanh toán trong app)
    CREDIT_PACKAGE_ST = int(os.getenv('CREDIT_PACKAGE_ST', '5'))
    PRICE_PACKAGE_ST_VND = int(os.getenv('PRICE_PACKAGE_ST_VND', '2000'))
    CREDIT_PACKAGE_30 = int(os.getenv('CREDIT_PACKAGE_30', '30'))
    CREDIT_PACKAGE_50 = int(os.getenv('CREDIT_PACKAGE_50', '50'))
    PRICE_PACKAGE_30_VND = int(os.getenv('PRICE_PACKAGE_30_VND', '2000'))
    PRICE_PACKAGE_50_VND = int(os.getenv('PRICE_PACKAGE_50_VND', '10000'))
    PACKAGE_NAME_ST = (os.getenv('PACKAGE_NAME_ST') or 'Khởi đầu').strip()
    PACKAGE_NAME_30 = (os.getenv('PACKAGE_NAME_30') or 'Cơ bản').strip()
    PACKAGE_NAME_50 = (os.getenv('PACKAGE_NAME_50') or 'Nâng cao').strip()

    # -------------------- Payments (manual bank transfer + SePay reconciliation) --------------------
    # NAME_WEB: phần "tên web" nhúng vào nội dung chuyển khoản để đối soát.
    NAME_WEB = (os.getenv('NAME_WEB') or 'LUMISTYLE').strip().upper()

    # Thông tin pháp lý / hỗ trợ (Privacy, Terms, Support — Google Play / App Store)
    APP_DISPLAY_NAME = (os.getenv('APP_DISPLAY_NAME') or 'StyleID').strip()
    COMPANY_NAME_VI = (os.getenv('COMPANY_NAME_VI') or 'CÔNG TY TNHH MỘT THÀNH VIÊN CÔNG NGHỆ KỸ THUẬT TIÊN PHONG').strip()
    COMPANY_NAME_EN = (os.getenv('COMPANY_NAME_EN') or 'TIEN PHONG ENGINEERING TECHNOLOGY CO., LTD').strip()
    COMPANY_TAX_ID = (os.getenv('COMPANY_TAX_ID') or '1801526082').strip()
    COMPANY_REPRESENTATIVE = (os.getenv('COMPANY_REPRESENTATIVE') or 'NGÔ HỒ ANH KHÔI').strip()
    COMPANY_ADDRESS = (os.getenv('COMPANY_ADDRESS') or 'P16, Đường số 8, KDC lô 49, Khu đô thị Nam Cần Thơ, Phường Cái Răng, TP. Cần Thơ').strip()
    SUPPORT_EMAIL = (os.getenv('SUPPORT_EMAIL') or 'levikhangbh2001@gmail.com').strip()
    SUPPORT_PHONE = (os.getenv('SUPPORT_PHONE') or '0916 416 409').strip()

    # Nội dung chuyển khoản bắt buộc bắt đầu bằng: SEVQR{NAME_WEB}NAPTOKEN{HEX_ID}
    PAYMENT_TRANSFER_PREFIX = (os.getenv('PAYMENT_TRANSFER_PREFIX') or 'SEVQR').strip().upper()

    # XOR obfuscation key: thay đổi key này cho mỗi dự án (đặt dạng số thập phân hoặc hex: 0x5EAFB)
    SECRET_XOR_KEY = int(os.getenv('SECRET_XOR_KEY', '0x5EAFB'), 0)

    # Hóa đơn hết hạn sau N phút (vẫn có thể tự "completed" nếu tiền về sau)
    PAYMENT_EXPIRES_MINUTES = int(os.getenv('PAYMENT_EXPIRES_MINUTES', '60'))

    # VietQR (dùng URL ảnh để frontend hiển thị QR quét chuyển khoản)
    VIETQR_BANK_CODE = (os.getenv('VIETQR_BANK_CODE') or '').strip()          # vd: VCB, ACB, TCB...
    # VietQR image URL dùng acquirer id (BIN 6 số). Nếu để trống, backend sẽ map từ VIETQR_BANK_CODE phổ biến.
    VIETQR_ACQUIRER_ID = (os.getenv('VIETQR_ACQUIRER_ID') or '').strip()      # vd: 970415 (VietinBank)
    VIETQR_ACCOUNT_NO = (os.getenv('VIETQR_ACCOUNT_NO') or '').strip()        # số tài khoản
    VIETQR_ACCOUNT_NAME = (os.getenv('VIETQR_ACCOUNT_NAME') or '').strip()    # tên chủ tài khoản (không bắt buộc)
    VIETQR_TEMPLATE = (os.getenv('VIETQR_TEMPLATE') or 'compact2').strip()    # compact2 | compact | qr_only | ...

    # SePay API (lấy lịch sử giao dịch)
    SEPAY_HISTORY_URL = (os.getenv('SEPAY_HISTORY_URL') or '').strip()        # vd: https://api.sepay.vn/...
    SEPAY_API_KEY = (os.getenv('SEPAY_API_KEY') or '').strip()                # token/key
    SEPAY_TIMEOUT = int(os.getenv('SEPAY_TIMEOUT', '20'))

    # Gmail SMTP — OTP đăng ký / quên mật khẩu (App Password)
    SMTP_HOST = (os.getenv('SMTP_HOST') or 'smtp.gmail.com').strip()
    SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
    SMTP_USER = (os.getenv('SMTP_USER') or '').strip()
    SMTP_PASSWORD = (os.getenv('SMTP_PASSWORD') or '').strip()
    SMTP_FROM = (os.getenv('SMTP_FROM') or SMTP_USER or '').strip()
    SMTP_FROM_NAME = (os.getenv('SMTP_FROM_NAME') or APP_DISPLAY_NAME or 'StyleID').strip()
    SMTP_USE_TLS = (os.getenv('SMTP_USE_TLS') or 'true').strip().lower() in ('1', 'true', 'yes')
    SMTP_TIMEOUT = int(os.getenv('SMTP_TIMEOUT', '20'))

    # Yêu cầu xóa tài khoản — grace period (ngày) và email thông báo admin
    ACCOUNT_DELETE_GRACE_DAYS = int(os.getenv('ACCOUNT_DELETE_GRACE_DAYS', '30'))
    ADMIN_NOTIFY_EMAIL = (
        os.getenv('ADMIN_NOTIFY_EMAIL') or os.getenv('SUPPORT_EMAIL') or 'levikhangbh2001@gmail.com'
    ).strip()

