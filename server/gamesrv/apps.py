"""各端口的 HTTP 处理器。

四个角色：
  cdn   —— 原来 cdn.shuangmawei.net：远程配置、热更新清单、公告
  gate  —— 服务器状态查询（登录界面服务器列表的在线状态）
  login —— /oauth/xxx 以及 WebSocket 登录握手
  game  —— 业务路由（加密的 POST）
"""

from __future__ import annotations

import io
import json
import os
import time

from . import config, logx
from .httpd import Response

log = logx.get("apps")

# ---------------------------------------------------------------------------
# 公告页
#
# 客户端 NOTICE_URL 已经被重定向到本服（见 docs/protocol.md 的等长替换），
# 请求路径是 /<CLIENT_URL_PATH>/notice/index.html。
#
# 内容优先从 var/notice.html 读 —— 私服想改公告直接编辑那个文件、
# 重启服务端即可，不用动代码。文件不存在就用下面的默认文案。
# ---------------------------------------------------------------------------
_DEFAULT_NOTICE = (
    "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<style>body{margin:0;padding:24px;background:#f5f0e1;color:#5b4a2f;"
    "font-family:sans-serif;font-size:15px;line-height:1.9}"
    "h3{margin:0 0 12px}</style></head><body>"
    "<h3>战场双马尾 · 私服公告</h3>"
    "<p>本服为个人搭建的单机模拟服，仅用于客户端研究与存档。</p>"
    "<p>点击右上角关闭即可返回登录界面。</p>"
    "</body></html>"
)

_NOTICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var", "notice.html"
)


def notice_html() -> str:
    """每次请求都重新读，改完公告不用重启也能生效。"""
    try:
        if os.path.isfile(_NOTICE_PATH):
            with io.open(_NOTICE_PATH, encoding="utf-8") as fh:
                text = fh.read()
            if text.strip():
                return text
    except Exception as exc:                      # noqa: BLE001
        log.warning("读 %s 失败，用默认公告: %s", _NOTICE_PATH, exc)
    return _DEFAULT_NOTICE


# 兼容旧引用
NOTICE_HTML = _DEFAULT_NOTICE


# ---------------------------------------------------------------------------
# 远程配置
# ---------------------------------------------------------------------------

def _server_entry(index: int, name: str) -> dict:
    """服务器列表里的一项。

    客户端 server.js 会读取 host / gatePort / loginPort / logindKey 等字段，
    再拿 gate 返回的 gamedStatus 做在线状态展示。
    """
    return {
        "id": index,
        "name": name,
        "host": config.PUBLIC_HOST,
        "ip": config.PUBLIC_HOST,
        "gatePort": config.GATE_PORT,
        "loginPort": config.LOGIN_PORT,
        "port": config.GAME_PORT,
        "logindKey": "emulator",
        "status": 0,
        "gamedStatus": 0,
        "isNew": False,
        "isRecommend": index == 1,
        "recommend": index == 1,
        "playerInfo": {"name": "", "lv": 0},
    }


def remote_config() -> dict:
    """远程配置（原来由 cdn.shuangmawei.net/.../config/config.txt 提供）。

    这些键名是从客户端 ex/remoteconfig.js + src/patch/update.js 的实际读取行为
    里探测出来的（用 Proxy 记录属性访问 + Error().stack 定位调用点）：

      getRemoteConfig('version')[appVersion]  -> 本版本的更新配置
      getRemoteConfig('update')               -> {code, msg}，code != 0 就弹窗
      getRemoteConfig('servers')              -> 服务器列表

    注意 version 是 **以 appVersion 为键的字典**，不是字符串。
    """
    servers = [_server_entry(1, "S1 双马尾")]
    return {
        "version": {
            config.APP_VERSION: {
                "version": config.APP_VERSION,
                "appVersion": config.APP_VERSION,
                "patchVersion": config.PATCH_VERSION,
                "updateConfig": {
                    "version": config.PATCH_VERSION,
                    "maxFailCount": 3,
                },
            }
        },
        "update": {"code": 0, "msg": ""},
        "servers": servers,
        "versionConfig": {
            "version": config.APP_VERSION,
            "appVersion": config.APP_VERSION,
            "patchVersion": config.PATCH_VERSION,
        },
        "updateConfig": {
            "version": config.PATCH_VERSION,
            "maxFailCount": 3,
        },
        "table_dictionary": {
            "servers": servers,
            "tipsMap": {},
            "notice": "",
            "announcement": "",
        },
    }


def build_cdn(service):
    router = service.router

    @router.any(f"/{config.CLIENT_URL_PATH}/config/config.txt")
    def _config(req):
        log.info("远程配置请求")
        return Response(200, remote_config())

    @router.prefix(f"/{config.CLIENT_URL_PATH}/patch/version/")
    def _patch_version(req):
        # 热更新版本文件：让客户端认为已经是最新
        log.info("热更新版本查询: %s", req.path)
        if req.path.endswith("project.txt"):
            return Response(200, json.dumps({"version": config.APP_VERSION}))
        return Response(200, json.dumps({"version": config.APP_VERSION}))

    @router.prefix(f"/{config.CLIENT_URL_PATH}/patch/")
    def _patch_other(req):
        log.info("热更新其他请求: %s", req.path)
        return Response(200, {})

    @router.any(f"/{config.CLIENT_URL_PATH}/notice/index.html")
    def _notice(req):
        # 客户端启动后会用一个 WebView 弹「公告」，这里返回一张干净的页面。
        # 注意不能返回 404，否则 WebView 会显示 ERR_HTTP_RESPONSE_CODE_FAILURE。
        log.info("公告页请求")
        return Response(200, notice_html(), content_type="text/html; charset=utf-8")

    # ---------------- 客户端探针通道 ----------------
    @router.any("/hook/ping")
    def _hook_ping(req):
        log.info("探针自检 ping 收到")
        return Response(200, {"pong": True})

    @router.any("/hook/poll")
    def _hook_poll(req):
        from . import repl

        raw = req.q("last") or req.q("data") or "0"
        try:
            last = int(raw)
        except ValueError:
            last = 0
        return Response(200, repl.poll(last))

    @router.any("/hook/result")
    def _hook_result(req):
        from . import repl

        payload = req.payload() or {}
        repl.push_result(payload)
        log.info("REPL <- id=%s ok=%s value=%s", payload.get("id"), payload.get("ok"), str(payload.get("value"))[:600])
        return Response(200, {"ok": True})

    # ---------------- 宿主机控制接口 ----------------
    @router.any("/control/eval")
    def _control_eval(req):
        from . import repl

        payload = req.json() or {}
        code = payload.get("code", "")
        timeout = float(payload.get("timeout", 20.0))
        log.info("REPL -> %s", code)
        result = repl.submit(code, timeout=timeout)
        if result is None:
            return Response(504, {"ok": False, "value": "timeout"})
        return Response(200, result)

    @router.set_fallback
    def _catch(req):
        log.warning("CDN 未处理请求: %s %s", req.method, req.raw_path)
        return Response(200, {})

    return service


# ---------------------------------------------------------------------------
# 网关
# ---------------------------------------------------------------------------

def build_gate(service):
    router = service.router

    def _status_payload():
        """客户端 server.js 的 syncServStatus 会 GET
        http://<host>:<gatePort>/get_login?key=<logindKey>
        读 gamedStatus / loginPort / port；code != 0 视为失败。"""
        return {
            "code": 0,
            "msg": "",
            "gamedStatus": 0,
            "status": 0,
            "loginPort": config.LOGIN_PORT,
            "port": config.GAME_PORT,
            "loginIp": config.PUBLIC_HOST,
            "data": {
                "gamedStatus": 0,
                "loginPort": config.LOGIN_PORT,
                "port": config.GAME_PORT,
            },
        }

    @router.any("/get_login")
    def _get_login(req):
        log.info("网关状态查询 /get_login key=%s", req.q("key"))
        return Response(200, _status_payload())

    @router.any("/")
    def _status(req):
        log.info("网关状态查询 %s key=%s", req.path, req.q("key"))
        return Response(200, _status_payload())

    @router.set_fallback
    def _catch(req):
        log.warning("GATE 未处理请求: %s %s", req.method, req.raw_path)
        return Response(200, _status_payload())

    return service


# ---------------------------------------------------------------------------
# 登录
# ---------------------------------------------------------------------------

def build_login(service):
    router = service.router

    @router.prefix("/oauth")
    def _oauth(req):
        """客户端 server.oauth(routh, msg, cb) 会请求
        <OAUTH_HOST>/oauth/<routh>?key=value&...

        返回 {code, msg, data}，客户端只看 data.code 和 data.data。
        """
        from . import accounts

        routh = req.path[len("/oauth/"):] if req.path.startswith("/oauth/") else ""
        params = {k: v[0] for k, v in req.query.items()}
        # httpc.sendWebGetRequest 也可能把参数塞在 data= 里
        if "data" in params and len(params) == 1:
            for part in params["data"].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        log.info("oauth/%s 参数=%s", routh, params)

        account = params.get("account", "")
        password = params.get("password", "")
        active_code = params.get("active_code", "")

        if routh in ("request_token", "login"):
            result = accounts.request_token(account, password)
        elif routh == "register":
            result = accounts.register(account, password, active_code)
        else:
            result = {"code": 0, "msg": "", "data": {}}

        result.setdefault("routh", routh)
        return Response(200, result)

    @router.set_fallback
    def _catch(req):
        log.warning("LOGIN 未处理请求: %s %s", req.method, req.raw_path)
        return Response(200, {"code": 0, "msg": "", "data": {}})

    return service


# ---------------------------------------------------------------------------
# 游戏主逻辑
# ---------------------------------------------------------------------------

def build_game(service):
    router = service.router

    @router.prefix("/")
    def _game(req):
        from . import gameproto, handlers, session as session_mod

        logx.capture(
            "game-raw",
            {
                "path": req.raw_path,
                "headers": dict(req.headers),
                "body": req.text[:40000],
            },
        )

        # 会话：先按 data 参数里的 session 找，找不到就退化成默认会话
        sess = None
        sid = req.q("session") or (req.payload() or {}).get("session")
        if sid:
            sess = session_mod.get_session(sid)
        if sess is None:
            sess = {"session": sid or "-", "secret": session_mod.DH_IDENTITY, "info": {}}

        payload = gameproto.unpack_request(req.body, sess["secret"])
        if payload is None:
            log.warning("GAME 无法解包: %s", req.text[:300])
            return Response(200, gameproto.pack_error(1, "bad request"), content_type="text/plain")

        name = payload.get("route", "")
        msg = payload.get("msg") or {}
        req_id = payload.get("reqId")
        log.info("GAME route=%s reqId=%s msg=%s", name, req_id, json.dumps(msg, ensure_ascii=False)[:400])

        result, known = handlers.dispatch(name, sess, msg, req_id)
        body = gameproto.pack_response(result, sess["secret"])
        if not known:
            log.warning("GAME 未知 route，返回空 data：%s", name)
        return Response(200, body, content_type="text/plain")

    return service
