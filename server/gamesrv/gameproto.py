"""游戏业务协议（HTTP POST 路由）。

从客户端 `src/util/server.js` 的 reqPack / resUnpack 字节码原子表还原：

    reqPack(route, msg, reqId):
        res  = crypt.utf16To8(JSON.stringify({route: route, msg: msg, reqId: reqId}))
        data = crypt.desEncode(key, res)
        return crypt.base64Encode(data)

    resUnpack(data, key):
        if data.indexOf('"code":') >= 0:      # 明文错误包
            return JSON.parse(data)
        return JSON.parse(crypt.utf8To16(crypt.desDecode(key, crypt.base64Decode(data))))

其中 key 就是登录时 DH 协商出来的共享密钥（本模拟服固定为 DH 单位元）。

**业务成功码是 200，不是 0！**
反汇编 `dataManager.playerLogin/</<` 可以看到：

    var code = data.code;
    if (code === 200) cb4AfterLogin(null, data.data);
    else              cb4AfterLogin(data, null);      // 会把响应当错误弹「温馨提示」

`Player.updateGuideMark` 的回调同样判断 `data.code == 200`，不是 200 就会无限重发。
所以所有业务路由都必须返回 `code: 200`。
"""

from __future__ import annotations

import base64
import json
import time

from . import logx
from .crypto.des import des_decode, des_encode

log = logx.get("gameproto")

# 客户端认可的成功码
CODE_OK = 200


def unpack_request(body: bytes, secret: bytes):
    """解析客户端请求体，返回 (route, msg, reqId) 或 None。"""
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return None
    try:
        raw = base64.b64decode(text)
    except Exception:  # noqa: BLE001
        return None
    for key in (secret, None):
        try:
            plain = des_decode(key if key is not None else secret, raw)
            payload = json.loads(plain.decode("utf-8"))
            return payload
        except Exception:  # noqa: BLE001
            continue
    return None


def pack_response(payload: dict, secret: bytes) -> bytes:
    """把响应打包成客户端能解的形式。"""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(des_encode(secret, body))


def pack_error(code: int, msg: str) -> bytes:
    """明文错误包：客户端看到 '"code":' 会直接当 JSON 解析。"""
    return json.dumps({"code": code, "msg": msg}, ensure_ascii=False).encode("utf-8")


def ok(data: dict | None = None, **extra) -> dict:
    payload = {"code": CODE_OK, "msg": "", "data": data or {}}
    payload.update(extra)
    return payload


def now_ms() -> int:
    return int(time.time() * 1000)
