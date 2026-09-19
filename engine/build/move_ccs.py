"""把 ccs.ActionTimelineCache 的注册并进 jsb_oppai_utilsex.cpp。

原因：cocos2d-x 的 deprecated/CCString.h 里有 `#define ccs StringMake`，
新建一个名字里带 ccs 的编译单元会踩到这个宏（试过，编译不过）。
放进已经能编译的 utilsex.cpp 最省事。
"""

import io
import os
import shutil

DIR = r"E:\code\zcsmw\engine\build\oppai-engine\Classes"
UTIL = os.path.join(DIR, "utilsex", "jsb_oppai_utilsex.cpp")

CODE = r'''

// ---------------------------------------------------------------------------
// ccs.ActionTimelineCache
//
// 原版 libcocos2djs.so 里有 js_cocos2dx_studio_ActionTimelineCache，
// 但 cocos2d-js v3.6 仓库里没有这个类的绑定。缺了它，
// script/studio/jsb_studio_load.js 会抛 TypeError: ... is undefined，
// 之后所有 .csb 界面都加载不了。
//
// 反汇编 jsb_studio_load.jsc 可以看到 JS 的用法：
//     ccs.actionTimelineCache = ccs.ActionTimelineCache.getInstance();
//     ccs.actionTimelineCache.createAction = function (...) { ... };   // JS 自己覆盖
// 所以原生只要提供 getInstance()。
//
// 注意：不能把这个注册放在单独文件里 —— cocos2d-x 的
// deprecated/CCString.h 里有 `#define ccs StringMake`，会踩到。
// ---------------------------------------------------------------------------
static const char* OPPAI_CCS_NS = "ccs";

static bool js_oppai_ccs_getInstance(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    JS::RootedObject obj(cx, JS_NewObject(cx, nullptr, JS::NullPtr(), JS::NullPtr()));
    args.rval().setObject(*obj);
    return true;
}

static bool js_oppai_ccs_destroyInstance(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().setUndefined();
    return true;
}

void register_all_oppai_ccs(JSContext* cx, JS::HandleObject obj)
{
    JS::RootedValue nsVal2(cx);
    JS_GetProperty(cx, obj, OPPAI_CCS_NS, &nsVal2);

    JS::RootedObject ns(cx);
    if (nsVal2.isObject()) {
        ns.set(&nsVal2.toObject());
    } else {
        ns.set(JS_NewObject(cx, nullptr, JS::NullPtr(), JS::NullPtr()));
        JS::RootedValue v(cx, OBJECT_TO_JSVAL(ns));
        JS_SetProperty(cx, obj, OPPAI_CCS_NS, v);
    }

    JS::RootedValue exist(cx);
    JS_GetProperty(cx, ns, "ActionTimelineCache", &exist);
    if (exist.isObject()) {
        CCLOG("[oppai] ActionTimelineCache already present, skip");
        return;
    }

    JS::RootedObject atc(cx, JS_NewObject(cx, nullptr, JS::NullPtr(), JS::NullPtr()));
    JS_DefineFunction(cx, atc, "getInstance", js_oppai_ccs_getInstance, 0,
                      JSPROP_PERMANENT | JSPROP_ENUMERATE);
    JS_DefineFunction(cx, atc, "destroyInstance", js_oppai_ccs_destroyInstance, 0,
                      JSPROP_PERMANENT | JSPROP_ENUMERATE);

    JS::RootedValue atcVal(cx, OBJECT_TO_JSVAL(atc));
    JS_SetProperty(cx, ns, "ActionTimelineCache", atcVal);

    CCLOG("[oppai] ActionTimelineCache binding registered (minimal)");
}
'''

s = io.open(UTIL, encoding="utf-8").read()
if "register_all_oppai_ccs" in s:
    print("utilsex.cpp 里已经有了")
else:
    s = s.rstrip() + "\n" + CODE
    io.open(UTIL, "w", encoding="utf-8", newline="\n").write(s)
    print("已把 ccs 注册并入 jsb_oppai_utilsex.cpp")

# 删掉单独的 ccs 文件
for f in ("Classes/ccs/jsb_oppai_ccs.cpp", "Classes/ccs/jsb_oppai_ccs.h"):
    p = os.path.join(os.path.dirname(DIR), f)
    if os.path.isfile(p):
        os.remove(p)
        print("已删除", f)
ccs_dir = os.path.join(DIR, "ccs")
if os.path.isdir(ccs_dir) and not os.listdir(ccs_dir):
    os.rmdir(ccs_dir)
    print("已删除 Classes/ccs/")

# Android.mk 去掉 jsb_oppai_ccs.cpp 和它的 include 路径
MK = os.path.join(os.path.dirname(DIR), "jni", "Android.mk")
m = io.open(MK, encoding="utf-8").read()
m = m.replace(" \\\n../Classes/ccs/jsb_oppai_ccs.cpp", "")
m = m.replace(" \\\n                    $(LOCAL_PATH)/../Classes/ccs", "")
io.open(MK, "w", encoding="utf-8", newline="\n").write(m)
print("Android.mk 已清理")
