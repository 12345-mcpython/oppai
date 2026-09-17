"""日志。

* 控制台输出（带颜色）
* 文件输出到 var/logs/
* HTTP / WS 原始报文单独落到 var/capture/ 便于事后翻查
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time

from . import config

_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[41m",
}
_RESET = "\033[0m"

_configured = False
_lock = threading.Lock()


class _ColorFormatter(logging.Formatter):
    def __init__(self, use_color: bool):
        super().__init__("%(asctime)s %(levelname)-7s %(name)-14s %(message)s", "%H:%M:%S")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if self.use_color:
            color = _LEVEL_COLORS.get(record.levelname, "")
            if color:
                text = f"{color}{text}{_RESET}"
        return text


def setup(level: int = logging.DEBUG, use_color: bool | None = None) -> None:
    global _configured
    with _lock:
        if _configured:
            return
        _configured = True

        if use_color is None:
            use_color = sys.stdout.isatty()

        root = logging.getLogger("gamesrv")
        root.setLevel(level)
        root.propagate = False

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(_ColorFormatter(use_color))
        root.addHandler(console)

        fh = logging.FileHandler(os.path.join(config.LOG_DIR, "server.log"), encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)-14s %(message)s"))
        root.addHandler(fh)


def get(name: str) -> logging.Logger:
    setup()
    return logging.getLogger("gamesrv." + name)


# --------------------------------------------------------------------------
# 原始报文记录
# --------------------------------------------------------------------------

_capture_lock = threading.Lock()


def capture(kind: str, payload: dict) -> None:
    """把一条原始交互记录追加到当天的 capture 文件。"""
    day = time.strftime("%Y%m%d")
    path = os.path.join(config.CAPTURE_DIR, f"{kind}-{day}.jsonl")
    record = {"ts": time.time(), **payload}
    line = json.dumps(record, ensure_ascii=False)
    with _capture_lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
