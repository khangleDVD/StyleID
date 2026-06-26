"""
Chuẩn hóa tên món trong data/fashion_style_dataset.json:
viết hoa chữ cái đầu mỗi từ (mỗi cụm chữ liên tiếp), phần còn lowercase.

Chạy: python scripts/normalize_item_capitalization.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "fashion_style_dataset.json"

# Chữ Latin + tiếng Việt có dấu; cho phép apostrophe trong từ (Chef's).
ITEM_WORD_RE = re.compile(r"[A-Za-z\u00c0-\u024f\u1ea0-\u1ef9]+(?:'[a-z\u00e0-\u024f\u1ea0-\u1ef9]+)?", re.UNICODE)


def capitalize_item_label(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s

    def repl(m: re.Match[str]) -> str:
        w = m.group(0)
        return w[0].upper() + w[1:].lower()

    return ITEM_WORD_RE.sub(repl, s)


def normalize_item_style_rules_keys(data: dict) -> tuple[int, int]:
    """Canonicalize keys in item_style_rules; merge lists if two keys collide."""
    rules = data.get("item_style_rules")
    if not isinstance(rules, dict):
        return (0, 0)
    new_rules: dict = {}
    changed_keys = 0
    merged = 0
    for k, v in rules.items():
        if not isinstance(k, str):
            continue
        nk = capitalize_item_label(k)
        if nk != k.strip():
            changed_keys += 1
        if nk in new_rules:
            merged += 1
            prev = new_rules[nk]
            if isinstance(prev, list) and isinstance(v, list):
                new_rules[nk] = list(dict.fromkeys(prev + v))
            elif isinstance(v, list):
                new_rules[nk] = v
        else:
            new_rules[nk] = v
    data["item_style_rules"] = new_rules
    return (changed_keys, merged)


def main() -> None:
    raw = DATA_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    items = data.get("items")
    if not isinstance(items, dict):
        print("missing valid key items")
        return

    changed = 0
    total = 0
    for _cat, lst in items.items():
        if not isinstance(lst, list):
            continue
        new_lst: list = []
        for x in lst:
            total += 1
            if isinstance(x, str):
                ns = capitalize_item_label(x)
                if ns != x:
                    changed += 1
                new_lst.append(ns)
            else:
                new_lst.append(x)
        items[_cat] = new_lst

    rk, merged = normalize_item_style_rules_keys(data)

    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {DATA_PATH}")
    print(f"items_strings={total} items_changed={changed}")
    print(f"item_style_rules keys renamed={rk} merges={merged}")


if __name__ == "__main__":
    main()
