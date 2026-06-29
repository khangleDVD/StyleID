-- ============================================================
-- StyleID — SQLite schema đầy đủ
-- Tham khảo / cài thủ công. App với DB_ENGINE=sqlite tự tạo schema
-- tương đương qua db.init_database() → data/styleid.db
-- ============================================================

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- users
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- auth_otp_sessions
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- history
-- ------------------------------------------------------------
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

-- ------------------------------------------------------------
-- payments
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  package_key TEXT NOT NULL,
  credits INTEGER NOT NULL,
  amount_vnd INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  sepay_tx_id TEXT NULL,
  reconcile_token TEXT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  expires_at TEXT NULL,
  completed_at TEXT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE ON UPDATE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_payments_user ON payments (user_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments (status);
CREATE INDEX IF NOT EXISTS idx_payments_expires ON payments (expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_reconcile_token ON payments (reconcile_token);

-- app_settings — cấu hình admin (Vercel / production)
CREATE TABLE IF NOT EXISTS app_settings (
  setting_key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
