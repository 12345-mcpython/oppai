"""把项目重排到 E:\\code\\zcsmw\\ 下（一次性脚本）。

    python E:\\code\\apk\\migrate.py

    E:\\code\\apk\\zcsmw        -> E:\\code\\zcsmw\\game          游戏包（apktool 解包目录）
    E:\\code\\apk\\backup       -> E:\\code\\zcsmw\\game\\original  原版 APK（唯一一份备份）
    E:\\code\\apk\\work         -> E:\\code\\zcsmw\\out           构建产物 / 中间产物
    E:\\code\\apk\\shots        -> E:\\code\\zcsmw\\out\\shots
    E:\\code\\python\\game_server -> E:\\code\\zcsmw\\server      服务端
    E:\\code\\python\\game_server\\tools -> E:\\code\\zcsmw\\script  实用脚本
    E:\\code\\apk\\move         -> E:\\code\\zcsmw\\engine        引擎源码 + NDK
    E:\\code\\apk\\sm           -> E:\\code\\zcsmw\\engine\\ref\\spidermonkey  参考源码
    apktool.bat / apktool_3.0.3.jar -> E:\\code\\zcsmw\\script\\
"""

from __future__ import annotations

import io
import os
import re
import shutil
import sys
# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402


APK = r"E:\code\apk"
PY = r"E:\code\python"
DEST = r"E:\code\zcsmw"

MOVES = [
    (os.path.join(APK, "zcsmw"), os.path.join(DEST, "game")),
    (os.path.join(PY, "game_server"), os.path.join(DEST, "server")),
    (os.path.join(APK, "move"), os.path.join(DEST, "engine")),
    (os.path.join(APK, "work"), os.path.join(DEST, "out")),
    (os.path.join(APK, "shots"), os.path.join(DEST, "out", "shots")),
]

# 路径替换（**按长度从长到短**，免得短的先命中把长的切了）
REPL = [
    (r"E:\code\python\game_server\tools", os.path.join(DEST, "script")),
    (r"E:\code\python\game_server", os.path.join(DEST, "server")),
    (r"E:\code\apk\zcsmw", os.path.join(DEST, "game")),
    (r"E:\code\apk\backup", os.path.join(DEST, "game", "original")),
    (r"E:\code\apk\work", os.path.join(DEST, "out")),
    (r"E:\code\apk\shots", os.path.join(DEST, "out", "shots")),
    (r"E:\code\apk\move", os.path.join(DEST, "engine")),
    (r"E:\code\apk\sm", os.path.join(DEST, "engine", "ref", "spidermonkey")),
    (r"E:\code\apk\apktool.bat", os.path.join(DEST, "script", "apktool.bat")),
    (r"E:\code\apk\apktool_3.0.3.jar", os.path.join(DEST, "script", "apktool_3.0.3.jar")),
    (r"E:\code\apk", DEST),
    (r"E:\code\python", DEST),
]

# `tools.xxx` -> `xxx`（脚本挪到 script/ 之后，它们就是同级模块了）
REL_IMPORT = re.compile(r"\bfrom\s+tools\.(\w+)\s+import\b")
REL_IMPORT2 = re.compile(r"\bimport\s+tools\.(\w+)\b")

BOOTSTRAP = '''
'''

TEXT_EXT = (".py", ".ps1", ".md", ".txt", ".json", ".mk", ".bat", ".cmd", ".yaml", ".yml")


def rewrite_text(path: str) -> int:
    try:
        with io.open(path, encoding="utf-8") as fh:
            text = fh.read()
    except Exception:  # noqa: BLE001
        return 0
    orig = text
    for old, new in REPL:
        text = text.replace(old, new)
    text = REL_IMPORT.sub(r"from \1 import", text)
    text = REL_IMPORT2.sub(r"import \1", text)
    if text != orig:
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return 1
    return 0


def add_bootstrap(path: str) -> bool:
    with io.open(path, encoding="utf-8") as fh:
        text = fh.read()
    if "_paths.py" in text:
        return False
    lines = text.split("\n")
    # 找最后一行 `import ...` / `from ... import ...` 里的 sys
    idx = None
    for i, ln in enumerate(lines[:80]):
        if re.match(r"^\s*(import|from)\s", ln):
            idx = i
    if idx is None:
        return False
    has_os = any(re.match(r"^\s*import os\b", ln) for ln in lines[:80])
    boot = BOOTSTRAP.replace("\n# ---", "\n# ---")
    if not has_os:
        boot = "\nimport os" + boot
    lines.insert(idx + 1, boot)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))
    return True


def main() -> int:
    if not os.path.isdir(APK):
        print(f"!! {APK} 不存在", file=sys.stderr)
        return 1
    os.makedirs(DEST, exist_ok=True)

    # 1) 搬目录
    for src, dst in MOVES:
        if not os.path.isdir(src):
            print(f"   跳过（不存在）{src}")
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isdir(dst):
            print(f"!! 目标已存在，跳过：{dst}")
            continue
        shutil.move(src, dst)
        print(f"   移动 {src}  ->  {dst}")

    # 2) 原版 APK 备份
    src_backup = os.path.join(APK, "backup")
    dst_backup = os.path.join(DEST, "game", "original")
    if os.path.isdir(src_backup):
        os.makedirs(dst_backup, exist_ok=True)
        for fn in os.listdir(src_backup):
            shutil.move(os.path.join(src_backup, fn), os.path.join(dst_backup, fn))
            print(f"   移动 {fn} -> {dst_backup}")
        try:
            os.rmdir(src_backup)
        except OSError:
            pass

    # 3) server/tools -> script
    tools = os.path.join(DEST, "server", "tools")
    script = os.path.join(DEST, "script")
    if os.path.isdir(tools) and not os.path.isdir(script):
        shutil.move(tools, script)
        print(f"   移动 {tools} -> {script}")
    shutil.rmtree(os.path.join(script, "__pycache__"), ignore_errors=True)

    # 4) apktool / sm
    os.makedirs(script, exist_ok=True)
    for fn in ("apktool.bat", "apktool_3.0.3.jar"):
        p = os.path.join(APK, fn)
        if os.path.isfile(p):
            shutil.move(p, os.path.join(script, fn))
            print(f"   移动 {fn} -> script\\")
    sm = os.path.join(APK, "sm")
    if os.path.isdir(sm):
        dst = os.path.join(DEST, "engine", "ref", "spidermonkey")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.isdir(dst):
            shutil.move(sm, dst)
            print(f"   移动 sm -> {dst}")

    # 5) 改路径
    n = 0
    roots = [os.path.join(DEST, "server"), os.path.join(DEST, "script"),
             os.path.join(DEST, "engine", "build"), os.path.join(DEST, "engine", "ref")]
    for root in roots:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "obj", "libs")]
            for fn in files:
                if fn.endswith(TEXT_EXT):
                    n += rewrite_text(os.path.join(dirpath, fn))
    for fn in ("ENGINE_PATCHES.md", "README.md"):
        p = os.path.join(DEST, "engine", fn)
        if os.path.isfile(p):
            n += rewrite_text(p)
    print(f"   改了 {n} 个文件的路径")

    # 6) 脚本加路径自举
    boot = 0
    for dirpath, dirs, files in os.walk(script):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            if dirpath.endswith("archive"):
                continue
            if add_bootstrap(os.path.join(dirpath, fn)):
                boot += 1
    print(f"   给 {boot} 个脚本加了路径自举")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
