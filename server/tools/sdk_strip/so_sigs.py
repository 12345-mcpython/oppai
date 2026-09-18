"""从 libcocos2djs.so 里挖 JNI 方法签名（形如 (Landroid/content/Context;Ljava/lang/String;)V）"""

from __future__ import annotations

import io
import re
import sys

SO = r"E:\code\apk\zcsmw\lib\armeabi\libcocos2djs.so"

SIG = re.compile(rb"^\(([^)]*)\)(\[?[LZA-Za-z;$/0-9]|[A-Za-z;$/0-9])[A-Za-z0-9_/$;]*$")
NAMEISH = re.compile(rb"^[a-zA-Z_$][A-Za-z0-9_$]{1,40}$")

CLASSES = [
    "com/tencent/bugly/cocos/Cocos2dxAgent",
    "com/tendcloud/tenddata/TDGAAccount",
    "com/tendcloud/tenddata/TDGAItem",
    "com/tendcloud/tenddata/TDGAMission",
    "com/tendcloud/tenddata/TDGAVirtualCurrency",
]


def main():
    data = io.open(SO, "rb").read()
    toks = []
    for m in re.finditer(rb"[\x20-\x7e]{1,200}", data):
        toks.append((m.start(), m.group(0).decode("latin-1")))

    for cls in CLASSES:
        offs = [o for o, s in toks if s == cls]
        if not offs:
            continue
        off = offs[0]
        near = [(o, s) for o, s in toks if abs(o - off) <= 1200]
        near.sort()
        sigs = [s for _o, s in near if re.match(r"^\(.*\).+$", s) and len(s) < 160]
        print(f"=== {cls}")
        seen = []
        for s in sigs:
            if s not in seen:
                seen.append(s)
        for s in seen:
            print("   ", s)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
