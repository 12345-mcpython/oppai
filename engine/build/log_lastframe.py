"""在 setLastFrameCallFunc 的 lambda 里加日志，验证两件事：

  1. 这个 lambda 到底有没有被调用（之前判断是"没调用"，但 AT.* 日志被降级成
     verbose 了，看不到，所以加一行常驻的引擎日志）
  2. 调用时传进去的动画名对不对（验证 oppai 的引擎改动是否生效）

每场战斗只会打几次，不吵。
"""

import io

P = (r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings\bindings\auto"
     r"\jsb_cocos2dx_studio_auto.cpp")

s = io.open(P, encoding="utf-8").read()

OLD = """		            jsval argv[1];
		            argv[0] = std_string_to_jsval(cx, tlRef->getCurrentAnimationName());
		            bool ok = func->invoke(1, argv, &rval);"""

NEW = """		            jsval argv[1];
		            argv[0] = std_string_to_jsval(cx, tlRef->getCurrentAnimationName());
		            cocos2d::log("[oppai] lastFrame 触发 anim=%s", tlRef->getCurrentAnimationName().c_str());
		            bool ok = func->invoke(1, argv, &rval);"""

if "lastFrame 触发" in s:
    print("已经加过")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入 lastFrame 日志")
else:
    print("!! 没找到目标片段")
