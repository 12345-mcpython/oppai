"""从 libcocos2djs.so 里挖出原生层 JNI 查找的类名 + 方法名。

JniHelper 的用法是：
    FindClass("com/tendcloud/tenddata/TDGAAccount")
    GetStaticMethodID(clazz, "setAccount", "(Ljava/lang/String;)V")
类名和方法名字符串都在这两个字符串附近，按偏移邻近关系配对即可。
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
]


def strings_with_offsets(data: bytes):
    for m in re.finditer(rb"[\x20-\x7e]{3,120}", data):
        yield m.start(), m.group(0).decode("latin-1")


def main():
    data = io.open(SO, "rb").read()
    items = list(strings_with_offsets(data))

    for cls in CLASSES:
        offs = [o for o, s in items if s == cls]
        print(f"=== {cls}  (出现 {len(offs)} 次)")
        for off in offs[:3]:
            # 附近 ±900 字节内的字符串
            near = [(o, s) for o, s in items if abs(o - off) <= 900]
            near.sort()
            methods = []
            for o, s in near:
                if o == off:
                    continue
                if re.fullmatch(r"[a-zA-Z_$][A-Za-z0-9_$]{1,40}", s) and not s.startswith("_"):
                    methods.append(s)
            # 去重保序
            seen = []
            for m in methods:
                if m not in seen:
                    seen.append(m)
            print("   附近候选方法名:", ", ".join(seen[:60]))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
