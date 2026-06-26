"""OTP đăng ký / quên mật khẩu — tạo, lưu hash, giới hạn thử lại."""
from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

GMAIL_RE = re.compile(r'^[a-zA-Z0-9](?:[a-zA-Z0-9._+-]*[a-zA-Z0-9])?@gmail\.com$', re.IGNORECASE)

OTP_REGISTER_TTL_MINUTES = 5
OTP_FORGOT_TTL_MINUTES = 10
OTP_DELETE_TTL_MINUTES = 5
OTP_RESTORE_TTL_MINUTES = 5
OTP_MAX_WRONG = 5
OTP_MAX_RESEND = 3
OTP_RESEND_WINDOW_MINUTES = 10


def is_gmail_address(email: str) -> bool:
    return bool(GMAIL_RE.match((email or '').strip()))


def generate_otp_code() -> str:
    return f'{secrets.randbelow(1_000_000):06d}'


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_value(value: str) -> str:
    return generate_password_hash(value)


def verify_hashed_value(value: str, stored_hash: str) -> bool:
    return check_password_hash(stored_hash, value)


def generate_temp_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + '!@#$%&*'
    while True:
        pwd = ''.join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in pwd) and any(c.isupper() for c in pwd) and any(c.isdigit() for c in pwd):
            return pwd


def utcnow() -> datetime:
    return datetime.utcnow()


def expires_at(minutes: int) -> datetime:
    return utcnow() + timedelta(minutes=minutes)


def is_expired(expires: datetime | None) -> bool:
    if not expires:
        return True
    if isinstance(expires, str):
        try:
            expires = datetime.fromisoformat(expires.replace('Z', ''))
        except ValueError:
            return True
    return utcnow() > expires


def can_resend(resend_count: int, window_start: datetime | None) -> tuple[bool, int, datetime | None]:
    now = utcnow()
    if window_start and isinstance(window_start, str):
        try:
            window_start = datetime.fromisoformat(window_start.replace('Z', ''))
        except ValueError:
            window_start = None
    if not window_start or (now - window_start) > timedelta(minutes=OTP_RESEND_WINDOW_MINUTES):
        return True, 0, now
    if resend_count >= OTP_MAX_RESEND:
        return False, resend_count, window_start
    return True, resend_count, window_start
