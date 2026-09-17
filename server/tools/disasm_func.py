r"""按函数名反汇编 jsc。

    python tools\disasm_func.py <file.jsc> [函数名关键字] [--all] [--list]

例：
    python tools\disasm_func.py ..\..\apk\zcsmw\assets\src\manager\datamanager.jsc cb4AfterLogin
    python tools\disasm_func.py ..\..\apk\zcsmw\assets\src\data\player.jsc --list
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jsc_disasm as J  # noqa: E402


def walk(sc):
    yield sc
    for c in getattr(sc, "children", []):
        yield from walk(c)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("pattern", nargs="?", default="")
    ap.add_argument("--list", action="store_true", help="只列出函数")
    ap.add_argument("--all", action="store_true", help="反汇编全部函数")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    root = J.read_file(args.path)
    funcs = [s for s in walk(root) if getattr(s, "name", None)]

    if args.list or (not args.pattern and not args.all):
        for s in funcs:
            print(f"{s.length:6d}B  {len(s.atoms):4d} atoms  {s.name}")
        return 0

    targets = funcs if args.all else [s for s in funcs if args.pattern in s.name]
    if not targets:
        print(f"没找到匹配 {args.pattern!r} 的函数", file=sys.stderr)
        return 1

    for i, s in enumerate(targets):
        if args.limit and i >= args.limit:
            break
        print(f"// ===== {s.name}  ({s.length}B) =====")
        print(f"// params/vars: " + ", ".join(f"{n}#{k}" for n, k in s.bindings))
        print(f"// atoms: {s.atoms}")
        for pc, text in J.disassemble(s):
            print(f"{pc:6d}  {text}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
