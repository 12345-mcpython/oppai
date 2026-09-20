r"""打印 jsc 里各 script 的 bindings / 槽位，用于把 `getaliasedvar slot=N` 对回变量名。

    python script\jsc_scope.py <file.jsc>

背景（详见 server\docs\reverse-engineering.md §1.8）：

* 模块（文件顶层那个 `Xxx<` 函数）的绑定按声明序排在**槽位 2 起**
  （0/1 被 `this` / `arguments` 两个保留槽占掉），也就是
  `bindings[i]` ↔ `slot(i+2)`；
* 子函数里 `getaliasedvar hops=0 slot=N` 的 N 就是它**最近那层 CallObject** 的槽位 ——
  函数自己没有 CallObject（内部没有 lambda）时，hops=0 直接落到模块作用域。
"""

from __future__ import annotations

import argparse
import os
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jsc_disasm as J  # noqa: E402


def walk(sc, depth=0):
    yield sc, depth
    for c in getattr(sc, "children", []):
        yield from walk(c, depth + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--depth", type=int, default=1, help="打到第几层（默认 1）")
    ap.add_argument("--only", default="", help="只看名字里含这个子串的 script")
    args = ap.parse_args()

    root = J.read_file(args.path)
    print("// %s" % args.path)
    for s, d in walk(root):
        name = getattr(s, "name", "<root>")
        if args.only and args.only not in name:
            continue
        if d > args.depth:
            continue
        print("%s%-46s nargs=%d nvars=%d nslots=%d" % (
            "  " * d, name, s.nargs, s.nvars, s.nslots))
        if s.bindings:
            for i, (n, _kind) in enumerate(s.bindings):
                # ⚠️ slot = i + 2 这条只在**模块作用域**（文件顶层那个 `Xxx<` 函数）验证过：
                #    0/1 是两个保留槽。子函数的 CallObject 槽位另有偏移，别照抄。
                print("%s    #%-3d slot%-4s %s" % (
                    "  " * d, i, (i + 2) if d <= 1 else "?", n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
