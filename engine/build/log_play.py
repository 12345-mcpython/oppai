"""在 ActionTimeline::play 里打日志，看游戏到底播了哪些动画名。

之前只看最后一帧回调收到的名字（loop / animation1 / began / default），
但预期应该是 began1/began2/began3/loop1/... —— 对不上。
直接把每次 play() 的名字打出来才清楚。
"""

import io

P = (r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings\cocos2d-x\cocos"
     r"\editor-support\cocostudio\ActionTimeline\CCActionTimeline.cpp")

s = io.open(P, encoding="utf-8").read()

OLD = "    _currentAnimationName = name;      // oppai: 记录，供最后一帧回调使用"
NEW = ("    _currentAnimationName = name;      // oppai: 记录，供最后一帧回调使用\n"
       '    cocos2d::log("[oppai] play 动画名=%s loop=%d", name.c_str(), loop ? 1 : 0);')

if "play 动画名" in s:
    print("已经加过")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入 play 日志")
else:
    print("!! 没找到目标行")
