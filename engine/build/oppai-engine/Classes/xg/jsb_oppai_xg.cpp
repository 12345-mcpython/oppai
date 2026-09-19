/*
 * jsb_oppai_xg.cpp —— 信鸽推送
 * 推送服务早停了，serviceEnabled 返回 false、getDeviceToken 返回空串即可，
 * 游戏会跳过推送相关逻辑。
 */

#include "jsb_oppai_utilsex.h"   // oppai_attach
#include "jsb_oppai_xg.h"

#include "cocos2d.h"
#include "ScriptingCore.h"
#include "js_manual_conversions.h"

#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
#include "platform/android/jni/JniHelper.h"
#include <jni.h>
#endif

USING_NS_CC;

#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
static const char* XG_CLASS = "org/cocos2dx/javascript/XGAdapter";

static void callJavaVoidString(const char* method, const std::string& arg)
{
    JniMethodInfo t;
    if (!JniHelper::getStaticMethodInfo(t, XG_CLASS, method, "(Ljava/lang/String;)V")) {
        return;
    }
    jstring jarg = t.env->NewStringUTF(arg.c_str());
    t.env->CallStaticVoidMethod(t.classID, t.methodID, jarg);
    t.env->DeleteLocalRef(jarg);
    t.env->DeleteLocalRef(t.classID);
}
#endif

bool js_oppai_xg_setTag(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string tag;
    if (argc >= 1) {
        jsval_to_std_string(cx, args.get(0), &tag);
    }
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    callJavaVoidString("setTag", tag);
#endif
    args.rval().setUndefined();
    return true;
}

bool js_oppai_xg_delTag(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string tag;
    if (argc >= 1) {
        jsval_to_std_string(cx, args.get(0), &tag);
    }
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    callJavaVoidString("delTag", tag);
#endif
    args.rval().setUndefined();
    return true;
}

// addNotification(long, String, String) —— 本地通知，服务停了直接忽略
bool js_oppai_xg_addNotification(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().setUndefined();
    return true;
}

bool js_oppai_xg_clearNotifications(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    JniMethodInfo t;
    if (JniHelper::getStaticMethodInfo(t, XG_CLASS, "clearNotifications", "()V")) {
        t.env->CallStaticVoidMethod(t.classID, t.methodID);
        t.env->DeleteLocalRef(t.classID);
    }
#endif
    args.rval().setUndefined();
    return true;
}

// 推送已停服 —— 明确返回 false，让游戏跳过推送初始化
bool js_oppai_xg_serviceEnabled(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().setBoolean(false);
    return true;
}

bool js_oppai_xg_getDeviceToken(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().set(std_string_to_jsval(cx, ""));
    return true;
}

#define OPPAI_FN(_cx, _obj, _name, _fn, _nargs) \
    JS_DefineFunction(_cx, _obj, _name, _fn, _nargs, \
                      JSPROP_READONLY | JSPROP_PERMANENT | JSPROP_ENUMERATE)

void register_all_oppai_xg(JSContext* cx, JS::HandleObject obj)
{
    JS::RootedObject parent(cx);
    JS::RootedObject proto(cx);
    JSObject* ns = JS_NewObject(cx, nullptr, proto, parent);
    JS::RootedObject xg(cx, ns);
    {
        JS::RootedObject rooted(cx, ns);
        oppai_attach(cx, obj, "XGAdapter", rooted);
    }

    OPPAI_FN(cx, xg, "setTag",             js_oppai_xg_setTag,             1);
    OPPAI_FN(cx, xg, "delTag",             js_oppai_xg_delTag,             1);
    OPPAI_FN(cx, xg, "addNotification",    js_oppai_xg_addNotification,    3);
    OPPAI_FN(cx, xg, "clearNotifications", js_oppai_xg_clearNotifications, 0);
    OPPAI_FN(cx, xg, "serviceEnabled",     js_oppai_xg_serviceEnabled,     0);
    OPPAI_FN(cx, xg, "getDeviceToken",     js_oppai_xg_getDeviceToken,     0);

    CCLOG("[oppai] XGAdapter binding registered");
}
