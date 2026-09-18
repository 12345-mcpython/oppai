"""客户端 REPL。

探针（assets/src/patch/probe.js）会不断轮询 /hook/poll，
拿到 JS 表达式后在游戏进程里 eval，再把结果 POST 回 /hook/result。
这样就能在不反编译字节码的情况下直接观察客户端内部状态。

⚠️ 命令 id 必须**单调递增、而且重启后不能回退**。
客户端那边是 `if (cmd.id <= replSeq) return;`（防重放），
服务端一重启计数器从 1 重来的话，客户端手里已经攒到几十了，
新命令全被判成旧命令丢掉 —— 表现就是 REPL 一路 504 超时，
但游戏本身完全正常（我在这上面浪费过时间）。
所以这里从 **当前时间戳** 起步。
"""

from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_cv = threading.Condition(_lock)
_seq = int(time.time())          # 见上面的说明：不能从 1 开始
_pending: list[dict] = []
_results: dict[int, dict] = {}
_history: list[dict] = []


def _next_id() -> int:
    global _seq
    _seq += 1
    return _seq


def submit(code: str, timeout: float = 20.0):
    """下发一条命令并等待结果。返回 None 表示超时。"""
    with _cv:
        cid = _next_id()
        _pending.append({"id": cid, "code": code})
        _cv.notify_all()
    deadline = time.time() + timeout
    with _cv:
        while cid not in _results:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            _cv.wait(remaining)
        result = _results.pop(cid)
    _history.append(result)
    return result


def poll(last: int, timeout: float = 0.0):
    """给客户端取命令。没有就立刻返回 code=None。"""
    deadline = time.time() + timeout
    with _cv:
        while True:
            for cmd in _pending:
                if cmd["id"] > last:
                    _pending.remove(cmd)
                    return cmd
            remaining = deadline - time.time()
            if remaining <= 0:
                return {"id": last, "code": None}
            _cv.wait(remaining)


def push_result(payload: dict):
    try:
        cid = int(payload.get("id"))
    except (TypeError, ValueError):
        return
    with _cv:
        _results[cid] = payload
        _cv.notify_all()


def pending_count() -> int:
    with _cv:
        return len(_pending)
