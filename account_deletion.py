"""Yêu cầu xóa tài khoản, khôi phục và xử lý tự động sau grace period."""
from __future__ import annotations

from datetime import datetime, timedelta

from auth_handlers import _get_session, _delete_session, _mask_email, _row_expires
from auth_otp import (
    OTP_DELETE_TTL_MINUTES,
    OTP_MAX_RESEND,
    OTP_MAX_WRONG,
    OTP_RESEND_WINDOW_MINUTES,
    OTP_RESTORE_TTL_MINUTES,
    can_resend,
    expires_at,
    generate_otp_code,
    generate_session_token,
    hash_value,
    is_expired,
    verify_hashed_value,
)
from config import Config
from db import DatabaseError, dict_cursor, get_db, plain_cursor
from email_service import EmailSendError, send_email


PURPOSE_DELETE = 'account_delete'
PURPOSE_RESTORE = 'account_restore'


def normalize_account_status(value) -> str:
    s = (value or 'active').strip().lower()
    return s if s in ('active', 'pending_delete', 'deleted') else 'active'


def _format_dt_display(dt_val) -> str:
    if not dt_val:
        return '—'
    if isinstance(dt_val, str):
        try:
            dt_val = datetime.fromisoformat(dt_val.replace('Z', ''))
        except ValueError:
            return dt_val[:19].replace('T', ' ')
    return dt_val.strftime('%d/%m/%Y %H:%M')


def _admin_email() -> str:
    return (Config.ADMIN_NOTIFY_EMAIL or Config.SUPPORT_EMAIL or '').strip()


def _send_delete_otp_email(email: str, otp_code: str):
    app_name = Config.APP_DISPLAY_NAME or 'StyleID'
    subject = f'[{app_name}] Mã xác minh yêu cầu xóa tài khoản'
    text = (
        f'Mã OTP xác nhận yêu cầu xóa tài khoản {app_name} của bạn là: {otp_code}\n'
        f'Mã có hiệu lực {OTP_DELETE_TTL_MINUTES} phút.\n'
        'Không chia sẻ mã này với bất kỳ ai.'
    )
    html = (
        f'<p>Mã OTP xác nhận <strong>yêu cầu xóa tài khoản</strong> {app_name}:</p>'
        f'<p style="font-size:24px;font-weight:bold;letter-spacing:4px">{otp_code}</p>'
        f'<p>Mã có hiệu lực <strong>{OTP_DELETE_TTL_MINUTES} phút</strong>.</p>'
        '<p>Không chia sẻ mã này với bất kỳ ai.</p>'
    )
    send_email(email, subject, text, html)


def _send_restore_otp_email(email: str, otp_code: str):
    app_name = Config.APP_DISPLAY_NAME or 'StyleID'
    subject = f'[{app_name}] Mã xác minh khôi phục tài khoản'
    text = (
        f'Mã OTP khôi phục tài khoản {app_name} của bạn là: {otp_code}\n'
        f'Mã có hiệu lực {OTP_RESTORE_TTL_MINUTES} phút.\n'
        'Không chia sẻ mã này với bất kỳ ai.'
    )
    html = (
        f'<p>Mã OTP xác nhận <strong>khôi phục tài khoản</strong> {app_name}:</p>'
        f'<p style="font-size:24px;font-weight:bold;letter-spacing:4px">{otp_code}</p>'
        f'<p>Mã có hiệu lực <strong>{OTP_RESTORE_TTL_MINUTES} phút</strong>.</p>'
        '<p>Không chia sẻ mã này với bất kỳ ai.</p>'
    )
    send_email(email, subject, text, html)


def _send_user_delete_confirmed_email(
    email: str,
    full_name: str,
    requested_at: datetime,
    scheduled_at: datetime,
):
    app_name = Config.APP_DISPLAY_NAME or 'StyleID'
    grace = Config.ACCOUNT_DELETE_GRACE_DAYS
    subject = 'Yêu cầu xóa tài khoản đã được ghi nhận'
    req_s = _format_dt_display(requested_at)
    sch_s = _format_dt_display(scheduled_at)
    text = (
        f'Xin chào {full_name},\n\n'
        f'Yêu cầu xóa tài khoản {app_name} của bạn đã được ghi nhận.\n\n'
        f'Email tài khoản: {email}\n'
        f'Thời gian gửi yêu cầu: {req_s}\n'
        f'Ngày dự kiến xóa tài khoản: {sch_s}\n\n'
        f'Trong vòng {grace} ngày, bạn có thể đăng nhập lại để khôi phục tài khoản nếu thay đổi quyết định.\n'
        f'Sau {grace} ngày, tài khoản sẽ bị xóa hoặc vô hiệu hóa vĩnh viễn.\n'
    )
    html = (
        f'<p>Xin chào <strong>{full_name}</strong>,</p>'
        f'<p>Yêu cầu xóa tài khoản <strong>{app_name}</strong> của bạn đã được ghi nhận.</p>'
        '<ul>'
        f'<li><strong>Email tài khoản:</strong> {email}</li>'
        f'<li><strong>Thời gian gửi yêu cầu:</strong> {req_s}</li>'
        f'<li><strong>Ngày dự kiến xóa:</strong> {sch_s}</li>'
        '</ul>'
        f'<p>Trong vòng <strong>{grace} ngày</strong>, bạn có thể đăng nhập lại để khôi phục tài khoản nếu thay đổi quyết định.</p>'
        f'<p>Sau {grace} ngày, tài khoản sẽ bị xóa hoặc vô hiệu hóa vĩnh viễn.</p>'
    )
    send_email(email, subject, text, html)


def _send_admin_delete_notification(
    user_id: int,
    full_name: str,
    email: str,
    requested_at: datetime,
    scheduled_at: datetime,
    delete_reason: str,
):
    admin_to = _admin_email()
    if not admin_to:
        return
    app_name = Config.APP_DISPLAY_NAME or 'StyleID'
    subject = 'Có yêu cầu xóa tài khoản từ người dùng'
    req_s = _format_dt_display(requested_at)
    sch_s = _format_dt_display(scheduled_at)
    reason_line = delete_reason.strip() if delete_reason else '(Không có)'
    text = (
        f'[{app_name}] Có yêu cầu xóa tài khoản mới.\n\n'
        f'ID người dùng: {user_id}\n'
        f'Tên: {full_name}\n'
        f'Email: {email}\n'
        f'Thời gian gửi yêu cầu: {req_s}\n'
        f'Ngày dự kiến xóa: {sch_s}\n'
        f'Lý do xóa: {reason_line}\n'
        f'Trạng thái hiện tại: pending_delete\n'
    )
    html = (
        f'<p><strong>[{app_name}]</strong> Có yêu cầu xóa tài khoản mới.</p>'
        '<ul>'
        f'<li><strong>ID người dùng:</strong> {user_id}</li>'
        f'<li><strong>Tên:</strong> {full_name}</li>'
        f'<li><strong>Email:</strong> {email}</li>'
        f'<li><strong>Thời gian gửi yêu cầu:</strong> {req_s}</li>'
        f'<li><strong>Ngày dự kiến xóa:</strong> {sch_s}</li>'
        f'<li><strong>Lý do xóa:</strong> {reason_line}</li>'
        f'<li><strong>Trạng thái:</strong> pending_delete</li>'
        '</ul>'
    )
    send_email(admin_to, subject, text, html)


def _fetch_user(cursor, user_id: int):
    cursor.execute(
        """
        SELECT id, username, full_name, email, email_verified, google_id,
               account_status, delete_requested_at, delete_scheduled_at, delete_reason
        FROM users WHERE id = %s LIMIT 1
        """,
        (user_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    if hasattr(row, 'keys'):
        return dict(row)
    cols = [
        'id', 'username', 'full_name', 'email', 'email_verified', 'google_id',
        'account_status', 'delete_requested_at', 'delete_scheduled_at', 'delete_reason',
    ]
    return dict(zip(cols, row))


def process_expired_deletions() -> int:
    """Chuyển tài khoản pending_delete quá hạn sang deleted (soft delete)."""
    db = get_db()
    cursor = plain_cursor(db)
    try:
        now = datetime.utcnow()
        cursor.execute(
            """
            UPDATE users
            SET account_status = 'deleted', deleted_at = %s
            WHERE account_status = 'pending_delete'
              AND delete_scheduled_at IS NOT NULL
              AND delete_scheduled_at <= %s
            """,
            (now, now),
        )
        count = cursor.rowcount if cursor.rowcount is not None else 0
        db.commit()
        return int(count or 0)
    except DatabaseError:
        db.rollback()
        return 0
    finally:
        cursor.close()
        db.close()


def delete_account_request(user_id: int, data: dict) -> tuple[dict, int]:
    delete_reason = (data.get('delete_reason') or '').strip()[:500]

    db = get_db()
    dcur = dict_cursor(db)
    cursor = plain_cursor(db)
    try:
        user = _fetch_user(dcur, user_id)
        if not user:
            return {'error': 'Không tìm thấy tài khoản'}, 404

        status = normalize_account_status(user.get('account_status'))
        if status == 'deleted':
            return {'error': 'Tài khoản đã bị xóa hoặc vô hiệu hóa.'}, 403
        if status == 'pending_delete':
            return {'error': 'Tài khoản đang trong thời gian chờ xóa.'}, 400

        email = (user.get('email') or '').strip()
        if not email:
            return {'error': 'Tài khoản chưa có email. Không thể gửi OTP xác minh.'}, 400
        if not int(user.get('email_verified') or 0):
            return {'error': 'Email chưa được xác thực. Không thể yêu cầu xóa tài khoản.'}, 400

        otp_code = generate_otp_code()
        session_token = generate_session_token()
        otp_hash = hash_value(otp_code)
        exp = expires_at(OTP_DELETE_TTL_MINUTES)

        try:
            _send_delete_otp_email(email, otp_code)
        except EmailSendError as exc:
            return {'error': str(exc)}, 503

        cursor.execute(
            'DELETE FROM auth_otp_sessions WHERE purpose = %s AND user_id = %s',
            (PURPOSE_DELETE, user_id),
        )
        cursor.execute(
            """
            INSERT INTO auth_otp_sessions
            (session_token, purpose, email, user_id, otp_hash, expires_at, resend_window_start)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (session_token, PURPOSE_DELETE, email, user_id, otp_hash, exp, datetime.utcnow()),
        )
        if delete_reason:
            cursor.execute(
                'UPDATE users SET delete_reason = %s WHERE id = %s',
                (delete_reason, user_id),
            )
        db.commit()
        return {
            'message': 'Mã OTP đã được gửi đến email của bạn. Vui lòng xác minh trong vài phút.',
            'session_token': session_token,
            'expires_in': OTP_DELETE_TTL_MINUTES * 60,
            'email_masked': _mask_email(email),
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()



def delete_account_verify(user_id: int, data: dict) -> tuple[dict, int]:
    session_token = (data.get('session_token') or '').strip()
    otp_code = (data.get('otp') or data.get('otp_code') or '').strip()
    if not session_token or not otp_code:
        return {'error': 'Thiếu mã OTP hoặc phiên xác minh'}, 400

    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        user = _fetch_user(dcur, user_id)
        if not user:
            return {'error': 'Không tìm thấy tài khoản'}, 404

        status = normalize_account_status(user.get('account_status'))
        if status == 'deleted':
            return {'error': 'Tài khoản đã bị xóa hoặc vô hiệu hóa.'}, 403
        if status == 'pending_delete':
            return {'error': 'Tài khoản đã ở trạng thái chờ xóa.'}, 400

        session = _get_session(dcur, session_token, PURPOSE_DELETE)
        if not session or int(session.get('user_id') or 0) != user_id:
            return {'error': 'Phiên xác minh không hợp lệ. Vui lòng yêu cầu lại.'}, 400
        if is_expired(_row_expires(session)):
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Mã OTP đã hết hạn. Vui lòng gửi lại mã.'}, 400
        if int(session.get('wrong_attempts') or 0) >= OTP_MAX_WRONG:
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Đã nhập sai OTP quá số lần cho phép. Vui lòng yêu cầu lại sau.'}, 429

        if not verify_hashed_value(otp_code, session['otp_hash']):
            wrong = int(session.get('wrong_attempts') or 0) + 1
            if wrong >= OTP_MAX_WRONG:
                _delete_session(cursor, session_token)
                db.commit()
                return {'error': 'Đã nhập sai OTP quá số lần cho phép. Vui lòng yêu cầu lại sau.'}, 429
            cursor.execute(
                'UPDATE auth_otp_sessions SET wrong_attempts = %s WHERE session_token = %s',
                (wrong, session_token),
            )
            db.commit()
            remain = OTP_MAX_WRONG - wrong
            return {'error': f'Mã OTP không đúng. Còn {remain} lần thử.'}, 400

        now = datetime.utcnow()
        scheduled = now + timedelta(days=max(1, Config.ACCOUNT_DELETE_GRACE_DAYS))
        delete_reason = (user.get('delete_reason') or '').strip()
        full_name = (user.get('full_name') or user.get('username') or '').strip()
        email = (user.get('email') or '').strip()

        cursor.execute(
            """
            UPDATE users
            SET account_status = 'pending_delete',
                delete_requested_at = %s,
                delete_scheduled_at = %s,
                delete_cancelled_at = NULL,
                deleted_at = NULL
            WHERE id = %s AND account_status = 'active'
            """,
            (now, scheduled, user_id),
        )
        if cursor.rowcount == 0:
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Không thể cập nhật trạng thái tài khoản.'}, 400

        _delete_session(cursor, session_token)
        db.commit()

        try:
            _send_user_delete_confirmed_email(email, full_name, now, scheduled)
        except EmailSendError:
            pass
        try:
            _send_admin_delete_notification(
                user_id, full_name, email, now, scheduled, delete_reason,
            )
        except EmailSendError:
            pass

        return {
            'message': (
                'Yêu cầu xóa tài khoản đã được ghi nhận. Tài khoản của bạn sẽ được xóa sau '
                f'{Config.ACCOUNT_DELETE_GRACE_DAYS} ngày. Trong thời gian này, bạn có thể đăng nhập lại '
                'để khôi phục tài khoản nếu thay đổi quyết định.'
            ),
            'account_status': 'pending_delete',
            'delete_requested_at': now.isoformat(),
            'delete_scheduled_at': scheduled.isoformat(),
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()



def delete_account_resend(user_id: int, data: dict) -> tuple[dict, int]:
    session_token = (data.get('session_token') or '').strip()
    if not session_token:
        return {'error': 'Thiếu phiên xác minh'}, 400

    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        user = _fetch_user(dcur, user_id)
        if not user:
            return {'error': 'Không tìm thấy tài khoản'}, 404
        if normalize_account_status(user.get('account_status')) != 'active':
            return {'error': 'Tài khoản không ở trạng thái active.'}, 400

        session = _get_session(dcur, session_token, PURPOSE_DELETE)
        if not session or int(session.get('user_id') or 0) != user_id:
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
        exp = expires_at(OTP_DELETE_TTL_MINUTES)

        try:
            _send_delete_otp_email(session['email'], otp_code)
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
            'message': 'Mã OTP mới đã được gửi đến email của bạn.',
            'expires_in': OTP_DELETE_TTL_MINUTES * 60,
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()



def restore_account_request(user_id: int, data: dict) -> tuple[dict, int]:
    db = get_db()
    dcur = dict_cursor(db)
    cursor = plain_cursor(db)
    try:
        user = _fetch_user(dcur, user_id)
        if not user:
            return {'error': 'Không tìm thấy tài khoản'}, 404

        status = normalize_account_status(user.get('account_status'))
        if status == 'deleted':
            return {'error': 'Tài khoản đã bị xóa hoặc vô hiệu hóa.'}, 403
        if status != 'pending_delete':
            return {'error': 'Tài khoản không ở trạng thái chờ xóa.'}, 400

        email = (user.get('email') or '').strip()
        if not email:
            return {'error': 'Tài khoản chưa có email. Không thể gửi OTP.'}, 400

        otp_code = generate_otp_code()
        session_token = generate_session_token()
        otp_hash = hash_value(otp_code)
        exp = expires_at(OTP_RESTORE_TTL_MINUTES)

        try:
            _send_restore_otp_email(email, otp_code)
        except EmailSendError as exc:
            return {'error': str(exc)}, 503

        cursor.execute(
            'DELETE FROM auth_otp_sessions WHERE purpose = %s AND user_id = %s',
            (PURPOSE_RESTORE, user_id),
        )
        cursor.execute(
            """
            INSERT INTO auth_otp_sessions
            (session_token, purpose, email, user_id, otp_hash, expires_at, resend_window_start)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (session_token, PURPOSE_RESTORE, email, user_id, otp_hash, exp, datetime.utcnow()),
        )
        db.commit()
        return {
            'message': 'Mã OTP khôi phục đã được gửi đến email của bạn.',
            'session_token': session_token,
            'expires_in': OTP_RESTORE_TTL_MINUTES * 60,
            'email_masked': _mask_email(email),
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()



def restore_account_verify(user_id: int, data: dict) -> tuple[dict, int]:
    session_token = (data.get('session_token') or '').strip()
    otp_code = (data.get('otp') or data.get('otp_code') or '').strip()
    if not session_token or not otp_code:
        return {'error': 'Thiếu mã OTP hoặc phiên xác minh'}, 400

    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        user = _fetch_user(dcur, user_id)
        if not user:
            return {'error': 'Không tìm thấy tài khoản'}, 404

        status = normalize_account_status(user.get('account_status'))
        if status == 'deleted':
            return {'error': 'Tài khoản đã bị xóa hoặc vô hiệu hóa.'}, 403
        if status != 'pending_delete':
            return {'error': 'Tài khoản không ở trạng thái chờ xóa.'}, 400

        session = _get_session(dcur, session_token, PURPOSE_RESTORE)
        if not session or int(session.get('user_id') or 0) != user_id:
            return {'error': 'Phiên xác minh không hợp lệ. Vui lòng yêu cầu lại.'}, 400
        if is_expired(_row_expires(session)):
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Mã OTP đã hết hạn. Vui lòng gửi lại mã.'}, 400
        if int(session.get('wrong_attempts') or 0) >= OTP_MAX_WRONG:
            _delete_session(cursor, session_token)
            db.commit()
            return {'error': 'Đã nhập sai OTP quá số lần cho phép. Vui lòng yêu cầu lại sau.'}, 429

        if not verify_hashed_value(otp_code, session['otp_hash']):
            wrong = int(session.get('wrong_attempts') or 0) + 1
            if wrong >= OTP_MAX_WRONG:
                _delete_session(cursor, session_token)
                db.commit()
                return {'error': 'Đã nhập sai OTP quá số lần cho phép. Vui lòng yêu cầu lại sau.'}, 429
            cursor.execute(
                'UPDATE auth_otp_sessions SET wrong_attempts = %s WHERE session_token = %s',
                (wrong, session_token),
            )
            db.commit()
            remain = OTP_MAX_WRONG - wrong
            return {'error': f'Mã OTP không đúng. Còn {remain} lần thử.'}, 400

        now = datetime.utcnow()
        cursor.execute(
            """
            UPDATE users
            SET account_status = 'active',
                delete_requested_at = NULL,
                delete_scheduled_at = NULL,
                delete_reason = NULL,
                delete_cancelled_at = %s,
                deleted_at = NULL
            WHERE id = %s AND account_status = 'pending_delete'
            """,
            (now, user_id),
        )
        _delete_session(cursor, session_token)
        db.commit()

        return {
            'message': 'Tài khoản đã được khôi phục thành công. Bạn có thể tiếp tục sử dụng dịch vụ.',
            'account_status': 'active',
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()



def restore_account_resend(user_id: int, data: dict) -> tuple[dict, int]:
    session_token = (data.get('session_token') or '').strip()
    if not session_token:
        return {'error': 'Thiếu phiên xác minh'}, 400

    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        user = _fetch_user(dcur, user_id)
        if not user:
            return {'error': 'Không tìm thấy tài khoản'}, 404
        if normalize_account_status(user.get('account_status')) != 'pending_delete':
            return {'error': 'Tài khoản không ở trạng thái chờ xóa.'}, 400

        session = _get_session(dcur, session_token, PURPOSE_RESTORE)
        if not session or int(session.get('user_id') or 0) != user_id:
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
        exp = expires_at(OTP_RESTORE_TTL_MINUTES)

        try:
            _send_restore_otp_email(session['email'], otp_code)
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
            'message': 'Mã OTP mới đã được gửi đến email của bạn.',
            'expires_in': OTP_RESTORE_TTL_MINUTES * 60,
        }, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()

def admin_restore_account(user_id: int) -> tuple[dict, int]:
    """Admin khôi phục tài khoản pending_delete / deleted (không cần OTP)."""
    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        user = _fetch_user(dcur, user_id)
        if not user:
            return {'error': 'Không tìm thấy tài khoản'}, 404
        status = normalize_account_status(user.get('account_status'))
        if status not in ('pending_delete', 'deleted'):
            return {'error': 'Tài khoản không ở trạng thái chờ xóa hoặc đã xóa.'}, 400
        now = datetime.utcnow()
        cursor.execute(
            """
            UPDATE users
            SET account_status = 'active',
                delete_requested_at = NULL,
                delete_scheduled_at = NULL,
                delete_reason = NULL,
                delete_cancelled_at = %s,
                deleted_at = NULL
            WHERE id = %s
            """,
            (now, user_id),
        )
        cursor.execute(
            'DELETE FROM auth_otp_sessions WHERE purpose IN (%s, %s) AND user_id = %s',
            (PURPOSE_DELETE, PURPOSE_RESTORE, user_id),
        )
        db.commit()
        return {'message': 'Đã khôi phục tài khoản thành công.', 'account_status': 'active'}, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()


def admin_approve_delete(user_id: int) -> tuple[dict, int]:
    """Admin duyệt xóa ngay (soft delete) — không chờ hết grace period."""
    db = get_db()
    cursor = plain_cursor(db)
    dcur = dict_cursor(db)
    try:
        user = _fetch_user(dcur, user_id)
        if not user:
            return {'error': 'Không tìm thấy tài khoản'}, 404
        status = normalize_account_status(user.get('account_status'))
        if status != 'pending_delete':
            return {'error': 'Chỉ duyệt xóa được tài khoản đang ở trạng thái chờ xóa.'}, 400
        now = datetime.utcnow()
        cursor.execute(
            """
            UPDATE users SET account_status = 'deleted', deleted_at = %s
            WHERE id = %s AND account_status = 'pending_delete'
            """,
            (now, user_id),
        )
        cursor.execute(
            'DELETE FROM auth_otp_sessions WHERE purpose IN (%s, %s) AND user_id = %s',
            (PURPOSE_DELETE, PURPOSE_RESTORE, user_id),
        )
        db.commit()
        return {'message': 'Đã duyệt xóa tài khoản (vô hiệu hóa).', 'account_status': 'deleted'}, 200
    except DatabaseError as exc:
        return {'error': 'Lỗi CSDL', 'detail': str(exc)}, 400
    finally:
        cursor.close()
        dcur.close()
        db.close()
