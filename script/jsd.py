r"""引擎自带的远程 JS 调试器 —— 命令行客户端。

    python tools\jsd.py tabs                       # 看看连不连得上
    python tools\jsd.py sources data/charcenter    # 列出匹配的脚本
    python tools\jsd.py repl                       # 交互（推荐）
    python tools\jsd.py demo probe.js 78           # 一条龙演示：下断点 -> 命中 -> 栈 -> 求值

## 这是什么

引擎（cocos2d-js 3.6 / SpiderMonkey 33）的 `ScriptingCore` 里**本来就带一个完整的
JS 调试器**，用的是 **SpiderMonkey Debugger API**（`JS_DefineDebuggerObject`），
外面套一层 **Firefox 远程调试协议**，在 TCP 上说话。断点、单步、调用栈、作用域、
暂停时求值全都有，而且是**引擎级**的 —— 游戏主循环真的会停住。

默认关着。`build\enable_js_debugger.py` 打开它（引擎侧），
`tools\patch_js_debugger.py` 修几个「这套 script.js 比 SM 33 新」导致的坑。
来龙去脉见 `docs/engine-debug.md`。

协议和客户端实现都在 `gamesrv/jsdlink.py`（devtools 的「调试器」面板用的是同一个）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request

# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamesrv import config                                    # noqa: E402
from gamesrv.jsdlink import (                                 # noqa: E402


    ADB_SERIAL, DEFAULT_PORT, JSD, ProtocolError, find_source, frames_brief,
    setup_adb_forward,
)

CDN = f"http://127.0.0.1:{config.CDN_PORT}"


def p(msg=""):
    print(msg, flush=True)


def print_frames(frames: list) -> None:
    if not frames:
        p("  (没有栈帧)")
        return
    for fr in frames_brief(frames):
        loc = f"{fr['url']}:{fr['line']}"
        mark = "→" if fr["depth"] == 0 else " "
        p(f"  {mark} #{fr['depth']:<2} {fr['name']:<30} {loc}")
        p(f"        actor={fr['actor']}")


def finish_of(reply: dict) -> dict:
    return ((reply.get("why") or {}).get("frameFinished") or {})


# ---------------------------------------------------------------------------

def cmd_tabs(jsd: JSD) -> int:
    tabs = jsd.list_tabs()
    if not tabs:
        p("没有 tab")
        return 1
    for i, tab in enumerate(tabs):
        p(f"[{i}] actor={tab.get('actor')}  title={tab.get('title')}  url={tab.get('url')}")
    return 0


def cmd_sources(jsd: JSD, needle: str = "") -> int:
    jsd.attach()
    p(f"已 attach，线程 {jsd.thread}（游戏现在是冻结的）")
    sources = jsd.list_sources()
    hits = [s for s in sources if not needle
            or needle in (s.get("url") or "")
            or needle.replace("/", "\\") in (s.get("url") or "")]
    p(f"共 {len(sources)} 个脚本，匹配 {len(hits)} 个：")
    for s in hits[:300]:
        p(f"  {s.get('actor'):<16} {s.get('url')}")
    jsd.detach()
    return 0


def cmd_demo(jsd: JSD, needle: str, line: int, trigger: str | None, wait: float) -> int:
    """一条完整的演示：attach -> 找脚本 -> 下断点 -> [触发] -> 命中 -> 栈 -> 求值。"""
    p("=" * 72)
    p("1) attach（这一步会把游戏冻住）")
    paused = jsd.attach()
    p(f"   线程 = {jsd.thread}   pauseActor = {jsd.pause_actor}")
    p(f"   暂停原因 = {json.dumps(paused.get('why'), ensure_ascii=False)}")

    p("")
    p("2) 列脚本")
    sources = jsd.list_sources()
    p(f"   共 {len(sources)} 个（jsc 的 url 是**构建机上的绝对路径**）")
    target = find_source(sources, needle)
    if not target:
        p(f"   ✗ 找不到含 {needle!r} 的脚本。挑几个看看：")
        for s in sources[:15]:
            p(f"     {s.get('url')}")
        jsd.detach()
        return 1
    url = target["url"]
    p(f"   选中 {url}")

    p("")
    p(f"3) 在 {url}:{line} 下断点（只能趁游戏暂停的时候下）")
    bp = jsd.set_breakpoint(url, line)
    actual = bp.get("actualLocation") or {}
    p(f"   bp actor = {bp.get('actor')}"
      + (f"   实际位置 = {actual.get('url')}:{actual.get('line')}" if actual else ""))

    if trigger:
        def fire():
            time.sleep(1.5)
            body = json.dumps({"code": trigger, "timeout": 8}).encode("utf-8")
            req = urllib.request.Request(CDN + "/devtools/api/console", data=body,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    p("   [触发] " + resp.read().decode()[:160])
            except Exception as exc:  # noqa: BLE001
                p(f"   [触发] {type(exc).__name__}（被断点冻住了，正常）")

        p("")
        p(f"4) 用 devtools 控制台触发一次： {trigger}")
        threading.Thread(target=fire, daemon=True).start()
    else:
        p("")
        p("4) 不额外触发，靠游戏自己的周期动作命中")

    p("")
    p(f"5) 恢复运行，等断点（最多 {wait:.0f} 秒）")
    jsd.resume()
    hit = jsd.wait_for_pause(timeout=wait)
    if not hit:
        p("   ✗ 超时没命中")
        jsd.detach()
        return 1
    p(f"   ★ 命中！{json.dumps(hit.get('why'), ensure_ascii=False)}")

    p("")
    p("6) 调用栈")
    frames = jsd.frames()
    print_frames(frames)

    if frames:
        p("")
        p("7) 在栈顶帧求值（等于在断点处开了一个 JS 控制台）")
        top = frames[0]["actor"]
        for expr in ["typeof res", "typeof line",
                     "dataManager && dataManager.player && dataManager.player.lv",
                     "Object.keys(dataManager.character.soldiers).length"]:
            try:
                p(f"   {expr:52s} => "
                  + json.dumps(finish_of(jsd.evaluate(expr, frame=top)), ensure_ascii=False)[:120])
            except ProtocolError as exc:
                p(f"   {expr:52s} !! {exc}")

    p("")
    p("8) 摘下来，游戏继续")
    try:
        jsd.resume()
    except ProtocolError:
        pass
    jsd.detach()
    p("=" * 72)
    return 0


# ---------------------------------------------------------------------------

REPL_HELP = """\
命令：
  tabs                     列 tab
  attach                   附加（会把游戏冻住）
  sources [关键词]         列脚本（jsc 的 url 是构建机绝对路径，用关键词筛）
  bp <url子串> <行号>      下断点（要先 attach / 处在暂停态）
  bps                      已下的断点
  c | resume               继续跑
  si | step-in             单步进入
  so | step-over           单步跳过
  su | step-out            单步跳出
  pause                    暂停（interrupt；注意这样停下时拿不到栈帧）
  bt | frames              调用栈
  eval <表达式>            在栈顶帧求值
  env                      栈顶帧有没有作用域
  pkt                      收一个包并打印
  raw <json>               直接发一个包
  log                      打印收发日志
  detach                   摘下（游戏继续）
  q | quit                 退出
"""


def repl(jsd: JSD) -> int:
    p(f"连上 {jsd.host}:{jsd.port}。`help` 看命令，`q` 退出。")
    while True:
        try:
            line = input("jsd> ").strip()
        except (EOFError, KeyboardInterrupt):
            p()
            break
        if not line:
            continue
        parts = line.split(None, 1)
        cmd, rest = parts[0], (parts[1] if len(parts) > 1 else "")
        try:
            if cmd in ("q", "quit", "exit"):
                break
            elif cmd == "help":
                p(REPL_HELP)
            elif cmd == "tabs":
                cmd_tabs(jsd)
            elif cmd == "attach":
                reply = jsd.attach(int(rest) if rest.strip().isdigit() else 0)
                p(f"paused: {json.dumps(reply.get('why'), ensure_ascii=False)}  thread={jsd.thread}")
            elif cmd == "sources":
                if not jsd.thread:
                    p("先 attach")
                    continue
                srcs = jsd.list_sources()
                hits = [s for s in srcs if not rest
                        or rest in (s.get("url") or "")
                        or rest.replace("/", "\\") in (s.get("url") or "")]
                for s in hits[:300]:
                    p(f"  {s.get('actor'):<16} {s.get('url')}")
                p(f"  ({len(hits)}/{len(srcs)})")
            elif cmd == "bp":
                args = rest.split()
                if len(args) < 2:
                    p("用法: bp <url子串> <行号>")
                    continue
                src = find_source(jsd.sources or [], args[0])
                if not src:
                    p("找不到脚本，先跑一次 sources")
                    continue
                reply = jsd.set_breakpoint(src["url"], int(args[1]))
                p(f"bp {src['url']}:{args[1]} -> {json.dumps(reply, ensure_ascii=False)[:200]}")
            elif cmd == "bps":
                for b in jsd.breakpoints:
                    p(f"  {b['url']}:{b['line']}  actor={b['actor']}  actual={b['actual']}")
            elif cmd in ("c", "resume"):
                jsd.resume()
                p("resumed")
            elif cmd in ("si", "step-in", "so", "step-over", "su", "step-out"):
                limit = {"si": "step", "step-in": "step",
                         "so": "next", "step-over": "next",
                         "su": "finish", "step-out": "finish"}[cmd]
                jsd.resume(limit)
                hit = jsd.wait_for_pause(10)
                p(f"停: {json.dumps((hit or {}).get('why'), ensure_ascii=False)}")
                print_frames(jsd.frames())
            elif cmd == "pause":
                jsd.interrupt()
                p("paused（interrupt 停在调试器自己的循环里，拿不到游戏栈帧）")
            elif cmd in ("bt", "frames"):
                print_frames(jsd.frames())
            elif cmd == "eval":
                frames = jsd.frames()
                r = jsd.evaluate(rest, frame=frames[0]["actor"] if frames else None)
                p(json.dumps(finish_of(r), ensure_ascii=False))
            elif cmd == "env":
                frames = jsd.frames()
                p(json.dumps((frames[0].get("environment") if frames else None),
                             ensure_ascii=False)[:400])
            elif cmd == "pkt":
                p(json.dumps(jsd.recv(timeout=5), ensure_ascii=False))
            elif cmd == "raw":
                jsd.send(json.loads(rest))
                p(json.dumps(jsd.recv(timeout=10), ensure_ascii=False))
            elif cmd == "log":
                for entry in jsd.log:
                    p("  " + entry)
            elif cmd == "detach":
                p(json.dumps(jsd.detach(), ensure_ascii=False))
            else:
                p(f"不认识的命令 {cmd!r}，`help` 看看")
        except ProtocolError as exc:
            p(f"!! {exc}")
        except Exception as exc:  # noqa: BLE001
            p(f"!! {type(exc).__name__}: {exc}")
    return 0


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="引擎远程 JS 调试器客户端")
    ap.add_argument("command", nargs="?", default="repl",
                    help="tabs / sources / demo / repl / <原始 JSON 包>")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-adb", action="store_true", help="不做 adb forward")
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    if args.no_adb:
        os.environ["GS_JSD_NO_ADB"] = "1"
    setup_adb_forward(args.port)

    try:
        jsd = JSD(args.host, args.port, timeout=args.timeout)
        greeting = jsd.connect()
    except Exception as exc:  # noqa: BLE001
        p(f"连不上 {args.host}:{args.port} —— {exc}")
        p("")
        p("检查：")
        p("  1) 引擎里开了调试器吗？logcat 里找 `[oppai] enableDebugger port=`")
        p(f"     adb -s {ADB_SERIAL} logcat -d -v brief | findstr /i \"oppai.*Debugger\"")
        p(f"  2) 端口转发做了吗？ adb forward tcp:{args.port} tcp:{args.port}")
        p("  3) 同一时间只能接一个客户端，别的地方还连着？")
        return 2

    p(f"greeting: {json.dumps(greeting, ensure_ascii=False)}")

    try:
        if args.command == "tabs":
            return cmd_tabs(jsd)
        if args.command == "sources":
            return cmd_sources(jsd, args.args[0] if args.args else "")
        if args.command == "demo":
            needle = args.args[0] if args.args else "probe.js"
            line = int(args.args[1]) if len(args.args) > 1 else 78
            trigger = args.args[2] if len(args.args) > 2 else None
            wait = float(args.args[3]) if len(args.args) > 3 else 30.0
            return cmd_demo(jsd, needle, line, trigger, wait)
        if args.command == "repl":
            return repl(jsd)
        packet = json.loads(args.command)
        jsd.send(packet)
        p(json.dumps(jsd.recv(timeout=args.timeout), ensure_ascii=False))
        return 0
    finally:
        jsd.close()


if __name__ == "__main__":
    raise SystemExit(main())
