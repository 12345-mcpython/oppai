"""从 SpiderMonkey 33.1.1 的 vm/Opcodes.h 生成操作码表。"""

from __future__ import annotations

import re
import sys

SRC = r"E:\code\apk\sm\vm_Opcodes.h"
OUT = r"E:\code\python\game_server\tools\_opcodes_gen.py"

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
    src = open(SRC, "r", encoding="utf-8", errors="replace").read()
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
    open(OUT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"解析到 {len(rows)} 个操作码 -> {OUT}")
    for val, name, length, jof in rows:
        if val in (81, 24, 25):
            print(f"  {val} {name} len={length} jof={jof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
