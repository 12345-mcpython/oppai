"""补 Bugly 的 JSB 绑定（4 个全局函数）。

原版 .so 里有：buglySetUserId / buglySetTag / buglyAddUserValue / buglyLog
（以及 BuglyJSAgent）。vanilla cocos2d-js v3.6 没有这些 —— 是 Bugly 的
cocos2d-x 插件自带的绑定。

游戏 src/sdk/bugly/bugly.js:22 直接调 buglySetUserId(...)，
缺了就抛 ReferenceError，登录后的初始化会中断。

转发到 Java 的 com/tencent/bugly/cocos/Cocos2dxAgent（桩里已有这些方法）：
    buglySetUserId(u)          -> Cocos2dxAgent.setUserId(String)
    buglySetTag(tag)           -> Cocos2dxAgent.setUserSceneTag(Context,int)
    buglyAddUserValue(k, v)    -> Cocos2dxAgent.putUserData(Context,String,String)
    buglyLog(level, tag, msg)  -> Cocos2dxAgent.setLog(int,String,String)
"""

import io

UTIL = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
HDR = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.h"
AD = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\AppDelegate.cpp"

CODE = r'''

// ---------------------------------------------------------------------------
// Bugly JSB 绑定（全局函数）
//
// 原版 .so 提供 buglySetUserId / buglySetTag / buglyAddUserValue / buglyLog，
// vanilla cocos2d-js v3.6 没有（是 Bugly 插件自带的）。
// 游戏 src/sdk/bugly/bugly.js:22 直接调用，缺了会抛
//     ReferenceError: buglySetUserId is not defined
//
// 转发到 Java 的 com/tencent/bugly/cocos/Cocos2dxAgent。
// 崩溃上报服务早停了，所以 Java 调用失败也无所谓，关键是别让 JS 报错。
// ---------------------------------------------------------------------------
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
static const char* BUGLY_CLASS = "com/tencent/bugly/cocos/Cocos2dxAgent";

static void bugly_callVoidString(const char* method, const std::string& a)
{
    JniMethodInfo t;
    if (!JniHelper::getStaticMethodInfo(t, BUGLY_CLASS, method, "(Ljava/lang/String;)V")) return;
    jstring ja = t.env->NewStringUTF(a.c_str());
    t.env->CallStaticVoidMethod(t.classID, t.methodID, ja);
    t.env->DeleteLocalRef(ja);
    t.env->DeleteLocalRef(t.classID);
}
#endif

bool js_oppai_buglySetUserId(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string uid;
    if (argc >= 1) jsval_to_std_string(cx, args.get(0), &uid);
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    bugly_callVoidString("setUserId", uid);
#endif
    args.rval().setUndefined();
    return true;
}

bool js_oppai_buglySetTag(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    int32_t tag = 0;
    if (argc >= 1) jsval_to_int32(cx, args.get(0), &tag);
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    JniMethodInfo t;
    if (JniHelper::getStaticMethodInfo(t, BUGLY_CLASS, "setUserSceneTag",
                                       "(Landroid/content/Context;I)V")) {
        JniMethodInfo g;
        jobject ctx = nullptr;
        if (JniHelper::getStaticMethodInfo(g, BUGLY_CLASS, "getContext",
                                           "()Landroid/content/Context;")) {
            ctx = g.env->CallStaticObjectMethod(g.classID, g.methodID);
            g.env->DeleteLocalRef(g.classID);
        }
        t.env->CallStaticVoidMethod(t.classID, t.methodID, ctx, (jint)tag);
        if (ctx) t.env->DeleteLocalRef(ctx);
        t.env->DeleteLocalRef(t.classID);
    }
#endif
    args.rval().setUndefined();
    return true;
}

bool js_oppai_buglyAddUserValue(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string k, v;
    if (argc >= 2) {
        jsval_to_std_string(cx, args.get(0), &k);
        jsval_to_std_string(cx, args.get(1), &v);
    }
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    JniMethodInfo t;
    if (JniHelper::getStaticMethodInfo(t, BUGLY_CLASS, "putUserData",
                                       "(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;)V")) {
        jobject ctx = nullptr;
        JniMethodInfo g;
        if (JniHelper::getStaticMethodInfo(g, BUGLY_CLASS, "getContext",
                                           "()Landroid/content/Context;")) {
            ctx = g.env->CallStaticObjectMethod(g.classID, g.methodID);
            g.env->DeleteLocalRef(g.classID);
        }
        jstring jk = t.env->NewStringUTF(k.c_str());
        jstring jv = t.env->NewStringUTF(v.c_str());
        t.env->CallStaticVoidMethod(t.classID, t.methodID, ctx, jk, jv);
        t.env->DeleteLocalRef(jk);
        t.env->DeleteLocalRef(jv);
        if (ctx) t.env->DeleteLocalRef(ctx);
        t.env->DeleteLocalRef(t.classID);
    }
#endif
    args.rval().setUndefined();
    return true;
}

bool js_oppai_buglyLog(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    int32_t level = 0;
    std::string tag, msg;
    if (argc >= 3) {
        jsval_to_int32(cx, args.get(0), &level);
        jsval_to_std_string(cx, args.get(1), &tag);
        jsval_to_std_string(cx, args.get(2), &msg);
    }
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    JniMethodInfo t;
    if (JniHelper::getStaticMethodInfo(t, BUGLY_CLASS, "setLog", "(ILjava/lang/String;Ljava/lang/String;)V")) {
        jstring jt = t.env->NewStringUTF(tag.c_str());
        jstring jm = t.env->NewStringUTF(msg.c_str());
        t.env->CallStaticVoidMethod(t.classID, t.methodID, (jint)level, jt, jm);
        t.env->DeleteLocalRef(jt);
        t.env->DeleteLocalRef(jm);
        t.env->DeleteLocalRef(t.classID);
    }
#endif
    args.rval().setUndefined();
    return true;
}

// 注册成全局函数（Bugly 的绑定就是全局的，不是命名空间）
void register_all_oppai_bugly(JSContext* cx, JS::HandleObject obj)
{
    JS_DefineFunction(cx, obj, "buglySetUserId",    js_oppai_buglySetUserId,    1,
                      JSPROP_READONLY | JSPROP_PERMANENT | JSPROP_ENUMERATE);
    JS_DefineFunction(cx, obj, "buglySetTag",       js_oppai_buglySetTag,       1,
                      JSPROP_READONLY | JSPROP_PERMANENT | JSPROP_ENUMERATE);
    JS_DefineFunction(cx, obj, "buglyAddUserValue", js_oppai_buglyAddUserValue, 2,
                      JSPROP_READONLY | JSPROP_PERMANENT | JSPROP_ENUMERATE);
    JS_DefineFunction(cx, obj, "buglyLog",          js_oppai_buglyLog,          3,
                      JSPROP_READONLY | JSPROP_PERMANENT | JSPROP_ENUMERATE);
    CCLOG("[oppai] Bugly globals registered");
}
'''

s = io.open(UTIL, encoding="utf-8").read()
if "register_all_oppai_bugly" not in s:
    io.open(UTIL, "w", encoding="utf-8", newline="\n").write(s.rstrip() + "\n" + CODE)
    print("utilsex.cpp: 已加入 Bugly 绑定")
else:
    print("utilsex.cpp: 已有")

h = io.open(HDR, encoding="utf-8").read()
if "register_all_oppai_bugly" not in h:
    h = h.replace(
        "void register_all_oppai_uihelper(JSContext* cx, JS::HandleObject obj);",
        "void register_all_oppai_uihelper(JSContext* cx, JS::HandleObject obj);\n"
        "void register_all_oppai_bugly(JSContext* cx, JS::HandleObject obj);",
        1,
    )
    io.open(HDR, "w", encoding="utf-8", newline="\n").write(h)
    print("utilsex.h: 已声明")

a = io.open(AD, encoding="utf-8").read()
if "register_all_oppai_bugly" not in a:
    a = a.replace(
        "sc->addRegisterCallback(register_all_oppai_uihelper);",
        "sc->addRegisterCallback(register_all_oppai_uihelper);\n"
        "    sc->addRegisterCallback(register_all_oppai_bugly);",
        1,
    )
    io.open(AD, "w", encoding="utf-8", newline="\n").write(a)
    print("AppDelegate: 已加入")
