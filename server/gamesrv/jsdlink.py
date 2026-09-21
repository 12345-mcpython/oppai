r"""引擎远程 JS 调试器的连接层（Firefox 远程调试协议，actors 版）。

引擎那边是 cocos2d-js 3.6 `ScriptingCore` 自带的调试器：
**SpiderMonkey Debugger API**（`JS_DefineDebuggerObject`）+ 一层 Firefox 远程调试协议，
在 TCP 上说话。默认关着，`build\enable_js_debugger.py` 打开它。
细节和踩过的坑见 `docs/engine-debug.md`。

这个模块只负责「连上去 + 收发 + 几个高层动作」；
CLI 在 `tools/jsd.py`，devtools 面板在 `gamesrv/devtools.py`。

## 协议速查

帧格式 `<utf8 字节长度>:<json>`。连上先收一个 greeting。

```
{"to":"root",   "type":"listTabs"}                      -> {tabs:[{actor,title,url}]}
{"to":<tab>,    "type":"attach"}                        -> {type:"tabAttached", threadActor}
{"to":<thread>, "type":"attach"}                        -> {type:"paused", actor:<pauseActor>}
                                                            ⚠️ 游戏从这一刻起冻结，直到 resume
{"to":<thread>, "type":"sources"}                       -> {sources:[...]}（前面会先来几百条 newSource）
{"to":<thread>, "type":"setBreakpoint",
                "location":{url,line}}                  -> {actor} 或 {actor, actualLocation}
{"to":<thread>, "type":"resume"}                        -> {type:"resumed"}
{"to":<thread>, "type":"resume","resumeLimit":{"type":"step|next|finish"}}
{"to":<thread>, "type":"interrupt"}                     -> {type:"paused", why:{type:"interrupted"}}
{"to":<thread>, "type":"frames"}                        -> {frames:[{actor,type,where,this,...}]}
{"to":<thread>, "type":"clientEvaluate",
                "frame":<frameActor>, "expression":"..."} -> {type:"paused", why:{type:"clientEvaluated",
                                                              frameFinished:{...}}}
{"to":<thread>, "type":"detach"}                        -> {type:"detached"}
{"to":<source>, "type":"source"}                        -> {source:{...}}   取源码（.jsc 取不到）
```

⚠️ 所有请求都发给 **thread actor**，不是 `paused` 包里的那个 `actor`（那是 PauseActor，
自己没有 requestTypes）。

⚠️ 协议里**没有请求 id**，回复只能靠形状认。`newSource` / `newGlobal` 之类是服务端
主动推的通知，等回包的时候必须跳过 —— 这个坑一开始就踩了：
`sources` 请求会先收到几百条 `newSource`，真正的回复在最后。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time

ADB = os.environ.get("GS_ADB", r"D:\Android\android-sdk\platform-tools\adb.exe")
ADB_SERIAL = os.environ.get("GS_ADB_SERIAL", "127.0.0.1:21503")
DEFAULT_PORT = int(os.environ.get("GS_JSD_PORT", "5086"))

# 服务端主动推的通知：等回包时要跳过
NOTIFICATION_TYPES = {
    "newSource", "newGlobal", "tabListChanged", "addonListChanged",
    "progress", "frameUpdate", "documentIncomplete",
}


class ProtocolError(Exception):
    pass


class JSD(object):
    """一个调试器连接。同一时间引擎只接受一个客户端。"""

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT, timeout: float = 15.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self._buf = b""
        self.root: str | None = None
        self.tab: str | None = None
        self.thread: str | None = None
        self.pause_actor: str | None = None
        self.breakpoints: list[dict] = []
        self.sources: list[dict] = []
        self.new_sources: list[dict] = []
        self.notifications: list[dict] = []
        self.last_paused: dict | None = None
        self.log: list[str] = []

    # ---------------- 连接 ----------------

    def connect(self) -> dict:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        greeting = self.recv()
        self.root = greeting.get("from")
        self.log.append(f"greeting: {json.dumps(greeting, ensure_ascii=False)}")
        return greeting

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except Exception:  # noqa: BLE001
                pass
            self.sock = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------- 收发 ----------------

    def send(self, packet: dict) -> None:
        if not self.sock:
            raise ProtocolError("还没连接")
        data = json.dumps(packet, ensure_ascii=False)
        frame = ("%d:" % len(data.encode("utf-8"))) + data
        self.sock.sendall(frame.encode("utf-8"))

    def recv(self, timeout: float | None = None) -> dict:
        deadline = time.time() + (self.timeout if timeout is None else timeout)
        while True:
            packet = self._try_parse()
            if packet is not None:
                return packet
            remain = deadline - time.time()
            if remain <= 0:
                raise ProtocolError("等回包超时（服务端没响应 / 端口不对）")
            self.sock.settimeout(remain)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise ProtocolError("等回包超时（服务端没响应 / 端口不对）")
            if not chunk:
                raise ProtocolError("连接被对端关了（游戏退出了？调试器一次只能接一个客户端）")
            self._buf += chunk

    def _try_parse(self):
        if b":" not in self._buf:
            return None
        head, _, rest = self._buf.partition(b":")
        if not head.isdigit():
            # 帧头不认识 —— 扔到第一个冒号之后重来
            self._buf = rest
            return None
        want = int(head)
        if len(rest) < want:
            return None
        # ⚠️ 长度是 **UTF-8 字节数**，按字节切，别先解码
        payload, self._buf = rest[:want], rest[want:]
        return json.loads(payload.decode("utf-8"))

    # ---------------- 请求 ----------------

    def request(self, to: str, type_: str, expect=None, timeout: float | None = None, **kw) -> dict:
        """发一个请求并等回复。`expect` 是判定函数，None = 第一个非通知包。"""
        packet = {"to": to, "type": type_}
        packet.update(kw)
        self.log.append(f"-> {json.dumps(packet, ensure_ascii=False)}")
        self.send(packet)
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise ProtocolError(f"{type_} 等回包超时")
            reply = self.recv(timeout=remain)
            self.log.append(f"<- {json.dumps(reply, ensure_ascii=False)[:600]}")
            if "error" in reply:
                raise ProtocolError(f"{type_} 失败: {reply.get('error')} {reply.get('message', '')}")

            kind = reply.get("type")
            if kind in NOTIFICATION_TYPES:
                self.notifications.append(reply)
                if kind == "newSource" and reply.get("source"):
                    self.new_sources.append(reply["source"])
                continue

            if kind == "paused":
                # 可能是回复（attach / interrupt / clientEvaluate），
                # 也可能是撞上断点主动推的
                self.last_paused = reply
                self.pause_actor = reply.get("actor")

            if expect is None or expect(reply):
                return reply
            self.notifications.append(reply)

    # ---------------- 高层动作 ----------------

    def list_tabs(self) -> list:
        return self.request(self.root or "root", "listTabs",
                            expect=lambda p: "tabs" in p).get("tabs") or []

    def attach(self, tab_index: int = 0) -> dict:
        tabs = self.list_tabs()
        if not tabs:
            raise ProtocolError("没有可附加的 tab（DebuggerServer 没起来？）")
        self.tab = tabs[tab_index]["actor"]
        attached = self.request(self.tab, "attach",
                                expect=lambda p: p.get("type") == "tabAttached")
        self.thread = attached.get("threadActor")
        if not self.thread:
            raise ProtocolError(f"tab attach 没给 threadActor: {attached}")
        # 再往 thread actor 上 attach —— 会把游戏冻住
        reply = self.request(self.thread, "attach", expect=lambda p: p.get("type") == "paused")
        self.pause_actor = reply.get("actor")
        self.last_paused = reply
        return reply

    def list_sources(self) -> list:
        reply = self.request(self.thread, "sources", expect=lambda p: "sources" in p)
        self.sources = reply.get("sources") or []
        if not self.sources and self.new_sources:
            self.sources = list(self.new_sources)
        return self.sources

    def set_breakpoint(self, url: str, line: int, column: int | None = None) -> dict:
        loc = {"url": url, "line": line}
        if column is not None:
            loc["column"] = column
        reply = self.request(self.thread, "setBreakpoint",
                             expect=lambda p: "actor" in p or "actualLocation" in p,
                             location=loc)
        self.breakpoints.append({"url": url, "line": line, "actor": reply.get("actor"),
                                 "actual": reply.get("actualLocation"),
                                 "error": reply.get("error")})
        return reply

    def resume(self, limit: str | None = None) -> dict:
        kw = {"resumeLimit": {"type": limit}} if limit else {}
        return self.request(self.thread, "resume",
                            expect=lambda p: p.get("type") == "resumed", **kw)

    def interrupt(self) -> dict:
        reply = self.request(self.thread, "interrupt", expect=lambda p: p.get("type") == "paused")
        self.last_paused = reply
        self.pause_actor = reply.get("actor")
        return reply

    def frames(self) -> list:
        return self.request(self.thread, "frames",
                            expect=lambda p: "frames" in p).get("frames") or []

    def evaluate(self, expression: str, frame: str | None = None) -> dict:
        kw = {"expression": expression}
        if frame:
            kw["frame"] = frame
        reply = self.request(self.thread, "clientEvaluate",
                             expect=lambda p: p.get("type") == "paused", **kw)
        self.last_paused = reply
        self.pause_actor = reply.get("actor")
        return reply

    def source_text(self, source_actor: str) -> dict:
        return self.request(source_actor, "source", expect=lambda p: "source" in p)

    def detach(self) -> dict:
        return self.request(self.thread, "detach",
                            expect=lambda p: p.get("type") in ("detached", "exited"))

    def is_paused(self) -> bool:
        return bool(self.last_paused) and self.last_paused.get("type") == "paused"

    # ---------------- 工具 ----------------

    def wait_for_pause(self, timeout: float = 30.0) -> dict | None:
        """等一条服务端主动推的 paused（命中断点 / 单步停下）。"""
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                return None
            try:
                packet = self.recv(timeout=remain)
            except ProtocolError:
                return None
            if packet.get("type") == "paused":
                self.last_paused = packet
                self.pause_actor = packet.get("actor")
                return packet
            self.notifications.append(packet)


def find_source(sources: list, needle: str) -> dict | None:
    """按 url 子串找脚本。`\\` 和 `/` 都认（jsc 的 url 是 Windows 构建机路径）。"""
    if not needle:
        return None
    alt = needle.replace("/", "\\")
    hits = [s for s in sources
            if needle in (s.get("url") or "") or alt in (s.get("url") or "")]
    if not hits:
        return None
    return sorted(hits, key=lambda s: (s.get("url") != needle, len(s.get("url") or "")))[0]


def find_sources(sources: list, needle: str) -> list:
    if not needle:
        return list(sources)
    alt = needle.replace("/", "\\")
    return [s for s in sources
            if needle in (s.get("url") or "") or alt in (s.get("url") or "")]


def setup_adb_forward(port: int = DEFAULT_PORT, quiet: bool = False) -> None:
    """MEmu 是 NAT 的，宿主机连不上模拟器里的端口，得先转发。"""
    if os.environ.get("GS_JSD_NO_ADB"):
        return
    if not os.path.isfile(ADB):
        if not quiet:
            print(f"（没找到 adb {ADB}，跳过端口转发）")
        return
    for args in (["connect", ADB_SERIAL], ["forward", f"tcp:{port}", f"tcp:{port}"]):
        try:
            out = subprocess.run([ADB, *args], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=15)
            if not quiet:
                print(f"adb {args[0]}: {(out.stdout or out.stderr or '').strip()}")
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                print(f"adb {args[0]} 失败: {exc}")


# ---------------------------------------------------------------------------
# 给 devtools 用的常驻会话
# ---------------------------------------------------------------------------

class Session:
    """把 JSD 包成「一个常驻会话」，供 devtools 面板从 HTTP 线程调用。

    ⚠️ 所有操作都要串行化：协议没有请求 id，两个线程同时收发必然串包。
    """

    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._lock = threading.RLock()
        self.jsd: JSD | None = None
        self.state = "idle"        # idle | attached | paused | running | error
        self.error = ""
        self.why: dict | None = None
        self._pump: threading.Thread | None = None
        self._stop = threading.Event()

    # -- 生命周期 --

    def connect(self) -> dict:
        with self._lock:
            if self.jsd:
                return self.status()
            setup_adb_forward(self.port, quiet=True)
            jsd = JSD(self.host, self.port)
            jsd.connect()
            self.jsd = jsd
            self.state = "attached"
            self.error = ""
        # attach 单独做，因为它会把游戏冻住
        with self._lock:
            reply = self.jsd.attach()
            self.state = "paused"
            self.why = reply.get("why")
        self._start_pump()
        return self.status()

    def disconnect(self) -> dict:
        self._stop.set()
        with self._lock:
            if self.jsd:
                try:
                    if self.state == "paused":
                        self.jsd.resume()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    self.jsd.detach()
                except Exception:  # noqa: BLE001
                    pass
                self.jsd.close()
            self.jsd = None
            self.state = "idle"
            self.why = None
        return self.status()

    def _start_pump(self) -> None:
        if self._pump and self._pump.is_alive():
            return
        self._stop.clear()

        def run():
            # 只在 running 状态下等「主动推的 paused」；
            # 暂停状态下 recv 会阻塞，所以用短超时轮着看。
            while not self._stop.is_set():
                with self._lock:
                    jsd = self.jsd
                    running = self.state == "running"
                if not jsd or not running:
                    time.sleep(0.2)
                    continue
                try:
                    pkt = jsd.recv(timeout=0.5)
                except ProtocolError:
                    continue
                except Exception:  # noqa: BLE001
                    time.sleep(0.2)
                    continue
                if pkt.get("type") == "paused":
                    with self._lock:
                        jsd.last_paused = pkt
                        jsd.pause_actor = pkt.get("actor")
                        self.state = "paused"
                        self.why = pkt.get("why")
                else:
                    jsd.notifications.append(pkt)

        self._pump = threading.Thread(target=run, name="jsd-pump", daemon=True)
        self._pump.start()

    # -- 操作 --

    def _need(self) -> JSD:
        if not self.jsd:
            raise ProtocolError("还没连上调试器")
        return self.jsd

    def sources(self, needle: str = "") -> list:
        with self._lock:
            jsd = self._need()
            if not jsd.sources:
                jsd.list_sources()
            return find_sources(jsd.sources, needle)

    def set_breakpoint(self, url: str, line: int) -> dict:
        with self._lock:
            if self.state not in ("paused",):
                raise ProtocolError(f"只能在暂停状态下下断点（当前 {self.state}）")
            return self._need().set_breakpoint(url, line)

    def resume(self, limit: str | None = None) -> dict:
        with self._lock:
            if self.state != "paused":
                raise ProtocolError(f"现在不是暂停状态（{self.state}）")
            reply = self._need().resume(limit)
            self.state = "running"
            self.why = None
            return reply

    def pause(self) -> dict:
        with self._lock:
            if self.state != "running":
                raise ProtocolError(f"现在不是运行状态（{self.state}）")
            reply = self._need().interrupt()
            self.state = "paused"
            self.why = reply.get("why")
            return reply

    def frames(self) -> list:
        with self._lock:
            if self.state != "paused":
                return []
            try:
                return self._need().frames()
            except ProtocolError as exc:
                self.error = str(exc)
                return []

    def evaluate(self, expression: str, frame: str | None = None) -> dict:
        with self._lock:
            if self.state != "paused":
                raise ProtocolError("只能在暂停状态下求值")
            reply = self._need().evaluate(expression, frame=frame)
            self.why = reply.get("why")
            return reply

    def breakpoints(self) -> list:
        with self._lock:
            return list(self.jsd.breakpoints) if self.jsd else []

    def status(self) -> dict:
        return {
            "connected": bool(self.jsd),
            "state": self.state,
            "why": self.why,
            "error": self.error,
            "host": self.host,
            "port": self.port,
            "sources": len(self.jsd.sources) if self.jsd else 0,
            "breakpoints": list(self.jsd.breakpoints) if self.jsd else [],
        }


session = Session()


def frames_brief(frames: list) -> list:
    """把协议里的栈帧整理成前端好渲染的形状。"""
    out = []
    for i, fr in enumerate(frames):
        where = fr.get("where") or {}
        callee = fr.get("callee") or {}
        out.append({
            "depth": i,
            "actor": fr.get("actor"),
            "name": callee.get("name") or callee.get("userDisplayName") or "(匿名)",
            "url": where.get("url"),
            "line": where.get("line"),
            "column": where.get("column"),
            "env": bool(fr.get("environment")),
            "this": fr.get("this"),
        })
    return out
