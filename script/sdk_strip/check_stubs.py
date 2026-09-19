"""确认剩下的 SDK 桩类是不是还有用。

对每个桩类检查三类引用：
  1. 游戏自己的 smali（非桩包）有没有引用
  2. libcocos2djs.so 的 .rodata 里有没有这个类名（JNI FindClass）
  3. 其它桩类 / 被 implements 的关系
"""

from __future__ import annotations

import io
import os
import re
import sys
# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402




APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\zcsmw\game")
SO = os.path.join(APK_DIR, "lib", "armeabi", "libcocos2djs.so")

STUB_PKGS = ("com/quicksdk", "com/tendcloud", "com/tencent/bugly",
             "com/tencent/mm", "com/tencent/android/tpush", "com/sina", "com/chukong")


def is_stub(rel: str) -> bool:
    return any(rel.startswith(p) for p in STUB_PKGS)


def main():
    so = io.open(SO, "rb").read() if os.path.isfile(SO) else b""

    # 读全部 smali
    game_blob = []      # 游戏自己的代码
    stub_files = []     # 桩类
    for dp, _dn, fn in os.walk(os.path.join(APK_DIR, "smali")):
        for f in fn:
            if not f.endswith(".smali"):
                continue
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, os.path.join(APK_DIR, "smali")).replace("\\", "/")
            cls = rel[:-6]
            src = io.open(full, encoding="utf-8", errors="replace").read()
            (stub_files if is_stub(cls) else game_blob).append((cls, src))

    game_text = "\n".join(s for _c, s in game_blob)
    stub_text = "\n".join(s for _c, s in stub_files)
    print(f"游戏代码 {len(game_blob)} 个  /  桩类 {len(stub_files)} 个\n")

    native_only, game_used, orphan = [], [], []
    for cls, _src in sorted(stub_files):
        in_game = f"L{cls};" in game_text
        in_so = cls.encode() in so
        # 被别的桩引用（排除自己）
        others = stub_text.replace(f".class public L{cls};", "")
        in_stub = f"L{cls};" in others
        tags = []
        if in_game:
            tags.append("游戏代码")
        if in_so:
            tags.append("原生.so")
        if in_stub:
            tags.append("其它桩")
        if not tags:
            orphan.append(cls)
        elif in_so and not in_game:
            native_only.append((cls, tags))
        else:
            game_used.append((cls, tags))

    def show(title, rows):
        print(f"=== {title}（{len(rows)}）")
        for item in rows:
            if isinstance(item, tuple):
                print(f"   {item[0]:60s} {'/'.join(item[1])}")
            else:
                print(f"   {item}")

    show("只有 .so 硬依赖（删了会 JNI abort）", native_only)
    show("游戏代码 / 其它桩 引用", game_used)
    show("完全没人引用（可以删）", orphan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
