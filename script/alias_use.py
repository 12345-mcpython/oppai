r"""列出某个 jsc 里所有 `getaliasedvar/setaliasedvar` 的槽位和上下文。

    python script\alias_use.py <file.jsc> [槽位...]

用途：把 `getaliasedvar hops=0 slot=6` 这种「数字槽位」和实际用法对起来 ——
上下文里能看到它被当函数调、被赋给哪个属性、和什么常量比较。
配合 `jsc_scope.py`（把槽位对回变量名）一起用。
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
    ap.add_argument("slots", nargs="*", type=int, help="只看这些槽位（不填=全部）")
    ap.add_argument("--func", default="", help="只看名字里含这个子串的函数")
    ap.add_argument("--ctx", type=int, default=4, help="上下文各取几条指令（默认 4）")
    args = ap.parse_args()

    root = J.read_file(args.path)
    for s, _d in walk(root):
        name = getattr(s, "name", "<root>")
        if args.func and args.func not in name:
            continue
        rows = J.disassemble(s)
        for i, (pc, text) in enumerate(rows):
            if "aliasedvar" not in text:
                continue
            slot = int(text.rsplit("slot=", 1)[1])
            if args.slots and slot not in args.slots:
                continue
            lo = max(0, i - args.ctx)
            ctx = " | ".join(t for _, t in rows[lo:i + args.ctx + 1])
            print("%-46s pc=%-5d %s" % (name, pc, ctx))
    return 0


if __name__ == "__main__":
    sys.exit(main())
