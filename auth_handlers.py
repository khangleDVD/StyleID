"""Xử lý OTP đăng ký, quên mật khẩu, gửi email."""
from __future__ import annotations

from datetime import datetime

from werkzeug.security import generate_password_hash

from auth_otp import (
    OTP_FORGOT_TTL_MINUTES,
    OTP_MAX_RESEND,
    OTP_MAX_WRONG,
    OTP_REGISTER_TTL_MINUTES,
    OTP_RESEND_WINDOW_MINUTES,
    can_resend,
    expires_at,
    generate_otp_code,
    generate_session_token,
    generate_temp_password,
    hash_value,
    is_expired,
    is_gmail_address,
    verify_hashed_value,
)
from config import Config
from db import IntegrityError, DatabaseError, dict_cursor, get_db, plain_cursor
from email_service import EmailSendError, send_email


FORGOT_GENERIC_MSG = (
    'Nếu thông tin hợp lệ, hệ thống đã gửi hướng dẫn khôi phục đến Gmail đã đăng ký.'
)


def _row_expires(row) -> datetime | None:
    val = row.get('expires_at') if isinstance(row, dict) else None
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val).replace('Z', ''))
    except ValueError:
        return None


def _delete_session(cursor, session_token: str):
    cursor.execute('DELETE FROM auth_otp_sessions WHERE session_token = %s', (session_token,))


def _get_session(cursor, session_token: str, purpose: str):
    cursor.execute(
        """
        SELECT id, session_token, purpose, email, username, full_name, password_hash, user_id,
               otp_hash, wrong_attempts, resend_count, resend_window_start, expires_at
        FROM auth_otp_sessions
        WHERE session_token = %s AND purpose = %s
        LIMIT 1
        """,
        (session_token, purpose),
    )
    row = cursor.fetchone()
    if not row:
        return None
    if hasattr(row, 'keys'):
        return dict(row)
    cols = [
        'id', 'session_token', 'purpose', 'email', 'username', 'full_name', 'password_hash',
        'user_id', 'otp_hash', 'wrong_attempts', 'resend_count', 'resend_window_start', 'expires_at',
    ]
    return dict(zip(cols, row))


def _send_register_otp_email(email: str, otp_code: str):
    app_name = Config.APP_DISPLAY_NAME or 'StyleID'
    subject = f'[{app_name}] Mã xác minh đăng ký tài khoản'
    text = (
        f'Mã OTP đăng ký tài khoản {app_name} của bạn là: {otp_code}\n'
        f'Mã có hiệu lực {OTP_REGISTER_TTL_MINUTES} phút.\n'
        'Không chia sẻ mã này với bất kỳ ai.'
    )
    html = (
        f'<p>Mã OTP đăng ký tài khoản <strong>{app_name}</strong> của bạn:</p>'
        f'<p style="font-size:24px;font-weight:bold;letter-spacing:4px">{otp_code}</p>'
        f'<p>Mã có hiệu lực <strong>{OTP_REGISTER_TTL_MINUTES} phút</strong>.</p>'
        '<p>Không chia sẻ mã này với bất kỳ ai.</p>'
    )
    send_email(email, subject, text, html)


def _send_forgot_otp_email(email: str, otp_code: str):
    app_name = Config.APP_DISPLAY_NAME or 'StyleID'
    subject = f'[{app_name}] Mã xác minh khôi phục mật khẩu'
    text = (
        f'Mã OTP khôi phục mật khẩu {app_name} của bạn là: {otp_code}\n'
        f'Mã có hiệu lực {OTP_FORGOT_TTL_MINUTES} phút.\n'
        'Không chia sẻ mã này với bất kỳ ai.'
    )
    html = (
        f'<p>Mã OTP khôi phục mật khẩu <strong>{app_name}</strong>:</p>'
        f'<p style="font-size:24px;font-weight:bold;letter-spacing:4px">{otp_code}</p>'
        f'<p>Mã có hiệu lực <strong>{OTP_FORGOT_TTL_MINUTES} phút</strong>.</p>'
        '<p>Không chia sẻ mã này với bất kỳ ai.</p>'
    )
    send_email(email, subject, text, html)


def _send_temp_password_email(email: str, temp_password: str):
    app_name = Config.APP_DISPLAY_NAME or 'StyleID'
    subject = f'[{app_name}] Mật khẩu tạm thời'
    text = (
        f'Mật khẩu tạm thời tài khoản {app_name} của bạn là: {temp_password}\n'
        'Vui lòng đăng nhập và đổi mật khẩu ngay.'
    )
    html = (
        f'<p>Mật khẩu tạm thời tài khoản <strong>{app_name}</strong>:</p>'
        f'<p style="font-size:18px;font-weight:bold">{temp_password}</p>'
        '<p>Vui lòng đăng nhập và <strong>đổi mật khẩu ngay</strong>.</p>'
    )
    send_email(email, subject, text, html)


def _user_exists(cursor, username: str = '', email: str = '') -> bool:
    if username:
        cursor.execute('SELECT 1 FROM users WHERE username = %s LIMIT 1', (username,))
        if cursor.fetchone():
            return True
    if email:
        cursor.execute('SELECT 1 FROM users WHERE LOWER(email) = %s LIMIT 1', (email.lower(),))
        if cursor.fetchone():
            return True
    return False


def register_request(data: dict) -> tuple[dict, int]:
    username = (data.get('username') or '').strip()
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    password_confirm = data.get('password_confirm') or data.get('confirm_password') or ''
    full_name = (data.get('full_name') or '').strip()
    terms_accepted = data.get('terms_accepted') in (True, 'true', '1', 1, 'on')

    if not full_name:
        return {'error': 'Vui lòng nhập họ tên'}, 400
    if not username:
        return {'error': 'Vui lòng nhập tên đăng nhập'}, 400
    if not email:
        return {'error': 'Vui lòng nhập địa chỉ Gmail'}, 400
    if not is_gmail_address(email):
        return {'error': 'Chỉ chấp nhận email Gmail (@gmail.com)'}, 400
    if not password or len(password) < 6:
        return {'error': 'Mật khẩu cần ít nhất 6 ký tự'}, 400
    if password != password_confirm:
        return {'error': 'Mật khẩu và xác nhận không trùng nhau'}, 400
    if not terms_accepted:
        return {'error': 'Vui lòng đồng ý điều khoản sử dụng'}, 400

    db = get_db()
    cursor = plain_cursor(db)
    try:
        if _user_exists(cursor, username=username):
            return {'error': 'Tên đăng nhập đã được sử dụng'}, 400
        if _user_exists(cursor, email=email):
            return {'error': 'Email đã được sử dụng'}, 400

        otp_code = generate_otp_code()
        session_token = generate_session_token()
        otp_hash = hash_value(otp_code)
        password_hash = generate_password_hash(password)
        exp = expires_at(OTP_REGISTER_TTL_MINUTES)

        try:
            _send_register_otp_email(email, otp_code)
        except EmailSendError as exc:
            return {'error': str(exc)}, 503

        cursor.execute(
            'DELETE FROM auth_otp_sessions WHERE purpose = %s AND (username = %s OR email = %s)',
            ('register', username, email),
        )
        cursor.execute(
            """
            INSERT INTO auth_otp_sessions
            (session_token, purpose, email, username, full_name, password_hash, otp_hash, expires_at, resend_window_start)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (session_token, 'register', email, username, full_name, password_hash, otp_hash, exp, datetime.utcnow()),
        )
        db.commit()
        return {
            'message': 'Mã OTP đã được gửi đến Gmail của bạn. Vui lòng xác minh trong 5 phút.',
            'session_token': session_token,
            'expires_in': OTP_REGISTER_TTL_MINUTES * 60,
            'email_masked': _mask_email(email),
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        db.close()


def register_verify(data: dict) -> tuple[dict, int]:
    session_token = (data.get('session_token') or '').strip()
    otp_code = (data.get('otp') or data.get('otp_code') or '').strip()
    if not session_token or not otp_code:
        return {'error': 'Thiếu mã OTP hoặc phiên xác minh'}, 400

    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        session = _get_session(dcur, session_token, 'register')
        if not session:
            return {'error': 'Phiên xác minh không hợp lệ. Vui lòng đăng ký lại.'}, 400
        if is_expired(_row_expires(session)):
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Mã OTP đã hết hạn. Vui lòng gửi lại mã.'}, 400
        if int(session.get('wrong_attempts') or 0) >= OTP_MAX_WRONG:
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Đã nhập sai OTP quá số lần cho phép. Vui lòng đăng ký lại.'}, 400

        if not verify_hashed_value(otp_code, session['otp_hash']):
            wrong = int(session.get('wrong_attempts') or 0) + 1
            if wrong >= OTP_MAX_WRONG:
                _delete_session(cursor, session_token)
                db.commit()
                return {'error': 'Đã nhập sai OTP quá số lần cho phép. Vui lòng đăng ký lại.'}, 400
            cursor.execute(
                'UPDATE auth_otp_sessions SET wrong_attempts = %s WHERE session_token = %s',
                (wrong, session_token),
            )
            db.commit()
            remain = OTP_MAX_WRONG - wrong
            return {'error': f'Mã OTP không đúng. Còn {remain} lần thử.'}, 400

        if _user_exists(cursor, username=session['username'], email=session['email']):
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Tài khoản đã tồn tại. Vui lòng đăng nhập.'}, 400

        cursor.execute(
            """
            INSERT INTO users (username, password, full_name, email, email_verified, analysis_credits)
            VALUES (%s, %s, %s, %s, 1, %s)
            """,
            (
                session['username'],
                session['password_hash'],
                session['full_name'],
                session['email'],
                Config.INITIAL_ANALYSIS_CREDITS,
            ),
        )
        _delete_session(cursor, session_token)
        db.commit()
        return {'message': 'Đăng ký thành công! Bạn có thể đăng nhập.'}, 201
    except IntegrityError:
        _delete_session(cursor, session_token)
        db.commit()
        return {'error': 'Tài khoản đã tồn tại. Vui lòng đăng nhập.'}, 400
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()


def register_resend(data: dict) -> tuple[dict, int]:
    session_token = (data.get('session_token') or '').strip()
    if not session_token:
        return {'error': 'Thiếu phiên xác minh'}, 400

    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        session = _get_session(dcur, session_token, 'register')
        if not session:
            return {'error': 'Phiên xác minh không hợp lệ. Vui lòng đăng ký lại.'}, 400

        allowed, count, window_start = can_resend(
            int(session.get('resend_count') or 0),
            session.get('resend_window_start'),
        )
        if not allowed:
            return {
                'error': f'Đã gửi lại OTP tối đa {OTP_MAX_RESEND} lần trong {OTP_RESEND_WINDOW_MINUTES} phút.',
            }, 429

        otp_code = generate_otp_code()
        otp_hash = hash_value(otp_code)
        exp = expires_at(OTP_REGISTER_TTL_MINUTES)

        try:
            _send_register_otp_email(session['email'], otp_code)
        except EmailSendError as exc:
            return {'error': str(exc)}, 503

        cursor.execute(
            """
            UPDATE auth_otp_sessions
            SET otp_hash = %s, expires_at = %s, wrong_attempts = 0,
                resend_count = %s, resend_window_start = %s
            WHERE session_token = %s
            """,
            (otp_hash, exp, count + 1, window_start or datetime.utcnow(), session_token),
        )
        db.commit()
        return {
            'message': 'Mã OTP mới đã được gửi đến Gmail của bạn.',
            'expires_in': OTP_REGISTER_TTL_MINUTES * 60,
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()


def _find_verified_user(cursor, identifier: str):
    identifier = (identifier or '').strip()
    if not identifier:
        return None
    if '@' in identifier:
        cursor.execute(
            """
            SELECT id, username, email, email_verified, password, google_id
            FROM users WHERE LOWER(email) = %s LIMIT 1
            """,
            (identifier.lower(),),
        )
    else:
        cursor.execute(
            """
            SELECT id, username, email, email_verified, password, google_id
            FROM users WHERE username = %s LIMIT 1
            """,
            (identifier,),
        )
    row = cursor.fetchone()
    if not row:
        return None
    if hasattr(row, 'keys'):
        return dict(row)
    cols = ['id', 'username', 'email', 'email_verified', 'password', 'google_id']
    return dict(zip(cols, row))


def forgot_password_request(data: dict) -> tuple[dict, int]:
    identifier = (data.get('identifier') or data.get('email') or data.get('username') or '').strip()
    if not identifier:
        return {'error': 'Vui lòng nhập email hoặc tên đăng nhập'}, 400

    db = get_db()
    dcur = dict_cursor(db)
    cursor = plain_cursor(db)
    try:
        user = _find_verified_user(dcur, identifier)
        if (
            user
            and user.get('email')
            and is_gmail_address(user['email'])
            and int(user.get('email_verified') or 0) == 1
            and user.get('password')
            and not user.get('google_id')
        ):
            otp_code = generate_otp_code()
            session_token = generate_session_token()
            otp_hash = hash_value(otp_code)
            exp = expires_at(OTP_FORGOT_TTL_MINUTES)
            try:
                _send_forgot_otp_email(user['email'], otp_code)
            except EmailSendError as exc:
                return {'error': str(exc)}, 503

            cursor.execute('DELETE FROM auth_otp_sessions WHERE purpose = %s AND user_id = %s', ('forgot_password', user['id']))
            cursor.execute(
                """
                INSERT INTO auth_otp_sessions
                (session_token, purpose, email, user_id, otp_hash, expires_at, resend_window_start)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (session_token, 'forgot_password', user['email'], user['id'], otp_hash, exp, datetime.utcnow()),
            )
            db.commit()
            session_out = session_token
        else:
            session_out = generate_session_token()

        return {'message': FORGOT_GENERIC_MSG, 'session_token': session_out}, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()


def forgot_password_verify(data: dict) -> tuple[dict, int]:
    session_token = (data.get('session_token') or '').strip()
    otp_code = (data.get('otp') or data.get('otp_code') or '').strip()
    if not session_token or not otp_code:
        return {'error': 'Thiếu mã OTP hoặc phiên xác minh'}, 400

    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        session = _get_session(dcur, session_token, 'forgot_password')
        if not session:
            return {'error': 'Phiên xác minh không hợp lệ. Vui lòng yêu cầu lại.'}, 400
        if is_expired(_row_expires(session)):
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Mã OTP đã hết hạn. Vui lòng gửi lại mã.'}, 400
        if int(session.get('wrong_attempts') or 0) >= OTP_MAX_WRONG:
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Đã nhập sai OTP quá số lần cho phép. Vui lòng yêu cầu lại.'}, 400

        if not verify_hashed_value(otp_code, session['otp_hash']):
            wrong = int(session.get('wrong_attempts') or 0) + 1
            if wrong >= OTP_MAX_WRONG:
                _delete_session(cursor, session_token)
                db.commit()
                return {'error': 'Đã nhập sai OTP quá số lần cho phép. Vui lòng yêu cầu lại.'}, 400
            cursor.execute(
                'UPDATE auth_otp_sessions SET wrong_attempts = %s WHERE session_token = %s',
                (wrong, session_token),
            )
            db.commit()
            remain = OTP_MAX_WRONG - wrong
            return {'error': f'Mã OTP không đúng. Còn {remain} lần thử.'}, 400

        user_id = session.get('user_id')
        if not user_id:
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Phiên xác minh không hợp lệ.'}, 400

        temp_password = generate_temp_password()
        temp_hash = generate_password_hash(temp_password)

        try:
            _send_temp_password_email(session['email'], temp_password)
        except EmailSendError as exc:
            return {'error': str(exc)}, 503

        cursor.execute(
            'UPDATE users SET password = %s, force_change_password = 1 WHERE id = %s',
            (temp_hash, user_id),
        )
        _delete_session(cursor, session_token)
        db.commit()
        return {
            'message': 'Mật khẩu tạm thời đã được gửi đến Gmail đã đăng ký. Vui lòng đăng nhập và đổi mật khẩu.',
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()


def forgot_password_resend(data: dict) -> tuple[dict, int]:
    session_token = (data.get('session_token') or '').strip()
    if not session_token:
        return {'error': 'Thiếu phiên xác minh'}, 400

    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        session = _get_session(dcur, session_token, 'forgot_password')
        if not session:
            return {'error': 'Phiên xác minh không hợp lệ. Vui lòng yêu cầu lại.'}, 400

        allowed, count, window_start = can_resend(
            int(session.get('resend_count') or 0),
            session.get('resend_window_start'),
        )
        if not allowed:
            return {
                'error': f'Đã gửi lại OTP tối đa {OTP_MAX_RESEND} lần trong {OTP_RESEND_WINDOW_MINUTES} phút.',
            }, 429

        otp_code = generate_otp_code()
        otp_hash = hash_value(otp_code)
        exp = expires_at(OTP_FORGOT_TTL_MINUTES)

        try:
            _send_forgot_otp_email(session['email'], otp_code)
        except EmailSendError as exc:
            return {'error': str(exc)}, 503

        cursor.execute(
            """
            UPDATE auth_otp_sessions
            SET otp_hash = %s, expires_at = %s, wrong_attempts = 0,
                resend_count = %s, resend_window_start = %s
            WHERE session_token = %s
            """,
            (otp_hash, exp, count + 1, window_start or datetime.utcnow(), session_token),
        )
        db.commit()
        return {
            'message': 'Mã OTP mới đã được gửi đến Gmail đã đăng ký.',
            'expires_in': OTP_FORGOT_TTL_MINUTES * 60,
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()


def _mask_email(email: str) -> str:
    parts = email.split('@')
    if len(parts) != 2:
        return email
    name = parts[0]
    if len(name) <= 2:
        masked = name[0] + '***'
    else:
        masked = name[0] + '***' + name[-1]
    return masked + '@' + parts[1]
