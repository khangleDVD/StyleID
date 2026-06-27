"""Gán role admin cho user theo id. Usage: python scripts/set_admin.py 1"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import get_db, plain_cursor


def main():
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    db = get_db()
    cur = plain_cursor(db)
    try:
        cur.execute("SELECT id, username, role FROM users WHERE id = %s", (uid,))
        row = cur.fetchone()
        if not row:
            print(f"[ERROR] Không tìm thấy user id={uid}")
            sys.exit(1)
        print(f"Before: id={row[0]}, username={row[1]}, role={row[2]}")
        cur.execute("UPDATE users SET role = 'admin' WHERE id = %s", (uid,))
        db.commit()
        cur.execute("SELECT id, username, role FROM users WHERE id = %s", (uid,))
        row = cur.fetchone()
        print(f"After:  id={row[0]}, username={row[1]}, role={row[2]}")
    finally:
        cur.close()
        db.close()


if __name__ == "__main__":
    main()
