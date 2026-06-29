import os
import json
import uuid
import secrets
import base64
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
import jwt as pyjwt
import re
import html
import requests
from flask import Flask, request, jsonify, send_from_directory, redirect, url_for, render_template, render_template_string, Response
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from auth_handlers import (
    forgot_password_request,
    forgot_password_resend,
    forgot_password_verify,
    register_request,
    register_resend,
    register_verify,
)
from account_deletion import (
    admin_approve_delete,
    admin_restore_account,
    delete_account_request,
    delete_account_resend,
    delete_account_verify,
    normalize_account_status,
    process_expired_deletions,
    restore_account_request,
    restore_account_resend,
    restore_account_verify,
)
from config import Config
from db import get_db, dict_cursor, plain_cursor, IntegrityError, DatabaseError, init_database
from settings_store import get_app_setting, set_app_setting, delete_app_setting
from translate_service import translate_text, translate_policy_html
from ai_service import (
    analyze_image,
    aggregate_styles,
    aggregate_styles_top_k,
    get_suggested_occasions,
    get_mix_suggestions,
    get_overall_style_description,
    get_item_bilingual,
    load_fashion_dataset,
    to_vietnamese_category,
    to_vietnamese_style,
)

app = Flask(__name__, static_folder='static')
app.config.from_object(Config)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
CORS(app, supports_credentials=True)

oauth = OAuth(app)
oauth.register(
    'google',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid profile email'},
)

def _ensure_runtime_dirs():
    for path in (
        Config.UPLOAD_FOLDER,
        os.path.join(app.static_folder, 'img'),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'),
    ):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            print(f'[Startup] skip mkdir {path}: {e}')


_ensure_runtime_dirs()

try:
    init_database()
    n = process_expired_deletions()
    if n:
        print(f'[Account] Đã chuyển {n} tài khoản quá hạn sang deleted.')
except Exception as e:
    print('[DB] init_database:', e)

_last_deletion_sweep_at = None


@app.context_processor
def inject_site_legal():
    """Biến dùng chung cho footer và trang pháp lý."""
    return {
        'site': {
            'app_name': Config.APP_DISPLAY_NAME,
            'company_name_vi': Config.COMPANY_NAME_VI,
            'company_name_en': Config.COMPANY_NAME_EN,
            'tax_id': Config.COMPANY_TAX_ID,
            'representative': Config.COMPANY_REPRESENTATIVE,
            'address': Config.COMPANY_ADDRESS,
            'support_email': Config.SUPPORT_EMAIL,
            'support_phone': Config.SUPPORT_PHONE,
            'logo_url': _site_logo_url(),
            'year': datetime.now().year,
        },
    }


def _site_logo_path():
    base = os.path.join(app.static_folder, 'img')
    for name in ('site-logo.png', 'site-logo.jpg', 'site-logo.jpeg', 'site-logo.webp'):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return p
    return os.path.join(base, 'site-logo.png')


def _site_logo_url():
    db_logo = _site_logo_from_db()
    if db_logo:
        v = (db_logo.get('updated_at') or db_logo.get('v') or '').strip()
        return '/api/site/logo' + (f'?v={v}' if v else '')
    path = _site_logo_path()
    if os.path.isfile(path):
        try:
            mtime = int(os.path.getmtime(path))
        except OSError:
            mtime = 0
        return f'/static/img/{os.path.basename(path)}?v={mtime}'
    return ''


def _site_logo_from_db():
    data = get_app_setting('site_logo')
    if isinstance(data, dict) and data.get('data_b64'):
        return data
    return None


def _site_has_logo() -> bool:
    if _site_logo_from_db():
        return True
    return os.path.isfile(_site_logo_path())


def _delete_site_logo_file_assets():
    base = os.path.join(app.static_folder, 'img')
    for name in ('site-logo.png', 'site-logo.jpg', 'site-logo.jpeg', 'site-logo.webp'):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass


def _delete_site_logo_files():
    _delete_site_logo_file_assets()
    try:
        delete_app_setting('site_logo')
    except Exception:
        pass


def _can_write_project_files() -> bool:
    if _on_vercel():
        return False
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        return os.access(base, os.W_OK)
    except OSError:
        return False


def _upload_basename_from_url_or_path(s: str) -> str:
    """Lấy tên file trong uploads từ URL tuyệt đối, đường dẫn đầy đủ hoặc chỉ tên file."""
    if not s:
        return ''
    raw = str(s).strip()
    try:
        path = urlparse(raw).path or raw
    except Exception:
        path = raw
    path = unquote(path).replace('\\', '/')
    if '/uploads/' in path:
        name = path.split('/uploads/')[-1].split('/')[0].split('?')[0]
    else:
        name = path.split('/')[-1].split('?')[0]
    return name.strip()


def _history_upload_urls(image_path, analysis_result):
    """Luôn trả đường dẫn tương đối /uploads/<file> — tránh URL host cũ (127.0.0.1 vs localhost) trong JSON."""
    seen = set()
    out = []

    def push(name: str):
        n = (name or '').strip()
        if not n or n in seen:
            return
        seen.add(n)
        out.append(f'/uploads/{n}')

    ar = analysis_result if isinstance(analysis_result, dict) else {}
    urls = ar.get('image_urls')
    if isinstance(urls, list):
        for u in urls:
            push(_upload_basename_from_url_or_path(u))
    ip = (image_path or '').strip()
    if ip:
        push(_upload_basename_from_url_or_path(ip))
    return out


def _apply_history_media_urls(row: dict) -> dict:
    ar = row.get('analysis_result')
    if not isinstance(ar, dict):
        ar = {}
    rel = _history_upload_urls(row.get('image_path'), ar)
    ar = dict(ar)
    ar['image_urls'] = rel
    row['analysis_result'] = ar
    row['image_url'] = rel[0] if rel else None
    return row


def _request_base_url() -> str:
    scheme = (request.headers.get('X-Forwarded-Proto') or request.scheme or 'https').split(',')[0].strip()
    host = (request.headers.get('X-Forwarded-Host') or request.host or '').split(',')[0].strip()
    if host:
        return f'{scheme}://{host}'.rstrip('/')
    return ''


def _is_dev_tunnel_url(url: str) -> bool:
    low = (url or '').lower()
    return any(x in low for x in ('ngrok', 'ngrok-free.dev', 'localhost', '127.0.0.1'))


def _on_vercel() -> bool:
    return bool(os.getenv('VERCEL') or os.getenv('VERCEL_ENV'))


def _google_redirect_uri():
    """URI callback gửi Google — phải khớp 100% mục Authorized redirect URIs trên Google Cloud Console."""
    dynamic = f'{_request_base_url()}/api/auth/google/callback' if _request_base_url() else ''
    configured = (Config.GOOGLE_REDIRECT_URI or '').strip()

    # Production (Vercel): không dùng ngrok/localhost cũ trong .env
    if configured and (_on_vercel() or (_request_base_url() and _is_dev_tunnel_url(configured))):
        cfg_host = (urlparse(configured).netloc or '').lower()
        req_host = (urlparse(_request_base_url()).netloc or '').lower()
        if _is_dev_tunnel_url(configured) or (req_host and cfg_host and cfg_host != req_host):
            if dynamic:
                return dynamic

    if configured:
        low = configured.lower()
        if ('localhost' in low or '127.0.0.1' in low):
            if dynamic and 'localhost' not in dynamic.lower() and '127.0.0.1' not in dynamic.lower():
                return dynamic
        return configured
    if dynamic:
        return dynamic
    return url_for('auth_google_callback', _external=True)


def _frontend_base_url():
    """URL giao diện sau OAuth — ưu tiên host thực tế của request (Vercel / LAN)."""
    root = _request_base_url()
    configured = (Config.FRONTEND_URL or '').strip().rstrip('/')
    if configured:
        if (_on_vercel() or (root and _is_dev_tunnel_url(configured))) and root:
            if _is_dev_tunnel_url(configured) or urlparse(configured).netloc != urlparse(root).netloc:
                return root
        low = configured.lower()
        if ('localhost' in low or '127.0.0.1' in low) and root:
            root_low = root.lower()
            if 'localhost' not in root_low and '127.0.0.1' not in root_low:
                return root
        return configured
    return root


def _redirect_frontend(**params):
    """Redirect về giao diện với query string (tránh JSON thô sau OAuth)."""
    q = urlencode({k: v for k, v in params.items() if v is not None})
    return redirect(f'{_frontend_base_url()}/?{q}')


def create_access_token(user_id: int, email: str) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=max(1, Config.JWT_EXPIRES_DAYS))
    payload = {
        'sub': str(user_id),
        'email': email or '',
        'iat': now,
        'exp': exp,
    }
    return pyjwt.encode(payload, Config.SECRET_KEY, algorithm='HS256')


def get_bearer_token():
    auth = request.headers.get('Authorization') or ''
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return None


USER_PROFILE_SELECT = (
    'id, username, password, full_name, email, analysis_credits, role, email_verified, '
    'force_change_password, google_id, avatar_path, created_at, account_status, '
    'delete_requested_at, delete_scheduled_at, delete_reason, delete_cancelled_at, deleted_at'
)


def _maybe_sweep_expired_deletions():
    global _last_deletion_sweep_at
    now = datetime.now(timezone.utc)
    if _last_deletion_sweep_at and (now - _last_deletion_sweep_at).total_seconds() < 300:
        return
    _last_deletion_sweep_at = now
    try:
        process_expired_deletions()
    except Exception:
        pass


def resolve_request_user_id():
    """Xác thực user từ Bearer JWT hoặc user_id trong body (SPA đăng nhập mật khẩu)."""
    raw = get_bearer_token()
    body_uid = None
    data = request.get_json(silent=True) or {}
    if data.get('user_id') is not None:
        try:
            body_uid = int(data['user_id'])
        except (TypeError, ValueError):
            body_uid = None
    if raw:
        try:
            payload = pyjwt.decode(raw, Config.SECRET_KEY, algorithms=['HS256'])
            token_uid = int(payload['sub'])
            if body_uid is not None and body_uid != token_uid:
                return None, (jsonify({'error': 'user_id không khớp token'}), 403)
            return token_uid, None
        except pyjwt.PyJWTError:
            return None, (jsonify({'error': 'Token không hợp lệ hoặc đã hết hạn'}), 401)
    if body_uid is not None:
        return body_uid, None
    uid = request.args.get('user_id', type=int)
    if uid is not None:
        return uid, None
    return None, (jsonify({'error': 'Thiếu xác thực người dùng'}), 401)


def _account_status_error(status: str):
    if status == 'deleted':
        return jsonify({'error': 'Tài khoản đã bị xóa hoặc vô hiệu hóa.'}), 403
    return None


def _user_account_access_error(user_id: int):
    """Trả về (response, status) nếu user không được dùng dịch vụ; None nếu OK."""
    if not user_id:
        return None
    db = get_db()
    cur = plain_cursor(db)
    try:
        cur.execute('SELECT account_status FROM users WHERE id = %s', (user_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Không tìm thấy tài khoản'}), 404
        status = normalize_account_status(row[0] if not isinstance(row, dict) else row.get('account_status'))
        if status == 'deleted':
            return jsonify({'error': 'Tài khoản đã bị xóa hoặc vô hiệu hóa.'}), 403
        if status == 'pending_delete':
            return jsonify({
                'error': 'Tài khoản đang trong thời gian chờ xóa. Vui lòng khôi phục tài khoản để tiếp tục.',
            }), 403
        return None
    finally:
        cur.close()
        db.close()


_ensure_payments_table = init_database


def encode_payment_id(p_id: int) -> str:
    return hex(int(p_id) ^ int(Config.SECRET_XOR_KEY))[2:].upper()


def decode_payment_id(hex_str: str) -> int:
    return int(str(hex_str).strip(), 16) ^ int(Config.SECRET_XOR_KEY)


def _generate_reconcile_token() -> str:
    return secrets.token_hex(16).upper()


def _payment_public_token(payment_row: dict) -> str:
    tok = (payment_row.get('reconcile_token') or '').strip().upper()
    if tok:
        return tok
    return encode_payment_id(int(payment_row['id']))


def _payment_transfer_content(payment_row: dict) -> str:
    return f"{_payment_transfer_prefix()}{_payment_public_token(payment_row)}"


def _fetch_payment_by_ref(cur, raw: str):
    raw = (raw or '').strip()
    if not raw:
        return None
    if raw.isdigit():
        cur.execute('SELECT * FROM payments WHERE id = %s', (int(raw),))
        row = cur.fetchone()
        if row:
            return row
    token = raw.upper()
    cur.execute('SELECT * FROM payments WHERE reconcile_token = %s', (token,))
    row = cur.fetchone()
    if row:
        return row
    try:
        payment_id = int(decode_payment_id(raw))
    except Exception:
        return None
    cur.execute('SELECT * FROM payments WHERE id = %s', (payment_id,))
    return cur.fetchone()


def _load_used_sepay_tx_ids(cur) -> set[str]:
    cur.execute(
        "SELECT sepay_tx_id FROM payments WHERE status = %s AND sepay_tx_id IS NOT NULL AND sepay_tx_id != ''",
        ('completed',),
    )
    return {
        str(r.get('sepay_tx_id'))
        for r in (cur.fetchall() or [])
        if r.get('sepay_tx_id') not in (None, '')
    }


def _amount_matches_invoice(parsed_amount: float | None, amount_vnd: int) -> bool:
    if parsed_amount is None or parsed_amount <= 0:
        return False
    return int(round(parsed_amount)) == int(amount_vnd)


def _payment_transfer_prefix() -> str:
    # Bắt buộc bắt đầu bằng SEVQR theo yêu cầu, sau đó đến NAME_WEB + NAPTOKEN
    pfx = (Config.PAYMENT_TRANSFER_PREFIX or 'SEVQR').strip().upper()
    name = (Config.NAME_WEB or '').strip().upper()
    return f"{pfx}{name}NAPTOKEN"


def _vietqr_image_bank_segment() -> str | None:
    """
    VietQR image endpoint yêu cầu acquirer id (thường là BIN 6 số), không phải mã viết tắt ngân hàng.
    Cho phép cấu hình trực tiếp VIETQR_ACQUIRER_ID; nếu không có thì map một số mã phổ biến.
    """
    acq = (getattr(Config, 'VIETQR_ACQUIRER_ID', '') or '').strip()
    if acq:
        return acq
    code = (Config.VIETQR_BANK_CODE or '').strip().upper()
    if not code:
        return None
    if code.isdigit():
        return code
    # Một số map phổ biến (có thể mở rộng theo nhu cầu)
    if code in ('CTG', 'VIETINBANK', 'VIETIN'):
        return '970415'
    # Fallback: thử dùng nguyên code (một số ngân hàng vẫn chấp nhận mã viết tắt)
    return code


def _build_vietqr_image_url(amount_vnd: int, add_info: str) -> str | None:
    bank = _vietqr_image_bank_segment()
    acc = (Config.VIETQR_ACCOUNT_NO or '').strip()
    if not bank or not acc:
        return None
    template = (Config.VIETQR_TEMPLATE or 'compact2').strip()
    # VietQR public image format: https://img.vietqr.io/image/{BANK}-{ACC}-{TEMPLATE}.png?amount=...&addInfo=...&accountName=...
    base = f"https://img.vietqr.io/image/{bank}-{acc}-{template}.png"
    params = {
        'amount': int(amount_vnd) if amount_vnd else None,
        'addInfo': (add_info or '').strip(),
        'accountName': (Config.VIETQR_ACCOUNT_NAME or '').strip() or None,
    }
    params = {k: v for k, v in params.items() if v is not None and v != ''}
    return base + ('?' + urlencode(params) if params else '')


def _extract_tx_list_from_sepay_json(data):
    """Normalize SePay JSON response into a list[dict]."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        # Common wrappers
        for k in ('data', 'transactions', 'items', 'results', 'records', 'rows'):
            v = data.get(k)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                for kk in ('data', 'transactions', 'items', 'results', 'records', 'rows'):
                    vv = v.get(kk)
                    if isinstance(vv, list):
                        return [x for x in vv if isinstance(x, dict)]
    return []


def _parse_money_vn_to_float(value) -> float | None:
    """Parse SePay/VN bank UI amounts like '+2.000 đ', '2000', '2,000', '2.000,5'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not float(value).is_integer() and value > 1e6:
            # Heuristic: some APIs return cents as float
            return float(value)
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    s = s.replace('đ', '').replace('VND', '').replace('vnd', '')
    s = s.replace('+', '').replace('-', '').strip()
    # Remove thousand separators like 1.234.567
    # If both '.' and ',' exist, assume ',' is decimal separator (VN style)
    if ',' in s and '.' in s:
        # remove dots (thousands), comma -> dot
        s2 = s.replace('.', '').replace(',', '.')
    elif ',' in s and '.' not in s:
        # If only comma: treat as decimal if looks like decimal; else thousands
        parts = s.split(',')
        if len(parts) == 2 and len(parts[1]) <= 2:
            s2 = parts[0].replace('.', '') + '.' + parts[1]
        else:
            s2 = s.replace(',', '')
    else:
        # only dots or plain digits
        # If multiple dots, treat as thousands separators
        if s.count('.') > 1:
            s2 = s.replace('.', '')
        elif s.count('.') == 1:
            left, right = s.split('.', 1)
            if len(right) == 3 and right.isdigit():
                # 2.000 style thousands
                s2 = left + right
            else:
                s2 = s  # decimal dot
        else:
            s2 = s
    try:
        return float(s2)
    except ValueError:
        return None


def _sepay_get_last_20_transactions():
    """Gọi SePay lấy lịch sử giao dịch. Trả về list giao dịch (mỗi item là dict)."""
    url = (Config.SEPAY_HISTORY_URL or '').strip()
    key = (Config.SEPAY_API_KEY or '').strip()
    if not url:
        return []
    headers = {}
    if key:
        # Chấp nhận cả Bearer token lẫn x-api-key tùy cấu hình SePay phía bạn
        headers['Authorization'] = f"Bearer {key}"
        headers['x-api-key'] = key
    try:
        # SePay user API thường hỗ trợ limit/offset; nếu không hỗ trợ, server sẽ bỏ qua query thừa.
        params = {
            'limit': 50,
            'offset': 0,
            'page_size': 50,
            'page': 1,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=int(Config.SEPAY_TIMEOUT))
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print('[SePay] fetch history error:', e)
        return []

    txs = _extract_tx_list_from_sepay_json(data)
    return txs[:50]


def _match_sepay_transaction_for_payment(payment_row: dict, *, used_sepay_tx_ids: set[str] | None = None):
    """Return (matched: bool, sepay_tx_id: str|None)."""

    def pick(tx: dict, keys: list[str]):
        """Pick first non-empty value by key (case-insensitive)."""
        if not isinstance(tx, dict):
            return None
        lower_map = {str(k).lower(): k for k in tx.keys()}
        for k in keys:
            kk = str(k).lower()
            if kk in lower_map:
                v = tx.get(lower_map[kk])
                if v is not None and v != '':
                    return v
        return None

    used = used_sepay_tx_ids or set()
    target_token = _payment_public_token(payment_row)
    prefix = _payment_transfer_prefix()
    pattern = rf"{re.escape(prefix)}([A-Fa-f0-9]+)"

    history = _sepay_get_last_20_transactions()
    for tx in history:
        if not isinstance(tx, dict):
            continue

        content = pick(tx, ['transaction_content', 'content', 'noi_dung', 'description', 'memo', 'message', 'note'])
        if content is None:
            content = ''
        content = str(content).strip()
        if not content:
            continue

        raw_amount = pick(tx, ['amount_in', 'amount', 'so_tien', 'amountIn', 'credit', 'credit_amount'])
        amount = _parse_money_vn_to_float(raw_amount)
        if not _amount_matches_invoice(amount, int(payment_row['amount_vnd'])):
            continue

        m = re.search(pattern, content, flags=re.IGNORECASE)
        if not m:
            continue

        found_token = (m.group(1) or '').strip().upper()
        if found_token != target_token:
            continue

        tx_id = pick(tx, ['reference_number', 'id', 'code', 'tx_id', 'transaction_id', 'ma_tham_chieu', 'reference', 'ref', 'ma_giao_dich'])
        if tx_id is not None and str(tx_id) in used:
            continue
        if tx_id is not None:
            print(f'[SePay] matched payment_id={payment_row.get("id")} tx_id={tx_id} amount={amount}')
        return True, str(tx_id) if tx_id is not None else None

    return False, None


def allowed_file(filename):
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    return ext in Config.ALLOWED_EXTENSIONS


def _user_avatar_setting_key(user_id):
    return f'user_avatar:{int(user_id)}'


def _user_avatar_from_db(user_id):
    data = get_app_setting(_user_avatar_setting_key(user_id))
    if isinstance(data, dict) and data.get('data_b64'):
        return data
    return None


def _delete_user_avatar_db(user_id):
    try:
        delete_app_setting(_user_avatar_setting_key(user_id))
    except Exception:
        pass


def _avatar_mime_for_ext(ext):
    ext = (ext or 'png').lower()
    if ext == 'jpeg':
        ext = 'jpg'
    return {
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'webp': 'image/webp',
    }.get(ext, 'application/octet-stream')


def _avatar_public_url(user_id, avatar_path=None):
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        uid = None
    if uid:
        data = _user_avatar_from_db(uid)
        if data:
            v = (data.get('updated_at') or '').strip()
            q = f'user_id={uid}'
            if v:
                q += f'&v={v}'
            return f'/api/user/avatar/image?{q}'
    if not avatar_path:
        return ''
    name = os.path.basename(str(avatar_path).strip().replace('\\', '/'))
    if not name or '..' in name:
        return ''
    return f'/uploads/{name}'


def _delete_avatar_file(avatar_path):
    if not avatar_path:
        return
    name = os.path.basename(str(avatar_path).strip().replace('\\', '/'))
    if not name or '..' in name or not name.startswith('avatar_'):
        return
    path = os.path.join(Config.UPLOAD_FOLDER, name)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _serialize_user_profile(row):
    if not row:
        return None
    status = normalize_account_status(row.get('account_status'))
    out = {
        'id': row['id'],
        'username': row['username'],
        'full_name': row.get('full_name') or row['username'],
        'email': row.get('email') or '',
        'role': _normalize_role(row.get('role')),
        'analysis_credits': int(
            row.get('analysis_credits')
            if row.get('analysis_credits') is not None
            else Config.INITIAL_ANALYSIS_CREDITS
        ),
        'force_change_password': bool(int(row.get('force_change_password') or 0)),
        'email_verified': bool(int(row.get('email_verified') or 0)),
        'avatar_url': _avatar_public_url(row['id'], row.get('avatar_path')),
        'created_at': row.get('created_at'),
        'is_google_only': _is_google_only_user(row),
        'account_status': status,
    }
    if status == 'pending_delete':
        out['delete_requested_at'] = row.get('delete_requested_at')
        out['delete_scheduled_at'] = row.get('delete_scheduled_at')
    return out


def _normalize_role(value):
    r = (value or 'user').strip().lower()
    return r if r in ('admin', 'user') else 'user'


def parse_admin_user_id():
    uid = request.args.get('admin_user_id', type=int)
    if uid is not None:
        return uid
    data = request.get_json(silent=True) or {}
    try:
        v = data.get('admin_user_id')
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def require_admin():
    """Trả về (admin_id, None) nếu hợp lệ; ngược lại (None, (jsonify, status))."""
    admin_id = parse_admin_user_id()
    if not admin_id:
        return None, (jsonify({'error': 'Thiếu admin_user_id'}), 400)
    db = get_db()
    cur = plain_cursor(db)
    try:
        cur.execute('SELECT role FROM users WHERE id = %s', (admin_id,))
        row = cur.fetchone()
        if not row:
            return None, (jsonify({'error': 'Không tìm thấy tài khoản'}), 404)
        if _normalize_role(row[0]) != 'admin':
            return None, (jsonify({'error': 'Không có quyền quản trị'}), 403)
        return admin_id, None
    finally:
        cur.close()
        db.close()


def _count_admins(cursor):
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM users WHERE LOWER(COALESCE(role, 'user')) = 'admin'",
    )
    row = cursor.fetchone()
    if not row:
        return 0
    return int(row['cnt'] if isinstance(row, dict) else row[0])


def _refund_analysis_credits(user_id, n):
    """Hoàn lượt khi đã trừ nhưng phân tích lỗi ngoại lệ."""
    if not user_id or n <= 0:
        return
    try:
        db = get_db()
        cur = plain_cursor(db)
        cur.execute('UPDATE users SET analysis_credits = analysis_credits + %s WHERE id = %s', (n, user_id))
        db.commit()
        cur.close()
        db.close()
    except DatabaseError as e:
        print('[Credits] Hoàn lượt thất bại:', e)


# ---------- API Đăng ký (OTP Gmail) ----------
@app.route('/api/register', methods=['POST'])
def register():
    body, status = register_request(request.get_json() or {})
    return jsonify(body), status


@app.route('/api/register/verify', methods=['POST'])
def register_verify_route():
    body, status = register_verify(request.get_json() or {})
    return jsonify(body), status


@app.route('/api/register/resend', methods=['POST'])
def register_resend_route():
    body, status = register_resend(request.get_json() or {})
    return jsonify(body), status


@app.route('/api/forgot-password', methods=['POST'])
def forgot_password_route():
    body, status = forgot_password_request(request.get_json() or {})
    return jsonify(body), status


@app.route('/api/forgot-password/verify', methods=['POST'])
def forgot_password_verify_route():
    body, status = forgot_password_verify(request.get_json() or {})
    return jsonify(body), status


@app.route('/api/forgot-password/resend', methods=['POST'])
def forgot_password_resend_route():
    body, status = forgot_password_resend(request.get_json() or {})
    return jsonify(body), status


# ---------- Yêu cầu xóa / khôi phục tài khoản ----------
@app.route('/api/account/delete/request', methods=['POST'])
def account_delete_request_route():
    user_id, err = resolve_request_user_id()
    if err:
        return err
    body, status = delete_account_request(user_id, request.get_json() or {})
    return jsonify(body), status


@app.route('/api/account/delete/verify', methods=['POST'])
def account_delete_verify_route():
    user_id, err = resolve_request_user_id()
    if err:
        return err
    body, status = delete_account_verify(user_id, request.get_json() or {})
    return jsonify(body), status


@app.route('/api/account/delete/resend', methods=['POST'])
def account_delete_resend_route():
    user_id, err = resolve_request_user_id()
    if err:
        return err
    body, status = delete_account_resend(user_id, request.get_json() or {})
    return jsonify(body), status


@app.route('/api/account/restore/request', methods=['POST'])
def account_restore_request_route():
    user_id, err = resolve_request_user_id()
    if err:
        return err
    body, status = restore_account_request(user_id, request.get_json() or {})
    return jsonify(body), status


@app.route('/api/account/restore/verify', methods=['POST'])
def account_restore_verify_route():
    user_id, err = resolve_request_user_id()
    if err:
        return err
    body, status = restore_account_verify(user_id, request.get_json() or {})
    return jsonify(body), status


@app.route('/api/account/restore/resend', methods=['POST'])
def account_restore_resend_route():
    user_id, err = resolve_request_user_id()
    if err:
        return err
    body, status = restore_account_resend(user_id, request.get_json() or {})
    return jsonify(body), status


# ---------- API Đăng nhập ----------
@app.route('/api/login', methods=['POST'])
def login():
    _maybe_sweep_expired_deletions()
    data = request.get_json() or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return jsonify({'error': 'Vui lòng nhập tài khoản và mật khẩu'}), 400

    db = get_db()
    cursor = dict_cursor(db)
    try:
        if '@' in username:
            cursor.execute(
                f'SELECT {USER_PROFILE_SELECT} FROM users WHERE LOWER(email) = %s',
                (username.lower(),),
            )
        else:
            cursor.execute(
                f'SELECT {USER_PROFILE_SELECT} FROM users WHERE username = %s',
                (username,),
            )
        user = cursor.fetchone()
        if not user:
            return jsonify({'error': 'Sai tài khoản hoặc mật khẩu!'}), 401
        status = normalize_account_status(user.get('account_status'))
        err_resp = _account_status_error(status)
        if err_resp:
            return err_resp
        # Password NULL = tài khoản chỉ đăng nhập Google (sau migration)
        if not user.get('password'):
            return jsonify({
                'error': 'Tài khoản này dùng Đăng nhập bằng Google. Vui lòng bấm "Đăng nhập bằng Google".',
            }), 401
        if not int(user.get('email_verified') or 0):
            return jsonify({
                'error': 'Tài khoản chưa xác thực Gmail. Vui lòng hoàn tất xác minh OTP khi đăng ký.',
            }), 403
        if not check_password_hash(user['password'], password):
            return jsonify({'error': 'Sai tài khoản hoặc mật khẩu!'}), 401
        return jsonify(_serialize_user_profile(user))
    finally:
        cursor.close()
        db.close()


def _is_google_only_user(row):
    """Chỉ đăng nhập Google — không có mật khẩu cục bộ."""
    if not row:
        return False
    return bool(row.get('google_id')) and not row.get('password')


def _google_username(google_id: str) -> str:
    gid = str(google_id or '').strip()
    return f'google_{gid}'[:50]


def _fetch_user_profile(cursor, user_id: int):
    cursor.execute(
        f'SELECT {USER_PROFILE_SELECT} FROM users WHERE id = %s',
        (user_id,),
    )
    return cursor.fetchone()


def _resolve_google_user(cursor, db, google_id, email, name):
    """
    Tìm user theo google_id; nếu Gmail đã đăng ký thủ công thì gắn google_id vào tài khoản đó
    thay vì tạo bản ghi mới với username = email.
    """
    cursor.execute(
        f'SELECT {USER_PROFILE_SELECT} FROM users WHERE google_id = %s',
        (google_id,),
    )
    user = cursor.fetchone()
    if user:
        return user, None

    if email:
        cursor.execute(
            f'SELECT {USER_PROFILE_SELECT} FROM users WHERE LOWER(email) = %s ORDER BY id ASC LIMIT 1',
            (email.lower(),),
        )
        existing = cursor.fetchone()
        if existing:
            existing_gid = existing.get('google_id')
            if existing_gid and str(existing_gid) != str(google_id):
                return None, 'account_conflict'
            if not existing_gid:
                pcursor = plain_cursor(db)
                try:
                    pcursor.execute(
                        """
                        UPDATE users
                        SET google_id = %s, email_verified = GREATEST(COALESCE(email_verified, 0), 1)
                        WHERE id = %s
                        """,
                        (google_id, existing['id']),
                    )
                    db.commit()
                finally:
                    pcursor.close()
                return _fetch_user_profile(cursor, existing['id']), None
            return existing, None

    created = _create_google_user(cursor, db, google_id, email, name)
    if not created:
        return None, 'db'
    return _fetch_user_profile(cursor, created['id']), None

@app.route('/api/auth/google', methods=['GET'])
def auth_google():
    """Chuyển hướng sang Google đăng nhập."""
    if not (Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET):
        return jsonify({'error': 'Chưa cấu hình Google OAuth (GOOGLE_CLIENT_ID/SECRET)'}), 503
    redirect_uri = _google_redirect_uri()
    print('[Google OAuth] authorize redirect_uri =', redirect_uri)
    oauth_state = 'mobile' if request.args.get('mobile') == '1' else None
    return oauth.google.authorize_redirect(redirect_uri, state=oauth_state)


@app.route('/api/auth/google/callback', methods=['GET'])
def auth_google_callback():
    """Google redirect về đây; đổi code lấy userinfo, tạo JWT hệ thống, redirect 302 về frontend ?token=..."""
    mobile_oauth = request.args.get('state') == 'mobile'

    def _oauth_fail(error_code: str):
        if mobile_oauth:
            return redirect(f'lumistyle://oauth?google_error={error_code}')
        return _redirect_frontend(google_error=error_code)

    if not (Config.GOOGLE_CLIENT_ID and Config.GOOGLE_CLIENT_SECRET):
        return _oauth_fail('config')
    try:
        token = oauth.google.authorize_access_token()
    except Exception as e:
        print('[Google OAuth] authorize_access_token error:', e)
        return _oauth_fail('token')
    userinfo = token.get('userinfo') or {}
    google_id = userinfo.get('sub')
    email = (userinfo.get('email') or '').strip()
    name = (userinfo.get('name') or email or 'Người dùng').strip()
    if not google_id:
        return _oauth_fail('no_sub')

    # Tìm theo google_id; gộp Gmail đã đăng ký; chỉ tạo mới khi chưa có tài khoản
    _maybe_sweep_expired_deletions()
    db = get_db()
    cursor = dict_cursor(db)
    try:
        user, link_error = _resolve_google_user(cursor, db, google_id, email, name)
        if link_error:
            return _oauth_fail(link_error)
        if not user:
            return _oauth_fail('db')
        status = normalize_account_status(user.get('account_status'))
        if status == 'deleted':
            return _oauth_fail('deleted')
        access = create_access_token(user['id'], email)
        if mobile_oauth:
            from urllib.parse import quote
            return redirect(f'lumistyle://oauth?token={quote(access, safe="")}')
        return _redirect_frontend(token=access)
    except DatabaseError as e:
        print('[Google OAuth] DB error:', e)
        return _oauth_fail('db')
    finally:
        cursor.close()
        db.close()


def _create_google_user(cursor, db, google_id, email, name, username=None):
    """Tạo user mới chỉ đăng nhập Google — không dùng email làm username."""
    if email:
        cursor.execute(
            'SELECT id FROM users WHERE LOWER(email) = %s LIMIT 1',
            (email.lower(),),
        )
        if cursor.fetchone():
            return None

    username = username or _google_username(google_id)
    pcursor = plain_cursor(db)
    try:
        pcursor.execute(
            'INSERT INTO users (username, password, full_name, google_id, email, email_verified, analysis_credits) VALUES (%s, NULL, %s, %s, %s, 1, %s)',
            (username, name, google_id, email or None, Config.INITIAL_ANALYSIS_CREDITS),
        )
        db.commit()
        return {'id': pcursor.lastrowid, 'username': username, 'full_name': name}
    except IntegrityError:
        db.rollback()
        alt_username = f'{username}_{str(google_id)[-6:]}'[:50]
        try:
            pcursor.execute(
                'INSERT INTO users (username, password, full_name, google_id, email, email_verified, analysis_credits) VALUES (%s, NULL, %s, %s, %s, 1, %s)',
                (alt_username, name, google_id, email or None, Config.INITIAL_ANALYSIS_CREDITS),
            )
            db.commit()
            return {'id': pcursor.lastrowid, 'username': alt_username, 'full_name': name}
        except IntegrityError:
            db.rollback()
            return None
    finally:
        pcursor.close()


@app.route('/api/auth/me', methods=['GET'])
def auth_me():
    """Xác thực JWT (Bearer) — dùng sau khi Google redirect ?token=... hoặc khi refresh trang."""
    _maybe_sweep_expired_deletions()
    raw = get_bearer_token()
    if not raw:
        return jsonify({'error': 'Thiếu token'}), 401
    try:
        payload = pyjwt.decode(raw, Config.SECRET_KEY, algorithms=['HS256'])
        user_id = int(payload['sub'])
    except pyjwt.PyJWTError:
        return jsonify({'error': 'Token không hợp lệ hoặc đã hết hạn'}), 401
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute(
            f'SELECT {USER_PROFILE_SELECT} FROM users WHERE id = %s',
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Tài khoản không tồn tại'}), 401
        status = normalize_account_status(row.get('account_status'))
        if status == 'deleted':
            return jsonify({'error': 'Tài khoản đã bị xóa hoặc vô hiệu hóa.'}), 403
        out = _serialize_user_profile(row)
        out['is_google_only'] = _is_google_only_user(row)
        return jsonify(out)
    finally:
        cur.close()
        db.close()


@app.route('/api/user/profile', methods=['GET'])
def user_profile():
    """Lấy lại lượt phân tích / thông tin hiển thị (sau khi mua gói hoặc refresh)."""
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'Thiếu user_id'}), 400
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute(
            f'SELECT {USER_PROFILE_SELECT} FROM users WHERE id = %s',
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Không tìm thấy tài khoản'}), 404
        status = normalize_account_status(row.get('account_status'))
        if status == 'deleted':
            return jsonify({'error': 'Tài khoản đã bị xóa hoặc vô hiệu hóa.'}), 403
        return jsonify(_serialize_user_profile(row))
    finally:
        cur.close()
        db.close()


@app.route('/api/user/profile', methods=['PUT', 'PATCH'])
def user_profile_update():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'error': 'Thiếu user_id'}), 400
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'user_id không hợp lệ'}), 400
    full_name = data.get('full_name')
    if full_name is None:
        return jsonify({'error': 'Không có trường cập nhật'}), 400
    full_name = (full_name or '').strip()
    if not full_name:
        return jsonify({'error': 'Họ tên không được để trống'}), 400
    if len(full_name) > 100:
        return jsonify({'error': 'Họ tên tối đa 100 ký tự'}), 400
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute('SELECT id FROM users WHERE id = %s', (user_id,))
        if not cur.fetchone():
            return jsonify({'error': 'Không tìm thấy tài khoản'}), 404
        cur.execute('UPDATE users SET full_name = %s WHERE id = %s', (full_name, user_id))
        db.commit()
        cur.execute(
            'SELECT id, username, full_name, google_id, email, analysis_credits, role, force_change_password, email_verified, avatar_path, created_at FROM users WHERE id = %s',
            (user_id,),
        )
        row = cur.fetchone()
        return jsonify({'message': 'Cập nhật hồ sơ thành công', 'user': _serialize_user_profile(row)})
    except DatabaseError:
        db.rollback()
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


@app.route('/api/user/avatar/image')
def user_avatar_image():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return '', 404
    data = _user_avatar_from_db(user_id)
    if data:
        try:
            raw = base64.b64decode(data.get('data_b64') or '')
        except (ValueError, TypeError):
            raw = b''
        if raw:
            return Response(raw, mimetype=_avatar_mime_for_ext(data.get('ext')))
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute('SELECT avatar_path FROM users WHERE id = %s', (user_id,))
        row = cur.fetchone()
    finally:
        cur.close()
        db.close()
    if not row:
        return '', 404
    avatar_path = row.get('avatar_path')
    if not avatar_path:
        return '', 404
    name = os.path.basename(str(avatar_path).strip().replace('\\', '/'))
    if not name or '..' in name or not name.startswith('avatar_'):
        return '', 404
    path = os.path.join(Config.UPLOAD_FOLDER, name)
    if not os.path.isfile(path):
        return '', 404
    try:
        with open(path, 'rb') as fp:
            file_raw = fp.read()
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else 'png'
        set_app_setting(_user_avatar_setting_key(user_id), {
            'ext': ext,
            'data_b64': base64.b64encode(file_raw).decode('ascii'),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        print(f'[Avatar DB seed {user_id}]', e)
    return send_from_directory(Config.UPLOAD_FOLDER, name)


@app.route('/api/user/avatar', methods=['POST', 'DELETE'])
def user_avatar():
    if request.method == 'DELETE':
        data = request.get_json(silent=True) or {}
        user_id = data.get('user_id') or request.args.get('user_id', type=int)
    else:
        user_id = request.form.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'Thiếu user_id'}), 400
    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'user_id không hợp lệ'}), 400
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute('SELECT id, avatar_path FROM users WHERE id = %s', (user_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Không tìm thấy tài khoản'}), 404
        if request.method == 'DELETE':
            _delete_user_avatar_db(user_id)
            _delete_avatar_file(row.get('avatar_path'))
            cur.execute('UPDATE users SET avatar_path = NULL WHERE id = %s', (user_id,))
            db.commit()
            return jsonify({'message': 'Đã xóa ảnh đại diện', 'avatar_url': ''})
        f = request.files.get('avatar')
        if not f or not f.filename:
            return jsonify({'error': 'Không có ảnh được gửi'}), 400
        if not allowed_file(f.filename):
            return jsonify({'error': 'Định dạng ảnh không hợp lệ (PNG, JPG, WEBP)'}), 400
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(0)
        if size > 2 * 1024 * 1024:
            return jsonify({'error': 'Ảnh đại diện tối đa 2MB'}), 400
        ext = f.filename.rsplit('.', 1)[-1].lower()
        if ext == 'jpeg':
            ext = 'jpg'
        filename = f'avatar_{user_id}_{uuid.uuid4().hex[:12]}.{ext}'
        path = os.path.join(Config.UPLOAD_FOLDER, filename)
        raw = f.read()
        set_app_setting(_user_avatar_setting_key(user_id), {
            'ext': ext,
            'data_b64': base64.b64encode(raw).decode('ascii'),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        })
        _delete_avatar_file(row.get('avatar_path'))
        try:
            os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
            with open(path, 'wb') as out:
                out.write(raw)
        except OSError as e:
            print(f'[Avatar file mirror {user_id}]', e)
        cur.execute('UPDATE users SET avatar_path = %s WHERE id = %s', (filename, user_id))
        db.commit()
        return jsonify({
            'message': 'Cập nhật ảnh đại diện thành công',
            'avatar_url': _avatar_public_url(user_id, filename),
        })
    except DatabaseError:
        db.rollback()
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


_PACKAGE_FEATURES = [
    'Phân tích outfit đa ảnh (PNG, JPG, WEBP)',
    'Mô tả từng món, phong cách tổng thể & gợi ý dịp',
    'Lưu lịch sử & thống kê khi đăng nhập',
]

_PACKAGE_KEY_RE = re.compile(r'^[a-z0-9_-]{1,32}$')


def _packages_json_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'packages.json')


def _landing_json_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'landing.json')


_DEFAULT_LANDING_HERO_IMAGE = (
    'https://lh3.googleusercontent.com/aida-public/AB6AXuBk2Knb30lCAojqQge0PzJQlCLTnpULj3THoW_7MSRe3nHAAt5El9WjKPj6T8sHPaEt6RVxw-y1u_bfbd0wZmvTnJuJX-_ztQIHlvaOG719UJiRblB23LDb2hEedGmcINkW1yjLTn6pFAbzOhGSaxlLKGEbmLT48kCvslbtMu6jJg8oz_653nND2UKTAUBoShpUW0lUiO_C0CcHKUyrHnDGijFGsgve0u6fBFY1XAWFDsPNh6yAx-xHKeIWz_CK1mrEuf1fscgb6ao'
)

LANDING_INTRO_KEYS = (
    'intro.kicker',
    'intro.heroTitleLine1',
    'intro.heroTitleLine2',
    'intro.lead',
    'intro.note',
    'intro.featuresTitle',
    'intro.featuresLead',
    'intro.f1Title',
    'intro.f1Desc',
    'intro.f2Title',
    'intro.f2Desc',
    'intro.f3Title',
    'intro.f3Desc',
    'intro.f4Title',
    'intro.f4Desc',
    'intro.galleryTitle',
    'intro.galleryLead',
    'intro.step1',
    'intro.wf1Title',
    'intro.step2',
    'intro.wf2Title',
    'intro.step3',
    'intro.wf3Title',
    'intro.techTitle',
    'intro.techLead',
    'intro.tech1Title',
    'intro.tech1Desc',
    'intro.tech2Title',
    'intro.tech2Desc',
    'intro.tech3Title',
    'intro.tech3Desc',
    'intro.howTitle',
    'intro.how1Title',
    'intro.how1Desc',
    'intro.how2Title',
    'intro.how2Desc',
    'intro.how3Title',
    'intro.how3Desc',
    'intro.faqTitle',
    'intro.faq1Q',
    'intro.faq1A',
    'intro.faq2Q',
    'intro.faq2A',
    'intro.faq3Q',
    'intro.faq3A',
    'intro.ctaEyebrow',
    'intro.ctaTitle',
    'intro.ctaRegister',
    'intro.ctaFootAlt',
)

LANDING_HTML_KEYS = frozenset({
    'intro.note',
    'intro.galleryLead',
    'intro.ctaFootAlt',
})

LANDING_SHORT_TEXT_KEYS = frozenset({
    'intro.kicker',
    'intro.heroTitleLine1',
    'intro.heroTitleLine2',
    'intro.howTitle',
    'intro.featuresTitle',
    'intro.galleryTitle',
    'intro.techTitle',
    'intro.faqTitle',
    'intro.ctaEyebrow',
    'intro.ctaTitle',
    'intro.ctaRegister',
    'intro.step1',
    'intro.step2',
    'intro.step3',
    'intro.wf1Title',
    'intro.wf2Title',
    'intro.wf3Title',
    'intro.tech1Title',
    'intro.tech2Title',
    'intro.tech3Title',
    'intro.how1Title',
    'intro.how2Title',
    'intro.how3Title',
    'intro.f1Title',
    'intro.f2Title',
    'intro.f3Title',
    'intro.f4Title',
    'intro.faq1Q',
    'intro.faq2Q',
    'intro.faq3Q',
})


def _strip_html_to_text(value):
    s = str(value or '').strip()
    if not s or '<' not in s:
        return s
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()


def _normalize_landing_field_value(key, value):
    raw = str(value or '').strip()
    if not raw:
        return ''
    if key in LANDING_HTML_KEYS:
        return raw
    text = _strip_html_to_text(raw)
    if key in LANDING_SHORT_TEXT_KEYS and len(text) > 160:
        first_line = text.split('\n')[0].strip()
        text = first_line[:160].strip() if len(first_line) > 160 else first_line
    return text


def _sanitize_landing_vi_block(vi):
    defaults = _default_landing_config()['vi']
    block = _normalize_landing_lang_block(vi, defaults)
    return {k: _normalize_landing_field_value(k, block.get(k, '')) for k in LANDING_INTRO_KEYS}


def _auto_translate_landing_en(vi_block, config, existing=None):
    en = {}
    for key in LANDING_INTRO_KEYS:
        src = str(vi_block.get(key) or '').strip()
        if not src:
            en[key] = ''
            continue
        allow_html = key in LANDING_HTML_KEYS
        text = src if allow_html else _normalize_landing_field_value(key, src)
        translated, _ = translate_text(text, 'en', config)
        en[key] = translated if allow_html else _normalize_landing_field_value(key, translated)
    return en


def _auto_translate_landing_marker_en(cfg, config, existing=None):
    out = {}
    for src_key, dst_key in (
        ('hero_marker_a', 'hero_marker_a_en'),
        ('hero_marker_b', 'hero_marker_b_en'),
    ):
        src = str(cfg.get(src_key) or '').strip()
        if not src:
            out[dst_key] = ''
            continue
        translated, _ = translate_text(src, 'en', config)
        out[dst_key] = _strip_html_to_text(translated)
    return out


def _i18n_json_path():
    return os.path.join(app.static_folder, 'data', 'i18n.json')


def _read_i18n_bucket(lang):
    path = _i18n_json_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print('[i18n JSON read]', e)
        return {}
    bucket = data.get(lang) if isinstance(data, dict) else None
    return bucket if isinstance(bucket, dict) else {}


def _default_landing_config():
    vi_src = _read_i18n_bucket('vi')
    en_src = _read_i18n_bucket('en')
    return {
        'vi': {k: str(vi_src.get(k) or '') for k in LANDING_INTRO_KEYS},
        'en': {k: str(en_src.get(k) or '') for k in LANDING_INTRO_KEYS},
        'hero_image_url': _DEFAULT_LANDING_HERO_IMAGE,
        'hero_marker_a': 'Wool Overcoat · 92% Match',
        'hero_marker_b': 'Leather Chelsea Boots · Minimalist',
    }


def _normalize_landing_lang_block(raw, fallback):
    out = dict(fallback)
    if not isinstance(raw, dict):
        return out
    for key in LANDING_INTRO_KEYS:
        if key in raw:
            out[key] = str(raw.get(key) or '')
    return out


def _load_landing_config():
    defaults = _default_landing_config()
    stored = get_app_setting('landing')
    if isinstance(stored, dict) and stored.get('vi'):
        return {
            'vi': _normalize_landing_lang_block(stored.get('vi'), defaults['vi']),
            'en': _normalize_landing_lang_block(stored.get('en'), defaults['en']),
            'hero_image_url': str(stored.get('hero_image_url') or defaults['hero_image_url']).strip(),
            'hero_marker_a': str(stored.get('hero_marker_a') or defaults['hero_marker_a']).strip(),
            'hero_marker_b': str(stored.get('hero_marker_b') or defaults['hero_marker_b']).strip(),
            'hero_marker_a_en': str(stored.get('hero_marker_a_en') or '').strip(),
            'hero_marker_b_en': str(stored.get('hero_marker_b_en') or '').strip(),
        }
    path = _landing_json_path()
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print('[Landing JSON read]', e)
            return defaults
        if isinstance(data, dict):
            cfg = {
                'vi': _normalize_landing_lang_block(data.get('vi'), defaults['vi']),
                'en': _normalize_landing_lang_block(data.get('en'), defaults['en']),
                'hero_image_url': str(data.get('hero_image_url') or defaults['hero_image_url']).strip(),
                'hero_marker_a': str(data.get('hero_marker_a') or defaults['hero_marker_a']).strip(),
                'hero_marker_b': str(data.get('hero_marker_b') or defaults['hero_marker_b']).strip(),
                'hero_marker_a_en': str(data.get('hero_marker_a_en') or '').strip(),
                'hero_marker_b_en': str(data.get('hero_marker_b_en') or '').strip(),
            }
            try:
                set_app_setting('landing', cfg)
            except Exception as e:
                print('[Landing DB seed]', e)
            return cfg
    try:
        saved = _save_landing_config(defaults)
        return saved
    except Exception:
        return defaults


def _landing_payload_from_cfg(cfg):
    return {
        'vi': {k: str(cfg.get('vi', {}).get(k) or '') for k in LANDING_INTRO_KEYS},
        'en': {k: str(cfg.get('en', {}).get(k) or '') for k in LANDING_INTRO_KEYS},
        'hero_image_url': str(cfg.get('hero_image_url') or '').strip(),
        'hero_marker_a': str(cfg.get('hero_marker_a') or '').strip(),
        'hero_marker_b': str(cfg.get('hero_marker_b') or '').strip(),
        'hero_marker_a_en': str(cfg.get('hero_marker_a_en') or '').strip(),
        'hero_marker_b_en': str(cfg.get('hero_marker_b_en') or '').strip(),
    }


def _save_landing_to_file(payload):
    path = _landing_json_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _save_landing_config(cfg):
    payload = _landing_payload_from_cfg(cfg)
    set_app_setting('landing', payload)
    if _can_write_project_files():
        try:
            _save_landing_to_file(payload)
        except OSError as e:
            print('[Landing file mirror]', e)
    return payload


def _validate_landing_payload(data):
    if not isinstance(data, dict):
        return False, 'Dữ liệu không hợp lệ.'
    vi = data.get('vi')
    if not isinstance(vi, dict):
        return False, 'Thiếu nội dung tiếng Việt.'
    required = ('intro.heroTitleLine1', 'intro.lead', 'intro.featuresTitle')
    for key in required:
        if not str(vi.get(key) or '').strip():
            return False, f'Thiếu {key}.'
    hero_url = str(data.get('hero_image_url') or '').strip()
    if hero_url and not (hero_url.startswith('http://') or hero_url.startswith('https://') or hero_url.startswith('/')):
        return False, 'URL ảnh hero không hợp lệ.'
    return True, {
        'vi': _sanitize_landing_vi_block(vi),
        'hero_image_url': hero_url or _DEFAULT_LANDING_HERO_IMAGE,
        'hero_marker_a': str(data.get('hero_marker_a') or '').strip(),
        'hero_marker_b': str(data.get('hero_marker_b') or '').strip(),
    }


def _default_packages_list():
    return [
        {
            'key': 'st',
            'name': Config.PACKAGE_NAME_ST,
            'credits': int(Config.CREDIT_PACKAGE_ST),
            'price_vnd': int(Config.PRICE_PACKAGE_ST_VND),
            'popular': False,
        },
        {
            'key': '30',
            'name': Config.PACKAGE_NAME_30,
            'credits': int(Config.CREDIT_PACKAGE_30),
            'price_vnd': int(Config.PRICE_PACKAGE_30_VND),
            'popular': True,
        },
        {
            'key': '50',
            'name': Config.PACKAGE_NAME_50,
            'credits': int(Config.CREDIT_PACKAGE_50),
            'price_vnd': int(Config.PRICE_PACKAGE_50_VND),
            'popular': False,
        },
    ]


def _normalize_package_item(raw):
    if not isinstance(raw, dict):
        return None
    key = str(raw.get('key') or '').strip().lower()
    name = str(raw.get('name') or '').strip()
    try:
        credits = int(raw.get('credits'))
        price_vnd = int(raw.get('price_vnd'))
    except (TypeError, ValueError):
        return None
    if not _PACKAGE_KEY_RE.match(key):
        return None
    if credits <= 0 or price_vnd < 0:
        return None
    if not name:
        name = key
    return {
        'key': key,
        'name': name,
        'credits': credits,
        'price_vnd': price_vnd,
        'popular': bool(raw.get('popular')),
    }


def _load_packages_raw():
    stored = get_app_setting('packages')
    if isinstance(stored, dict):
        pkgs = stored.get('packages')
        if isinstance(pkgs, list) and pkgs:
            out = []
            for item in pkgs:
                norm = _normalize_package_item(item)
                if norm:
                    out.append(norm)
            if out:
                return out
    path = _packages_json_path()
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print('[Packages JSON read]', e)
            return _default_packages_list()
        pkgs = data.get('packages') if isinstance(data, dict) else data
        if isinstance(pkgs, list) and pkgs:
            out = []
            for item in pkgs:
                norm = _normalize_package_item(item)
                if norm:
                    out.append(norm)
            if out:
                try:
                    set_app_setting('packages', {'packages': out})
                except Exception as e:
                    print('[Packages DB seed]', e)
                return out
    pkgs = _default_packages_list()
    try:
        set_app_setting('packages', {'packages': pkgs})
    except Exception as e:
        print('[Packages DB seed default]', e)
    if _can_write_project_files():
        try:
            _save_packages_to_file(pkgs)
        except OSError:
            pass
    return pkgs


def _save_packages_to_file(packages):
    path = _packages_json_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump({'packages': packages}, f, ensure_ascii=False, indent=2)


def _save_packages_raw(packages):
    set_app_setting('packages', {'packages': packages})
    if _can_write_project_files():
        try:
            _save_packages_to_file(packages)
        except OSError as e:
            print('[Packages file mirror]', e)


def _validate_packages_list(packages):
    if not isinstance(packages, list) or len(packages) == 0:
        return False, 'Cần ít nhất một gói cước.'
    keys = set()
    normalized = []
    for i, raw in enumerate(packages):
        item = _normalize_package_item(raw)
        if not item:
            return False, f'Gói #{i + 1} không hợp lệ (mã, tên, lượt, giá).'
        if item['key'] in keys:
            return False, f'Mã gói trùng: {item["key"]}'
        keys.add(item['key'])
        normalized.append(item)
    popular_count = sum(1 for p in normalized if p.get('popular'))
    if popular_count > 1:
        return False, 'Chỉ được đánh dấu tối đa một gói «Phổ biến».'
    return True, normalized


def _packages_for_public_api():
    items = []
    for p in _load_packages_raw():
        items.append({
            'key': p['key'],
            'credits': int(p['credits']),
            'price_vnd': int(p['price_vnd']),
            'name': p.get('name') or p['key'],
            'tagline': f"nhận {int(p['credits'])} lượt phân tích ảnh",
            'features': _PACKAGE_FEATURES,
            'popular': bool(p.get('popular')),
        })
    return items


def _packages_map():
    out = {}
    for p in _load_packages_raw():
        out[p['key']] = (int(p['credits']), int(p['price_vnd']))
    return out


@app.route('/api/packages', methods=['GET'])
def api_packages():
    """Chỉ hiển thị gói (lượt + giá tham khảo từ cấu hình), không thanh toán trực tuyến."""
    return jsonify({'packages': _packages_for_public_api()})


# ---------- Payments: tạo hóa đơn + polling trạng thái ----------
@app.route('/api/v1/payment/create', methods=['POST'])
def payment_create():
    _ensure_payments_table()
    data = request.get_json(silent=True) or {}
    user_id = data.get('user_id')
    package_id = (data.get('package_id') or '').strip()

    try:
        user_id = int(user_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'Thiếu hoặc sai user_id'}), 400

    pkg_map = _packages_map()
    if package_id not in pkg_map:
        return jsonify({'error': 'package_id không hợp lệ.'}), 400

    credits, amount_vnd = pkg_map[package_id]
    if amount_vnd <= 0 or credits <= 0:
        return jsonify({'error': 'Gói nạp chưa cấu hình hợp lệ'}), 500

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=max(1, int(Config.PAYMENT_EXPIRES_MINUTES)))
    reconcile_token = _generate_reconcile_token()

    db = get_db()
    cur = dict_cursor(db)
    try:
        # verify user exists
        cur.execute('SELECT id FROM users WHERE id = %s', (user_id,))
        if not cur.fetchone():
            return jsonify({'error': 'Không tìm thấy tài khoản'}), 404

        cur.execute(
            'INSERT INTO payments (user_id, package_key, credits, amount_vnd, status, expires_at, reconcile_token) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (user_id, package_id, credits, amount_vnd, 'pending', expires_at.replace(tzinfo=None), reconcile_token),
        )
        db.commit()
        payment_id = cur.lastrowid
        pay_row = {'id': int(payment_id), 'reconcile_token': reconcile_token}
        hex_id = _payment_public_token(pay_row)
        transfer_content = _payment_transfer_content(pay_row)
        qr_url = _build_vietqr_image_url(amount_vnd=amount_vnd, add_info=transfer_content)

        return jsonify({
            'id': int(payment_id),
            'hex_id': hex_id,
            'status': 'pending',
            'amount_vnd': int(amount_vnd),
            'credits': int(credits),
            'package_id': package_id,
            'expires_at': expires_at.isoformat(),
            'transfer_content': transfer_content,
            'qr_url': qr_url,
            'bank': {
                'bank_code': (Config.VIETQR_BANK_CODE or '').strip() or None,
                'account_no': (Config.VIETQR_ACCOUNT_NO or '').strip() or None,
                'account_name': (Config.VIETQR_ACCOUNT_NAME or '').strip() or None,
            },
        }), 201
    except DatabaseError as e:
        print('[Payment create] DB error:', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


@app.route('/api/v1/payment/status/<id_or_hex>', methods=['GET'])
def payment_status(id_or_hex):
    _ensure_payments_table()

    db = get_db()
    cur = dict_cursor(db)
    try:
        pay = _fetch_payment_by_ref(cur, id_or_hex)
        if not pay:
            return jsonify({'error': 'Không tìm thấy hóa đơn'}), 404
        payment_id = int(pay['id'])

        # Expire logic (soft fail) — vẫn có thể completed nếu tiền về sau
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if pay.get('status') != 'completed':
            exp = pay.get('expires_at')
            if exp and isinstance(exp, datetime) and now > exp and pay.get('status') == 'pending':
                cur.execute("UPDATE payments SET status = 'failed' WHERE id = %s AND status = 'pending'", (payment_id,))
                db.commit()
                pay['status'] = 'failed'

        # Reconcile with SePay if not completed yet
        if pay.get('status') != 'completed':
            used_tx_ids = _load_used_sepay_tx_ids(cur)
            matched, tx_id = _match_sepay_transaction_for_payment(pay, used_sepay_tx_ids=used_tx_ids)
            if matched:
                # Idempotent crediting
                cur.execute('SELECT status FROM payments WHERE id = %s', (payment_id,))
                st_row = cur.fetchone()
                if st_row and st_row.get('status') != 'completed':
                    cur.execute(
                        "UPDATE payments SET status = 'completed', sepay_tx_id = %s, completed_at = %s WHERE id = %s",
                        (tx_id, now, payment_id),
                    )
                    cur.execute(
                        'UPDATE users SET analysis_credits = analysis_credits + %s WHERE id = %s',
                        (int(pay['credits']), int(pay['user_id'])),
                    )
                    db.commit()
                    pay['status'] = 'completed'
                    pay['sepay_tx_id'] = tx_id
                    pay['completed_at'] = now

        hex_id = _payment_public_token(pay)
        transfer_content = _payment_transfer_content(pay)
        qr_url = _build_vietqr_image_url(amount_vnd=int(pay['amount_vnd']), add_info=transfer_content)

        return jsonify({
            'id': int(pay['id']),
            'hex_id': hex_id,
            'status': pay.get('status'),
            'amount_vnd': int(pay.get('amount_vnd') or 0),
            'credits': int(pay.get('credits') or 0),
            'package_id': pay.get('package_key'),
            'sepay_tx_id': pay.get('sepay_tx_id'),
            'created_at': pay.get('created_at').isoformat() if pay.get('created_at') else None,
            'expires_at': pay.get('expires_at').isoformat() if pay.get('expires_at') else None,
            'completed_at': pay.get('completed_at').isoformat() if pay.get('completed_at') else None,
            'transfer_content': transfer_content,
            'qr_url': qr_url,
        })
    except DatabaseError as e:
        print('[Payment status] DB error:', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


# ---------- API Đổi mật khẩu ----------
@app.route('/api/change-password', methods=['POST'])
def change_password():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    old_password = data.get('old_password') or ''
    new_password = data.get('new_password') or ''
    force_change = data.get('force_change') in (True, 'true', '1', 1)
    if not user_id or not new_password:
        return jsonify({'error': 'Vui lòng nhập đầy đủ thông tin'}), 400
    if len(new_password) < 6:
        return jsonify({'error': 'Mật khẩu mới cần ít nhất 6 ký tự'}), 400
    db = get_db()
    cursor = dict_cursor(db)
    try:
        cursor.execute(
            'SELECT id, password, force_change_password FROM users WHERE id = %s',
            (user_id,),
        )
        user = cursor.fetchone()
        if not user:
            return jsonify({'error': 'Không tìm thấy tài khoản'}), 401
        if not user.get('password'):
            return jsonify({'error': 'Tài khoản đăng nhập Google không đổi mật khẩu tại đây.'}), 400
        must_force = bool(int(user.get('force_change_password') or 0))
        if force_change or must_force:
            if not must_force:
                return jsonify({'error': 'Không thể đổi mật khẩu theo chế độ bắt buộc.'}), 400
        elif not old_password:
            return jsonify({'error': 'Vui lòng nhập mật khẩu hiện tại'}), 400
        elif not check_password_hash(user['password'], old_password):
            return jsonify({'error': 'Mật khẩu hiện tại không đúng'}), 401
        hashed = generate_password_hash(new_password)
        cursor.execute(
            'UPDATE users SET password = %s, force_change_password = 0 WHERE id = %s',
            (hashed, user_id),
        )
        db.commit()
        return jsonify({'message': 'Đổi mật khẩu thành công!', 'force_change_password': False})
    except DatabaseError as e:
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cursor.close()
        db.close()


# ---------- Phân tích ảnh & Lưu lịch sử ----------
@app.route('/api/translate', methods=['POST'])
def api_translate():
    """Dịch nội dung động giao diện qua LLM (OpenRouter). API key chỉ ở backend."""
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    target_language = (data.get('target_language') or '').strip().lower()

    if not text:
        return jsonify({'error': 'Missing text'}), 400
    if target_language not in ('en', 'vi'):
        return jsonify({'error': 'Invalid target_language (en or vi)'}), 400

    translated, from_cache = translate_text(text, target_language, app.config)
    return jsonify({
        'translated_text': translated,
        'cached': from_cache,
    })


@app.route('/api/analyze', methods=['POST'])
def analyze():
    """Upload 1 hoặc nhiều ảnh, gửi AI, tổng hợp phong cách và dịp phù hợp. Lưu vào history nếu có user_id (trừ khi skip_history=1)."""
    if 'images' not in request.files and 'image' not in request.files:
        return jsonify({'error': 'Không có ảnh được gửi'}), 400

    user_id = request.form.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'Vui lòng đăng nhập để phân tích ảnh.'}), 401

    blocked = _user_account_access_error(user_id)
    if blocked:
        return blocked

    is_admin_analyze = False
    if user_id:
        _db_chk = get_db()
        _c_chk = plain_cursor(_db_chk)
        try:
            _c_chk.execute('SELECT role FROM users WHERE id = %s', (user_id,))
            _r_chk = _c_chk.fetchone()
            if _r_chk and _normalize_role(_r_chk[0]) == 'admin':
                is_admin_analyze = True
        finally:
            _c_chk.close()
            _db_chk.close()

    files = request.files.getlist('images') or request.files.getlist('image')
    if not files:
        files = [request.files.get('image')] if request.files.get('image') else []
    files = [f for f in files if f and f.filename and allowed_file(f.filename)]
    if not files:
        return jsonify({'error': 'Không có ảnh hợp lệ (png, jpg, jpeg, webp)'}), 400

    max_images = getattr(Config, 'MAX_ANALYZE_IMAGES', 5)
    if len(files) > max_images:
        return jsonify({
            'error': f'Tối đa {max_images} ảnh mỗi lần phân tích. Bạn đã gửi {len(files)} ảnh.',
        }), 400

    saved_paths = []
    for f in files:
        ext = f.filename.rsplit('.', 1)[-1].lower()
        name = f'{uuid.uuid4().hex}.{ext}'
        path = os.path.join(Config.UPLOAD_FOLDER, name)
        f.save(path)
        saved_paths.append(path)

    n = len(saved_paths)
    credits_remaining = None
    refund_n = 0
    if user_id and not is_admin_analyze:
        db = get_db()
        cur = plain_cursor(db)
        cur.execute(
            'UPDATE users SET analysis_credits = analysis_credits - %s WHERE id = %s AND analysis_credits >= %s',
            (n, user_id, n),
        )
        if cur.rowcount != 1:
            cur.execute('SELECT analysis_credits FROM users WHERE id = %s', (user_id,))
            row = cur.fetchone()
            rem = int(row[0]) if row else 0
            cur.close()
            db.close()
            for p in saved_paths:
                try:
                    os.remove(p)
                except OSError:
                    pass
            return jsonify({
                'error': (
                    f'Không đủ lượt phân tích. Bạn còn {rem} lượt, cần {n} lượt cho {n} ảnh. '
                    'Liên hệ quản trị viên để được cấp thêm lượt (trang Gói cước chỉ mang tính tham khảo).'
                ),
                'credits_remaining': rem,
                'credits_required': n,
            }), 402
        cur.execute('SELECT analysis_credits FROM users WHERE id = %s', (user_id,))
        credits_remaining = int(cur.fetchone()[0])
        db.commit()
        cur.close()
        db.close()
        refund_n = n
    elif user_id and is_admin_analyze:
        db = get_db()
        cur = plain_cursor(db)
        cur.execute('SELECT analysis_credits FROM users WHERE id = %s', (user_id,))
        row = cur.fetchone()
        credits_remaining = int(row[0]) if row else Config.INITIAL_ANALYSIS_CREDITS
        cur.close()
        db.close()

    try:
        base_url = request.host_url.rstrip('/')
        rel_paths = [os.path.basename(p) for p in saved_paths]
        image_urls = [f'{base_url}/uploads/{p}' for p in rel_paths]

        analysis_errors = []
        has_key = bool((getattr(app.config, 'OPENROUTER_API_KEY', None) or os.environ.get('OPENROUTER_API_KEY') or '').strip())
        if not has_key:
            print('[OpenRouter] Chưa có OPENROUTER_API_KEY trong .env — đang dùng kết quả demo.')

        # Phân tích song song nhiều ảnh
        def analyze_one(path):
            return path, analyze_image(path, app.config)

        results = []
        if len(saved_paths) == 1:
            _, result = analyze_one(saved_paths[0])
            raw_results = [result]
        else:
            raw_results = [None] * len(saved_paths)
            with ThreadPoolExecutor(max_workers=min(len(saved_paths), 5)) as executor:
                futures = {executor.submit(analyze_one, p): i for i, p in enumerate(saved_paths)}
                for future in as_completed(futures):
                    path, result = future.result()
                    raw_results[saved_paths.index(path)] = result

        _ds_labels = load_fashion_dataset()
        for result in raw_results:
            items = result.get('items', [])
            trace = result.get('trace') if isinstance(result, dict) else None
            if result.get('error'):
                analysis_errors.append(result['error'])
                print('[OpenRouter] Lỗi phân tích ảnh:', result['error'])
            overall_style = aggregate_styles(items)
            overall_topk_raw = aggregate_styles_top_k(items, k=3)
            occasions = get_suggested_occasions(overall_style)
            items_display = []
            for it in items:
                raw_item = it.get('item') or it.get('item_type') or ''
                item_en, item_vi = get_item_bilingual(raw_item, _ds_labels)
                vote_debug = it.get('vote_debug') if isinstance(it, dict) else None
                per_model = vote_debug.get('per_model') if isinstance(vote_debug, dict) else None
                item_row = {
                        'item': item_en,
                        'item_en': item_en,
                        'item_vi': item_vi,
                        'category': it.get('category') or '',
                        'detected_styles': it.get('detected_styles') or [],
                        'final_style': it.get('final_style') or it.get('style') or 'casual',
                        'confidence': it.get('confidence'),
                        'reason': it.get('reason') or [],
                        'category_display': to_vietnamese_category(it.get('category')),
                        'style_display': to_vietnamese_style(it.get('final_style') or it.get('style')),
                    }
                if is_admin_analyze:
                    item_row['model_detected_styles'] = per_model if isinstance(per_model, list) else []
                items_display.append(item_row)
            _raw = (overall_style or 'casual').strip()
            _en = _raw.replace('_', ' ').title() if _raw else 'Casual'
            overall_style_top_k = []
            for t in overall_topk_raw:
                tr = (t or 'casual').strip()
                overall_style_top_k.append(tr.replace('_', ' ').title() if tr else 'Casual')
            result_row = {
                'items': items_display,
                'overall_style': _en,
                'overall_style_top_k': overall_style_top_k,
                'overall_style_description': get_overall_style_description(_raw),
                'suggested_occasions': occasions,
                'mix_suggestions': [],  # Chỉ gọi khi user bấm "Gợi ý mix"
            }
            if is_admin_analyze:
                result_row['analysis_trace'] = trace
            results.append(result_row)

        response_data = {
            'results': results,
            'image_count': len(saved_paths),
            'image_urls': image_urls,
            'analysis_error': analysis_errors[0] if analysis_errors else None,
        }
    except Exception as e:
        print('[Analyze] Lỗi:', e)
        if refund_n:
            _refund_analysis_credits(user_id, refund_n)
        for p in saved_paths:
            try:
                os.remove(p)
            except OSError:
                pass
        return jsonify({'error': 'Lỗi khi phân tích ảnh. Vui lòng thử lại.'}), 500

    if user_id:
        response_data['credits_remaining'] = credits_remaining

    # Lưu lịch sử nếu có user_id (đã đăng nhập); eval/benchmark gửi skip_history=1 để bỏ qua
    skip_history = (request.form.get('skip_history') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    if user_id and saved_paths and not skip_history:
        image_path_value = rel_paths[0]  # Tên file ảnh đầu (phục vụ qua /uploads/<filename>)
        try:
            db = get_db()
            cursor = plain_cursor(db)
            cursor.execute(
                'INSERT INTO history (user_id, image_path, analysis_result) VALUES (%s, %s, %s)',
                (user_id, image_path_value, json.dumps(response_data)),
            )
            db.commit()
            cursor.close()
            db.close()
        except DatabaseError as e:
            print('[DB] Lỗi lưu lịch sử:', e)

    return jsonify(response_data)


@app.route('/api/mix-suggestions', methods=['POST'])
def mix_suggestions():
    """Gợi ý mix khi user bấm nút. Body: { "overall_style": "...", "items": [...] }."""
    data = request.get_json() or {}
    overall_style = (data.get('overall_style') or '').strip() or 'Casual'
    items = data.get('items') or []
    if not isinstance(items, list):
        items = []
    suggestions = get_mix_suggestions(overall_style, items, app.config)
    return jsonify({'suggestions': suggestions})


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(Config.UPLOAD_FOLDER, filename)


# ---------- API Lịch sử phân tích ----------
def _sanitize_analysis_item_for_role(it, is_admin: bool):
    if is_admin or not isinstance(it, dict):
        return it
    cleaned = dict(it)
    cleaned.pop('model_detected_styles', None)
    return cleaned


def _sanitize_analysis_slice_for_role(sl, is_admin: bool):
    if is_admin or not isinstance(sl, dict):
        return sl
    cleaned = dict(sl)
    cleaned.pop('analysis_trace', None)
    items = cleaned.get('items')
    if isinstance(items, list):
        cleaned['items'] = [_sanitize_analysis_item_for_role(it, is_admin) for it in items]
    return cleaned


def _sanitize_analysis_result_for_role(ar, is_admin: bool):
    if is_admin or not isinstance(ar, dict):
        return ar
    cleaned = dict(ar)
    cleaned.pop('analysis_trace', None)
    results = cleaned.get('results')
    if isinstance(results, list):
        cleaned['results'] = [_sanitize_analysis_slice_for_role(r, is_admin) for r in results]
        return cleaned
    items = cleaned.get('items')
    if isinstance(items, list):
        cleaned['items'] = [_sanitize_analysis_item_for_role(it, is_admin) for it in items]
    return cleaned


@app.route('/api/history/<int:user_id>', methods=['GET'])
def get_history(user_id):
    db = get_db()
    cursor = dict_cursor(db)
    try:
        cursor.execute('SELECT role FROM users WHERE id = %s', (user_id,))
        role_row = cursor.fetchone()
        is_admin = bool(role_row and _normalize_role(role_row.get('role')) == 'admin')
        cursor.execute(
            'SELECT id, user_id, image_path, analysis_result, timestamp FROM history WHERE user_id = %s ORDER BY timestamp DESC',
            (user_id,),
        )
        rows = cursor.fetchall()
        for row in rows:
            if row.get('analysis_result'):
                try:
                    row['analysis_result'] = json.loads(row['analysis_result']) if isinstance(row['analysis_result'], str) else row['analysis_result']
                except (TypeError, json.JSONDecodeError):
                    row['analysis_result'] = {}
            else:
                row['analysis_result'] = {}
            row['analysis_result'] = _sanitize_analysis_result_for_role(row['analysis_result'], is_admin)
            _apply_history_media_urls(row)
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        db.close()


# ---------- API Thống kê theo user ----------
def _stats_normalize_style(raw: str) -> str:
    s = (raw or '').strip()
    if not s:
        return ''
    if ' / ' in s:
        s = s.split(' / ', 1)[0].strip()
    return s


def _stats_parse_analysis(ar):
    if isinstance(ar, str):
        try:
            ar = json.loads(ar)
        except (TypeError, json.JSONDecodeError):
            ar = {}
    return ar if isinstance(ar, dict) else {}


def _stats_iter_slices(ar):
    results = ar.get('results')
    if isinstance(results, list) and results:
        for r in results:
            if isinstance(r, dict):
                yield r
    else:
        yield ar


def _stats_item_confidence_01(it) -> float:
    if not isinstance(it, dict):
        return -1.0
    raw = it.get('confidence')
    if raw is None:
        raw = it.get('detection_confidence')
    if raw is None:
        return -1.0
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return -1.0
    if n > 1:
        n = n / 100.0
    if n < 0 or n > 1:
        return -1.0
    return n


def _stats_aggregate_rows(rows, period: str) -> dict:
    total_analyses = 0
    total_images = 0
    style_counts = {}
    item_counts = {}
    occasion_counts = {}
    category_counts = {}
    confidence_values = []
    by_day = {}

    for row in rows:
        total_analyses += 1
        ts = row.get('timestamp')
        if isinstance(ts, datetime):
            day_key = ts.strftime('%Y-%m-%d')
        else:
            try:
                day_key = str(ts)[:10]
            except Exception:
                day_key = ''
        if day_key:
            by_day[day_key] = by_day.get(day_key, 0) + 1

        ar = _stats_parse_analysis(row.get('analysis_result'))
        slices = list(_stats_iter_slices(ar))
        if slices:
            total_images += len(slices)
        else:
            total_images += ar.get('image_count', 1) or 1

        for sl in slices:
            style_raw = (sl.get('overall_style') or '').strip()
            style_key = _stats_normalize_style(style_raw) or '—'
            if style_key != '—':
                style_counts[style_key] = style_counts.get(style_key, 0) + 1

            occs = sl.get('suggested_occasions') or ar.get('suggested_occasions') or []
            if isinstance(occs, list):
                for occ in occs:
                    name = (occ or '').strip() if isinstance(occ, str) else str(occ or '').strip()
                    if name:
                        occasion_counts[name] = occasion_counts.get(name, 0) + 1

            for it in (sl.get('items') or ar.get('items') or []):
                if not isinstance(it, dict):
                    continue
                item_name = (it.get('item') or it.get('item_type') or '').strip()
                if item_name:
                    item_counts[item_name] = item_counts.get(item_name, 0) + 1
                cat = (it.get('category') or '').strip().lower()
                if not cat:
                    cat = 'other'
                category_counts[cat] = category_counts.get(cat, 0) + 1
                conf = _stats_item_confidence_01(it)
                if conf >= 0:
                    confidence_values.append(conf)

    days_limit = 7 if period == '7d' else (30 if period == '30d' else 30)
    days_sorted = sorted(by_day.items(), key=lambda x: x[0])
    if days_sorted:
        days_sorted = days_sorted[-days_limit:]
    by_day_series = [{'day': d, 'count': int(c)} for d, c in days_sorted]

    top_styles = sorted(style_counts.items(), key=lambda x: -x[1])[:10]
    top_items = sorted(item_counts.items(), key=lambda x: -x[1])[:15]
    top_occasions = sorted(occasion_counts.items(), key=lambda x: -x[1])[:10]
    top_categories = sorted(category_counts.items(), key=lambda x: -x[1])[:10]

    primary_style = None
    if top_styles and total_analyses > 0:
        ps_name, ps_count = top_styles[0]
        primary_style = {
            'style': ps_name,
            'count': int(ps_count),
            'percent': round(ps_count / total_analyses * 100, 1),
        }

    avg_confidence = None
    if confidence_values:
        avg_confidence = round(sum(confidence_values) / len(confidence_values) * 100, 1)

    return {
        'total_analyses': total_analyses,
        'total_images': total_images,
        'top_styles': [{'style': s, 'count': c} for s, c in top_styles],
        'top_items': [{'item': i, 'count': c} for i, c in top_items],
        'top_occasions': [{'occasion': o, 'count': c} for o, c in top_occasions],
        'top_categories': [{'category': cat, 'count': c} for cat, c in top_categories],
        'avg_confidence': avg_confidence,
        'confidence_samples': len(confidence_values),
        'by_day': by_day_series,
        'primary_style': primary_style,
        'period': period,
    }


def _stats_fetch_rows(cursor, user_id: int, scope: str):
    """scope: 7d | 30d | all | prev_7d | prev_30d | recent_30d | prev_30d_recent"""
    queries = {
        '7d': (
            'SELECT analysis_result, timestamp FROM history WHERE user_id = %s '
            'AND timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)',
            (user_id,),
        ),
        '30d': (
            'SELECT analysis_result, timestamp FROM history WHERE user_id = %s '
            'AND timestamp >= DATE_SUB(NOW(), INTERVAL 30 DAY)',
            (user_id,),
        ),
        'all': (
            'SELECT analysis_result, timestamp FROM history WHERE user_id = %s',
            (user_id,),
        ),
        'prev_7d': (
            'SELECT analysis_result, timestamp FROM history WHERE user_id = %s '
            'AND timestamp >= DATE_SUB(NOW(), INTERVAL 14 DAY) '
            'AND timestamp < DATE_SUB(NOW(), INTERVAL 7 DAY)',
            (user_id,),
        ),
        'prev_30d': (
            'SELECT analysis_result, timestamp FROM history WHERE user_id = %s '
            'AND timestamp >= DATE_SUB(NOW(), INTERVAL 60 DAY) '
            'AND timestamp < DATE_SUB(NOW(), INTERVAL 30 DAY)',
            (user_id,),
        ),
        'recent_30d': (
            'SELECT analysis_result, timestamp FROM history WHERE user_id = %s '
            'AND timestamp >= DATE_SUB(NOW(), INTERVAL 30 DAY)',
            (user_id,),
        ),
        'prev_30d_recent': (
            'SELECT analysis_result, timestamp FROM history WHERE user_id = %s '
            'AND timestamp >= DATE_SUB(NOW(), INTERVAL 60 DAY) '
            'AND timestamp < DATE_SUB(NOW(), INTERVAL 30 DAY)',
            (user_id,),
        ),
    }
    sql, params = queries[scope]
    cursor.execute(sql, params)
    return cursor.fetchall() or []


def _stats_build_period_comparison(current_rows, previous_rows, compare_key: str) -> dict:
    cur = _stats_aggregate_rows(current_rows, compare_key)
    prev = _stats_aggregate_rows(previous_rows, compare_key)
    cur_a = int(cur.get('total_analyses') or 0)
    prev_a = int(prev.get('total_analyses') or 0)
    cur_i = int(cur.get('total_images') or 0)
    prev_i = int(prev.get('total_images') or 0)
    if cur_a == 0 and prev_a == 0:
        return None

    def _delta_pct(delta, base):
        if base <= 0:
            return None
        return round(delta / base * 100, 1)

    delta_a = cur_a - prev_a
    delta_i = cur_i - prev_i
    cur_ps = (cur.get('primary_style') or {}).get('style')
    prev_ps = (prev.get('primary_style') or {}).get('style')

    return {
        'compare_window': compare_key,
        'current_analyses': cur_a,
        'previous_analyses': prev_a,
        'delta_analyses': delta_a,
        'delta_analyses_pct': _delta_pct(delta_a, prev_a),
        'current_images': cur_i,
        'previous_images': prev_i,
        'delta_images': delta_i,
        'delta_images_pct': _delta_pct(delta_i, prev_i),
        'current_primary_style': cur_ps,
        'previous_primary_style': prev_ps,
        'primary_style_changed': bool(cur_ps and prev_ps and cur_ps != prev_ps),
    }


@app.route('/api/stats/<int:user_id>', methods=['GET'])
def get_stats(user_id):
    """Thống kê user: KPI, biểu đồ, màu sắc, độ tin cậy, so sánh kỳ."""
    period = request.args.get('period', 'all')  # all | 7d | 30d
    db = get_db()
    cursor = dict_cursor(db)
    try:
        scope = period if period in ('7d', '30d') else 'all'
        rows = _stats_fetch_rows(cursor, user_id, scope)
        if period == '7d':
            prev_rows = _stats_fetch_rows(cursor, user_id, 'prev_7d')
            cmp_cur, cmp_prev, cmp_key = rows, prev_rows, '7d'
        elif period == '30d':
            prev_rows = _stats_fetch_rows(cursor, user_id, 'prev_30d')
            cmp_cur, cmp_prev, cmp_key = rows, prev_rows, '30d'
        else:
            cmp_cur = _stats_fetch_rows(cursor, user_id, 'recent_30d')
            cmp_prev = _stats_fetch_rows(cursor, user_id, 'prev_30d_recent')
            cmp_key = '30d'
    finally:
        cursor.close()
        db.close()

    payload = _stats_aggregate_rows(rows or [], period)
    payload['period_comparison'] = _stats_build_period_comparison(cmp_cur, cmp_prev, cmp_key)
    return jsonify(payload)


# ---------- API Xóa lịch sử ----------
@app.route('/api/history/<int:history_id>', methods=['DELETE'])
def delete_history(history_id):
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'Thiếu user_id'}), 400
    db = get_db()
    cursor = plain_cursor(db)
    try:
        cursor.execute('SELECT id FROM history WHERE id = %s AND user_id = %s', (history_id, user_id))
        row = cursor.fetchone()
        if not row:
            return jsonify({'error': 'Không tìm thấy bản ghi hoặc không có quyền xóa'}), 404
        cursor.execute('DELETE FROM history WHERE id = %s AND user_id = %s', (history_id, user_id))
        db.commit()
        return jsonify({'message': 'Đã xóa'})
    except DatabaseError as e:
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cursor.close()
        db.close()


# ---------- API Quản trị (admin) ----------
@app.route('/api/admin/dashboard', methods=['GET'])
def admin_dashboard():
    aid, err = require_admin()
    if err:
        return err[0], err[1]
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute('SELECT COUNT(*) AS c FROM users')
        total_users = int(cur.fetchone()['c'])
        cur.execute('SELECT COUNT(*) AS c FROM history')
        total_history = int(cur.fetchone()['c'])
        cur.execute('SELECT COALESCE(SUM(analysis_credits), 0) AS s FROM users')
        credits_sum = int(cur.fetchone()['s'])
        cur.execute(
            "SELECT COUNT(*) AS c FROM users WHERE account_status = 'pending_delete'",
        )
        pending_delete_users = int(cur.fetchone()['c'])
        return jsonify({
            'total_users': total_users,
            'total_history': total_history,
            'total_credits_in_system': credits_sum,
            'pending_delete_users': pending_delete_users,
        })
    except DatabaseError as e:
        print('[Admin dashboard]', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


ADMIN_USER_SELECT = (
    'id, username, full_name, email, google_id, role, analysis_credits, created_at, '
    'account_status, delete_requested_at, delete_scheduled_at, delete_reason'
)


@app.route('/api/admin/users', methods=['GET'])
def admin_users_list():
    aid, err = require_admin()
    if err:
        return err[0], err[1]
    page = max(1, request.args.get('page', 1, type=int) or 1)
    per_page = min(max(1, request.args.get('per_page', 20, type=int) or 20), 100)
    q = (request.args.get('q') or '').strip()
    status_filter = (request.args.get('account_status') or '').strip().lower()
    if status_filter and status_filter not in ('active', 'pending_delete', 'deleted'):
        status_filter = ''
    offset = (page - 1) * per_page
    db = get_db()
    cur = dict_cursor(db)
    try:
        where_parts = []
        params = []
        if q:
            like = f'%{q}%'
            where_parts.append('(username LIKE %s OR full_name LIKE %s OR email LIKE %s)')
            params.extend([like, like, like])
        if status_filter:
            where_parts.append('account_status = %s')
            params.append(status_filter)
        where_sql = (' WHERE ' + ' AND '.join(where_parts)) if where_parts else ''

        cur.execute(f'SELECT COUNT(*) AS c FROM users{where_sql}', tuple(params))
        total = int(cur.fetchone()['c'])
        cur.execute(
            f'SELECT {ADMIN_USER_SELECT} FROM users{where_sql} '
            'ORDER BY CASE WHEN account_status = \'pending_delete\' THEN 0 ELSE 1 END, id DESC '
            'LIMIT %s OFFSET %s',
            tuple(params + [per_page, offset]),
        )
        rows = cur.fetchall()
        for r in rows:
            r['role'] = _normalize_role(r.get('role'))
            r['is_google_only'] = _is_google_only_user(r)
            r['account_status'] = normalize_account_status(r.get('account_status'))
        return jsonify({'users': rows, 'total': total, 'page': page, 'per_page': per_page})
    except DatabaseError as e:
        print('[Admin users]', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


@app.route('/api/admin/users', methods=['POST'])
def admin_user_create():
    aid, err = require_admin()
    if err:
        return err[0], err[1]

    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    full_name = (data.get('full_name') or '').strip() or None
    role = _normalize_role(data.get('role'))

    analysis_credits = data.get('analysis_credits')
    if analysis_credits is None or analysis_credits == '':
        credits = int(Config.INITIAL_ANALYSIS_CREDITS)
    else:
        try:
            credits = int(analysis_credits)
        except (TypeError, ValueError):
            return jsonify({'error': 'analysis_credits không hợp lệ'}), 400
        if credits < 0:
            return jsonify({'error': 'analysis_credits không được âm'}), 400

    if not username:
        return jsonify({'error': 'Vui lòng nhập username'}), 400
    if not password or len(str(password)) < 6:
        return jsonify({'error': 'Mật khẩu cần ít nhất 6 ký tự'}), 400

    hashed_pw = generate_password_hash(password)

    db = get_db()
    cur = dict_cursor(db)
    try:
        # Tạo user "mật khẩu" (không tạo google_id tại đây)
        cur.execute(
            'INSERT INTO users (username, password, full_name, role, analysis_credits) VALUES (%s, %s, %s, %s, %s)',
            (username, hashed_pw, full_name, role, credits),
        )
        db.commit()
        new_id = int(cur.lastrowid)

        cur.execute(
            'SELECT id, username, full_name, email, google_id, role, analysis_credits, created_at FROM users WHERE id = %s',
            (new_id,),
        )
        out = cur.fetchone()
        if out:
            out['role'] = _normalize_role(out.get('role'))
            out['is_google_only'] = _is_google_only_user(out)
        return jsonify({'user': out}), 201
    except IntegrityError as e:
        # Thường là trùng username unique
        print('[Admin create user] IntegrityError:', e)
        return jsonify({'error': 'Username đã được sử dụng.'}), 400
    except DatabaseError as e:
        db.rollback()
        print('[Admin create user] DB error:', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


@app.route('/api/admin/users/<int:target_id>', methods=['PATCH'])
def admin_user_patch(target_id):
    aid, err = require_admin()
    if err:
        return err[0], err[1]
    data = request.get_json() or {}
    full_name = data.get('full_name')
    analysis_credits = data.get('analysis_credits')
    role = data.get('role')
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute(
            'SELECT id, role FROM users WHERE id = %s',
            (target_id,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Không tìm thấy người dùng'}), 404
        old_role = _normalize_role(row.get('role'))
        new_role = old_role
        if role is not None:
            new_role = _normalize_role(role)
            if new_role == 'user' and old_role == 'admin':
                n_adm = _count_admins(cur)
                if n_adm <= 1:
                    return jsonify({'error': 'Không thể bỏ quyền admin của tài khoản admin duy nhất.'}), 400
        updates = []
        params = []
        if full_name is not None:
            updates.append('full_name = %s')
            params.append((full_name or '').strip() or None)
        if analysis_credits is not None:
            try:
                ac = int(analysis_credits)
                if ac < 0:
                    return jsonify({'error': 'analysis_credits không được âm'}), 400
            except (TypeError, ValueError):
                return jsonify({'error': 'analysis_credits không hợp lệ'}), 400
            updates.append('analysis_credits = %s')
            params.append(ac)
        if role is not None:
            updates.append('role = %s')
            params.append(new_role)
        if not updates:
            return jsonify({'error': 'Không có trường cập nhật'}), 400
        params.append(target_id)
        cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", tuple(params))
        db.commit()
        cur.execute(
            'SELECT id, username, full_name, email, role, analysis_credits, created_at FROM users WHERE id = %s',
            (target_id,),
        )
        out = cur.fetchone()
        if out:
            out['role'] = _normalize_role(out.get('role'))
        return jsonify({'user': out})
    except DatabaseError as e:
        db.rollback()
        print('[Admin patch user]', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


@app.route('/api/admin/users/<int:target_id>', methods=['DELETE'])
def admin_user_delete(target_id):
    aid, err = require_admin()
    if err:
        return err[0], err[1]
    if target_id == aid:
        return jsonify({'error': 'Không thể xóa chính tài khoản đang đăng nhập.'}), 400
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute('SELECT role FROM users WHERE id = %s', (target_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({'error': 'Không tìm thấy người dùng'}), 404
        if _normalize_role(row.get('role')) == 'admin':
            if _count_admins(cur) <= 1:
                return jsonify({'error': 'Không thể xóa admin duy nhất trong hệ thống.'}), 400
        cur.execute('DELETE FROM users WHERE id = %s', (target_id,))
        db.commit()
        return jsonify({'message': 'Đã xóa người dùng'})
    except DatabaseError as e:
        db.rollback()
        print('[Admin delete user]', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


@app.route('/api/admin/users/<int:target_id>/restore-account', methods=['POST'])
def admin_user_restore_account(target_id):
    aid, err = require_admin()
    if err:
        return err[0], err[1]
    body, status = admin_restore_account(target_id)
    return jsonify(body), status


@app.route('/api/admin/users/<int:target_id>/approve-delete', methods=['POST'])
def admin_user_approve_delete(target_id):
    aid, err = require_admin()
    if err:
        return err[0], err[1]
    if target_id == aid:
        return jsonify({'error': 'Không thể duyệt xóa chính tài khoản admin đang đăng nhập.'}), 400
    body, status = admin_approve_delete(target_id)
    return jsonify(body), status


@app.route('/api/admin/history', methods=['GET'])
def admin_history_list():
    aid, err = require_admin()
    if err:
        return err[0], err[1]
    page = max(1, request.args.get('page', 1, type=int) or 1)
    per_page = min(max(1, request.args.get('per_page', 25, type=int) or 25), 100)
    filter_uid = request.args.get('user_id', type=int)
    offset = (page - 1) * per_page
    db = get_db()
    cur = dict_cursor(db)
    try:
        if filter_uid:
            cur.execute('SELECT COUNT(*) AS c FROM history WHERE user_id = %s', (filter_uid,))
            total = int(cur.fetchone()['c'])
            cur.execute(
                'SELECT h.id, h.user_id, h.image_path, h.analysis_result, h.timestamp, u.username '
                'FROM history h LEFT JOIN users u ON u.id = h.user_id WHERE h.user_id = %s '
                'ORDER BY h.timestamp DESC LIMIT %s OFFSET %s',
                (filter_uid, per_page, offset),
            )
        else:
            cur.execute('SELECT COUNT(*) AS c FROM history')
            total = int(cur.fetchone()['c'])
            cur.execute(
                'SELECT h.id, h.user_id, h.image_path, h.analysis_result, h.timestamp, u.username '
                'FROM history h LEFT JOIN users u ON u.id = h.user_id '
                'ORDER BY h.timestamp DESC LIMIT %s OFFSET %s',
                (per_page, offset),
            )
        rows = cur.fetchall()
        for row in rows:
            ar = row.get('analysis_result')
            if isinstance(ar, str):
                try:
                    row['analysis_result'] = json.loads(ar)
                except (TypeError, json.JSONDecodeError):
                    row['analysis_result'] = {}
            elif ar is None:
                row['analysis_result'] = {}
            _apply_history_media_urls(row)
        return jsonify({'items': rows, 'total': total, 'page': page, 'per_page': per_page})
    except DatabaseError as e:
        print('[Admin history]', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


@app.route('/api/admin/history/stats', methods=['GET'])
def admin_history_stats():
    """Thống kê lịch sử toàn hệ thống (admin): theo ngày + top phong cách."""
    aid, err = require_admin()
    if err:
        return err[0], err[1]

    period = (request.args.get('period') or '30d').strip().lower()  # 7d | 30d | all
    filter_uid = request.args.get('user_id', type=int)
    days_limit = request.args.get('days', type=int)
    if days_limit is None:
        days_limit = 30 if period != 'all' else 90
    days_limit = min(max(7, int(days_limit)), 365)

    where = []
    params = []
    if filter_uid:
        where.append('user_id = %s')
        params.append(filter_uid)

    if period == '7d':
        where.append('timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)')
    elif period == '30d':
        where.append('timestamp >= DATE_SUB(NOW(), INTERVAL 30 DAY)')
    else:
        period = 'all'

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    def _normalize_style(raw: str) -> str:
        s = (raw or '').strip()
        if not s:
            return ''
        # bỏ hậu tố " / ..." (lịch sử cũ)
        if ' / ' in s:
            s = s.split(' / ', 1)[0].strip()
        return s

    by_day = {}
    by_style = {}
    total = 0

    db = get_db()
    cur = dict_cursor(db)
    try:
        # Lấy đủ cho thống kê; với all có thể lớn nên giới hạn số dòng đọc
        limit = 20000 if period == 'all' else 5000
        cur.execute(
            f"SELECT analysis_result, timestamp FROM history {where_sql} ORDER BY timestamp DESC LIMIT %s",
            tuple(params + [limit]),
        )
        rows = cur.fetchall() or []
        for r in rows:
            total += 1
            ts = r.get('timestamp')
            if isinstance(ts, datetime):
                day_key = ts.strftime('%Y-%m-%d')
            else:
                try:
                    day_key = str(ts)[:10]
                except Exception:
                    day_key = ''
            if day_key:
                by_day[day_key] = by_day.get(day_key, 0) + 1

            ar = r.get('analysis_result')
            if isinstance(ar, str):
                try:
                    ar = json.loads(ar)
                except (TypeError, json.JSONDecodeError):
                    ar = {}
            if not isinstance(ar, dict):
                ar = {}
            style_raw = ''
            res = ar.get('results')
            if isinstance(res, list) and res:
                r0 = res[0] if isinstance(res[0], dict) else {}
                style_raw = r0.get('overall_style') or ''
            else:
                style_raw = ar.get('overall_style') or ''
            style = _normalize_style(style_raw)
            if style:
                by_style[style] = by_style.get(style, 0) + 1
    except DatabaseError as e:
        print('[Admin history stats]', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()

    # Chuẩn hóa trục ngày: lấy N ngày gần nhất có dữ liệu (hoặc N ngày gần nhất theo thời gian nếu có period)
    days_sorted = sorted(by_day.items(), key=lambda x: x[0])
    if days_sorted:
        days_sorted = days_sorted[-days_limit:]
    day_series = [{'day': d, 'count': int(c)} for d, c in days_sorted]

    top_styles = sorted(by_style.items(), key=lambda x: -x[1])[:12]
    style_series = [{'style': s, 'count': int(c)} for s, c in top_styles]

    return jsonify({
        'period': period,
        'days_limit': days_limit,
        'total_rows_scanned': total,
        'by_day': day_series,
        'top_styles': style_series,
        'filtered_user_id': filter_uid,
    })


@app.route('/api/admin/history/<int:history_id>', methods=['DELETE'])
def admin_history_delete(history_id):
    aid, err = require_admin()
    if err:
        return err[0], err[1]
    db = get_db()
    cur = plain_cursor(db)
    try:
        cur.execute('DELETE FROM history WHERE id = %s', (history_id,))
        db.commit()
        if cur.rowcount != 1:
            return jsonify({'error': 'Không tìm thấy bản ghi'}), 404
        return jsonify({'message': 'Đã xóa lịch sử'})
    except DatabaseError as e:
        db.rollback()
        print('[Admin delete history]', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


@app.route('/api/user/payments', methods=['GET'])
def user_payments_list():
    """Lịch sử mua/nạp gói (payments) của user."""
    _ensure_payments_table()
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'Thiếu user_id'}), 400
    limit = min(max(1, request.args.get('limit', 50, type=int) or 50), 200)

    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute(
            'SELECT id, user_id, package_key, credits, amount_vnd, status, sepay_tx_id, reconcile_token, '
            'created_at, updated_at, expires_at, completed_at '
            'FROM payments WHERE user_id = %s ORDER BY id DESC LIMIT %s',
            (user_id, limit),
        )
        rows = cur.fetchall() or []
        out = []
        for r in rows:
            rid = int(r['id'])
            hex_id = _payment_public_token(r)
            transfer_content = _payment_transfer_content(r)
            out.append({
                'id': rid,
                'hex_id': hex_id,
                'package_id': r.get('package_key'),
                'credits': int(r.get('credits') or 0),
                'amount_vnd': int(r.get('amount_vnd') or 0),
                'status': r.get('status'),
                'sepay_tx_id': r.get('sepay_tx_id'),
                'transfer_content': transfer_content,
                'created_at': r.get('created_at').isoformat() if r.get('created_at') else None,
                'expires_at': r.get('expires_at').isoformat() if r.get('expires_at') else None,
                'completed_at': r.get('completed_at').isoformat() if r.get('completed_at') else None,
            })
        return jsonify({'items': out, 'total': len(out)})
    except DatabaseError as e:
        print('[User payments]', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


@app.route('/api/admin/payments', methods=['GET'])
def admin_payments_list():
    aid, err = require_admin()
    if err:
        return err[0], err[1]
    _ensure_payments_table()

    page = max(1, request.args.get('page', 1, type=int) or 1)
    per_page = min(max(1, request.args.get('per_page', 25, type=int) or 25), 100)
    filter_uid = request.args.get('user_id', type=int)
    status = (request.args.get('status') or '').strip().lower()
    q = (request.args.get('q') or '').strip()
    offset = (page - 1) * per_page

    db = get_db()
    cur = dict_cursor(db)
    try:
        where = []
        params = []

        if filter_uid:
            where.append('p.user_id = %s')
            params.append(filter_uid)

        if status in ('pending', 'completed', 'failed'):
            where.append('LOWER(p.status) = %s')
            params.append(status)

        if q:
            like = f'%{q}%'
            where.append('(u.username LIKE %s OR u.full_name LIKE %s OR u.email LIKE %s OR CAST(p.id AS CHAR) LIKE %s OR p.sepay_tx_id LIKE %s)')
            params.extend([like, like, like, like, like])

        where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

        cur.execute(f'SELECT COUNT(*) AS c FROM payments p LEFT JOIN users u ON u.id = p.user_id {where_sql}', tuple(params))
        total = int(cur.fetchone()['c'])

        sql = (
            'SELECT p.id, p.user_id, p.package_key, p.credits, p.amount_vnd, p.status, p.sepay_tx_id, '
            'p.reconcile_token, p.created_at, p.updated_at, p.expires_at, p.completed_at, '
            'u.username, u.full_name, u.email '
            f'FROM payments p LEFT JOIN users u ON u.id = p.user_id {where_sql} '
            'ORDER BY p.id DESC LIMIT %s OFFSET %s'
        )
        cur.execute(sql, tuple(params + [per_page, offset]))
        rows = cur.fetchall() or []

        items = []
        for r in rows:
            rid = int(r['id'])
            hex_id = _payment_public_token(r)
            transfer_content = _payment_transfer_content(r)
            items.append({
                'id': rid,
                'hex_id': hex_id,
                'user_id': int(r.get('user_id') or 0),
                'username': r.get('username'),
                'full_name': r.get('full_name'),
                'email': r.get('email'),
                'package_id': r.get('package_key'),
                'credits': int(r.get('credits') or 0),
                'amount_vnd': int(r.get('amount_vnd') or 0),
                'status': r.get('status'),
                'sepay_tx_id': r.get('sepay_tx_id'),
                'transfer_content': transfer_content,
                'created_at': r.get('created_at').isoformat() if r.get('created_at') else None,
                'expires_at': r.get('expires_at').isoformat() if r.get('expires_at') else None,
                'completed_at': r.get('completed_at').isoformat() if r.get('completed_at') else None,
            })

        return jsonify({'items': items, 'total': total, 'page': page, 'per_page': per_page})
    except DatabaseError as e:
        print('[Admin payments]', e)
        return jsonify({'error': 'Lỗi CSDL'}), 500
    finally:
        cur.close()
        db.close()


# ---------- Admin: cấu hình trang chính sách (sửa trực tiếp file HTML) ----------
POLICY_PAGES = {
    'privacy': {
        'filename': 'privacy.html',
        'url': '/privacy',
        'label_vi': 'Chính sách bảo mật',
        'label_en': 'Privacy & Security',
    },
    'terms': {
        'filename': 'terms.html',
        'url': '/terms',
        'label_vi': 'Điều khoản sử dụng',
        'label_en': 'Terms of Service',
    },
    'data_deletion': {
        'filename': 'data_deletion.html',
        'url': '/data-deletion',
        'label_vi': 'Chính sách xóa dữ liệu',
        'label_en': 'Data Deletion Policy',
    },
    'payment_policy': {
        'filename': 'payment_policy.html',
        'url': '/payment-policy',
        'label_vi': 'Chính sách thanh toán',
        'label_en': 'Payment Policy',
    },
    'ai_terms': {
        'filename': 'ai_terms.html',
        'url': '/ai-terms',
        'label_vi': 'Điều khoản AI',
        'label_en': 'AI Terms of Use',
    },
    'support': {
        'filename': 'support.html',
        'url': '/support',
        'label_vi': 'Trung tâm hỗ trợ',
        'label_en': 'Support center',
    },
    'payment_guide': {
        'filename': 'payment_guide.html',
        'url': '/payment-guide',
        'label_vi': 'Hướng dẫn thanh toán',
        'label_en': 'Payment guide',
    },
    'user_guide': {
        'filename': 'user_guide.html',
        'url': '/user-guide',
        'label_vi': 'Hướng dẫn sử dụng',
        'label_en': 'User guide',
    },
    'install_guide': {
        'filename': 'install_guide.html',
        'url': '/install-guide',
        'label_vi': 'Hướng dẫn cài đặt',
        'label_en': 'Install guide',
    },
}

POLICY_TITLE_I18N = {
    'privacy': 'meta.titlePrivacy',
    'terms': 'meta.titleTerms',
    'data_deletion': 'meta.titleDataDeletion',
    'payment_policy': 'meta.titlePaymentPolicy',
    'ai_terms': 'meta.titleAiTerms',
    'support': 'meta.titleSupport',
    'payment_guide': 'meta.titlePaymentGuide',
    'user_guide': 'meta.titleUserGuide',
    'install_guide': 'meta.titleInstallGuide',
}

SITE_CONTACT_FIELDS = {
    'app_display_name': ('APP_DISPLAY_NAME', 'APP_DISPLAY_NAME'),
    'company_name_vi': ('COMPANY_NAME_VI', 'COMPANY_NAME_VI'),
    'company_name_en': ('COMPANY_NAME_EN', 'COMPANY_NAME_EN'),
    'company_tax_id': ('COMPANY_TAX_ID', 'COMPANY_TAX_ID'),
    'company_representative': ('COMPANY_REPRESENTATIVE', 'COMPANY_REPRESENTATIVE'),
    'company_address': ('COMPANY_ADDRESS', 'COMPANY_ADDRESS'),
    'support_email': ('SUPPORT_EMAIL', 'SUPPORT_EMAIL'),
    'support_phone': ('SUPPORT_PHONE', 'SUPPORT_PHONE'),
}

_POLICY_BLOCK_RE = re.compile(
    r'(\{% block legal_content %\})(.*?)(\{% endblock %\})',
    re.DOTALL,
)

_POLICY_VI_BLOCK_RE = re.compile(
    r'<div\s+class="legal-lang\s+legal-lang--vi"\s*>(.*?)</div>\s*<div\s+class="legal-lang\s+legal-lang--en(?:\s+hidden)?"\s*>',
    re.DOTALL | re.IGNORECASE,
)

_POLICY_EN_BLOCK_RE = re.compile(
    r'<div\s+class="legal-lang\s+legal-lang--en(?:\s+hidden)?"\s*>(.*)</div>\s*$',
    re.DOTALL | re.IGNORECASE,
)

_POLICY_JINJA_RE = re.compile(r'\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}')
_POLICY_JINJA_PLACEHOLDER_RE = re.compile(r'\[\[JINJA:(\d+)\]\]')


def _split_policy_lang_blocks(block_html):
    block = (block_html or '').strip()
    if not block:
        return '', ''
    vi_m = _POLICY_VI_BLOCK_RE.search(block)
    en_m = _POLICY_EN_BLOCK_RE.search(block)
    if vi_m:
        return vi_m.group(1).strip(), (en_m.group(1).strip() if en_m else '')
    return block, ''


def _indent_policy_inner(html):
    lines = (html or '').strip().splitlines()
    if not lines:
        return ''
    return '\n'.join(('    ' + line) if line.strip() else line for line in lines)


def _merge_policy_lang_blocks(vi_inner, en_inner):
    vi_body = _indent_policy_inner(vi_inner)
    en_body = _indent_policy_inner(en_inner)
    return (
        '  <div class="legal-lang legal-lang--vi">\n'
        + vi_body
        + '\n  </div>\n\n'
        + '  <div class="legal-lang legal-lang--en hidden">\n'
        + en_body
        + '\n  </div>\n'
    )


def _protect_policy_jinja(html):
    tokens = []

    def repl(match):
        tokens.append(match.group(0))
        return '[[JINJA:' + str(len(tokens) - 1) + ']]'

    protected = _POLICY_JINJA_RE.sub(repl, html or '')
    return protected, tokens


def _restore_policy_jinja(html, tokens):
    def repl(match):
        idx = int(match.group(1))
        return tokens[idx] if 0 <= idx < len(tokens) else match.group(0)

    return _POLICY_JINJA_PLACEHOLDER_RE.sub(repl, html or '')


def _split_policy_chunks(html):
    text = (html or '').strip()
    if not text:
        return []
    parts = re.split(r'(?=<section\b)', text, flags=re.IGNORECASE)
    chunks = []
    if parts[0].strip():
        chunks.append(parts[0].strip())
    for part in parts[1:]:
        if part.strip():
            chunks.append(part.strip())
    return chunks or [text]


def _normalize_policy_en_html(html):
    out = html or ''
    out = out.replace('id="legal-page-title"', 'id="legal-page-title-en"')
    out = out.replace("id='legal-page-title'", "id='legal-page-title-en'")
    out = out.replace('{{ site.company_name_vi }}', '{{ site.company_name_en }}')
    return out


def _auto_translate_policy_en(vi_html, config):
    protected, tokens = _protect_policy_jinja(vi_html)
    chunks = _split_policy_chunks(protected)
    en_chunks = []
    for chunk in chunks:
        translated, _ = translate_policy_html(chunk, 'en', config)
        en_chunks.append(translated)
    en_html = '\n\n    '.join(en_chunks)
    en_html = _restore_policy_jinja(en_html, tokens)
    return _normalize_policy_en_html(en_html)


def _policy_template_path(slug):
    meta = POLICY_PAGES.get(slug)
    if not meta:
        return None
    base = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'pages'))
    path = os.path.abspath(os.path.join(base, meta['filename']))
    if not path.startswith(base + os.sep):
        return None
    return path


def _read_policy_block_from_file(slug):
    path = _policy_template_path(slug)
    if not path or not os.path.isfile(path):
        return None, None
    with open(path, 'r', encoding='utf-8') as f:
        raw = f.read()
    m = _POLICY_BLOCK_RE.search(raw)
    if not m:
        return None, None
    return raw, m.group(2)


def _read_policy_block(slug):
    stored = get_app_setting(f'policy:{slug}')
    if isinstance(stored, dict):
        content = stored.get('content')
        if isinstance(content, str) and content.strip():
            return None, content
    raw, content = _read_policy_block_from_file(slug)
    if content:
        try:
            set_app_setting(f'policy:{slug}', {'content': content})
        except Exception as e:
            print(f'[Policy DB seed {slug}]', e)
    return raw, content


def _write_policy_block_to_file(slug, new_block_content):
    path = _policy_template_path(slug)
    if not path or not os.path.isfile(path):
        return False, 'Không tìm thấy file trang chính sách.'
    with open(path, 'r', encoding='utf-8') as f:
        raw = f.read()
    m = _POLICY_BLOCK_RE.search(raw)
    if not m:
        return False, 'Không tìm thấy khối legal_content trong file HTML.'
    updated = raw[:m.start(2)] + new_block_content + raw[m.end(2):]
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(updated)
    return True, None


def _write_policy_block(slug, new_block_content):
    if not isinstance(new_block_content, str):
        return False, 'Nội dung không hợp lệ.'
    try:
        set_app_setting(f'policy:{slug}', {'content': new_block_content})
    except Exception as e:
        print(f'[Policy save DB {slug}]', e)
        return False, 'Không lưu được cấu hình chính sách.'
    if _can_write_project_files():
        try:
            _write_policy_block_to_file(slug, new_block_content)
        except OSError as e:
            print(f'[Policy file mirror {slug}]', e)
    return True, None


def _render_policy_page(slug: str):
    if slug not in POLICY_PAGES:
        return jsonify({'error': 'Not Found'}), 404
    _, content = _read_policy_block(slug)
    if not content:
        meta = POLICY_PAGES[slug]
        return render_template(f'pages/{meta["filename"]}')
    title_key = POLICY_TITLE_I18N.get(slug, 'meta.title')
    tpl = (
        f"{{% set title_i18n_key = '{title_key}' %}}\n"
        f"{{% extends 'layouts/legal_page.html' %}}\n"
        f"{{% block legal_content %}}{content}{{% endblock %}}"
    )
    return render_template_string(tpl)


def _env_file_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')


def _env_quote_value(val):
    s = str(val or '').strip()
    if not s:
        return ''
    if any(c in s for c in (' ', '#', '=', '"', "'")):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def _write_env_key(env_key, value):
    path = _env_file_path()
    lines = []
    if os.path.isfile(path):
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    quoted = _env_quote_value(value)
    found = False
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or '=' not in line:
            out.append(line)
            continue
        key = line.split('=', 1)[0].strip()
        if key == env_key:
            out.append(f'{env_key}={quoted}\n')
            found = True
        else:
            out.append(line)
    if not found:
        if out and not out[-1].endswith('\n'):
            out[-1] = out[-1] + '\n'
        out.append(f'{env_key}={quoted}\n')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(out)


def _read_contact_settings():
    return {
        'app_display_name': Config.APP_DISPLAY_NAME,
        'company_name_vi': Config.COMPANY_NAME_VI,
        'company_name_en': Config.COMPANY_NAME_EN,
        'company_tax_id': Config.COMPANY_TAX_ID,
        'company_representative': Config.COMPANY_REPRESENTATIVE,
        'company_address': Config.COMPANY_ADDRESS,
        'support_email': Config.SUPPORT_EMAIL,
        'support_phone': Config.SUPPORT_PHONE,
    }


def _apply_contact_runtime(values):
    for api_key, (env_key, config_attr) in SITE_CONTACT_FIELDS.items():
        if api_key not in values:
            continue
        val = str(values[api_key] or '').strip()
        os.environ[env_key] = val
        setattr(Config, config_attr, val)


SITE_CONFIG_STR_FIELDS = {
    'app_display_name': ('APP_DISPLAY_NAME', 'APP_DISPLAY_NAME'),
}

API_CONFIG_STR_FIELDS = {
    'openrouter_model': ('OPENROUTER_MODEL', 'OPENROUTER_MODEL'),
    'openrouter_translate_model': ('OPENROUTER_TRANSLATE_MODEL', 'OPENROUTER_TRANSLATE_MODEL'),
    'openrouter_vision_models': ('OPENROUTER_VISION_MODELS', 'OPENROUTER_VISION_MODELS'),
    'openrouter_vision_model_weights': ('OPENROUTER_VISION_MODEL_WEIGHTS', 'OPENROUTER_VISION_MODEL_WEIGHTS'),
}

API_CONFIG_INT_FIELDS = {
    'openrouter_timeout': ('OPENROUTER_TIMEOUT', 'OPENROUTER_TIMEOUT'),
    'openrouter_max_tokens': ('OPENROUTER_MAX_TOKENS', 'OPENROUTER_MAX_TOKENS'),
}


def _mask_api_key(key):
    k = (key or '').strip()
    if not k:
        return '', False
    if len(k) <= 10:
        return '••••••••', True
    return k[:8] + '…' + k[-4:], True


def _read_api_config():
    key = (Config.OPENROUTER_API_KEY or '').strip()
    masked, has_key = _mask_api_key(key)
    return {
        'has_api_key': has_key,
        'api_key_masked': masked,
        'openrouter_model': Config.OPENROUTER_MODEL or 'openai/gpt-4o-mini',
        'openrouter_translate_model': Config.OPENROUTER_TRANSLATE_MODEL or '',
        'openrouter_vision_models': Config.OPENROUTER_VISION_MODELS or '',
        'openrouter_vision_model_weights': Config.OPENROUTER_VISION_MODEL_WEIGHTS or '',
        'openrouter_timeout': int(Config.OPENROUTER_TIMEOUT or 90),
        'openrouter_max_tokens': int(Config.OPENROUTER_MAX_TOKENS or 512),
    }


def _apply_api_config_runtime(values):
    if 'openrouter_api_key' in values:
        val = str(values['openrouter_api_key'] or '').strip()
        os.environ['OPENROUTER_API_KEY'] = val
        setattr(Config, 'OPENROUTER_API_KEY', val)
        app.config['OPENROUTER_API_KEY'] = val
    for api_key, (env_key, config_attr) in API_CONFIG_STR_FIELDS.items():
        if api_key not in values:
            continue
        val = str(values[api_key] or '').strip()
        os.environ[env_key] = val
        setattr(Config, config_attr, val)
        app.config[config_attr] = val
    for api_key, (env_key, config_attr) in API_CONFIG_INT_FIELDS.items():
        if api_key not in values:
            continue
        val = int(values[api_key])
        os.environ[env_key] = str(val)
        setattr(Config, config_attr, val)
        app.config[config_attr] = val


def _read_site_config():
    return {
        'app_display_name': Config.APP_DISPLAY_NAME,
        'logo_url': _site_logo_url(),
        'has_logo': _site_has_logo(),
        'packages': _load_packages_raw(),
    }


def _apply_site_config_runtime(values):
    for api_key, (env_key, config_attr) in SITE_CONFIG_STR_FIELDS.items():
        if api_key not in values:
            continue
        val = str(values[api_key] or '').strip()
        os.environ[env_key] = val
        setattr(Config, config_attr, val)


def _hydrate_runtime_settings_from_db():
    """Nạp cấu hình admin từ DB vào Config khi khởi động (Vercel / production)."""
    try:
        contact = get_app_setting('contact')
        if isinstance(contact, dict) and contact:
            _apply_contact_runtime(contact)
        api_cfg = get_app_setting('api_config')
        if isinstance(api_cfg, dict) and api_cfg:
            _apply_api_config_runtime(api_cfg)
        site_meta = get_app_setting('site_meta')
        if isinstance(site_meta, dict) and site_meta:
            _apply_site_config_runtime(site_meta)
    except Exception as e:
        print('[Settings] hydrate:', e)


_hydrate_runtime_settings_from_db()


@app.route('/api/site/logo')
def api_site_logo():
    data = _site_logo_from_db()
    if not data:
        return '', 404
    try:
        raw = base64.b64decode(data.get('data_b64') or '')
    except (ValueError, TypeError):
        return '', 404
    ext = (data.get('ext') or 'png').lower()
    mime = {
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'webp': 'image/webp',
    }.get(ext, 'application/octet-stream')
    return Response(raw, mimetype=mime)


@app.route('/api/site/config', methods=['GET'])
def api_site_config_public():
    cfg = _read_site_config()
    return jsonify({
        'app_display_name': cfg['app_display_name'],
        'logo_url': cfg['logo_url'],
    })


@app.route('/api/admin/site-config', methods=['GET'])
def admin_site_config_get():
    aid, err = require_admin()
    if err:
        return err
    return jsonify(_read_site_config())


@app.route('/api/admin/site-config', methods=['PUT'])
def admin_site_config_save():
    aid, err = require_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    values = {}
    for api_key in SITE_CONFIG_STR_FIELDS:
        if api_key in data:
            values[api_key] = str(data.get(api_key) or '').strip()
    packages_payload = data.get('packages')
    has_packages = packages_payload is not None
    if has_packages:
        ok, result = _validate_packages_list(packages_payload)
        if not ok:
            return jsonify({'error': result}), 400
        try:
            _save_packages_raw(result)
        except Exception as e:
            print('[Admin site-config packages]', e)
            return jsonify({'error': 'Không lưu được gói cước.'}), 500
    if not values and not has_packages:
        return jsonify({'error': 'Không có dữ liệu để lưu.'}), 400
    try:
        if has_packages:
            pass  # already saved via _save_packages_raw
        if values:
            set_app_setting('site_meta', values)
            _apply_site_config_runtime(values)
            if _can_write_project_files():
                for api_key, val in values.items():
                    env_key, _ = SITE_CONFIG_STR_FIELDS[api_key]
                    _write_env_key(env_key, val)
    except Exception as e:
        print('[Admin site-config]', e)
        return jsonify({'error': 'Không lưu được cấu hình site.'}), 500
    return jsonify({'ok': True, 'config': _read_site_config()})


@app.route('/api/admin/site-config/logo', methods=['POST', 'DELETE'])
def admin_site_config_logo():
    aid, err = require_admin()
    if err:
        return err
    if request.method == 'DELETE':
        try:
            _delete_site_logo_files()
        except Exception as e:
            print('[Admin site logo delete]', e)
            return jsonify({'error': 'Không xóa được logo.'}), 500
        return jsonify({'ok': True, 'logo_url': '', 'has_logo': False})
    f = request.files.get('logo')
    if not f or not f.filename:
        return jsonify({'error': 'Không có ảnh logo.'}), 400
    if not allowed_file(f.filename):
        return jsonify({'error': 'Định dạng không hợp lệ (PNG, JPG, WEBP).'}), 400
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > 2 * 1024 * 1024:
        return jsonify({'error': 'Logo tối đa 2MB.'}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext == 'jpeg':
        ext = 'jpg'
    dest = os.path.join(app.static_folder, 'img', f'site-logo.{ext}')
    try:
        raw = f.read()
        set_app_setting('site_logo', {
            'ext': ext,
            'data_b64': base64.b64encode(raw).decode('ascii'),
            'updated_at': datetime.now(timezone.utc).isoformat(),
        })
        if _can_write_project_files():
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            _delete_site_logo_file_assets()
            with open(dest, 'wb') as out:
                out.write(raw)
    except Exception as e:
        print('[Admin site logo upload]', e)
        return jsonify({'error': 'Không lưu được logo.'}), 500
    return jsonify({'ok': True, 'logo_url': _site_logo_url(), 'has_logo': True})


@app.route('/api/admin/policies', methods=['GET'])
def admin_policies_list():
    aid, err = require_admin()
    if err:
        return err
    items = []
    for slug, meta in POLICY_PAGES.items():
        items.append({
            'slug': slug,
            'url': meta['url'],
            'label_vi': meta['label_vi'],
            'label_en': meta['label_en'],
            'kind': 'page',
        })
        if slug == 'ai_terms':
            items.append({
                'slug': 'site_contact',
                'url': '/support#contact',
                'label_vi': 'Thông tin liên hệ',
                'label_en': 'Contact information',
                'kind': 'contact',
            })
    return jsonify({'items': items})


@app.route('/api/admin/policies/<slug>', methods=['GET'])
def admin_policy_get(slug):
    aid, err = require_admin()
    if err:
        return err
    if slug not in POLICY_PAGES:
        return jsonify({'error': 'Trang chính sách không tồn tại.'}), 404
    _, content = _read_policy_block(slug)
    if not content:
        return jsonify({'error': 'Không đọc được nội dung trang.'}), 500
    vi_content, en_content = _split_policy_lang_blocks(content)
    meta = POLICY_PAGES[slug]
    return jsonify({
        'slug': slug,
        'url': meta['url'],
        'label_vi': meta['label_vi'],
        'label_en': meta['label_en'],
        'content': content,
        'vi_content': vi_content,
        'en_content': en_content,
    })


@app.route('/api/admin/policies/<slug>', methods=['PUT'])
def admin_policy_save(slug):
    aid, err = require_admin()
    if err:
        return err
    if slug not in POLICY_PAGES:
        return jsonify({'error': 'Trang chính sách không tồn tại.'}), 404
    data = request.get_json(silent=True) or {}
    vi_content = data.get('vi_content')
    if vi_content is None and data.get('content') is not None:
        vi_content, _ = _split_policy_lang_blocks(data.get('content'))
    if vi_content is None:
        return jsonify({'error': 'Thiếu nội dung tiếng Việt (vi_content).'}), 400
    vi_content = str(vi_content).strip()
    if not vi_content:
        return jsonify({'error': 'Nội dung tiếng Việt không được để trống.'}), 400
    existing_en = ''
    _, existing_block = _read_policy_block(slug)
    if existing_block:
        _, existing_en = _split_policy_lang_blocks(existing_block)
    try:
        en_content = _auto_translate_policy_en(vi_content, app.config)
    except Exception as e:
        print('[Admin policy translate]', e)
        en_content = existing_en
    if not str(en_content or '').strip():
        en_content = existing_en or vi_content
    merged = _merge_policy_lang_blocks(vi_content, en_content)
    ok, msg = _write_policy_block(slug, merged)
    if not ok:
        return jsonify({'error': msg or 'Không lưu được.'}), 500
    return jsonify({
        'ok': True,
        'slug': slug,
        'vi_content': vi_content,
        'en_content': en_content,
        'en_auto_translated': True,
    })


@app.route('/api/site/landing', methods=['GET'])
def api_site_landing_public():
    return jsonify(_load_landing_config())


@app.route('/api/admin/landing-config', methods=['GET'])
def admin_landing_config_get():
    aid, err = require_admin()
    if err:
        return err
    return jsonify(_load_landing_config())


@app.route('/api/admin/landing-config', methods=['PUT'])
def admin_landing_config_save():
    aid, err = require_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    ok, result = _validate_landing_payload(data)
    if not ok:
        return jsonify({'error': result}), 400
    try:
        result['en'] = _auto_translate_landing_en(result['vi'], app.config)
        result.update(_auto_translate_landing_marker_en(result, app.config))
        saved = _save_landing_config(result)
    except Exception as e:
        print('[Admin landing-config]', e)
        return jsonify({'error': 'Không lưu được cấu hình trang chủ.'}), 500
    return jsonify({'ok': True, 'config': saved, 'en_auto_translated': True})


@app.route('/api/admin/api-config', methods=['GET'])
def admin_api_config_get():
    aid, err = require_admin()
    if err:
        return err
    return jsonify(_read_api_config())


@app.route('/api/admin/api-config', methods=['PUT'])
def admin_api_config_save():
    aid, err = require_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    values = {}
    raw_key = data.get('openrouter_api_key')
    if raw_key is not None:
        key_val = str(raw_key or '').strip()
        if key_val and not key_val.startswith('•'):
            values['openrouter_api_key'] = key_val
    for api_key in API_CONFIG_STR_FIELDS:
        if api_key not in data:
            continue
        val = str(data.get(api_key) or '').strip()
        if api_key in ('openrouter_model', 'openrouter_vision_models') and not val:
            return jsonify({'error': 'Model không được để trống.'}), 400
        values[api_key] = val
    for api_key in API_CONFIG_INT_FIELDS:
        if api_key not in data:
            continue
        try:
            val = int(data.get(api_key))
        except (TypeError, ValueError):
            return jsonify({'error': 'Giá trị số không hợp lệ.'}), 400
        if val < 1:
            return jsonify({'error': 'Timeout và max tokens phải ≥ 1.'}), 400
        values[api_key] = val
    if not values:
        return jsonify({'error': 'Không có dữ liệu để lưu.'}), 400
    try:
        stored = get_app_setting('api_config') or {}
        if not isinstance(stored, dict):
            stored = {}
        stored.update(values)
        set_app_setting('api_config', stored)
        _apply_api_config_runtime(values)
        if _can_write_project_files():
            if 'openrouter_api_key' in values:
                _write_env_key('OPENROUTER_API_KEY', values['openrouter_api_key'])
            for api_key, val in values.items():
                if api_key == 'openrouter_api_key':
                    continue
                if api_key in API_CONFIG_STR_FIELDS:
                    env_key, _ = API_CONFIG_STR_FIELDS[api_key]
                    _write_env_key(env_key, val)
                elif api_key in API_CONFIG_INT_FIELDS:
                    env_key, _ = API_CONFIG_INT_FIELDS[api_key]
                    _write_env_key(env_key, str(val))
    except Exception as e:
        print('[Admin api-config]', e)
        return jsonify({'error': 'Không lưu được cấu hình API.'}), 500
    return jsonify({'ok': True, 'config': _read_api_config()})


@app.route('/api/admin/site-contact', methods=['GET'])
def admin_site_contact_get():
    aid, err = require_admin()
    if err:
        return err
    return jsonify(_read_contact_settings())


@app.route('/api/admin/site-contact', methods=['PUT'])
def admin_site_contact_save():
    aid, err = require_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    values = {}
    for api_key in SITE_CONTACT_FIELDS:
        if api_key not in data:
            continue
        values[api_key] = str(data.get(api_key) or '').strip()
    if not values:
        return jsonify({'error': 'Không có dữ liệu để lưu.'}), 400
    if 'support_email' in values and values['support_email'] and '@' not in values['support_email']:
        return jsonify({'error': 'Email hỗ trợ không hợp lệ.'}), 400
    try:
        stored = get_app_setting('contact') or {}
        if not isinstance(stored, dict):
            stored = {}
        stored.update(values)
        set_app_setting('contact', stored)
        _apply_contact_runtime(values)
        if _can_write_project_files():
            for api_key, val in values.items():
                env_key, _ = SITE_CONTACT_FIELDS[api_key]
                _write_env_key(env_key, val)
    except Exception as e:
        print('[Admin site-contact]', e)
        return jsonify({'error': 'Không lưu được thông tin liên hệ.'}), 500
    return jsonify({'ok': True, 'values': _read_contact_settings()})


# ---------- Favicon (tránh 404) ----------
@app.route('/favicon.ico')
def favicon():
    return '', 204


# ---------- Static (frontend) ----------
@app.route('/')
def index():
    return render_template('pages/index.html')


# ---------- Feature pages (SPA: render the same shell) ----------
@app.route('/intro')
def intro_page():
    return render_template('pages/index.html')


@app.route('/analyze')
def analyze_page():
    return render_template('pages/index.html')


@app.route('/history')
def history_page():
    return render_template('pages/index.html')


@app.route('/stats')
def stats_page():
    return render_template('pages/index.html')


@app.route('/packages')
def packages_page():
    return render_template('pages/index.html')


@app.route('/admin')
def admin_page():
    return render_template('pages/index.html')


@app.route('/login')
def login_page():
    return render_template('pages/login.html')


@app.route('/register')
def register_page():
    return render_template('pages/register.html')


@app.route('/forgot-password')
def forgot_password_page():
    return render_template('pages/forgot-password.html')


@app.route('/change-password')
def change_password_page():
    return render_template('pages/change-password.html')


@app.route('/profile')
def profile_page():
    return render_template('pages/index.html')


@app.route('/privacy')
def privacy_page():
    return _render_policy_page('privacy')


@app.route('/terms')
def terms_page():
    return _render_policy_page('terms')


@app.route('/data-deletion')
def data_deletion_page():
    return _render_policy_page('data_deletion')


@app.route('/support')
def support_page():
    return _render_policy_page('support')


@app.route('/install-guide')
def install_guide_page():
    return _render_policy_page('install_guide')


@app.route('/user-guide')
def user_guide_page():
    return _render_policy_page('user_guide')


@app.route('/payment-guide')
def payment_guide_page():
    return _render_policy_page('payment_guide')


@app.route('/payment-policy')
def payment_policy_page():
    return _render_policy_page('payment_policy')


@app.route('/ai-terms')
def ai_terms_page():
    return _render_policy_page('ai_terms')


@app.route('/<path:path>')
def static_files(path):
    if path.startswith('.well-known'):
        return jsonify({'error': 'Not Found'}), 404
    try:
        return send_from_directory('static', path)
    except Exception:
        return jsonify({'error': 'Not Found'}), 404


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
