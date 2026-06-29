"""Persistent app settings in DB (Vercel-safe; survives redeploy)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from db import DatabaseError, dict_cursor, get_db, is_sqlite


def get_app_setting(key: str):
    key = (key or '').strip()
    if not key:
        return None
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute('SELECT value_json FROM app_settings WHERE setting_key = %s', (key,))
        row = cur.fetchone()
        if not row:
            return None
        raw = row.get('value_json') if isinstance(row, dict) else row[0]
        if raw is None or raw == '':
            return None
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, DatabaseError) as e:
        print(f'[Settings] get {key!r}:', e)
        return None
    finally:
        cur.close()
        db.close()


def set_app_setting(key: str, value) -> None:
    key = (key or '').strip()
    if not key:
        raise ValueError('setting_key required')
    payload = json.dumps(value, ensure_ascii=False)
    db = get_db()
    cur = dict_cursor(db)
    try:
        if is_sqlite():
            cur.execute(
                'INSERT OR REPLACE INTO app_settings (setting_key, value_json, updated_at) '
                'VALUES (%s, %s, %s)',
                (key, payload, datetime.now(timezone.utc).isoformat()),
            )
        else:
            cur.execute(
                'INSERT INTO app_settings (setting_key, value_json) VALUES (%s, %s) '
                'ON DUPLICATE KEY UPDATE value_json = VALUES(value_json), updated_at = CURRENT_TIMESTAMP',
                (key, payload),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
        db.close()


def delete_app_setting(key: str) -> None:
    key = (key or '').strip()
    if not key:
        return
    db = get_db()
    cur = dict_cursor(db)
    try:
        cur.execute('DELETE FROM app_settings WHERE setting_key = %s', (key,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        cur.close()
        db.close()
