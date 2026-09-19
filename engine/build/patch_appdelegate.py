"""给 runtime 版 AppDelegate.cpp 注入 4 个自研绑定的注册。"""

import io

P = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\AppDelegate.cpp"
s = io.open(P, encoding="utf-8").read()
orig = s

if "jsb_oppai_utilsex.h" not in s:
    anchor = '#include "ConfigParser.h"'
    assert anchor in s, "找不到 ConfigParser.h 锚点"
    s = s.replace(anchor, anchor + '''

// ---- 游戏自研绑定（重写版）----
#include "jsb_oppai_utilsex.h"
#include "jsb_oppai_gameshare.h"
#include "jsb_oppai_xg.h"
#include "jsb_oppai_talkingdata.h"''', 1)
    print("已加入 4 个 include")

if "register_all_oppai_utilsex" not in s:
    anchor = "sc->start();"
    assert anchor in s, "找不到 sc->start() 锚点"
    s = s.replace(anchor, '''// ---- 游戏自研绑定 ----
    // 原本在 Classes/{utilsex,gameshare,xg,talkingdata}/ 下，是游戏私有代码，
    // 这里按从 libcocos2djs.so 挖出的符号表重写。
    sc->addRegisterCallback(register_all_oppai_utilsex);
    sc->addRegisterCallback(register_all_oppai_gameshare);
    sc->addRegisterCallback(register_all_oppai_xg);
    sc->addRegisterCallback(register_all_oppai_talkingdata);

    sc->start();''', 1)
    print("已加入 4 个 addRegisterCallback")

if s != orig:
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("AppDelegate.cpp 更新完成")
