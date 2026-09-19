"""在**所有** .jsc 里按字符串原子反查：这个 key / route / 方法名在哪个文件、哪个函数里用过。

    python script\\jsc_find.py useGiftStatus
    python script\\jsc_find.py favor.usegift --ns        # 按命名空间正则找（favor\\..*）
    python script\\jsc_find.py charKey --dir assets\\src\\ui

## 为什么需要它

`jsc_strings.py` / `jsc_funcs.py` 都是**单个文件**的视角，可逆向时最常见的
第一个问题恰恰是「这个 key 是谁在读」：

    * route 的响应形状 —— 先找谁处理 `secret.<key>`（`src/util/server.js` 的
      responseConfig），再看那个 callback 读了哪些字段
    * 反查一张表被谁用
    * 确认某个字段**压根没人读**（那就不用回给客户端）

裸 `grep` 对 .jsc 没用（它是二进制，ripgrep 默认直接跳过），所以要么按
原子表扫、要么白忙。这个脚本就是那条扫的路。
"""

from __future__ import annotations

import argparse
import os
import re
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

import jsc_funcs  # noqa: E402

# 默认扫这些子树，按源码/编译产物的实际布局来
DEFAULT_DIRS = ("assets/src", "assets/srcex", "assets/script")


def iter_jsc(root: str, subdirs):
    for sub in subdirs:
        base = os.path.join(root, sub)
        for r, _dirs, fs in os.walk(base):
            for f in fs:
                if f.endswith(".jsc"):
                    yield os.path.join(r, f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="按原子反查 .jsc")
    ap.add_argument("needle", help="要查的字符串（默认按子串匹配）")
    ap.add_argument("--exact", action="store_true", help="整个原子完全相同才算命中")
    ap.add_argument("--regex", action="store_true", help="needle 当正则用")
    ap.add_argument("--func", action="store_true", help="连所在函数名一起打印")
    ap.add_argument("--dir", action="append", default=None,
                    help=f"限定子树（可多次；默认 {', '.join(DEFAULT_DIRS)}）")
    ap.add_argument("--game", default=_paths.GAME, help="客户端根目录")
    args = ap.parse_args(argv)

    if args.regex:
        pat = re.compile(args.needle)
        hit = lambda s: bool(pat.search(s))          # noqa: E731
    elif args.exact:
        hit = lambda s: s == args.needle             # noqa: E731
    else:
        hit = lambda s: args.needle in s             # noqa: E731

    subdirs = args.dir or list(DEFAULT_DIRS)
    files = sorted(iter_jsc(args.game, subdirs))
    total_files = total_hits = 0
    for path in files:
        try:
            funcs = jsc_funcs.parse_functions(path)
        except Exception:  # noqa: BLE001  坏文件不该拖垮整轮扫描
            continue
        rel = os.path.relpath(path, args.game)
        shown = False
        for name, atoms in funcs:
            hits = [a for a in atoms if hit(a)]
            if not hits:
                continue
            if not shown:
                print(f"\n=== {rel} ===")
                shown = True
                total_files += 1
            total_hits += len(hits)
            if args.func:
                # 一个函数里可能同一个原子出现多次，去重保序
                uniq = list(dict.fromkeys(hits))
                print(f"  {name}\n      {' '.join(uniq)}")
            else:
                print(f"  {name}: {' '.join(dict.fromkeys(hits))}")
    print(f"\n共 {total_hits} 处命中，分布在 {total_files} 个文件"
          f"（扫了 {len(files)} 个 .jsc）")
    return 0 if total_hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
