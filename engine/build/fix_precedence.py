"""修 cocos2d-x 3.6 的一个运算符优先级 bug（原生崩溃根因）。

崩溃堆栈（从 tombstone 拿到的完整符号）：
    cocostudio::timeline::RotationSkewFrame::onApply(float)
    cocostudio::timeline::Frame::apply(float)
    cocostudio::timeline::Timeline::apply(int)
    cocostudio::timeline::Timeline::gotoFrame(int)
    -> SIGSEGV, null pointer dereference

源码（CCFrame.cpp）：
    if (nullptr != _node && _betweenSkewX != 0 || _betweenSkewY != 0)
    {
        _node->setRotationSkewX(skewx);     // _node 可能是 null
    }

&& 优先级高于 ||，实际解析成：
    if ((nullptr != _node && _betweenSkewX != 0) || (_betweenSkewY != 0))

所以只要 _betweenSkewY != 0 而 _node == nullptr，条件成立 -> 空指针解引用。

同文件 403 行是已经修好的写法：
    if (nullptr != _node && (_betweenX != 0 || _betweenY != 0))
说明上游后来正是这么改的。

本文件共有 4 处同样的 bug：285 / 345 / 463 / 697。
"""

import io

P = (r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings"
     r"\cocos2d-x\cocos\editor-support\cocostudio\ActionTimeline\CCFrame.cpp")

FIXES = [
    # (行号, 原文本, 修好的文本)
    (285,
     "if (nullptr != _node && _betweenSkewX != 0 || _betweenSkewY != 0)",
     "if (nullptr != _node && (_betweenSkewX != 0 || _betweenSkewY != 0))"),
    (463,
     "if (nullptr != _node && _betweenScaleX != 0 || _betweenScaleY != 0)",
     "if (nullptr != _node && (_betweenScaleX != 0 || _betweenScaleY != 0))"),
    (697,
     "if (nullptr != _node && _betweenRed != 0 || _betweenGreen != 0 || _betweenBlue != 0)",
     "if (nullptr != _node && (_betweenRed != 0 || _betweenGreen != 0 || _betweenBlue != 0))"),
]

s = io.open(P, encoding="utf-8").read()
lines = s.split("\n")

n = 0
for idx, old, new in FIXES:
    i = idx - 1
    if 0 <= i < len(lines) and old in lines[i]:
        lines[i] = lines[i].replace(old, new)
        n += 1
        print("  第 %d 行已修" % idx)
    elif old in s:
        s = s.replace(old, new)
        n += 1
        print("  已替换（行号有偏移）: %s" % old[:50])
    else:
        print("  !! 第 %d 行没匹配上" % idx)

# 第 345 行要单独处理：它和第 285 行文本完全相同，靠行号定位
if 0 < 345 <= len(lines):
    old = "if (nullptr != _node && _betweenSkewX != 0 || _betweenSkewY != 0)"
    if old in lines[344]:
        lines[344] = lines[344].replace(
            old, "if (nullptr != _node && (_betweenSkewX != 0 || _betweenSkewY != 0))")
        n += 1
        print("  第 345 行已修")

io.open(P, "w", encoding="utf-8", newline="\n").write("\n".join(lines))
print("共修 %d 处" % n)

# 复查
print()
print("=== 复查 ===")
for i, l in enumerate(io.open(P, encoding="utf-8").read().split("\n")):
    if "nullptr != _node &&" in l:
        print("  %4d: %s" % (i + 1, l.strip()))
