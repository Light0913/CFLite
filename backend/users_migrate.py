"""users.json 迁移：为旧格式（无 id / 无 next_id）补充 id 字段。"""
from __future__ import annotations

import json
import pathlib


def migrate_if_needed(path: pathlib.Path) -> None:
    if not path.is_file():
        return
    data = json.loads(path.read_text("utf-8"))
    users = data.get("users", [])
    has_next = "next_id" in data and isinstance(data.get("next_id"), int)
    all_have_id = all(isinstance(u.get("id"), int) and u.get("id", 0) >= 1 for u in users)
    if has_next and all_have_id:
        return

    backup = path.with_suffix(".json.bak")
    backup.write_text(path.read_text("utf-8"), encoding="utf-8")

    # 按 username 排序后分配 id（稳定、可复现）
    users = sorted(users, key=lambda u: u.get("username", ""))
    seen_ids = set()
    for i, u in enumerate(users, start=1):
        if not isinstance(u.get("id"), int) or u.get("id", 0) < 1 or u["id"] in seen_ids:
            # 重新分配 id，避免重复
            while i in seen_ids:
                i += 1
            u["id"] = i
        seen_ids.add(u["id"])
    new_data = {"next_id": (max(seen_ids, default=0) + 1), "users": users}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new_data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
