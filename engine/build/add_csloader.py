"""给 jsb_oppai_utilsex.cpp 加上 ccs.CSLoader 的注册。"""

import io
import os

UTIL = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
MK = r"E:\code\zcsmw\engine\build\oppai-engine\jni\Android.mk"

# ---- 1) 头文件 + include 路径 ----
s = io.open(UTIL, encoding="utf-8").read()

if "CSLoader.h" not in s:
    anchor = '#include "jsb_helper.h"'
    if anchor in s:
        s = s.replace(anchor, anchor + '''

// cocostudio 的 .csb 加载器
#include "cocostudio/ActionTimeline/CSLoader.h"
#include "cocostudio/ActionTimeline/CCActionTimeline.h"
// ccs.ActionTimelineCache / ccs.CSLoader 用到的命名空间
// 注意：cocos2d-x 的 deprecated/CCString.h 里有 `#define ccs StringMake`，
// 下面立刻 undef 掉，否则本文件里的标识符会被替换。
#undef ccs''', 1)
    else:
        raise SystemExit("找不到 jsb_helper.h 锚点")

CSCODE = r'''

// ---------------------------------------------------------------------------
// ccs.CSLoader —— .csb 界面加载器
//
// 原版 .so 里有 js_cocos2dx_studio_CSLoader，vanilla 3.6 仓库里没有。
// 反汇编 jsb_studio_load.jsc 看到 JS 需要：
//     ccs.CSLoader.createNode(filename)
//     ccs.CSLoader.createTimeline(filename)
// ---------------------------------------------------------------------------
static bool js_oppai_csloader_createNode(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string file;
    if (argc >= 1) {
        jsval_to_std_string(cx, args.get(0), &file);
    }
    cocos2d::Node* node = cocostudio::timeline::CSLoader::createNode(file);
    jsval ret = JSVAL_NULL;
    if (node) {
        ret = OBJECT_TO_JSVAL(js_get_or_create_proxy<cocos2d::Node>(cx, node)->obj);
    }
    args.rval().set(ret);
    return true;
}

static bool js_oppai_csloader_createTimeline(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string file;
    if (argc >= 1) {
        jsval_to_std_string(cx, args.get(0), &file);
    }
    cocostudio::timeline::ActionTimeline* tl = cocostudio::timeline::CSLoader::createTimeline(file);
    jsval ret = JSVAL_NULL;
    if (tl) {
        ret = OBJECT_TO_JSVAL(js_get_or_create_proxy<cocostudio::timeline::ActionTimeline>(cx, tl)->obj);
    }
    args.rval().set(ret);
    return true;
}

static bool js_oppai_csloader_getInstance(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    JS::RootedObject obj(cx, JS_NewObject(cx, nullptr, JS::NullPtr(), JS::NullPtr()));
    args.rval().setObject(*obj);
    return true;
}

static bool js_oppai_csloader_destroyInstance(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    cocostudio::timeline::CSLoader::destroyInstance();
    args.rval().setUndefined();
    return true;
}

void register_all_oppai_csloader(JSContext* cx, JS::HandleObject obj)
{
    JS::RootedValue nsVal(cx);
    JS_GetProperty(cx, obj, OPPAI_CCS_NS, &nsVal);
    if (!nsVal.isObject()) {
        CCLOG("[oppai] CSLoader: ccs 命名空间不存在");
        return;
    }
    JS::RootedObject ns(cx, &nsVal.toObject());

    JS::RootedValue exist(cx);
    JS_GetProperty(cx, ns, "CSLoader", &exist);
    if (exist.isObject()) {
        CCLOG("[oppai] CSLoader already present, skip");
        return;
    }

    JS::RootedObject cl(cx, JS_NewObject(cx, nullptr, JS::NullPtr(), JS::NullPtr()));
    JS_DefineFunction(cx, cl, "getInstance",     js_oppai_csloader_getInstance,     0, JSPROP_PERMANENT | JSPROP_ENUMERATE);
    JS_DefineFunction(cx, cl, "destroyInstance", js_oppai_csloader_destroyInstance, 0, JSPROP_PERMANENT | JSPROP_ENUMERATE);
    JS_DefineFunction(cx, cl, "createNode",      js_oppai_csloader_createNode,      1, JSPROP_PERMANENT | JSPROP_ENUMERATE);
    JS_DefineFunction(cx, cl, "createTimeline",  js_oppai_csloader_createTimeline,  1, JSPROP_PERMANENT | JSPROP_ENUMERATE);

    JS::RootedValue clVal(cx, OBJECT_TO_JSVAL(cl));
    JS_SetProperty(cx, ns, "CSLoader", clVal);

    CCLOG("[oppai] CSLoader binding registered");
}
'''

if "register_all_oppai_csloader" not in s:
    s = s.rstrip() + "\n" + CSCODE
    io.open(UTIL, "w", encoding="utf-8", newline="\n").write(s)
    print("utilsex.cpp: 已加入 CSLoader 注册")
else:
    print("utilsex.cpp: 已经有了")

# ---- 2) 头文件里声明 ----
H = UTIL[:-4] + ".h"
h = io.open(H, encoding="utf-8").read()
if "register_all_oppai_csloader" not in h:
    h = h.replace(
        "void register_all_oppai_ccs(JSContext* cx, JS::HandleObject obj);",
        "void register_all_oppai_ccs(JSContext* cx, JS::HandleObject obj);\n"
        "void register_all_oppai_csloader(JSContext* cx, JS::HandleObject obj);",
        1,
    )
    io.open(H, "w", encoding="utf-8", newline="\n").write(h)
    print("utilsex.h: 已声明 register_all_oppai_csloader")

# ---- 3) AppDelegate 调用 ----
AD = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\AppDelegate.cpp"
a = io.open(AD, encoding="utf-8").read()
if "register_all_oppai_csloader" not in a:
    a = a.replace(
        "sc->addRegisterCallback(register_all_oppai_ccs);",
        "sc->addRegisterCallback(register_all_oppai_ccs);\n"
        "    sc->addRegisterCallback(register_all_oppai_csloader);",
        1,
    )
    io.open(AD, "w", encoding="utf-8", newline="\n").write(a)
    print("AppDelegate.cpp: 已加入 register_all_oppai_csloader")

# ---- 4) Android.mk 加回 cocostudio 头文件路径 ----
m = io.open(MK, encoding="utf-8").read()
if "OPPAI_MOVE" not in m:
    block = """# cocostudio 的头文件（CSLoader.h / CCActionTimeline.h）不在 bindings 模块的导出路径里
OPPAI_MOVE := $(LOCAL_PATH)/../../..
LOCAL_C_INCLUDES += \\
$(OPPAI_MOVE)/src/cocos2d-js/frameworks/js-bindings/cocos2d-x/cocos/editor-support \\
$(OPPAI_MOVE)/src/cocos2d-js/frameworks/js-bindings/cocos2d-x/cocos/editor-support/cocostudio

LOCAL_STATIC_LIBRARIES := cocos_jsb_static"""
    m = m.replace("LOCAL_STATIC_LIBRARIES := cocos_jsb_static", block, 1)
    io.open(MK, "w", encoding="utf-8", newline="\n").write(m)
    print("Android.mk: 已加回 cocostudio 头文件路径")
