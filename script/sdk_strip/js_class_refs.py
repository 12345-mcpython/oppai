"""把 JS 里 jsb.reflection 调用的 Java 类名/方法名全部捞出来。

原生层 JniHelper 是按名字 FindClass 的，JS 里写了什么名字就必须有什么类，
否则会 JNI abort（java_class == null）。
"""

import io
import os
import re
from collections import Counter
# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402




ASSETS = r"E:\code\zcsmw\game\assets"

# jsc 里字符串是明文，直接扫字节
PAT_CLASS = re.compile(rb"[A-Za-z0-9_$/]*(?:org/cocos2dx|com/cm|com/quicksdk|com/tencent|com/sina|com/baidu|com/tendcloud|com/kurogame|com/chukong|com/qk)[A-Za-z0-9_/$]*")

def main():
    classes = Counter()
    for root, _dirs, files in os.walk(ASSETS):
        for name in files:
            if not name.endswith(".jsc"):
                continue
            p = os.path.join(root, name)
            try:
                d = io.open(p, "rb").read()
            except Exception:
                continue
            for m in PAT_CLASS.finditer(d):
                s = m.group(0).decode("latin-1")
                if "/" in s and len(s) > 6:
                    classes[s] += 1

    print(f"JS 里出现过的类名共 {len(classes)} 个：\n")
    for cls, n in sorted(classes.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {cls}")
        # 提示：这个类还在不在
        rel = cls + ".smali"
        found = False
        for d in ("smali", "smali_classes2"):
            if os.path.isfile(os.path.join(r"E:\code\zcsmw\game", d, rel)):
                found = True
                break
        if not found:
            print("         <-- smali 里已经没有了！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
