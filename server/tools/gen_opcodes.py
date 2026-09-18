"""从 SpiderMonkey 33.1.1 的 vm/Opcodes.h 生成操作码表。

    python tools/gen_opcodes.py [Opcodes.h 的路径]

不传路径时按下面的顺序找：
    1) 环境变量 GS_OPCODES_H
    2) <仓库同级>/apk/sm/vm_Opcodes.h（当初从 mozilla 源码里摘出来的那份）

输出固定写回 `tools/_opcodes_gen.py`（`jsc_disasm.py` 会 import 它）。
"""

from __future__ import annotations

import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE_DIR, "_opcodes_gen.py")


def default_src() -> str:
    env = os.environ.get("GS_OPCODES_H")
    if env:
        return env
    # tools/ -> game_server/ -> python/ -> code/ 下的 apk/sm/
    guess = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(BASE_DIR))),
                         "apk", "sm", "vm_Opcodes.h")
    return guess if os.path.exists(guess) else "vm_Opcodes.h"

JOF = {
    "JOF_BYTE": 0, "JOF_JUMP": 1, "JOF_ATOM": 2, "JOF_UINT16": 3,
    "JOF_TABLESWITCH": 4, "JOF_QARG": 6, "JOF_LOCAL": 7, "JOF_DOUBLE": 8,
    "JOF_UINT24": 12, "JOF_UINT8": 13, "JOF_INT32": 14, "JOF_OBJECT": 15,
    "JOF_REGEXP": 17, "JOF_INT8": 18, "JOF_ATOMOBJECT": 19, "JOF_SCOPECOORD": 21,
}

PAT = re.compile(
    r"macro\(JSOP_(\w+),\s*(\d+),\s*(?:\"[^\"]*\"|\w+),\s*[^,]+,\s*"
    r"(-?\d+),\s*-?\d+,\s*-?\d+,\s*([A-Za-z_0-9| ]+)\)"
)


def main():
    src_path = sys.argv[1] if len(sys.argv) > 1 else default_src()
    if not os.path.exists(src_path):
        print(f"找不到 Opcodes.h: {src_path}", file=sys.stderr)
        print("用法: python tools/gen_opcodes.py <vm/Opcodes.h 的路径>", file=sys.stderr)
        return 1
    src = open(src_path, "r", encoding="utf-8", errors="replace").read()
    rows = []
    for m in PAT.finditer(src):
        name = m.group(1).lower()
        val = int(m.group(2))
        length = int(m.group(3))
        base = m.group(4).strip().split("|")[0].strip()
        jof = JOF.get(base, 0)
        rows.append((val, name, length, jof))
    if not rows:
        print("没解析到任何操作码", file=sys.stderr)
        return 1
    rows.sort()
    lines = ["# 由 tools/gen_opcodes.py 从 SpiderMonkey 33.1.1 vm/Opcodes.h 生成",
             "OPCODES_LIST = ["]
    for val, name, length, jof in rows:
        lines.append(f"    ({val}, {name!r}, {length}, {jof}),")
    lines.append("]")
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print(f"解析到 {len(rows)} 个操作码 -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
