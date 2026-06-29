-- ============================================================
-- StyleID — MySQL schema đầy đủ (lumistyle_db)
-- Chạy một lần: mysql -u root -p < scripts/init_mysql.sql
-- ============================================================

SET NAMES utf8mb4;
SET CHARACTER SET utf8mb4;

CREATE DATABASE IF NOT EXISTS lumistyle_db
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE lumistyle_db;

-- ------------------------------------------------------------
-- users — tài khoản (đăng ký thường + Google OAuth)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50) NOT NULL UNIQUE,
  password VARCHAR(255) NULL COMMENT 'NULL = chỉ đăng nhập Google',
  full_name VARCHAR(100) NULL,
  role VARCHAR(20) NOT NULL DEFAULT 'user' COMMENT 'user | admin',
  account_status VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT 'active | pending_delete | deleted',
  delete_requested_at DATETIME NULL,
  delete_scheduled_at DATETIME NULL,
  delete_reason TEXT NULL,
  delete_cancelled_at DATETIME NULL,
  deleted_at DATETIME NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  google_id VARCHAR(255) NULL UNIQUE COMMENT 'Google OAuth sub',
  email VARCHAR(255) NULL,
  email_verified TINYINT NOT NULL DEFAULT 0,
  force_change_password TINYINT NOT NULL DEFAULT 0,
  analysis_credits INT NOT NULL DEFAULT 5 COMMENT 'Lượt phân tích còn lại',
  avatar_path VARCHAR(255) NULL COMMENT 'Tên file trong uploads/avatars/',
  INDEX idx_users_account_status (account_status),
  INDEX idx_users_delete_scheduled (delete_scheduled_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- auth_otp_sessions — OTP đăng ký / quên mật khẩu / xóa tài khoản
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auth_otp_sessions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  session_token VARCHAR(64) NOT NULL UNIQUE,
  purpose VARCHAR(32) NOT NULL COMMENT 'register | forgot_password | delete_account | restore_account',
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- history — lịch sử phân tích ảnh
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS history (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  image_path TEXT NULL COMMENT 'Tên file ảnh trong uploads/',
  analysis_result JSON NULL COMMENT 'Kết quả phân tích (JSON)',
  timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_history_user (user_id),
  INDEX idx_history_timestamp (timestamp),
  CONSTRAINT fk_history_user FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- payments — nạp gói / đối soát SePay
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS payments (
  id INT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  package_key VARCHAR(32) NOT NULL,
  credits INT NOT NULL,
  amount_vnd INT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT 'pending | completed | failed',
  sepay_tx_id VARCHAR(128) NULL,
  reconcile_token VARCHAR(32) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  expires_at DATETIME NULL,
  completed_at DATETIME NULL,
  INDEX idx_payments_user (user_id),
  INDEX idx_payments_status (status),
  INDEX idx_payments_expires (expires_at),
  UNIQUE INDEX idx_payments_reconcile_token (reconcile_token),
  CONSTRAINT fk_payments_user FOREIGN KEY (user_id) REFERENCES users(id)
    ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Tài khoản cũ có mật khẩu hoặc Google coi như đã xác thực email
UPDATE users SET email_verified = 1
WHERE email_verified = 0 AND (password IS NOT NULL OR google_id IS NOT NULL);

-- Gán admin (tuỳ chọn, sau khi đăng ký user đầu tiên):
-- UPDATE users SET role = 'admin' WHERE id = 1;

-- ------------------------------------------------------------
-- app_settings — cấu hình admin (Vercel / production)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
  setting_key VARCHAR(64) PRIMARY KEY,
  value_json LONGTEXT NOT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
