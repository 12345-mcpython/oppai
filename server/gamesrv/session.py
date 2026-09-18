"""WebSocket 登录会话。

协议（用运行时探针 + 把客户端本身当成 oracle 反推出来的）：

    S -> C   base64(欢迎包)                          触发客户端开始握手
    C -> S   base64(clientKey)                       clientKey = dhExchange(randomKey())
    S -> C   base64(serverKey)                       服务端 DH 公钥
    C -> S   base64(hmac64(hashKey(seed#clientKey), secret))
    C -> S   base64(desEncode(secret, loginInfoJson))
    S -> C   base64(desEncode(secret, {code,data:{session,gameServUrl}}))

之后所有业务走 HTTP POST 到 gameServUrl。

关于 DH：客户端的 crypt.dhExchange/dhSecret 是自研实现，直接用 Python 复刻代价很高。
但实测它满足正常 DH 群性质，并且存在一个「单位元」：

    X = dhExchange(0x0000000000000000) = 01 00 00 00 00 00 00 00
    dhSecret(X, *) == dhSecret(*, 0) == X

所以服务端把自己的公钥设成 X，双方共享密钥必然等于 X —— 经典 small-subgroup 思路，
对单机模拟服完全够用，而且不需要知道 p / g。

DES 部分已经用标准实现复刻并对着客户端密文验证过（见 gamesrv/crypto/des.py）。
"""

from __future__ import annotations

import base64
import json
import time
import uuid

from . import config, logx
from .crypto.des import des_decode, des_encode
from .gameproto import CODE_OK

log = logx.get("session")

# dhExchange(0) 的结果，同时也是 dhSecret 的单位元
DH_IDENTITY = bytes.fromhex("0100000000000000")

# 已建立的登录会话： session -> 会话数据
_sessions: dict[str, dict] = {}


def get_session(session_id: str):
    return _sessions.get(session_id)


def create_session(info: dict) -> tuple[str, dict]:
    session_id = uuid.uuid4().hex
    data = {
        "session": session_id,
        "createTime": int(time.time()),
        "info": info,
        "secret": DH_IDENTITY,
    }
    _sessions[session_id] = data
    return session_id, data


def on_ws_connect(ws, req):
    log.info("WS 会话开始 path=%s query=%s", req.path, req.query)
    state = {"step": 0}

    # 1) 先发欢迎包，客户端 onmessage 才会开始握手
    greeting = base64.b64encode(b"{}").decode()
    ws.send_text(greeting)
    log.info("WS -> 欢迎包 %s", greeting)
    logx.capture("ws-out", {"step": 0, "data": greeting})

    while not ws.closed:
        try:
            opcode, payload = ws.recv_frame()
        except ConnectionError:
            break
        except Exception as exc:  # noqa: BLE001
            log.debug("recv error: %s", exc)
            break
        if payload is None:
            break

        text = payload.decode("utf-8", "replace")
        state["step"] += 1
        log.info("WS <- #%d len=%d %.160s", state["step"], len(text), text)
        logx.capture("ws-in", {"step": state["step"], "path": req.path, "data": text[:40000]})

        reply = _handle(state, text)
        if reply is not None:
            ws.send_text(reply)
            log.info("WS -> #%d len=%d %.160s", state["step"], len(reply), reply)
            logx.capture("ws-out", {"step": state["step"], "data": reply[:40000]})

    log.info("WS 会话结束")


def _handle(state: dict, text: str):
    step = state["step"]

    if step == 1:
        # 客户端公钥。服务端公钥用单位元，共享密钥必然是 DH_IDENTITY。
        state["clientKey"] = text
        state["secret"] = DH_IDENTITY
        return base64.b64encode(DH_IDENTITY).decode()

    if step == 2:
        state["hmac"] = text
        return None

    if step == 3:
        return _finish_login(state, text)

    return None


def _finish_login(state: dict, text: str):
    secret = state["secret"]
    try:
        raw = base64.b64decode(text)
        plain = des_decode(secret, raw)
        info = json.loads(plain.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        log.warning("登录包解密失败: %s", exc)
        info = {}

    log.info("登录信息: %s", json.dumps(info, ensure_ascii=False)[:600])
    logx.capture("login-info", {"info": info})

    session_id, _ = create_session(info)

    # 成功响应形状（session / gameServUrl 顶层和 data 里各给一份，兼容两种读法）。
    #
    # ⚠️ code 必须是 200。反汇编 `User.login` 的回调：
    #
    #     if (result == undefined) { cb(table_dictionary[622]); return; }
    #     if (result.code !== 200) {
    #         cc.log('error on login, code :' + result.code);
    #         cb({code: 299, data: table_dictionary[623] + result.code}, result);
    #         return;
    #     }
    #
    # 回 0（或者压根不带 code）都会走错误分支 ——
    # 表现是：点「开始游戏」先弹一个「温馨提示」，然后才进游戏
    # （弹窗是这条链报的错，进游戏是探针那条链干完的活）。
    result = {
        "code": CODE_OK,
        "msg": "",
        "session": session_id,
        "gameServUrl": config.GAME_SERV_URL,
        "userId": config.DEFAULT_USER_ID,
        "data": {
            "session": session_id,
            "gameServUrl": config.GAME_SERV_URL,
            "userId": config.DEFAULT_USER_ID,
        },
    }
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(des_encode(secret, body)).decode()
