"""修一下加错位置的路径自举（一次性）。

migrate.py 把自举块插到了「前 80 行里最后一条 import 之后」——
import os
# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

而这些脚本恰恰是 `from gamesrv import config` 在 `sys.path.insert` **前面**，
于是自举插在了 gamesrv 导入之后，等于没生效。

改成：插在**第一条 `sys.path.insert` 之前**。
"""

from __future__ import annotations

import io
import os
import re

SCRIPT = r"E:\code\zcsmw\script"

MARK = "# --- 路径自举"
BOOT_LINES = 8          # 标记行 + 注释 + 5 行代码 + import 行


def strip_boot(text: str) -> str:
    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        if lines[i].startswith(MARK):
            # 往上回退掉可能的 `import os`
            if out and out[-1].strip() == "import os" and not any(
                    l.strip().startswith("import os") for l in out[:-1]):
                out.pop()
            i += 1
            while i < len(lines) and (lines[i].startswith("#") or
                                      lines[i].startswith("_d =") or
                                      lines[i].startswith("while _d") or
                                      lines[i].startswith("    _d =") or
                                      lines[i].startswith("sys.path.insert(0, _d)") or
                                      lines[i].startswith("import _paths")):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def insert_boot(text: str) -> str:
    lines = text.split("\n")
    idx = None
    for i, ln in enumerate(lines[:120]):
        if "sys.path.insert" in ln:
            idx = i
            break
    if idx is None:
        for i, ln in enumerate(lines[:120]):
            if re.match(r"^\s*(import|from)\s", ln) and "from __future__" not in ln:
                idx = i
        if idx is None:
            return text
        idx += 1
    has_os = any(re.match(r"^\s*import os\b", ln) for ln in lines[:idx])
    boot = []
    if not has_os:
        boot.append("import os")
    boot += [
        MARK + "（项目已重排：脚本在 script/、服务端在 server/）---",
        "# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。",
        "_d = os.path.dirname(os.path.abspath(__file__))",
        "while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, \"_paths.py\")):",
        "    _d = os.path.dirname(_d)",
        "sys.path.insert(0, _d)",
        "import _paths  # noqa: F401,E402",
        "",
    ]
    lines[idx:idx] = boot
    return "\n".join(lines)


n = 0
for dirpath, dirs, files in os.walk(SCRIPT):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for fn in files:
        if not fn.endswith(".py") or fn == "_paths.py":
            continue
        p = os.path.join(dirpath, fn)
        with io.open(p, encoding="utf-8") as fh:
            text = fh.read()
        if MARK not in text:
            continue
        fixed = insert_boot(strip_boot(text))
        if fixed != text:
            with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(fixed)
            n += 1
print(f"修了 {n} 个脚本的自举位置")
