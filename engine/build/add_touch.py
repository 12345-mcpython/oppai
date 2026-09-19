"""补 cc.Touch.getCurrentForce / getMaxForce。

战斗摇杆 src/battle/control/joystick.jsc 的触摸处理里会调：
    touch.getCurrentForce()
    touch.getMaxForce()
（配合 _isForceTouch 做 3D-touch 力度感应）

但 cocos2d-x 3.6 的 cocos2d::Touch 里这两个方法是
#if (CC_TARGET_PLATFORM == CC_PLATFORM_IOS) 包起来的 —— Android 上根本没编。
原版 .so 里有 js_cocos2dx_Touch_getCurrentForce/getMaxForce 的绑定，
说明游戏那版把绑定加上了（Android 上返回 0 即可）。

缺了它就抛 TypeError，摇杆的 _onTouchMoved 直接中断 ——
表现就是「战斗时滑动有问题」。

这里直接挂到 cc.Touch.prototype 上。
"""

import io

UTIL = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
HDR = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.h"
AD = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\AppDelegate.cpp"

CODE = r'''

// ---------------------------------------------------------------------------
// cc.Touch.getCurrentForce / getMaxForce
//
// 战斗摇杆 src/battle/control/joystick.jsc 里会调这两个（3D-touch 力度），
// 但 cocos2d-x 3.6 的 cocos2d::Touch 把这两个方法用
//     #if (CC_TARGET_PLATFORM == CC_PLATFORM_IOS)
// 包起来了，Android 上不存在。
// 原版 .so 里有对应的绑定，说明游戏那版补上了 —— Android 返回 0 即可。
//
// 缺了会抛 TypeError，摇杆的 _onTouchMoved 中断 -> 战斗滑动失效。
// ---------------------------------------------------------------------------
static bool js_oppai_touch_getCurrentForce(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().setDouble(0.0);
    return true;
}

static bool js_oppai_touch_getMaxForce(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().setDouble(0.0);
    return true;
}

void register_all_oppai_touch(JSContext* cx, JS::HandleObject obj)
{
    JS::RootedValue ccVal(cx);
    JS_GetProperty(cx, obj, "cc", &ccVal);
    if (!ccVal.isObject()) { CCLOG("[oppai] touch: cc 不存在"); return; }
    JS::RootedObject ccObj(cx, &ccVal.toObject());

    JS::RootedValue touchVal(cx);
    JS_GetProperty(cx, ccObj, "Touch", &touchVal);
    if (!touchVal.isObject()) { CCLOG("[oppai] touch: cc.Touch 不存在"); return; }
    JS::RootedObject touchCtor(cx, &touchVal.toObject());

    JS::RootedValue protoVal(cx);
    JS_GetProperty(cx, touchCtor, "prototype", &protoVal);
    if (!protoVal.isObject()) { CCLOG("[oppai] touch: Touch.prototype 不存在"); return; }
    JS::RootedObject proto(cx, &protoVal.toObject());

    JS_DefineFunction(cx, proto, "getCurrentForce", js_oppai_touch_getCurrentForce, 0,
                      JSPROP_PERMANENT | JSPROP_ENUMERATE);
    JS_DefineFunction(cx, proto, "getMaxForce", js_oppai_touch_getMaxForce, 0,
                      JSPROP_PERMANENT | JSPROP_ENUMERATE);

    CCLOG("[oppai] cc.Touch.getCurrentForce/getMaxForce registered");
}
'''

s = io.open(UTIL, encoding="utf-8").read()
if "register_all_oppai_touch" not in s:
    io.open(UTIL, "w", encoding="utf-8", newline="\n").write(s.rstrip() + "\n" + CODE)
    print("utilsex.cpp: 已加入 cc.Touch 力度方法")
else:
    print("utilsex.cpp: 已有")

h = io.open(HDR, encoding="utf-8").read()
if "register_all_oppai_touch" not in h:
    h = h.replace(
        "void register_all_oppai_bugly(JSContext* cx, JS::HandleObject obj);",
        "void register_all_oppai_bugly(JSContext* cx, JS::HandleObject obj);\n"
        "void register_all_oppai_touch(JSContext* cx, JS::HandleObject obj);",
        1,
    )
    io.open(HDR, "w", encoding="utf-8", newline="\n").write(h)
    print("utilsex.h: 已声明")

a = io.open(AD, encoding="utf-8").read()
if "register_all_oppai_touch" not in a:
    a = a.replace(
        "sc->addRegisterCallback(register_all_oppai_bugly);",
        "sc->addRegisterCallback(register_all_oppai_bugly);\n"
        "    sc->addRegisterCallback(register_all_oppai_touch);",
        1,
    )
    io.open(AD, "w", encoding="utf-8", newline="\n").write(a)
    print("AppDelegate: 已加入")
