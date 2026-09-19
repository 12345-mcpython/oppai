"""devtools 自测：不开浏览器把每个接口都打一遍。

    python tools/check_devtools.py

服务端在跑就行（`python script\\serve.py`）。
需要客户端探针的接口在游戏没开时会报「探针没在响应」，那不算失败 ——
脚本会把「需要游戏」和「不需要游戏」分开统计。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamesrv import config  # noqa: E402

BASE = f"http://127.0.0.1:{config.CDN_PORT}/devtools"


def call(path: str, body=None, timeout: float = 40.0):
    data = None
    method = "GET"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        method = "POST"
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return {"__http": exc.code, "body": exc.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"__err": str(exc)}
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {"__raw": raw[:200]}


def static_checks() -> int:
    """前端和后端的「对得上吗」检查（不需要服务端在跑）。

    两个最容易犯、又最难在浏览器里定位的错：

    1. JS 里 `$('xxx')` 写的 id 在 HTML 里根本不存在 ——
       `$()` 返回 null，后面 `.addEventListener` 直接抛异常，
       整个 `init()` 挂掉，页面看起来就是"空的但也不报错"。
    2. JS 请求的接口路径后端没注册 —— 只会静默 404 / 返回 `{}`。
    """
    import re



    root = _paths.SERVER
    web = os.path.join(root, "gamesrv", "web")
    with open(os.path.join(web, "devtools.html"), encoding="utf-8") as fh:
        html = fh.read()
    with open(os.path.join(web, "devtools.js"), encoding="utf-8") as fh:
        js = fh.read()
    with open(os.path.join(root, "gamesrv", "devtools.py"), encoding="utf-8") as fh:
        py = fh.read()

    bad = 0
    html_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
    # 运行时才创建的：`toast` 是提示条、`fatal` 是兜底报错红条，
    # `ch-*` 是作弊面板按 CHEATS 配置生成的输入框
    html_ids.add("toast")
    html_ids.add("fatal")
    html_ids |= set(re.findall(r"id:\s*'([A-Za-z0-9_-]+)'", js))
    used_ids = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", js))
    used_ids |= set(re.findall(r"getElementById\('([A-Za-z0-9_-]+)'\)", js))
    missing = sorted(used_ids - html_ids)
    if missing:
        print(f"✗ devtools.js 引用了 HTML 里没有的 id: {missing}")
        bad += 1
    else:
        print(f"✓ 前端 id 引用一致（{len(used_ids)} 个）")

    # API 路径
    paths = set()
    for raw in re.findall(r"\b(?:api|post)\(\s*'([^']*)'", js):
        path = raw.split("?")[0]
        if path.startswith("/api/") or path == "/api/events":
            paths.add(path)
    # 后端注册的路径（含 /api 前缀的那些）
    registered = set(re.findall(r'router\.any\("(/[^"]+)"\)', py))
    unknown = sorted(p for p in paths
                     if p not in registered and ("/devtools" + p) not in registered)
    if unknown:
        print(f"✗ 前端请求了后端没注册的路径: {unknown}")
        bad += 1
    else:
        print(f"✓ 前端接口路径都有对应路由（{len(paths)} 个）")

    # 后台轮询：`setInterval(asyncFn)` 是个陷阱 —— setInterval 不管返回值，
    # async 函数一 reject（服务端重启时 fetch 必然 reject）就是一个
    # **unhandledrejection**，会往页顶那条兜底红条里一直追加。
    # 约定：只能通过 `poll()` 包一层，所以代码里 `setInterval(` 只该出现一次。
    # ⚠️ 注释里提到 setInterval 不算 —— 先剔掉行注释和块注释再数。
    stripped = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    stripped = "\n".join(ln for ln in stripped.split("\n")
                         if not ln.lstrip().startswith(("//", "*")))
    n_interval = len(re.findall(r"\bsetInterval\s*\(", stripped))
    if n_interval == 1:
        print("✓ setInterval 只在 poll() 里出现一次（后台轮询不会漏成 unhandledrejection）")
    else:
        print(f"✗ setInterval 出现了 {n_interval} 次（应该只有 poll() 里那一次）"
              f" —— 直接用 setInterval(asyncFn) 会漏 unhandledrejection")
        bad += 1

    # 兜底红条必须关得掉、有上限 —— 否则它会一直挂在页顶挡着工具栏
    if "FATAL_MAX" in js and "关闭" in js:
        print("✓ 兜底红条可关闭、有行数上限")
    else:
        print("✗ 兜底红条没有关闭按钮 / 行数上限（会一直挂在页顶）")
        bad += 1
    return bad


def event_stream_checks() -> int:
    """事件流（长轮询）的两个坑，都踩过一次：

    1. **游标跑到服务端前面 → 面板永久空白**。
       前端的 `state.since` 是**无条件采纳**服务端回的 `seq` 的。
       服务端重启后 `_seq` 从 1 重新数，而前端还抱着上一次进程的 `since=570`；
       老代码回的是 `max(since, latest)` = 570，于是永远问 `since=570`、
       服务端永远答「没有新事件」—— 表现就是「控制台页里没有东西、流量页也不再动了」。
       现在服务端发现 `since > latest` 会**回退到 0 重放整个缓冲**，并且必须**立刻**返回
       （不能还把 25 秒的长轮询等满）。

    2. **单条日志 131KB → 浏览器卡死**。
       客户端探针会把整个登录响应 dump 成 hex：
       `CRYPT base64Decode(b64len=131136 hex=436b...)`。
       入库前必须过 `devbus._clip()`。
    """
    bad = 0

    # 1. 超前游标：必须立刻返回、reset=True、seq 回退到 latest
    t0 = time.time()
    got = call("/api/events?since=999999&timeout=25&limit=2000")
    dt = time.time() - t0
    if not isinstance(got, dict) or "__err" in got or "__http" in got:
        print(f"✗ 超前游标：请求失败 {got}")
        return bad + 1
    latest = got.get("latest")
    if not got.get("reset"):
        print(f"✗ 超前游标：reset 不是 True（服务端没认出游标超前）{str(got)[:160]}")
        bad += 1
    elif got.get("seq") != latest:
        print(f"✗ 超前游标：seq={got.get('seq')} 应回退到 latest={latest}")
        bad += 1
    elif dt > 5:
        print(f"✗ 超前游标：还等满了长轮询（{dt:.1f}s），应该立刻返回")
        bad += 1
    else:
        print(f"✓ 超前游标 since=999999 -> 立刻返回（{dt:.2f}s）reset=True "
              f"seq 回退到 {got.get('seq')}，重放 {len(got.get('events') or [])} 条")
    if got.get("seq") != latest:
        bad += 1

    # 正常游标（=latest）时不该 reset，也不该立刻返回一堆历史
    got2 = call(f"/api/events?since={latest}&timeout=0")
    if isinstance(got2, dict) and not got2.get("reset") and not (got2.get("events") or []):
        print(f"✓ 正常游标 since={latest} -> 不 reset、无历史重放")
    else:
        print(f"✗ 正常游标表现异常：{str(got2)[:160]}")
        bad += 1

    # 2. 事件里不该有超长字段
    got3 = call("/api/events?since=0&timeout=0&limit=2000")
    longest, where = 0, ""
    for ev in (got3.get("events") or []):
        for k, v in ev.items():
            s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            if len(s) > longest:
                longest, where = len(s), f"{ev.get('kind')}.{k}"
    limit = 40000          # 单个字符串上限 4000；整条事件留点余量
    if longest <= limit:
        print(f"✓ 事件字段长度：最长 {longest} 字符（{where}），没有超长 hex dump")
    else:
        print(f"✗ 事件字段太长：{where} = {longest} 字符（上限约 {limit}）"
              f" —— devbus._clip 没生效？")
        bad += 1

    # 3. favicon 不该落到 fallback（否则日志面板会被自己的 warning 刷屏）
    #    ⚠️ 是**站点根**的 /favicon.ico，不是 BASE 底下的 —— 浏览器要的是根那个。
    origin = f"http://127.0.0.1:{config.CDN_PORT}"
    try:
        req = urllib.request.Request(origin + "/favicon.ico")
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.getcode()
        if code == 204:
            print("✓ /favicon.ico -> 204（不再刷「CDN 未处理请求」）")
        else:
            print(f"✗ /favicon.ico -> {code}（期望 204）")
            bad += 1
    except Exception as exc:  # noqa: BLE001
        print(f"✗ /favicon.ico: {exc}")
        bad += 1

    return bad


def main() -> int:
    bad = 0
    skipped = 0

    bad += static_checks()
    print()

    print("---- 事件流 ----")
    bad += event_stream_checks()
    print()

    # ---- 静态页面 ----
    for path, expect in (("/", "<!DOCTYPE html>"), ("/app.js", "pump"), ("/app.css", ".row")):
        req = urllib.request.Request(BASE + path)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            print(f"✗ {path}: {exc}")
            bad += 1
            continue
        if expect in text:
            print(f"✓ {path:34s} {len(text)} 字节")
        else:
            print(f"✗ {path:34s} 内容里找不到 {expect!r}")
            bad += 1

    # ---- 不需要客户端的接口 ----
    plain = [
        ("/api/overview", None),
        ("/api/players", None),
        ("/api/routes", None),
        ("/api/backups", None),
        ("/api/events?since=0&timeout=0", None),
        ("/api/table?name=table_quest&limit=3", None),
        ("/api/table?name=table_soldier&q=sasm&limit=3", None),
    ]
    for path, body in plain:
        got = call(path, body)
        ok = isinstance(got, dict) and got.get("ok") and "__err" not in got and "__http" not in got
        if ok:
            print(f"✓ {path:40s} {json.dumps(got, ensure_ascii=False)[:110]}")
        else:
            print(f"✗ {path:40s} {json.dumps(got, ensure_ascii=False)[:220]}")
            bad += 1

    # ---- 需要游戏在跑 ----
    overview = call("/api/overview")
    alive = bool(overview.get("probeAlive"))
    print(f"\n客户端探针：{'在线' if alive else '离线（下面几条需要开着游戏）'}")

    live = [
        ("/api/console", {"code": "1+1", "timeout": 12}),
        ("/api/console", {"code": "JSON.stringify(Object.keys(dataManager.character.soldiers).length)",
                          "timeout": 12}),
        ("/api/dict", {"q": "指挥部", "limit": 20}),
        ("/api/tables?refresh=1", None),
    ]
    for path, body in live:
        if not alive:
            print(f"- {path:40s} 跳过（游戏没开）")
            skipped += 1
            continue
        got = call(path, body)
        ok = isinstance(got, dict) and got.get("ok")
        if ok:
            print(f"✓ {path:40s} {json.dumps(got, ensure_ascii=False)[:140]}")
        else:
            print(f"✗ {path:40s} {json.dumps(got, ensure_ascii=False)[:220]}")
            bad += 1

    # ---- 中文往返（专门验 UTF-8 没被吃掉） ----
    if alive:
        got = call("/api/console", {"code": "table_dictionary['201']", "timeout": 12})
        value = str(got.get("value") or "")
        if "指挥部" in value:
            print(f"✓ 中文往返 OK：{value}")
        else:
            print(f"✗ 中文往返失败：{value!r}")
            bad += 1

    # ---- 作弊 / 快照（只做无副作用的） ----
    if alive:
        created = call("/api/backup", {"label": "selftest"})
        if created.get("ok"):
            name = created["name"]
            print(f"✓ /api/backup                        建了快照 {name}")
            back = call("/api/restore", {"name": name})
            if back.get("ok"):
                print("✓ /api/restore                       回滚成功")
            else:
                print(f"✗ /api/restore {back}")
                bad += 1
        else:
            print(f"✗ /api/backup {created}")
            bad += 1

    print()
    if bad:
        print(f"有 {bad} 项失败 ❌" + (f"（另有 {skipped} 项跳过）" if skipped else ""))
    else:
        print("全部通过 ✅" + (f"（{skipped} 项因游戏没开而跳过）" if skipped else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
