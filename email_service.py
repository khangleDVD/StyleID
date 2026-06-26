"""Gửi email qua SMTP (Gmail App Password)."""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import Config


class EmailSendError(Exception):
    """Không gửi được email."""


def _smtp_configured() -> bool:
    return bool(
        (Config.SMTP_HOST or '').strip()
        and (Config.SMTP_USER or '').strip()
        and (Config.SMTP_PASSWORD or '').strip()
    )


def send_email(to_email: str, subject: str, body_text: str, body_html: str | None = None) -> None:
    if not _smtp_configured():
        raise EmailSendError('Chưa cấu hình SMTP (SMTP_HOST, SMTP_USER, SMTP_PASSWORD).')

    to_email = (to_email or '').strip()
    if not to_email:
        raise EmailSendError('Thiếu địa chỉ người nhận.')

    from_addr = (Config.SMTP_FROM or Config.SMTP_USER or '').strip()
    from_name = (Config.SMTP_FROM_NAME or Config.APP_DISPLAY_NAME or 'StyleID').strip()
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f'{from_name} <{from_addr}>'
    msg['To'] = to_email
    msg.attach(MIMEText(body_text, 'plain', 'utf-8'))
    if body_html:
        msg.attach(MIMEText(body_html, 'html', 'utf-8'))

    try:
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=Config.SMTP_TIMEOUT) as server:
            if Config.SMTP_USE_TLS:
                server.starttls()
            server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            server.sendmail(from_addr, [to_email], msg.as_string())
    except Exception as exc:
        raise EmailSendError('Không gửi được email. Vui lòng thử lại sau.') from exc
