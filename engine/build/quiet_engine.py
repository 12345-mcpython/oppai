"""引擎侧降噪：

1) JniHelper::getJavaVM() 每次调用都 LOGD 一行 —— 游戏调 JNI 很频繁，
   日志被它刷屏。这行纯噪音，去掉。
2) 我加的 [oppai] CCLOG 里，按次调用的（如每次 setFileName/play）降级，
   只保留注册类的一次性输出。
"""

import io
import re

ROOT = r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings\cocos2d-x"

# ---------------------------------------------------------- 1) JniHelper
P = ROOT + r"\cocos\platform\android\jni\JniHelper.cpp"
s = io.open(P, encoding="utf-8").read()

if "oppai: 这行每次调 JNI 都打" in s:
    print("JniHelper 已处理过")
else:
    OLD = '    LOGD("JniHelper::getJavaVM(), pthread_self() = %ld", thisthread);'
    NEW = ('    // oppai: 这行每次调 JNI 都打，会把 logcat 刷爆，去掉\n'
           '    // LOGD("JniHelper::getJavaVM(), pthread_self() = %ld", thisthread);')
    if OLD in s:
        s = s.replace(OLD, NEW, 1)
        io.open(P, "w", encoding="utf-8", newline="\n").write(s)
        print("JniHelper::getJavaVM 的 LOGD 已注释")
    else:
        print("!! JniHelper 里没找到目标行")

# ------------------------------------------------- 2) 我的 [oppai] 按次日志
U = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
u = io.open(U, encoding="utf-8").read()

DROP = [
    '        CCLOG("[oppai] new ccui.VideoPlayer()");\n',
    '        CCLOG("[oppai] VideoPlayer.setFileName %s", s.c_str());\n',
    '    if (cobj) { CCLOG("[oppai] VideoPlayer.play()"); cobj->play(); }\n',
    '        CCLOG("[oppai] VideoPlayer.addEventListener 已注册");\n',
    '    CCLOG("[oppai] utilsex binding registered (platform=%d extra=%d)",\n'
    '          OPPAI_PLATFORM, OPPAI_EXTRA_PLATFORM);\n',
]
n = 0
for d in DROP:
    if d in u:
        if "VideoPlayer.play" in d:
            u = u.replace(d, '    if (cobj) { cobj->play(); }\n', 1)
        else:
            u = u.replace(d, "", 1)
        n += 1
print("去掉 %d 处按次 [oppai] 日志" % n)

if n:
    io.open(U, "w", encoding="utf-8", newline="\n").write(u)
print("完成")
