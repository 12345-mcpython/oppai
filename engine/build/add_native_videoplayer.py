"""原生 ccui.VideoPlayer 绑定（真正播放视频）。

原版 .so 有 17 个方法的绑定，UIVideoPlayer-android.cpp 也已经编进引擎
（只是没人引用被链接器丢了），Java 侧 Cocos2dxVideoHelper / Cocos2dxVideoView
在 APK 里也完整。所以手写一份 JSB 类注册即可恢复真实播放。

绑定到 ccui.VideoPlayer，父类原型取 ccui.Widget.prototype（从 JS 侧拿，
因为 jsb_cocos2dx_ui_Widget_prototype 是自动绑定文件里的 static 变量，取不到）。
"""

import io

UTIL = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
HDR = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.h"
AD = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\AppDelegate.cpp"

CODE = r'''

// ---------------------------------------------------------------------------
// ccui.VideoPlayer —— 原生绑定
//
// 原版 .so 有 cocos2dx_experimental_video_VideoPlayer 的绑定（17 个方法），
// vanilla cocos2d-js v3.6 仓库里没有（游戏当年自己加的）。
// 战斗引导层 launchguidelayer.js 用它播 res/video/newplayer.mp4。
//
// UIVideoPlayer-android.cpp 已经在 cocos/ui/Android.mk 里编进引擎，
// Java 侧 Cocos2dxVideoHelper / Cocos2dxVideoView 在 APK 里也完整，
// 所以手写一份 JSB 类注册就能真正播放。
//
// 父类原型从 JS 侧取（ccui.Widget.prototype）—— 自动绑定文件里的
// jsb_cocos2dx_ui_Widget_prototype 是 static 变量，外部拿不到。
// ---------------------------------------------------------------------------
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
#include "ui/UIVideoPlayer.h"

using namespace cocos2d::experimental::ui;

static JSClass*  oppai_vp_class = nullptr;
static JSObject* oppai_vp_proto = nullptr;

static void js_oppai_vp_finalize(JSFreeOp* fop, JSObject* obj)
{
    js_proxy_t* p = jsb_get_js_proxy(obj);
    if (p) {
        VideoPlayer* vp = (VideoPlayer*)p->ptr;
        if (vp) { vp->release(); }
        jsb_remove_proxy(p, nullptr);
    }
}

static VideoPlayer* oppai_vp_native(JSContext* cx, JS::CallArgs& args)
{
    JS::RootedObject obj(cx, args.thisv().toObjectOrNull());
    if (!obj) { return nullptr; }
    js_proxy_t* p = jsb_get_js_proxy(obj);
    return p ? (VideoPlayer*)p->ptr : nullptr;
}

bool js_oppai_vp_ctor(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = VideoPlayer::create();
    if (!cobj) { args.rval().setUndefined(); return false; }
    cobj->retain();

    JS::RootedObject proto(cx, oppai_vp_proto);
    JS::RootedObject parent(cx);
    JS::RootedObject obj(cx, JS_NewObject(cx, oppai_vp_class, proto, parent));
    jsb_new_proxy(cobj, obj);
    args.rval().setObject(*obj);
    CCLOG("[oppai] new ccui.VideoPlayer()");
    return true;
}

bool js_oppai_vp_setFileName(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj && argc >= 1) {
        std::string s; jsval_to_std_string(cx, args.get(0), &s);
        cobj->setFileName(s);
        CCLOG("[oppai] VideoPlayer.setFileName %s", s.c_str());
    }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_getFileName(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    args.rval().set(std_string_to_jsval(cx, cobj ? cobj->getFileName() : std::string("")));
    return true;
}

bool js_oppai_vp_setURL(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj && argc >= 1) {
        std::string s; jsval_to_std_string(cx, args.get(0), &s);
        cobj->setURL(s);
    }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_getURL(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    args.rval().set(std_string_to_jsval(cx, cobj ? cobj->getURL() : std::string("")));
    return true;
}

bool js_oppai_vp_play(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj) { CCLOG("[oppai] VideoPlayer.play()"); cobj->play(); }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_pause(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj) { cobj->pause(); }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_resume(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj) { cobj->resume(); }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_stop(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj) { cobj->stop(); }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_seekTo(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj && argc >= 1) {
        double sec = 0;
        JS::ToNumber(cx, args.get(0), &sec);
        cobj->seekTo((float)sec);
    }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_isPlaying(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    args.rval().setBoolean(cobj ? cobj->isPlaying() : false);
    return true;
}

bool js_oppai_vp_setFullScreenEnabled(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj && argc >= 1) { cobj->setFullScreenEnabled(JS::ToBoolean(args.get(0))); }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_isFullScreenEnabled(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    args.rval().setBoolean(cobj ? cobj->isFullScreenEnabled() : false);
    return true;
}

bool js_oppai_vp_setKeepAspectRatioEnabled(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj && argc >= 1) { cobj->setKeepAspectRatioEnabled(JS::ToBoolean(args.get(0))); }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_isKeepAspectRatioEnabled(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    args.rval().setBoolean(cobj ? cobj->isKeepAspectRatioEnabled() : false);
    return true;
}

bool js_oppai_vp_addEventListener(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj && argc >= 1 && JS_TypeOfValue(cx, args.get(0)) == JSTYPE_FUNCTION) {
        std::shared_ptr<JSFunctionWrapper> func(
            new JSFunctionWrapper(cx, args.thisv().toObjectOrNull(), args.get(0)));
        cobj->addEventListener([=](Ref* sender, VideoPlayer::EventType event) {
            JSB_AUTOCOMPARTMENT_WITH_GLOBAL_OBJCET
            jsval argv[2];
            js_proxy_t* sp = sender ? jsb_get_native_proxy(sender) : nullptr;
            argv[0] = sp ? OBJECT_TO_JSVAL(sp->obj) : JSVAL_NULL;
            argv[1] = INT_TO_JSVAL((int)event);
            JS::RootedValue rval(cx);
            func->invoke(2, argv, &rval);
        });
        CCLOG("[oppai] VideoPlayer.addEventListener 已注册");
    }
    args.rval().setUndefined();
    return true;
}

bool js_oppai_vp_onPlayEvent(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj && argc >= 1) {
        int32_t e = 0; jsval_to_int32(cx, args.get(0), &e);
        cobj->onPlayEvent(e);
    }
    args.rval().setUndefined();
    return true;
}

void register_all_oppai_videoplayer(JSContext* cx, JS::HandleObject obj)
{
    JS::RootedValue ccuiVal(cx);
    JS_GetProperty(cx, obj, "ccui", &ccuiVal);
    if (!ccuiVal.isObject()) { CCLOG("[oppai] videoplayer: ccui 不存在"); return; }
    JS::RootedObject ccui(cx, &ccuiVal.toObject());

    // 父类原型 ccui.Widget.prototype
    JS::RootedValue wv(cx);
    JS_GetProperty(cx, ccui, "Widget", &wv);
    if (!wv.isObject()) { CCLOG("[oppai] videoplayer: ccui.Widget 不存在"); return; }
    JS::RootedObject wctor(cx, &wv.toObject());
    JS::RootedValue pv(cx);
    JS_GetProperty(cx, wctor, "prototype", &pv);
    if (!pv.isObject()) { CCLOG("[oppai] videoplayer: Widget.prototype 不存在"); return; }
    JS::RootedObject parentProto(cx, &pv.toObject());

    JS::RootedValue exist(cx);
    JS_GetProperty(cx, ccui, "VideoPlayer", &exist);
    if (exist.isObject()) { CCLOG("[oppai] VideoPlayer 已存在，跳过"); return; }

    oppai_vp_class = (JSClass*)calloc(1, sizeof(JSClass));
    oppai_vp_class->name = "VideoPlayer";
    oppai_vp_class->addProperty = JS_PropertyStub;
    oppai_vp_class->delProperty = JS_DeletePropertyStub;
    oppai_vp_class->getProperty = JS_PropertyStub;
    oppai_vp_class->setProperty = JS_StrictPropertyStub;
    oppai_vp_class->enumerate = JS_EnumerateStub;
    oppai_vp_class->resolve = JS_ResolveStub;
    oppai_vp_class->convert = JS_ConvertStub;
    oppai_vp_class->finalize = js_oppai_vp_finalize;
    oppai_vp_class->flags = JSCLASS_HAS_RESERVED_SLOTS(2);

    static JSFunctionSpec funcs[] = {
        JS_FN("setFileName",                js_oppai_vp_setFileName,                1, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("getFileName",                js_oppai_vp_getFileName,                0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("setURL",                     js_oppai_vp_setURL,                     1, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("getURL",                     js_oppai_vp_getURL,                     0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("play",                       js_oppai_vp_play,                       0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("pause",                      js_oppai_vp_pause,                      0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("resume",                     js_oppai_vp_resume,                     0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("stop",                       js_oppai_vp_stop,                       0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("seekTo",                     js_oppai_vp_seekTo,                     1, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("isPlaying",                  js_oppai_vp_isPlaying,                  0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("setFullScreenEnabled",       js_oppai_vp_setFullScreenEnabled,       1, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("isFullScreenEnabled",        js_oppai_vp_isFullScreenEnabled,        0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("setKeepAspectRatioEnabled",  js_oppai_vp_setKeepAspectRatioEnabled,  1, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("isKeepAspectRatioEnabled",   js_oppai_vp_isKeepAspectRatioEnabled,   0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("addEventListener",           js_oppai_vp_addEventListener,           1, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FN("onPlayEvent",                js_oppai_vp_onPlayEvent,                1, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FS_END
    };

    oppai_vp_proto = JS_InitClass(cx, ccui, parentProto, oppai_vp_class,
                                  js_oppai_vp_ctor, 0, nullptr, funcs, nullptr, nullptr);
    CCLOG("[oppai] ccui.VideoPlayer 原生绑定已注册 (proto=%p)", oppai_vp_proto);
}
#else
void register_all_oppai_videoplayer(JSContext* cx, JS::HandleObject obj)
{
    CCLOG("[oppai] VideoPlayer 仅在 Android 上绑定");
}
#endif
'''

s = io.open(UTIL, encoding="utf-8").read()
if "register_all_oppai_videoplayer" not in s:
    io.open(UTIL, "w", encoding="utf-8", newline="\n").write(s.rstrip() + "\n" + CODE)
    print("utilsex.cpp: 已加入原生 VideoPlayer")
else:
    print("utilsex.cpp: 已有")

h = io.open(HDR, encoding="utf-8").read()
if "register_all_oppai_videoplayer" not in h:
    h = h.replace(
        "void register_all_oppai_touch(JSContext* cx, JS::HandleObject obj);",
        "void register_all_oppai_touch(JSContext* cx, JS::HandleObject obj);\n"
        "void register_all_oppai_videoplayer(JSContext* cx, JS::HandleObject obj);",
        1,
    )
    io.open(HDR, "w", encoding="utf-8", newline="\n").write(h)
    print("utilsex.h: 已声明")

a = io.open(AD, encoding="utf-8").read()
if "register_all_oppai_videoplayer" not in a:
    a = a.replace(
        "sc->addRegisterCallback(register_all_oppai_touch);",
        "sc->addRegisterCallback(register_all_oppai_touch);\n"
        "    sc->addRegisterCallback(register_all_oppai_videoplayer);",
        1,
    )
    io.open(AD, "w", encoding="utf-8", newline="\n").write(a)
    print("AppDelegate: 已加入")
