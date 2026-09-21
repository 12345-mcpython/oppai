r"""离线抽表：把 `assets/src/table/*.jsc` 直接转成服务端 JSON（不需要游戏在跑）。

    python script\\decompile_table.py tablequestreward
    python script\\decompile_table.py tablequestreward tablequestcondition   # 一次多张
    python script\\decompile_table.py tablequestreward --out out\\x.json --keep-js

常规路径是 `extract_client_tables.py` —— 它走探针，让**运行中的客户端**把
`window.table_*` 序列化成 JSON。但有两种情况走不通：

* 装的那份包里没有 probe.js（比如模拟器上的旧包）；
* 手边根本没开游戏，只是想补一张表。

表脚本其实就是一句 `table_xxx = { ... };` 的对象字面量，所以可以离线来：
**`jsc_decompile.py` 反编译成 JS → `node --check` 验语法 → node 求值 → JSON**。
产物落 `server/gamesrv/data/<全局名>.json`，和探针路径的形状完全一致
（探针也是 `JSON.stringify` 同一个对象），所以要提交进仓库。

⚠️ 三个坑（都踩过）：

1. **必须先 `node --check`**：反编译器对个别字节码形状会吐出不合法 JS
   （`assets/src/**` 里 3/575 个文件过不了），不合法的话求值那步才炸，报错还难读。
2. **全局名要自己认**：文件名是 `tablequestreward.jsc`，全局名却是 `table_quest_reward`
   —— 这里从反编译结果里扫 `^\s*(table_\w+)\s*=` 自动认（认不到才要求手填 `--name`）。
3. **求值用 `new Function`**：表脚本在 sloppy mode 下是裸赋值（隐式全局），
   `new Function(src + "; return <name>;")()` 能拿到；`import`/`require` 不行（它不是模块）。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

ROOT = _paths.ROOT
GAME = _paths.GAME
DATA = os.path.join(_paths.SERVER, "gamesrv", "data")
TABLE_DIR = os.path.join(GAME, "assets", "src", "table")
DECOMPILER = os.path.join(_paths.SCRIPT, "jsc_decompile.py")
NODE_DRIVER = os.path.join(_paths.OUT, "_table_eval.js")

_ASSIGN = re.compile(r"(?m)^\s*(?:var|let|const)?\s*(table_\w+)\s*=")


def _node() -> str:
    return "node"


def eval_table(js_path: str, global_name: str) -> object:
    """把反编译出来的表 JS 求值成 Python 对象（借用 node）。"""
    driver = (
        'const fs=require("fs");\n'
        'const src=fs.readFileSync(process.argv[2],"utf8");\n'
        'const name=process.argv[3];\n'
        'const fn=new Function(src + "\\n; return (typeof " + name + " !== \'undefined\') ? " + name + " : null;");\n'
        'process.stdout.write(JSON.stringify(fn()));\n'
    )
    os.makedirs(os.path.dirname(NODE_DRIVER), exist_ok=True)
    with io.open(NODE_DRIVER, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(driver)
    r = subprocess.run([_node(), NODE_DRIVER, js_path, global_name],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:400])
    return json.loads(r.stdout or "null")


def one(name: str, out_path: str | None, keep_js: bool, want_name: str | None) -> bool:
    jsc = os.path.join(TABLE_DIR, name if name.endswith(".jsc") else name + ".jsc")
    if not os.path.isfile(jsc):
        print(f"!! 找不到 {jsc}")
        return False
    js = os.path.join(_paths.OUT, os.path.splitext(os.path.basename(jsc))[0] + ".js")
    r = subprocess.run([sys.executable, DECOMPILER, jsc, "-o", js],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0 or not os.path.isfile(js):
        print(f"!! 反编译失败：{(r.stderr or r.stdout or '').strip()[:300]}")
        return False
    c = subprocess.run([_node(), "--check", js], capture_output=True, text=True, encoding="utf-8")
    if c.returncode != 0:
        print(f"!! 反编译出来的 JS 语法不合法（换探针路径抽这张表）：{(c.stderr or '').strip()[:200]}")
        return False
    if not want_name:
        with io.open(js, encoding="utf-8") as fh:
            head = fh.read(4000)
        m = _ASSIGN.search(head)
        if not m:
            print(f"!! 认不出全局表名（用 --name 指定）")
            return False
        want_name = m.group(1)
    try:
        data = eval_table(js, want_name)
    except Exception as exc:                              # noqa: BLE001
        print(f"!! 求值失败：{exc}")
        return False
    if not isinstance(data, dict) or not data:
        print(f"!! {want_name} 求值结果不是非空对象：{type(data)}")
        return False
    dst = out_path or os.path.join(DATA, want_name + ".json")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with io.open(dst, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, ensure_ascii=False))
    size = os.path.getsize(dst)
    print(f"  {os.path.relpath(jsc, ROOT)} -> {os.path.relpath(dst, ROOT)}"
          f"（{want_name}，{len(data)} 行，{size / 1024:.1f} KB）")
    if not keep_js:
        try:
            os.remove(js)
        except OSError:
            pass
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="离线把 table jsc 转成服务端 JSON")
    ap.add_argument("names", nargs="+", help="表文件名（可带 .jsc），如 tablequestreward")
    ap.add_argument("--name", default=None, help="客户端全局名（默认从反编译结果里认）")
    ap.add_argument("--out", default=None, help="输出路径（默认 server/gamesrv/data/<全局名>.json）")
    ap.add_argument("--keep-js", action="store_true", help="保留反编译出来的中间 .js")
    args = ap.parse_args()
    ok = True
    for n in args.names:
        ok = one(n, args.out if len(args.names) == 1 else None, args.keep_js, args.name) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
