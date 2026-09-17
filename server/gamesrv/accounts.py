"""账号存储。

客户端走的是自建账号体系（server.oauth('request_token'|'register')），
这里就用一个 JSON 文件存着，够单机模拟服用。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

from . import config, logx

log = logx.get("accounts")

_path = os.path.join(config.DATA_DIR, "accounts.json")
_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(_path):
        return {}
    try:
        with open(_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        log.exception("账号文件损坏，重建")
        return {}


def _save(data: dict) -> None:
    tmp = _path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _path)


def request_token(account: str, password: str) -> dict:
    """登录：账号不存在时自动注册（模拟服常见做法），返回 token。"""
    with _lock:
        db = _load()
        user = db.get(account)
        if user is None:
            user = {
                "account": account,
                "password": password,
                "userId": len(db) + 1,
                "token": uuid.uuid4().hex,
                "createTime": int(time.time()),
            }
            db[account] = user
            _save(db)
            log.info("自动注册账号 %s (userId=%s)", account, user["userId"])
        elif user.get("password") and password and user["password"] != password:
            return {"code": 1, "msg": "密码错误"}
        elif not user.get("token"):
            user["token"] = uuid.uuid4().hex
            _save(db)
        return {"code": 0, "msg": "", "data": dict(user)}


def register(account: str, password: str, active_code: str = "") -> dict:
    with _lock:
        db = _load()
        if account in db:
            return {"code": 2, "msg": "账号已存在"}
        user = {
            "account": account,
            "password": password,
            "activeCode": active_code,
            "userId": len(db) + 1,
            "token": uuid.uuid4().hex,
            "createTime": int(time.time()),
        }
        db[account] = user
        _save(db)
        log.info("注册账号 %s (userId=%s)", account, user["userId"])
        return {"code": 0, "msg": "", "data": dict(user)}


def get_by_token(token: str):
    with _lock:
        for user in _load().values():
            if user.get("token") == token:
                return dict(user)
    return None


def all_accounts() -> dict:
    with _lock:
        return _load()
