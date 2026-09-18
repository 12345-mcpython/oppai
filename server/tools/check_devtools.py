"""devtools 自测：不开浏览器把每个接口都打一遍。

    python tools/check_devtools.py

服务端在跑就行（`python tools\\serve.py`）。
需要客户端探针的接口在游戏没开时会报「探针没在响应」，那不算失败 ——
脚本会把「需要游戏」和「不需要游戏」分开统计。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

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

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    return bad


def main() -> int:
    bad = 0
    skipped = 0

    bad += static_checks()
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
