"""用户存储：users.json（id + username + SHA256）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import re
import threading

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TYPES = ("default", "admin", "banned")


def sha256_hex(p: str) -> str:
    return hashlib.sha256(p.encode("utf-8")).hexdigest()


class UserStore:
    def __init__(self, path: pathlib.Path):
        self.path = path
        self._users: dict[int, dict] = {}
        self._by_name: dict[str, int] = {}
        self._next_id = 1
        self._lock = threading.Lock()
        from .users_migrate import migrate_if_needed
        migrate_if_needed(path)
        self.reload()

    # ---------- load / validate ----------
    def reload(self) -> None:
        data = json.loads(self.path.read_text("utf-8"))
        users, by_name = {}, {}
        for u in data.get("users", []):
            uid = u.get("id")
            name = u.get("username")
            if not isinstance(uid, int) or uid < 1:
                raise ValueError(f"非法用户 id: {uid!r}")
            if uid in users:
                raise ValueError(f"id 重复: {uid}")
            if not isinstance(name, str) or not USERNAME_RE.match(name):
                raise ValueError(f"非法用户名: {name!r}")
            if name in by_name:
                raise ValueError(f"用户名重复: {name}")
            if u.get("type") not in TYPES:
                raise ValueError(f"未知用户类型: {u.get('type')!r}")
            pw = u.get("password_sha256", "")
            if not SHA256_RE.match(pw):
                raise ValueError(f"密码哈希格式错误（用户 {name}）")
            users[uid] = {"id": uid, "username": name,
                          "password_sha256": pw, "type": u["type"]}
            by_name[name] = uid
        if not any(u["type"] == "admin" for u in users.values()):
            raise ValueError("至少需要一个 admin 用户")
        self._users, self._by_name = users, by_name
        self._next_id = data.get("next_id", (max(users.keys(), default=0) + 1))
        if self._next_id <= max(users.keys(), default=0):
            self._next_id = max(users.keys(), default=0) + 1

    # ---------- read ----------
    def get(self, uid: int) -> dict | None:
        return self._users.get(uid)

    def get_by_name(self, name: str) -> dict | None:
        uid = self._by_name.get(name)
        return self._users.get(uid) if uid else None

    def all(self) -> list[dict]:
        return list(self._users.values())

    def is_admin(self, uid) -> bool:
        return self._users.get(uid, {}).get("type") == "admin"

    def is_banned(self, uid) -> bool:
        return self._users.get(uid, {}).get("type") == "banned"

    def authenticate(self, name: str, password: str) -> dict | None:
        u = self.get_by_name(name)
        if not u:
            return None
        if hmac.compare_digest(u["password_sha256"].encode("ascii"),
                               sha256_hex(password).encode("ascii")):
            return u
        return None

    # ---------- mutate ----------
    def create(self, username: str, password: str, type_: str = "default",
               min_len: int = 6, max_len: int = 64) -> dict:
        with self._lock:
            if username in self._by_name:
                raise ValueError("用户名已存在")
            if not USERNAME_RE.match(username):
                raise ValueError("用户名只能包含字母数字 _ -，长度 1-32")
            if type_ not in TYPES:
                raise ValueError("未知用户类型")
            if not (min_len <= len(password) <= max_len):
                raise ValueError(f"密码长度 {min_len}-{max_len}")
            uid = self._next_id
            self._next_id += 1
            self._users[uid] = {"id": uid, "username": username,
                                "password_sha256": sha256_hex(password), "type": type_}
            self._by_name[username] = uid
            self._flush()
            return dict(self._users[uid])

    def update_username(self, uid: int, new_name: str, current_pwd: str) -> None:
        with self._lock:
            u = self._users.get(uid)
            if not u:
                raise KeyError(uid)
            if not hmac.compare_digest(u["password_sha256"].encode(),
                                       sha256_hex(current_pwd).encode()):
                raise PermissionError("当前密码错误")
            if not USERNAME_RE.match(new_name):
                raise ValueError("用户名只能包含字母数字 _ -，长度 1-32")
            if new_name == u["username"]:
                return
            if new_name in self._by_name:
                raise ValueError("用户名已被占用")
            del self._by_name[u["username"]]
            u["username"] = new_name
            self._by_name[new_name] = uid
            self._flush()

    def update_password(self, uid: int, current_pwd: str, new_pwd: str,
                        min_len: int = 6, max_len: int = 64) -> None:
        with self._lock:
            u = self._users.get(uid)
            if not u:
                raise KeyError(uid)
            if not hmac.compare_digest(u["password_sha256"].encode(),
                                       sha256_hex(current_pwd).encode()):
                raise PermissionError("当前密码错误")
            if not (min_len <= len(new_pwd) <= max_len):
                raise ValueError(f"新密码长度 {min_len}-{max_len}")
            u["password_sha256"] = sha256_hex(new_pwd)
            self._flush()

    def reset_password(self, uid: int, new_pwd: str,
                       min_len: int = 6, max_len: int = 64) -> None:
        """管理员重置密码，无需旧密码。"""
        with self._lock:
            u = self._users.get(uid)
            if not u:
                raise KeyError(uid)
            if not (min_len <= len(new_pwd) <= max_len):
                raise ValueError(f"新密码长度 {min_len}-{max_len}")
            u["password_sha256"] = sha256_hex(new_pwd)
            self._flush()

    def update_type(self, uid: int, type_: str) -> None:
        with self._lock:
            u = self._users.get(uid)
            if not u:
                raise KeyError(uid)
            if type_ not in TYPES:
                raise ValueError("未知用户类型")
            if u["type"] == "admin" and type_ != "admin":
                admins = [x for x in self._users.values() if x["type"] == "admin"]
                if len(admins) <= 1:
                    raise ValueError("不能移除最后一个 admin")
            u["type"] = type_
            self._flush()

    def delete(self, uid: int) -> None:
        with self._lock:
            u = self._users.get(uid)
            if not u:
                raise KeyError(uid)
            if u["type"] == "admin":
                admins = [x for x in self._users.values() if x["type"] == "admin"]
                if len(admins) <= 1:
                    raise ValueError("不能删除最后一个 admin")
            del self._users[uid]
            del self._by_name[u["username"]]
            self._flush()

    # ---------- persist ----------
    def _flush(self) -> None:
        data = {"next_id": self._next_id, "users": list(self._users.values())}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
