"""把客户端里能当 route 的字符串全抽出来，和服务端已实现的对一遍。

    python script\\route_gap.py            # 只看服务端日志里确认的缺口
    python script\\route_gap.py --static   # 静态扫 assets/src 里所有 route 字符串

## 为什么需要静态扫

日志只能抓到「实际被调用过」的 route。客户端是按需调用的 —— 打开某个面板才会
打对应的 route，所以只测过主界面的话，商店/好友/公会那些 route 一个都不会出现。
静态扫能把「客户端**可能**打的 route」全列出来。

## route 长什么样

`<命名空间>.<动词>`，例如 `boss.getbosslist`、`char.upgradesoldierlv`。
`.jsc` 的字符串原子是 ASCII 存的，直接按正则捞就行；捞完按命名空间分组，
人眼一扫就知道哪些是 route、哪些是 `cc.director` 这种引擎 API。
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = os.path.join(ROOT, "game")
LOG = os.path.join(ROOT, "server", "var", "logs", "server.log")
API = "http://127.0.0.1:18080/devtools"

# 引擎 / 库的命名空间，捞出来也没意义
NOISE = {
    "cc", "ccs", "ccui", "ccexp", "ccui", "jsb", "js", "op", "sp", "spine",
    "sys", "window", "console", "document", "Math", "JSON", "Object", "Array",
    "String", "Number", "Boolean", "Date", "Promise", "Error", "this", "self",
    "plugin", "anysdk", "anysdkagent", "AgentManager", "io", "os", "util", "utils",
    "localStorage", "sessionStorage", "Event", "EventTarget", "performance",
    "navigator", "location", "setTimeout", "setInterval", "clearTimeout",
    # 第三方 JS 库 / 渠道 SDK 的 JS 接口 —— 它们不是「服务端 route」，
    # 名字长得像而已（quicksdk.login 是调渠道 SDK，不是发给我们的）
    "async", "underscore", "lodash", "moment", "quicksdk", "yesdk", "mtsdk",
    "swjpsdk", "august", "sixwaves", "bugly", "iab", "iap", "one", "yepay",
    "quick", "gdt", "kurogame", "tendcloud", "mob", "umeng", "talkingdata",
}

# 长得像 route 其实是**文件名**的：`storybg12.jpg`、`chapter02.png`
RE_FILELIKE = re.compile(r"\.(png|jpg|jpeg|csb|plist|mp3|mp4|json|js|jsc|fnt|ttf|bin|cfg|txt|xml|fsh|vsh|manifest)$", re.I)

RE_ROUTE = re.compile(rb"[a-z][a-z0-9]{1,15}\.[a-z][a-z0-9]{1,22}")
# 原子表里的 route 形态：两段小写标识符
RE_ATOM = re.compile(r"^[a-z][a-z0-9]{1,20}\.[a-z][a-z0-9]{1,24}$")
# 第二段是这些的肯定不是 route，是属性/方法名
JS_BUILTIN = {
    "prototype", "length", "constructor", "call", "apply", "push", "pop", "sort",
    "slice", "splice", "indexOf", "forEach", "map", "filter", "join", "split",
    "replace", "match", "test", "from", "to", "type", "why", "name", "id", "key",
    "add", "remove", "get", "set", "has", "create", "destroy", "purge", "register",
    "on", "off", "emit", "bind", "then", "catch", "resolve", "reject", "stringify",
    "parse", "floor", "ceil", "round", "random", "max", "min", "abs", "now",
}
RE_SEEN = re.compile(r"GAME route=([A-Za-z][\w.]*)")
RE_BAD = re.compile(r"未实现的 route[:：]\s*([A-Za-z][\w.]*)|未知 route[，,]\S*\s*data[:：]?\s*([A-Za-z][\w.]*)")


def server_routes():
    try:
        r = json.loads(urllib.request.urlopen(API + "/api/routes", timeout=10).read())
        return {x["route"] for x in r["routes"]}
    except Exception as exc:  # noqa: BLE001
        print(f"  (拿不到服务端路由表：{exc}）", file=sys.stderr)
        return set()


def static_routes():
    """{route: 出现它的文件数}。

    ⚠️ 必须走 `jsc_strings.parse()` 的**原子表**，不能在裸字节上正则 ——
    后者会把文件名片段（`achabeganeffect1.csb`）和半截原子（`ache.get`）也算进来，
    实测能捞出 4000+ 条噪音。
    """
    sys.path.insert(0, HERE)
    import jsc_strings  # noqa: E402

    files = collections.defaultdict(set)
    for sub in ("assets/src", "assets/srcex", "assets/script"):
        d = os.path.join(GAME, sub)
        for r, _, fs in os.walk(d):
            for f in fs:
                if not f.endswith(".jsc"):
                    continue
                p = os.path.join(r, f)
                try:
                    _size, atoms = jsc_strings.parse(p)
                except Exception:  # noqa: BLE001
                    continue
                for _off, _len, text in atoms:
                    if not RE_ATOM.match(text) or RE_FILELIKE.search(text):
                        continue
                    ns = text.split(".")[0]
                    if ns in NOISE or text.rsplit(".", 1)[1] in JS_BUILTIN:
                        continue
                    files[text].add(os.path.relpath(p, GAME))
    return {k: len(v) for k, v in files.items()}


def log_gap(tail: int, since_start: bool = True):
    """返回 (已实现, 缺口)。

    ⚠️ **必须按「服务端最后一次启动」切窗口**：路由是逐步补的，看全量会把
    「早就实现了」的当成缺口（实测把 6 条真实缺口报成 18 条）。
    默认从最后一次 `HTTP [game] listening` 之后开始算。
    """
    if not os.path.isfile(LOG):
        return collections.Counter(), collections.Counter()
    with io.open(LOG, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")

    start = 0
    if since_start:
        for i, ln in enumerate(lines):
            if "HTTP [game] listening" in ln:
                start = i
    window = "\n".join(lines[max(start, len(lines) - tail):])
    seen = collections.Counter(RE_SEEN.findall(window))
    bad = collections.Counter()
    for m in RE_BAD.finditer(window):
        bad[m.group(1) or m.group(2)] += 1
    for r in list(seen):
        if r in bad:
            del seen[r]
    return seen, bad


def main() -> int:
    ap = argparse.ArgumentParser(description="客户端 route vs 服务端实现")
    ap.add_argument("--static", action="store_true", help="静态扫客户端所有 route 字符串")
    ap.add_argument("--tail", type=int, default=200000,
                    help="最多回溯多少行；窗口起点还会被「服务端最后一次启动」截断")
    ap.add_argument("--all", action="store_true",
                    help="不要按服务端启动切窗口（会把早就实现了的当成缺口）")
    args = ap.parse_args()

    impl = server_routes()
    print(f"服务端已实现 {len(impl)} 条\n")

    if args.static:
        cand = static_routes()
        byns = collections.defaultdict(list)
        for r in cand:
            byns[r.split(".")[0]].append(r)
        print(f"=== 客户端静态出现的候选 route：{len(cand)} 条，{len(byns)} 个命名空间 ===")
        tot_missing = 0
        for ns in sorted(byns):
            rs = sorted(byns[ns])
            miss = [r for r in rs if r not in impl]
            mark = "" if not miss else f"   ← 缺 {len(miss)}"
            print(f"\n  [{ns}] {len(rs)} 条{mark}")
            for r in rs:
                flag = "  " if r in impl else "!!"
                print(f"    {flag} {r}   ({cand[r]} 个文件)")
            tot_missing += len(miss)
        print(f"\n  静态候选里服务端没实现的：{tot_missing} 条")
        return 0

    seen, bad = log_gap(args.tail, since_start=not args.all)
    print(f"=== 自服务端最后一次启动以来，客户端调过、且已实现：{len(seen)} 条 ===")
    for r, c in sorted(seen.items(), key=lambda kv: -kv[1]):
        print(f"  {c:>5} 次  {r}")
    print(f"\n=== 确认的缺口：{len(bad)} 条（客户端在要，服务端返回空 data）===")
    for r, c in sorted(bad.items(), key=lambda kv: -kv[1]):
        print(f"  {c:>5} 次警告  {r}")
    if not bad:
        print("  （无）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
