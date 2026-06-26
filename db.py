"""
Database layer — MySQL (production) hoặc SQLite (local, không cần XAMPP).

Cấu hình .env:
  DB_ENGINE=sqlite          # mặc định mysql nếu không đặt
  SQLITE_PATH=data/styleid.db
"""
from __future__ import annotations

import os
import sqlite3

from config import Config

try:
    import mysql.connector
    from mysql.connector import Error as _MySQLError
    from mysql.connector import IntegrityError as _MySQLIntegrityError
except Exception:  # pragma: no cover
    mysql = None
    _MySQLError = Exception
    _MySQLIntegrityError = Exception


class DatabaseError(Exception):
    """Lỗi CSDL chung (MySQL hoặc SQLite)."""


class IntegrityError(DatabaseError):
    """Vi phạm UNIQUE / FK."""


def is_sqlite() -> bool:
    return (getattr(Config, 'DB_ENGINE', 'mysql') or 'mysql').strip().lower() == 'sqlite'


def _sqlite_path() -> str:
    raw = (getattr(Config, 'SQLITE_PATH', '') or 'data/styleid.db').strip()
    if os.path.isabs(raw):
        return raw
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, raw)


def _adapt_sql(sql: str) -> str:
    if not is_sqlite():
        return sql
    s = sql
    s = s.replace('DATE_SUB(NOW(), INTERVAL 7 DAY)', "datetime('now', '-7 days')")
    s = s.replace('DATE_SUB(NOW(), INTERVAL 14 DAY)', "datetime('now', '-14 days')")
    s = s.replace('DATE_SUB(NOW(), INTERVAL 30 DAY)', "datetime('now', '-30 days')")
    s = s.replace('DATE_SUB(NOW(), INTERVAL 60 DAY)', "datetime('now', '-60 days')")
    s = s.replace('CAST(p.id AS CHAR)', 'CAST(p.id AS TEXT)')
    s = s.replace('%s', '?')
    return s


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password TEXT NULL,
  full_name TEXT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  account_status TEXT NOT NULL DEFAULT 'active',
  delete_requested_at TEXT NULL,
  delete_scheduled_at TEXT NULL,
  delete_reason TEXT NULL,
  delete_cancelled_at TEXT NULL,
  deleted_at TEXT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  google_id TEXT NULL UNIQUE,
  email TEXT NULL,
  email_verified INTEGER NOT NULL DEFAULT 0,
  force_change_password INTEGER NOT NULL DEFAULT 0,
  analysis_credits INTEGER NOT NULL DEFAULT 5,
  avatar_path TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_account_status ON users (account_status);
CREATE INDEX IF NOT EXISTS idx_users_delete_scheduled ON users (delete_scheduled_at);

CREATE TABLE IF NOT EXISTS auth_otp_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_token TEXT NOT NULL UNIQUE,
  purpose TEXT NOT NULL,
  email TEXT NOT NULL,
  username TEXT NULL,
  full_name TEXT NULL,
  password_hash TEXT NULL,
  user_id INTEGER NULL,
  otp_hash TEXT NOT NULL,
  wrong_attempts INTEGER NOT NULL DEFAULT 0,
  resend_count INTEGER NOT NULL DEFAULT 0,
  resend_window_start TEXT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_auth_otp_token ON auth_otp_sessions (session_token);
CREATE INDEX IF NOT EXISTS idx_auth_otp_email ON auth_otp_sessions (email);

CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NULL,
  image_path TEXT NULL,
  analysis_result TEXT NULL,
  timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_history_user ON history (user_id);
CREATE INDEX IF NOT EXISTS idx_history_timestamp ON history (timestamp);

CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  package_key TEXT NOT NULL,
  credits INTEGER NOT NULL,
  amount_vnd INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  sepay_tx_id TEXT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT NULL,
  completed_at TEXT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_payments_user ON payments (user_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments (status);
CREATE INDEX IF NOT EXISTS idx_payments_expires ON payments (expires_at);
"""


class CursorWrapper:
    def __init__(self, cursor, as_dict: bool):
        self._cursor = cursor
        self._as_dict = as_dict

    def execute(self, sql, params=None):
        return self._cursor.execute(_adapt_sql(sql), params or ())

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if not self._as_dict:
            return row
        if hasattr(row, 'keys'):
            return dict(row)
        cols = [d[0] for d in (self._cursor.description or [])]
        return dict(zip(cols, row))

    def fetchall(self):
        rows = self._cursor.fetchall()
        if not self._as_dict:
            return rows
        out = []
        for row in rows:
            if hasattr(row, 'keys'):
                out.append(dict(row))
            else:
                cols = [d[0] for d in (self._cursor.description or [])]
                out.append(dict(zip(cols, row)))
        return out

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return getattr(self._cursor, 'rowcount', -1)

    def close(self):
        self._cursor.close()


def get_db():
    if is_sqlite():
        path = _sqlite_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        return conn
    if mysql is None:
        raise DatabaseError('mysql-connector-python chưa cài. Chạy: pip install mysql-connector-python')
    try:
        return mysql.connector.connect(
            host=Config.MYSQL_HOST,
            user=Config.MYSQL_USER,
            password=Config.MYSQL_PASSWORD,
            database=Config.MYSQL_DATABASE,
        )
    except _MySQLError as e:
        raise DatabaseError(str(e)) from e


def dict_cursor(conn):
    if is_sqlite():
        return CursorWrapper(conn.cursor(), as_dict=True)
    return CursorWrapper(conn.cursor(dictionary=True), as_dict=True)


def plain_cursor(conn):
    return CursorWrapper(conn.cursor(), as_dict=False)


def _init_sqlite(conn):
    conn.executescript(_SQLITE_SCHEMA)
    conn.commit()


def _ensure_mysql_payments_table(conn):
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                package_key VARCHAR(32) NOT NULL,
                credits INT NOT NULL,
                amount_vnd INT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                sepay_tx_id VARCHAR(128) NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                expires_at DATETIME NULL,
                completed_at DATETIME NULL,
                INDEX idx_payments_user (user_id),
                INDEX idx_payments_status (status),
                INDEX idx_payments_expires (expires_at)
            )
            """
        )
        conn.commit()
    finally:
        cur.close()


def _mysql_column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s
        LIMIT 1
        """,
        (table, column),
    )
    return cur.fetchone() is not None


def _sqlite_column_exists(cur, table: str, column: str) -> bool:
    cur.execute(f'PRAGMA table_info({table})')
    return any(row[1] == column for row in cur.fetchall())


def _ensure_sqlite_avatar_column(conn):
    cur = conn.cursor()
    try:
        if not _sqlite_column_exists(cur, 'users', 'avatar_path'):
            cur.execute('ALTER TABLE users ADD COLUMN avatar_path TEXT NULL')
        conn.commit()
    finally:
        cur.close()


def _ensure_account_deletion_schema(conn):
    cur = conn.cursor()
    try:
        if is_sqlite():
            cols = [
                ('account_status', "TEXT NOT NULL DEFAULT 'active'"),
                ('delete_requested_at', 'TEXT NULL'),
                ('delete_scheduled_at', 'TEXT NULL'),
                ('delete_reason', 'TEXT NULL'),
                ('delete_cancelled_at', 'TEXT NULL'),
                ('deleted_at', 'TEXT NULL'),
            ]
            for name, definition in cols:
                if not _sqlite_column_exists(cur, 'users', name):
                    cur.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
        else:
            cols = [
                ('account_status', "VARCHAR(20) NOT NULL DEFAULT 'active'"),
                ('delete_requested_at', 'DATETIME NULL'),
                ('delete_scheduled_at', 'DATETIME NULL'),
                ('delete_reason', 'TEXT NULL'),
                ('delete_cancelled_at', 'DATETIME NULL'),
                ('deleted_at', 'DATETIME NULL'),
            ]
            for name, definition in cols:
                if not _mysql_column_exists(cur, 'users', name):
                    cur.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
        conn.commit()
    finally:
        cur.close()


def _ensure_mysql_auth_schema(conn):
    cur = conn.cursor()
    try:
        cols = [
            ('email_verified', 'TINYINT NOT NULL DEFAULT 0'),
            ('force_change_password', 'TINYINT NOT NULL DEFAULT 0'),
            ('avatar_path', 'VARCHAR(255) NULL'),
        ]
        for name, definition in cols:
            if not _mysql_column_exists(cur, 'users', name):
                cur.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_otp_sessions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                session_token VARCHAR(64) NOT NULL UNIQUE,
                purpose VARCHAR(32) NOT NULL,
                email VARCHAR(255) NOT NULL,
                username VARCHAR(50) NULL,
                full_name VARCHAR(100) NULL,
                password_hash VARCHAR(255) NULL,
                user_id INT NULL,
                otp_hash VARCHAR(255) NOT NULL,
                wrong_attempts INT NOT NULL DEFAULT 0,
                resend_count INT NOT NULL DEFAULT 0,
                resend_window_start DATETIME NULL,
                expires_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_auth_otp_token (session_token),
                INDEX idx_auth_otp_email (email)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
        cur.execute(
            """
            UPDATE users SET email_verified = 1
            WHERE email_verified = 0 AND (password IS NOT NULL OR google_id IS NOT NULL)
            """
        )
        conn.commit()
    finally:
        cur.close()


def init_database():
    """Khởi tạo schema (SQLite) hoặc đảm bảo bảng payments + auth (MySQL)."""
    conn = get_db()
    try:
        if is_sqlite():
            _init_sqlite(conn)
            _ensure_sqlite_avatar_column(conn)
            _ensure_account_deletion_schema(conn)
            cur = conn.cursor()
            try:
                cur.execute(
                    """
                    UPDATE users SET email_verified = 1
                    WHERE email_verified = 0 AND (password IS NOT NULL OR google_id IS NOT NULL)
                    """
                )
                conn.commit()
            finally:
                cur.close()
        else:
            _ensure_mysql_payments_table(conn)
            _ensure_mysql_auth_schema(conn)
            _ensure_account_deletion_schema(conn)
    finally:
        conn.close()


def map_db_error(exc: Exception):
    if isinstance(exc, _MySQLIntegrityError):
        return IntegrityError(str(exc))
    if isinstance(exc, sqlite3.IntegrityError):
        return IntegrityError(str(exc))
    if isinstance(exc, _MySQLError):
        return DatabaseError(str(exc))
    if isinstance(exc, sqlite3.Error):
        return DatabaseError(str(exc))
    return exc
