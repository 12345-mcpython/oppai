"""开发调试事件总线。

devtools 页面（`gamesrv/devtools.py` 托管在 CDN 端口的 `/devtools`）靠它拿实时数据。
所有产出方只管 `publish()`，消费方（浏览器）用**长轮询**拉：

    GET /devtools/api/events?since=<上次拿到的 seq>&timeout=25

为什么不用 SSE / WebSocket：
    `httpd.py` 是「一线程一连接 + 一定写 Content-Length」的极简实现，
    要支持 SSE 得给它加一条 `Transfer-Encoding: chunked` 的流式分支。
    那条路改动共享代码（游戏本身跑在上面），风险不划算。
    长轮询在这个场景下没有实质差别（本机、单标签页、延迟 <100ms）。

事件形状（都是扁平 dict，方便前端直接渲染）::

    {"seq": 12, "ts": 1789741234.5, "kind": "traffic", ...}

kind 约定：

    traffic  客户端发来的业务请求 + 服务端回包（成对，同一个 reqId）
    client   客户端探针日志（adb logcat / probe.js 上报）
    console  在客户端跑的 JS（devtools 控制台 / repl.py）
    server   服务端自己的日志（logging handler 转发进来）
    action   devtools 上的操作（改存档、跑作弊、恢复快照……）
"""

from __future__ import annotations

import collections
import json
import threading
import time

# 环形缓冲长度。devtools 页面打开时先拿这一份当"历史"，
# 2000 条够翻很久了，再多就只是吃内存。
RING_MAX = 2000

# 单条事件里，单个字符串最多留多少字符。
#
# ⚠️⚠️ **这个不是省内存，是防浏览器卡死**。踩过一次：客户端探针会把整个
# 登录响应 dump 成 hex 打进日志 ——
#
#     CRYPT base64Decode(b64len=131136 hex=436b794f4e4a7276...)
#
# 一条就 131KB。日志面板一次性收到 2000 条这种行直接把标签页卡住，
# 看起来就是「控制台页里没有东西、流量页也不再动了」。
# 截断之后仍然看得出是什么，只是看不到全文。
MAX_TEXT = 4000

# 控制台（`/api/console` / 浏览器控制台 / repl.py）的结果给宽得多 ——
# 那是人**主动要看**的输出（`JSON.stringify(某个大对象)`），截到 4000 会很难用，
# 而它是单条、不会像日志那样每帧刷。
MAX_TEXT_CONSOLE = 20000

# 按 kind 覆盖上面的默认上限
KIND_TEXT_LIMIT = {"console": MAX_TEXT_CONSOLE}

# 数组最多留几项（`traffic` 事件的 `res.data` 里可能有几百项列表）。
MAX_LIST = 100

# 字典最多留几个 key。
#
# ⚠️ 光截字符串和数组**不够**。踩过一次：`traffic.res` 是登录响应那个
# `res.data`，它是个**上千 key 的 map**（1142 个关卡 × `{starMark,challengeTimes,...}`），
# 每个值是短字符串/小数字，一条字符串都没超限、一个数组都不长，
# 合起来照样 **22 万字符** —— 自测 `check_devtools.py` 的「事件字段长度」
# 那条直接把它抓出来了（上限 4 万）。
MAX_KEYS = 200

# 单条事件序列化后的总上限。上面几个都是"分项上限"，合起来仍可能很大，
# 这里是最后一道闸：超了就用更狠的额度再过一遍 `_clip`。
MAX_EVENT_JSON = 60000


def _clip(obj, limit: int = MAX_TEXT):
    """把事件里超长的字符串 / 超长的数组截掉。返回 (截断后的对象, 截了几个)。

    只动展示用的字段（`line` / `msg` / `res` …），**不影响业务数据** ——
    事件总线只喂调试台，存档和回包本身走的是各自的路。
    """
    if isinstance(obj, str):
        if len(obj) > limit:
            return obj[:limit] + "…(截断，原长 %d)" % len(obj), 1
        return obj, 0
    if isinstance(obj, dict):
        out, n = {}, 0
        keys = list(obj.keys())
        for k in keys[:MAX_KEYS]:
            nv, c = _clip(obj[k], limit)
            out[k] = nv
            n += c
        if len(keys) > MAX_KEYS:
            out["…"] = "(截断，原有 %d 个 key)" % len(keys)
            n += 1
        return out, n
    if isinstance(obj, (list, tuple)):
        out, n = [], 0
        for v in obj[:MAX_LIST]:
            nv, c = _clip(v, limit)
            out.append(nv)
            n += c
        if len(obj) > MAX_LIST:
            out.append("…(截断，原长 %d 项)" % len(obj))
            n += 1
        return out, n
    return obj, 0


class _Bus:
    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._events: collections.deque = collections.deque(maxlen=RING_MAX)
        self._seq = 0
        self._dropped = 0
        self._counts: dict[str, int] = {}

    # ---------------- 产出 ----------------

    def publish(self, kind: str, **fields) -> dict:
        """发一条事件。字段名随便给，前端按 kind 渲染。

        ⚠️ 入库前会过一遍 `_clip()`：**超长的字符串必须截掉**（见 MAX_TEXT 的说明），
        否则一条 131KB 的 `CRYPT ... hex=...` 就够把 devtools 页面卡死。
        """
        clipped, cut = _clip(fields, KIND_TEXT_LIMIT.get(kind, MAX_TEXT))
        # 分项上限都过了还嫌大？用更狠的额度再过一遍（只留 1/4），
        # 保证「一条事件最多占多少」有个硬上限。
        try:
            if len(json.dumps(clipped, ensure_ascii=False)) > MAX_EVENT_JSON:
                clipped, cut2 = _clip(clipped, max(200, MAX_TEXT // 4))
                cut += cut2 + 1
        except (TypeError, ValueError):
            clipped = {"_unserializable": True}
            cut += 1
        with self._cv:
            self._seq += 1
            event = {"seq": self._seq, "ts": time.time(), "kind": kind}
            event.update(clipped)
            if cut:
                event["clipped"] = cut
            if len(self._events) == RING_MAX:
                self._dropped += 1
            self._events.append(event)
            self._counts[kind] = self._counts.get(kind, 0) + 1
            self._cv.notify_all()
            return event

    # ---------------- 消费 ----------------

    def since(self, seq: int, limit: int = 500) -> list:
        """拿 seq 之后的事件（不含 seq 本身）。"""
        with self._cv:
            out = [e for e in self._events if e["seq"] > seq]
        return out[:limit]

    def wait(self, seq: int, timeout: float = 25.0) -> list:
        """长轮询：等到有 seq 之后的事件、或者超时。返回可能是空列表。"""
        deadline = time.time() + max(0.0, timeout)
        with self._cv:
            while True:
                out = [e for e in self._events if e["seq"] > seq]
                if out or timeout <= 0:
                    return out
                remaining = deadline - time.time()
                if remaining <= 0:
                    # 超时也把「最新 seq」带回去，前端才能推进游标 ——
                    # 否则一旦漏了一条事件，轮询会永远立刻返回空、变成忙循环。
                    return []
                self._cv.wait(remaining)

    def latest(self) -> int:
        with self._cv:
            return self._seq

    def snapshot(self) -> list:
        with self._cv:
            return list(self._events)

    def clear(self) -> None:
        with self._cv:
            self._events.clear()
            self._dropped = 0
            self._counts.clear()
            self._cv.notify_all()

    def stats(self) -> dict:
        with self._cv:
            return {
                "seq": self._seq,
                "buffered": len(self._events),
                "dropped": self._dropped,
                "counts": dict(self._counts),
            }


bus = _Bus()


def publish(kind: str, **fields) -> dict:
    return bus.publish(kind, **fields)


# ---------------------------------------------------------------------------
# 把服务端日志接到总线上
# ---------------------------------------------------------------------------

class _LogBridge:
    """logging.Handler -> 总线。

    ⚠️ 只在 devtools 页面真的打开时才值得转发。不过这个私服的日志量很小
    （一次请求几条 INFO），无条件开着更省心 —— 不然「先开页面再复现」
    这种最常见的用法会漏掉最前面那段。

    跳过 `gamesrv.http`：它是「每个 HTTP 请求两条 DEBUG」，和流量面板完全重复。
    """

    SKIP_LOGGERS = ("gamesrv.http",)

    def __init__(self, level: int = 20, enabled: bool = True):
        import logging

        self.level = level
        self.enabled = enabled

        bridge = self

        class _Handler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if not bridge.enabled:
                    return
                if record.levelno < bridge.level:
                    return
                if record.name.startswith(bridge.SKIP_LOGGERS):
                    return
                try:
                    message = record.getMessage()
                except Exception:  # noqa: BLE001
                    message = "<格式化失败>"
                if record.exc_info:
                    message += " | " + str(record.exc_info[1])
                bus.publish(
                    "server",
                    # 级别统一成前端那**五档**：debug / info / warning / error / fatal。
                    # 1) 一律**小写**：Python 的 levelname 是大写，而客户端行的级别是按
                    #    行首判的、本来就是小写，并排显示会出现 `store/INFO` 挨着 `client/info`。
                    # 2) `CRITICAL` 归到 **fatal**：两套名字同一个档，看着乱。
                    level={"critical": "fatal"}.get(record.levelname.lower(),
                                                    record.levelname.lower()),
                    logger=record.name.replace("gamesrv.", "", 1),
                    message=message[:4000],
                )

        self.handler = _Handler()

    def attach(self) -> None:
        import logging

        logging.getLogger("gamesrv").addHandler(self.handler)


_bridge: _LogBridge | None = None


def attach_logging(level: int = 20) -> _LogBridge:
    global _bridge
    if _bridge is None:
        _bridge = _LogBridge(level=level)
        _bridge.attach()
    return _bridge


def set_logging_enabled(enabled: bool) -> bool:
    if _bridge is None:
        attach_logging()
    _bridge.enabled = bool(enabled)
    return _bridge.enabled
