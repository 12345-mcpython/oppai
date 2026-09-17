"""极简 HTTP 服务框架（仅标准库）。

* 多端口，一线程一连接
* 路由表：精确匹配 + 前缀匹配
* 所有请求/响应完整落盘，方便对着客户端逐条比对
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, logx

log = logx.get("http")

MAX_BODY = 32 * 1024 * 1024


class Request:
    __slots__ = ("method", "raw_path", "path", "query", "headers", "body", "client", "server_port")

    def __init__(self, method, raw_path, headers, body, client, server_port):
        self.method = method
        self.raw_path = raw_path
        self.raw_path = raw_path
        parsed = urllib.parse.urlsplit(raw_path)
        self.path = urllib.parse.unquote(parsed.path)
        self.query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        self.headers = headers
        self.body = body
        self.client = client
        self.server_port = server_port

    def q(self, key, default=None):
        values = self.query.get(key)
        return values[0] if values else default

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception:
            return None

    def payload(self):
        """宽松解析请求体。

        游戏的 httpc 会把参数包成 `data=<urlencoded>`，探针则直接发 JSON。
        """
        parsed = self.json()
        if isinstance(parsed, dict):
            return parsed
        try:
            text = self.body.decode("utf-8")
        except Exception:
            return {}
        form = urllib.parse.parse_qs(text, keep_blank_values=True)
        if "data" in form:
            raw = form["data"][0]
            try:
                value = json.loads(raw)
            except Exception:
                return {"value": raw}
            if isinstance(value, dict):
                return value
            return {"value": value}
        return form

    def __repr__(self):
        return f"<{self.method} {self.raw_path} from {self.client}>"


class Response:
    def __init__(self, status=200, body=b"", headers=None, content_type="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        elif isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.status = status
        self.body = body
        self.headers = dict(headers or {})
        self.headers.setdefault("Content-Type", content_type)
        self.headers.setdefault("Access-Control-Allow-Origin", "*")


class Router:
    def __init__(self, name):
        self.name = name
        self._exact = {}
        self._prefix = []
        self.fallback = None

    def route(self, method, path):
        def deco(fn):
            self._exact[(method.upper(), path)] = fn
            return fn

        return deco

    def any(self, path):
        def deco(fn):
            for m in ("GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"):
                self._exact[(m, path)] = fn
            return fn

        return deco

    def prefix(self, prefix):
        def deco(fn):
            self._prefix.append((prefix, fn))
            return fn

        return deco

    def set_fallback(self, fn):
        self.fallback = fn

    def resolve(self, req: Request):
        handler = self._exact.get((req.method, req.path))
        if handler:
            return handler
        for prefix, fn in self._prefix:
            if req.path.startswith(prefix):
                return fn
        return self.fallback


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "OppaiEmu/1.0"

    # 由 HttpService 注入
    router: Router = None
    port_name: str = ""

    def log_message(self, fmt, *args):  # 关掉默认 stderr 输出
        pass

    # ------------------------------------------------------------------
    def _read_body(self) -> bytes:
        length = self.headers.get("Content-Length")
        if length:
            try:
                return self.rfile.read(min(int(length), MAX_BODY))
            except Exception:
                return b""
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            chunks = []
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                try:
                    size = int(line, 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            return b"".join(chunks)
        return b""

    def _handle(self):
        raw_path = self.path
        body = self._read_body()
        client = f"{self.client_address[0]}:{self.client_address[1]}"
        req = Request(self.command, raw_path, dict(self.headers), body, client, self.server.server_address[1])

        if (self.headers.get("Upgrade") or "").lower() == "websocket":
            from .wsserver import handle_upgrade

            handle_upgrade(self, req)
            return

        if self.path.startswith("/hook/"):
            log.log(5, "[%s] %s %s", self.port_name, self.command, raw_path)
        else:
            log.debug("[%s] %s %s", self.port_name, self.command, raw_path)
        is_poll = req.path.startswith("/hook/poll")
        if not is_poll:
            logx.capture(
                "http",
                {
                    "port": self.port_name,
                    "method": self.command,
                    "path": raw_path,
                    "headers": dict(self.headers),
                    "body_len": len(body),
                    "body": body.decode("utf-8", "replace")[:20000],
                },
            )

        try:
            handler = self.router.resolve(req)
            if handler is None:
                resp = Response(404, {"error": "not_found", "path": req.path})
            else:
                resp = handler(req)
                if resp is None:
                    resp = Response(200, {})
                elif not isinstance(resp, Response):
                    resp = Response(200, resp)
        except Exception as exc:  # noqa: BLE001
            log.exception("[%s] handler error for %s", self.port_name, raw_path)
            resp = Response(500, {"error": "internal", "detail": str(exc)})

        if not is_poll:
            logx.capture(
                "http-resp",
                {
                    "port": self.port_name,
                    "path": raw_path,
                    "status": resp.status,
                    "body": resp.body.decode("utf-8", "replace")[:20000],
                },
            )
        if not (is_poll and resp.status < 400) and (resp.status >= 400 or log.isEnabledFor(10)):
            log.debug("[%s] -> %s %s", self.port_name, resp.status, resp.body[:400])

        try:
            self.send_response(resp.status)
            for key, value in resp.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(resp.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


class HttpService:
    def __init__(self, name: str, port: int, bind: str = None):
        self.name = name
        self.port = port
        self.bind = bind or config.BIND_HOST
        self.router = Router(name)
        self._httpd = None
        self._thread = None

    def start(self):
        handler = type(
            f"Handler_{self.name}",
            (_Handler,),
            {"router": self.router, "port_name": self.name},
        )
        self._httpd = _Server((self.bind, self.port), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name=f"http-{self.name}", daemon=True)
        self._thread.start()
        log.info("HTTP [%s] listening on %s:%d", self.name, self.bind, self.port)
        return self

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
