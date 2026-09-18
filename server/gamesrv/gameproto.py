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

# 和 session.DH_IDENTITY 同一个值（dhSecret 的单位元），这里复制一份避免循环 import
DH_IDENTITY = bytes.fromhex("0100000000000000")

# 客户端认可的成功码
CODE_OK = 200


# 客户端真实请求体 = base64(" " + sessionId) + base64(des(payload))
#
# server.js 的 reqPack 会在密文前面拼一段 session 字段：
#   " " + 32 位十六进制 session id（共 33 字节）→ 正好编成 44 个 base64 字符、没有 '='。
# 早先探针是用自己重发的请求（没这段），所以服务端一直没见过它；
# 探针改成「包一层」之后暴露出真实格式，必须先把这段摘掉再解密，
# 否则 DES 解出来是乱码 → unpack_request 返回 None → 客户端弹
# 「温馨提示 {"code":1,"msg":"bad request"}」。
SESSION_FIELD_LEN = 44


def split_session_field(text: str):
    """返回 (sessionId, 剩下的密文)，没有 session 前缀时返回 (None, 原文)。"""
    if len(text) < SESSION_FIELD_LEN:
        return None, text
    head, rest = text[:SESSION_FIELD_LEN], text[SESSION_FIELD_LEN:]
    try:
        decoded = base64.b64decode(head, validate=True)
    except Exception:  # noqa: BLE001
        return None, text
    if len(decoded) != 33 or decoded[:1] != b" ":
        return None, text
    sid = decoded[1:].decode("ascii", "ignore")
    if len(sid) != 32 or any(c not in "0123456789abcdefABCDEF" for c in sid):
        return None, text
    return sid.lower(), rest


def unpack_request(body: bytes, secret: bytes):
    """解析客户端请求体，返回 (route, msg, reqId) 或 None。

    容错：带不带 session 前缀都认（`split_session_field` 先摘）。
    """
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return None
    _, text = split_session_field(text)
    if not text:
        return None
    try:
        raw = base64.b64decode(text)
    except Exception:  # noqa: BLE001
        return None
    for key in (secret, DH_IDENTITY):
        try:
            plain = des_decode(key, raw)
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
