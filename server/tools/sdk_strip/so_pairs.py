"""把 libcocos2djs.so 里「方法名 + JNI 签名」成对挖出来。

JNI 的 GetStaticMethodID(clazz, name, sig) 要求 name 和 sig 两个字符串，
它们在 .rodata 里一般紧挨着。按位置相邻配对即可。
"""

from __future__ import annotations

import io
import re
import sys

SO = r"E:\code\apk\zcsmw\lib\armeabi\libcocos2djs.so"

CLASSES = [
    "com/tencent/bugly/cocos/Cocos2dxAgent",
    "com/tendcloud/tenddata/TDGAAccount",
    "com/tendcloud/tenddata/TDGAItem",
    "com/tendcloud/tenddata/TDGAMission",
    "com/tendcloud/tenddata/TDGAVirtualCurrency",
    "com/tendcloud/tenddata/TalkingDataGA",
    "com/chukong/cocosplay/client/CocosPlayClient",
]

NAME_RE = re.compile(r"^[a-zA-Z_$][A-Za-z0-9_$]{1,40}$")
SIG_RE = re.compile(r"^\([^)]*\)(\[?[LZA-Za-z;/$0-9]).*$")


def main():
    data = io.open(SO, "rb").read()
    toks = [(m.start(), m.group(0).decode("latin-1"))
            for m in re.finditer(rb"[\x20-\x7e]{1,220}", data)]

    for cls in CLASSES:
        offs = [o for o, s in toks if s == cls]
        if not offs:
            continue
        off = offs[0]
        near = sorted((o, s) for o, s in toks if abs(o - off) <= 2500)
        pairs = []
        for i, (o, s) in enumerate(near):
            if not NAME_RE.match(s) or s == cls:
                continue
            # 往后找最近的签名
            for o2, s2 in near[i + 1:i + 4]:
                if SIG_RE.match(s2):
                    pairs.append((s, s2))
                    break
        print(f"=== {cls}  ({len(pairs)} 对)")
        for n, sg in pairs:
            print(f"    {n}{sg}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
