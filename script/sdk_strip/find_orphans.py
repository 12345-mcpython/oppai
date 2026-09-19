"""检查 smali_classes2 里的包还有没有被保留代码引用。

判定依据：
  1. smali/（保留的代码）里有没有 L包名/...; 形式的引用
  2. assets 里的 jsc / JS（jsb.reflection 调用的类名）有没有引用
  3. AndroidManifest.xml 有没有引用
"""

from __future__ import annotations

import io
import os
import re
import sys
from collections import defaultdict
# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402




APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\zcsmw\game")

# 待检查的包（smali_classes2 里的）
TARGETS = [
    "android/net",
    "cn/gov",
    "com/android",
    "okio",
    "org/apache",
    "org/json",
]

# 保留代码所在目录
KEPT_DIRS = ["smali"]


def all_smali(root):
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if f.endswith(".smali"):
                yield os.path.join(dp, f)


def main():
    # 先把保留代码全读进来（只读一次，后面反复匹配）
    kept = []
    for d in KEPT_DIRS:
        p = os.path.join(APK_DIR, d)
        if os.path.isdir(p):
            for f in all_smali(p):
                kept.append(io.open(f, encoding="utf-8", errors="replace").read())
    blob_kept = "\n".join(kept)
    print(f"保留代码：{len(kept)} 个 smali，{len(blob_kept)/1024/1024:.1f} MB\n")

    # assets 里的 js/jsc
    blob_assets = ""
    ap = os.path.join(APK_DIR, "assets")
    for dp, _dn, fn in os.walk(ap):
        for f in fn:
            if f.endswith((".js", ".jsc", ".json")):
                try:
                    blob_assets += io.open(os.path.join(dp, f), "rb").read().decode("latin-1")
                except Exception:
                    pass
    print(f"assets(js/jsc/json)：{len(blob_assets)/1024/1024:.1f} MB\n")

    man = io.open(os.path.join(APK_DIR, "AndroidManifest.xml"), encoding="utf-8", errors="replace").read()

    for pkg in TARGETS:
        pat = re.compile(r"L" + re.escape(pkg) + r"/")
        n_kept = len(pat.findall(blob_kept))
        n_assets = len(re.findall(re.escape(pkg) + r"/", blob_assets))
        n_man = len(re.findall(re.escape(pkg.replace("/", ".")), man))
        verdict = "可以删" if (n_kept == 0 and n_assets == 0 and n_man == 0) else "有引用，保留"
        print(f"{pkg:16s}  smali引用={n_kept:5d}  assets引用={n_assets:4d}  manifest={n_man}   -> {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
