"""最小可用的 WebSocket 服务端（RFC6455），只用标准库。

游戏登录阶段用一条 WebSocket 完成 DH 握手，之后所有业务请求走 HTTP POST，
所以这里只需要处理少量文本帧。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct
import threading

from . import logx

log = logx.get("ws")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class WebSocket:
    def __init__(self, sock: socket.socket, client: str):
        self.sock = sock
        self.client = client
        self.closed = False
        self._send_lock = threading.Lock()
        self._buf = b""

    # ------------------------------------------------------------------
    # 收
    # ------------------------------------------------------------------
    def _recv_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("socket closed")
            data += chunk
        return data

    def recv_frame(self):
        """返回 (opcode, payload)；连接关闭时返回 (None, None)。"""
        header = self._recv_exact(2)
        b1, b2 = header[0], header[1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b"\x00\x00\x00\x00"
        payload = bytearray(self._recv_exact(length)) if length else bytearray()
        if masked:
            for i in range(length):
                payload[i] ^= mask[i % 4]
        payload = bytes(payload)

        if opcode == OP_CLOSE:
            return None, None
        if opcode == OP_PING:
            self.send_frame(payload, OP_PONG)
            return self.recv_frame()
        if opcode == OP_PONG:
            return self.recv_frame()

        # 分片：简单拼接
        if not fin:
            while True:
                nxt_op, nxt = self.recv_frame()
                if nxt is None:
                    return None, None
                payload += nxt
                break
        return opcode, payload

    # ------------------------------------------------------------------
    # 发
    # ------------------------------------------------------------------
    def send_frame(self, payload: bytes, opcode: int = OP_TEXT):
        if self.closed:
            return
        header = bytearray()
        header.append(0x80 | opcode)
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < (1 << 16):
            header.append(126)
            header += struct.pack(">H", length)
        else:
            header.append(127)
            header += struct.pack(">Q", length)
        with self._send_lock:
            try:
                self.sock.sendall(bytes(header) + payload)
            except OSError as exc:
                log.debug("send failed: %s", exc)
                self.closed = True

    def send_text(self, text: str):
        self.send_frame(text.encode("utf-8"), OP_TEXT)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.send_frame(b"", OP_CLOSE)
        except Exception:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def handle_upgrade(handler, req):
    """从 BaseHTTPRequestHandler 手上接管连接，升级成 WebSocket。"""
    key = req.headers.get("Sec-WebSocket-Key")
    if not key:
        handler.send_error(400, "missing Sec-WebSocket-Key")
        return

    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Accept: {accept}",
    ]
    # 客户端带 Sec-WebSocket-Protocol 时服务端必须选一个回显，
    # 否则 RFC6455 规定客户端要主动断开（cocos 的 Java-WebSocket 就是这么做的）。
    proto = req.headers.get("Sec-WebSocket-Protocol")
    if proto:
        lines.append(f"Sec-WebSocket-Protocol: {proto.split(',')[0].strip()}")
    handler.wfile.write(("\r\n".join(lines) + "\r\n\r\n").encode())
    handler.wfile.flush()
    handler.close_connection = True

    from .session import on_ws_connect

    ws = WebSocket(handler.connection, f"{handler.client_address[0]}:{handler.client_address[1]}")
    log.info("WS connected from %s (%s)", ws.client, req.path)
    try:
        on_ws_connect(ws, req)
    except Exception:  # noqa: BLE001
        log.exception("WS session error")
    finally:
        ws.close()
        log.info("WS closed: %s", ws.client)
