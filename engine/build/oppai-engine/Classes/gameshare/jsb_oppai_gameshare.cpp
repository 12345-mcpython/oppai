/*
 * jsb_oppai_gameshare.cpp —— 分享（微信 / 微博）
 * 这两个功能对应的 SDK 服务早就停了，所以 Java 侧调用失败也无所谓，
 * 只要 JS 里 GameShare.shareToWeChat/xxx 不抛异常即可。
 */

#include "jsb_oppai_utilsex.h"   // oppai_attach
#include "jsb_oppai_gameshare.h"

#include "cocos2d.h"
#include "ScriptingCore.h"
#include "js_manual_conversions.h"

#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
#include "platform/android/jni/JniHelper.h"
#include <jni.h>
#endif

USING_NS_CC;

#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
static const char* GAMESHARE_CLASS = "org/cocos2dx/javascript/GameShare";

static void callJavaShare(const char* method, const std::string& arg)
{
    JniMethodInfo t;
    if (!JniHelper::getStaticMethodInfo(t, GAMESHARE_CLASS, method,
                                        "(Ljava/lang/String;)V")) {
        return;
    }
    jstring jarg = t.env->NewStringUTF(arg.c_str());
    t.env->CallStaticVoidMethod(t.classID, t.methodID, jarg);
    t.env->DeleteLocalRef(jarg);
    t.env->DeleteLocalRef(t.classID);
}
#endif

bool js_oppai_gameshare_shareToWeChat(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string param;
    if (argc >= 1) {
        jsval_to_std_string(cx, args.get(0), &param);
    }
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    callJavaShare("shareToWeChat", param);
#endif
    args.rval().setUndefined();
    return true;
}

bool js_oppai_gameshare_shareToSina(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string param;
    if (argc >= 1) {
        jsval_to_std_string(cx, args.get(0), &param);
    }
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    callJavaShare("shareToSina", param);
#endif
    args.rval().setUndefined();
    return true;
}

#define OPPAI_FN(_cx, _obj, _name, _fn, _nargs) \
    JS_DefineFunction(_cx, _obj, _name, _fn, _nargs, \
                      JSPROP_READONLY | JSPROP_PERMANENT | JSPROP_ENUMERATE)

void register_all_oppai_gameshare(JSContext* cx, JS::HandleObject obj)
{
    JS::RootedObject parent(cx);
    JS::RootedObject proto(cx);
    JSObject* ns = JS_NewObject(cx, nullptr, proto, parent);
    JS::RootedObject share(cx, ns);
    {
        JS::RootedObject rooted(cx, ns);
        oppai_attach(cx, obj, "GameShare", rooted);
    }

    OPPAI_FN(cx, share, "shareToWeChat", js_oppai_gameshare_shareToWeChat, 1);
    OPPAI_FN(cx, share, "shareToSina",   js_oppai_gameshare_shareToSina,   1);

    CCLOG("[oppai] GameShare binding registered");
}
