/*
 * jsb_oppai_utilsex.cpp —— 见 .h 里的说明
 */

#include "jsb_oppai_utilsex.h"

#include "cocos2d.h"
#include "ScriptingCore.h"
#include "js_manual_conversions.h"
#include "jsb_helper.h"

// cocostudio 的 .csb 加载器
#include "cocostudio/ActionTimeline/CSLoader.h"
#include "cocostudio/ActionTimeline/CCActionTimeline.h"
// ccs.ActionTimelineCache / ccs.CSLoader 用到的命名空间
// 注意：cocos2d-x 的 deprecated/CCString.h 里有 `#define ccs StringMake`，
// 下面立刻 undef 掉，否则本文件里的标识符会被替换。
#undef ccs

#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
#include "platform/android/jni/JniHelper.h"
#include <jni.h>
#endif

USING_NS_CC;

// ---------------------------------------------------------------------------
// 平台常量，从 src/config/appconfig.jsc 反汇编出来的
// ---------------------------------------------------------------------------
#define OPPAI_PLATFORM_YE          234592      // PLATFORM_CONFIG.YE   （本包）
#define OPPAI_EXTRA_PLATFORM_QUICK 234501      // EXTRA_PLATFORM_CONFIG.QUICK（本包）

// 这个包用的是 YE + QUICK（见 gameEvent.onRoleCreate 的分支）
#ifndef OPPAI_PLATFORM
#define OPPAI_PLATFORM        OPPAI_PLATFORM_YE
#endif
#ifndef OPPAI_EXTRA_PLATFORM
#define OPPAI_EXTRA_PLATFORM  OPPAI_EXTRA_PLATFORM_QUICK
#endif

// ---------------------------------------------------------------------------
// Java 转发小工具
// ---------------------------------------------------------------------------
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
static const char* UTILSEX_CLASS = "org/cocos2dx/javascript/Utilsex";

static bool callJavaString(const char* method, std::string* out)
{
    JniMethodInfo t;
    if (!JniHelper::getStaticMethodInfo(t, UTILSEX_CLASS, method, "()Ljava/lang/String;")) {
        return false;
    }
    jstring jstr = (jstring)t.env->CallStaticObjectMethod(t.classID, t.methodID);
    if (jstr) {
        *out = JniHelper::jstring2string(jstr);
        t.env->DeleteLocalRef(jstr);
    }
    t.env->DeleteLocalRef(t.classID);
    return true;
}

static bool callJavaBool(const char* method, bool* out)
{
    JniMethodInfo t;
    if (!JniHelper::getStaticMethodInfo(t, UTILSEX_CLASS, method, "()Z")) {
        return false;
    }
    *out = t.env->CallStaticBooleanMethod(t.classID, t.methodID) == JNI_TRUE;
    t.env->DeleteLocalRef(t.classID);
    return true;
}
#endif

// ---------------------------------------------------------------------------
// 无参 → 字符串
// ---------------------------------------------------------------------------
static bool stringFunc(JSContext* cx, uint32_t argc, jsval* vp, const char* javaMethod,
                       const char* fallback)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string ret = fallback ? fallback : "";
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    callJavaString(javaMethod, &ret);
#endif
    args.rval().set(std_string_to_jsval(cx, ret));
    return true;
}

// ---------------------------------------------------------------------------
// 纯原生实现的几个（Java 里没有对应方法）
// ---------------------------------------------------------------------------
bool js_oppai_utilsex_getPlatform(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().setInt32(OPPAI_PLATFORM);
    return true;
}

bool js_oppai_utilsex_getExtraPlatform(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().setInt32(OPPAI_EXTRA_PLATFORM);
    return true;
}

bool js_oppai_utilsex_getPlatformName(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().set(std_string_to_jsval(cx, "ye"));
    return true;
}

// 设备标识：原版从系统取的，这里给稳定的占位值（服务端不校验）
bool js_oppai_utilsex_getDeviceUId(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string ret = "emulator";
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    callJavaString("getDeviceId", &ret);
#endif
    args.rval().set(std_string_to_jsval(cx, ret));
    return true;
}

bool js_oppai_utilsex_getUdid(JSContext* cx, uint32_t argc, jsval* vp)
{
    return js_oppai_utilsex_getDeviceUId(cx, argc, vp);
}

bool js_oppai_utilsex_getVid(JSContext* cx, uint32_t argc, jsval* vp)
{
    return js_oppai_utilsex_getDeviceUId(cx, argc, vp);
}

bool js_oppai_utilsex_getMacAddress(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    args.rval().set(std_string_to_jsval(cx, "02:00:00:00:00:00"));
    return true;
}

// ---------------------------------------------------------------------------
// 转发到 Java 的
// ---------------------------------------------------------------------------
bool js_oppai_utilsex_getAppVersion(JSContext* cx, uint32_t argc, jsval* vp)
{
    return stringFunc(cx, argc, vp, "getAppVersion", "2.2.0");
}
bool js_oppai_utilsex_getBuildVersion(JSContext* cx, uint32_t argc, jsval* vp)
{
    return stringFunc(cx, argc, vp, "getBuildVersion", "51");
}
bool js_oppai_utilsex_getDeviceId(JSContext* cx, uint32_t argc, jsval* vp)
{
    return stringFunc(cx, argc, vp, "getDeviceId", "emulator");
}
bool js_oppai_utilsex_getPhoneModel(JSContext* cx, uint32_t argc, jsval* vp)
{
    return stringFunc(cx, argc, vp, "getPhoneModel", "Android");
}
bool js_oppai_utilsex_getSystemVersion(JSContext* cx, uint32_t argc, jsval* vp)
{
    return stringFunc(cx, argc, vp, "getSystemVersion", "9");
}

bool js_oppai_utilsex_isScreenLock(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    bool ret = false;
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    callJavaBool("isScreenLock", &ret);
#endif
    args.rval().setBoolean(ret);
    return true;
}

bool js_oppai_utilsex_setScreenLock(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    bool locked = false;
    if (argc >= 1) {
        locked = JS::ToBoolean(args.get(0));
    }
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    JniMethodInfo t;
    if (JniHelper::getStaticMethodInfo(t, UTILSEX_CLASS, "setScreenLock", "(Z)V")) {
        t.env->CallStaticVoidMethod(t.classID, t.methodID, locked ? JNI_TRUE : JNI_FALSE);
        t.env->DeleteLocalRef(t.classID);
    }
#endif
    args.rval().setUndefined();
    return true;
}

// exit(I) —— 退出游戏
bool js_oppai_utilsex_exit(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    int32_t code = 0;
    if (argc >= 1) {
        jsval_to_int32(cx, args.get(0), &code);
    }
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    JniMethodInfo t;
    if (JniHelper::getStaticMethodInfo(t, UTILSEX_CLASS, "exit", "(I)V")) {
        t.env->CallStaticVoidMethod(t.classID, t.methodID, (jint)code);
        t.env->DeleteLocalRef(t.classID);
    }
#else
    Director::getInstance()->end();
#endif
    args.rval().setUndefined();
    return true;
}

// openURL(String) —— 转给 Cocos2dxHelper
bool js_oppai_utilsex_openURL(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    std::string url;
    if (argc >= 1) {
        jsval_to_std_string(cx, args.get(0), &url);
    }
#if (CC_TARGET_PLATFORM == CC_PLATFORM_ANDROID)
    JniMethodInfo t;
    if (JniHelper::getStaticMethodInfo(t, "org/cocos2dx/lib/Cocos2dxHelper",
                                       "openURL", "(Ljava/lang/String;)V")) {
        jstring jurl = t.env->NewStringUTF(url.c_str());
        t.env->CallStaticVoidMethod(t.classID, t.methodID, jurl);
        t.env->DeleteLocalRef(jurl);
        t.env->DeleteLocalRef(t.classID);
    }
#endif
    args.rval().setUndefined();
    return true;
}

// ---------------------------------------------------------------------------
// 注册
// ---------------------------------------------------------------------------
#define OPPAI_FN(_cx, _obj, _name, _fn, _nargs) \
    JS_DefineFunction(_cx, _obj, _name, _fn, _nargs, \
                      JSPROP_READONLY | JSPROP_PERMANENT | JSPROP_ENUMERATE)

// ---------------------------------------------------------------------------
// JS error reporter
//
// 默认的 reporter 把错误写 stderr，Android 直接丢掉，
// 于是只看到 (evaluatedOK == JS_FALSE) 不知道错在哪。
// 这里换成写 logcat。
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// 绑定挂载助手：同时挂到 全局 和 cc 上
//
// 实测 JS 里用的是 `cc.utilsex`（见 assets/src/patch/update.js:71），
// 而 XGAdapter 之前探针测出来是全局的 —— 两边都挂最省事。
// ---------------------------------------------------------------------------
static JSObject* oppai_get_cc(JSContext* cx, JS::HandleObject global)
{
    JS::RootedValue ccVal(cx);
    JS_GetProperty(cx, global, "cc", &ccVal);
    if (ccVal.isObject()) {
        return &ccVal.toObject();
    }
    return nullptr;
}

void oppai_attach(JSContext* cx, JS::HandleObject global,
                         const char* name, JS::HandleObject ns)
{
    JS::RootedValue v(cx, OBJECT_TO_JSVAL(ns));
    // 全局
    JS_SetProperty(cx, global, name, v);
    // cc.<name>
    JSObject* cc = oppai_get_cc(cx, global);
    if (cc) {
        JS::RootedObject cco(cx, cc);
        JS_SetProperty(cx, cco, name, v);
    }
    CCLOG("[oppai] bound %s (global%s)", name, cc ? " + cc" : "");
}

static void oppaiJsErrorReporter(JSContext* cx, const char* message, JSErrorReport* report)
{
    if (report) {
        cocos2d::log("[oppai] JS ERROR: %s  @ %s:%u",
                     message ? message : "(no message)",
                     report->filename ? report->filename : "(no file)",
                     (unsigned)report->lineno);
    } else {
        cocos2d::log("[oppai] JS ERROR: %s", message ? message : "(no message)");
    }
}

void oppai_install_error_reporter(JSContext* cx)
{
    JS_SetErrorReporter(cx, oppaiJsErrorReporter);
    CCLOG("[oppai] JS error reporter installed");
}

void register_all_oppai_utilsex(JSContext* cx, JS::HandleObject obj)
{
    oppai_install_error_reporter(cx);

    JS::RootedObject parent(cx);
    JS::RootedObject proto(cx);
    JSObject* ns = JS_NewObject(cx, nullptr, proto, parent);
    JS::RootedObject utilsex(cx, ns);
    {
        JS::RootedObject rooted(cx, ns);
        oppai_attach(cx, obj, "utilsex", rooted);
    }

    OPPAI_FN(cx, utilsex, "getPlatform",      js_oppai_utilsex_getPlatform,      0);
    OPPAI_FN(cx, utilsex, "getPlatformName",  js_oppai_utilsex_getPlatformName,  0);
    OPPAI_FN(cx, utilsex, "getExtraPlatform", js_oppai_utilsex_getExtraPlatform, 0);
    OPPAI_FN(cx, utilsex, "getAppVersion",    js_oppai_utilsex_getAppVersion,    0);
    OPPAI_FN(cx, utilsex, "getBuildVersion",  js_oppai_utilsex_getBuildVersion,  0);
    OPPAI_FN(cx, utilsex, "getDeviceId",      js_oppai_utilsex_getDeviceId,      0);
    OPPAI_FN(cx, utilsex, "getDeviceUId",     js_oppai_utilsex_getDeviceUId,     0);
    OPPAI_FN(cx, utilsex, "getUdid",          js_oppai_utilsex_getUdid,          0);
    OPPAI_FN(cx, utilsex, "getVid",           js_oppai_utilsex_getVid,           0);
    OPPAI_FN(cx, utilsex, "getMacAddress",    js_oppai_utilsex_getMacAddress,    0);
    OPPAI_FN(cx, utilsex, "getPhoneModel",    js_oppai_utilsex_getPhoneModel,    0);
    OPPAI_FN(cx, utilsex, "getSystemVersion", js_oppai_utilsex_getSystemVersion, 0);
    OPPAI_FN(cx, utilsex, "isScreenLock",     js_oppai_utilsex_isScreenLock,     0);
    OPPAI_FN(cx, utilsex, "setScreenLock",    js_oppai_utilsex_setScreenLock,    1);
    OPPAI_FN(cx, utilsex, "openURL",          js_oppai_utilsex_openURL,          1);
    OPPAI_FN(cx, utilsex, "exit",             js_oppai_utilsex_exit,             1);

}


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
    cocos2d::Node* node = CSLoader::createNode(file);
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
    cocostudio::timeline::ActionTimeline* tl = CSLoader::createTimeline(file);
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
    CSLoader::destroyInstance();
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


// ---------------------------------------------------------------------------
// ccui.helper.seekNodeByName / seekNodeByTag
//
// 原版 .so 有 js_cocos2dx_ui_Helper_seekNodeByName / seekNodeByTag，
// 但 cocos2d-x 3.6 的 ui::Helper 只提供 seekWidgetByName/ByTag（收 Widget*），
// 收 Node* 的版本是 3.7 才加的。游戏 UpdateScene._init() 就卡在这里：
//     TypeError: seekNodeByName is not a function
// 自己写递归查找。
// ---------------------------------------------------------------------------
static cocos2d::Node* oppai_seek_by_name(cocos2d::Node* root, const std::string& name)
{
    if (!root) return nullptr;
    if (root->getName() == name) return root;
    const auto& kids = root->getChildren();
    for (auto it = kids.begin(); it != kids.end(); ++it) {
        cocos2d::Node* hit = oppai_seek_by_name(*it, name);
        if (hit) return hit;
    }
    return nullptr;
}

static cocos2d::Node* oppai_seek_by_tag(cocos2d::Node* root, int tag)
{
    if (!root) return nullptr;
    if (root->getTag() == tag) return root;
    const auto& kids = root->getChildren();
    for (auto it = kids.begin(); it != kids.end(); ++it) {
        cocos2d::Node* hit = oppai_seek_by_tag(*it, tag);
        if (hit) return hit;
    }
    return nullptr;
}

// JS 对象 -> cocos2d::Node*
static cocos2d::Node* oppai_jsval_to_node(JSContext* cx, JS::HandleValue v)
{
    if (!v.isObject()) return nullptr;
    JS::RootedObject obj(cx, v.toObjectOrNull());
    js_proxy_t* proxy = jsb_get_js_proxy(obj);
    return proxy ? (cocos2d::Node*)proxy->ptr : nullptr;
}

static bool js_oppai_helper_seekNodeByName(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    cocos2d::Node* ret = nullptr;
    if (argc >= 2) {
        cocos2d::Node* root = oppai_jsval_to_node(cx, args.get(0));
        std::string name;
        jsval_to_std_string(cx, args.get(1), &name);
        ret = oppai_seek_by_name(root, name);
    }
    jsval jret = JSVAL_NULL;
    if (ret) {
        jret = OBJECT_TO_JSVAL(js_get_or_create_proxy<cocos2d::Node>(cx, ret)->obj);
    }
    args.rval().set(jret);
    return true;
}

static bool js_oppai_helper_seekNodeByTag(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    cocos2d::Node* ret = nullptr;
    if (argc >= 2) {
        cocos2d::Node* root = oppai_jsval_to_node(cx, args.get(0));
        int32_t tag = 0;
        jsval_to_int32(cx, args.get(1), &tag);
        ret = oppai_seek_by_tag(root, tag);
    }
    jsval jret = JSVAL_NULL;
    if (ret) {
        jret = OBJECT_TO_JSVAL(js_get_or_create_proxy<cocos2d::Node>(cx, ret)->obj);
    }
    args.rval().set(jret);
    return true;
}

void register_all_oppai_uihelper(JSContext* cx, JS::HandleObject obj)
{
    // 找 ccui
    JS::RootedValue ccuiVal(cx);
    JS_GetProperty(cx, obj, "ccui", &ccuiVal);
    if (!ccuiVal.isObject()) {
        CCLOG("[oppai] uihelper: ccui 不存在");
        return;
    }
    JS::RootedObject ccui(cx, &ccuiVal.toObject());

    // 找/建 ccui.helper
    JS::RootedValue helperVal(cx);
    JS_GetProperty(cx, ccui, "helper", &helperVal);
    JS::RootedObject helper(cx);
    if (helperVal.isObject()) {
        helper.set(&helperVal.toObject());
    } else {
        helper.set(JS_NewObject(cx, nullptr, JS::NullPtr(), JS::NullPtr()));
        JS::RootedValue v(cx, OBJECT_TO_JSVAL(helper));
        JS_SetProperty(cx, ccui, "helper", v);
    }

    JS_DefineFunction(cx, helper, "seekNodeByName", js_oppai_helper_seekNodeByName, 2,
                      JSPROP_PERMANENT | JSPROP_ENUMERATE);
    JS_DefineFunction(cx, helper, "seekNodeByTag", js_oppai_helper_seekNodeByTag, 2,
                      JSPROP_PERMANENT | JSPROP_ENUMERATE);

    // 也挂一份全局的 ccui.helper 到 cc 上（有些代码走 cc.ui.helper）
    CCLOG("[oppai] uihelper binding registered");
}


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
    // 标准写法：jsb_remove_proxy(nativeProxy, jsProxy)
    // 之前写成 jsb_remove_proxy(jsProxy, nullptr) 会把代理映射表搞坏，
    // 渲染线程拿到空指针 -> SIGSEGV
    js_proxy_t* jsproxy = jsb_get_js_proxy(obj);
    if (jsproxy) {
        js_proxy_t* nproxy = jsb_get_native_proxy(jsproxy->ptr);
        jsb_remove_proxy(nproxy, jsproxy);
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
    js_proxy_t* p = jsb_new_proxy(cobj, obj);
    // 标准写法：把 JS 对象挂进 root 集合，防止它被提前 GC
    // （少了这句，JS 对象可能先被回收，原生对象就成了野指针 -> 渲染线程 SIGSEGV）
    if (p) {
        JS::AddNamedObjectRoot(cx, &p->obj, "cocos2d::experimental::ui::VideoPlayer");
    }
    args.rval().setObject(*obj);
    return true;
}

bool js_oppai_vp_setFileName(JSContext* cx, uint32_t argc, jsval* vp)
{
    JS::CallArgs args = JS::CallArgsFromVp(argc, vp);
    VideoPlayer* cobj = oppai_vp_native(cx, args);
    if (cobj && argc >= 1) {
        std::string s; jsval_to_std_string(cx, args.get(0), &s);
        cobj->setFileName(s);
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
    if (cobj) { cobj->play(); }
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

    // 静态工厂 create() —— 游戏用的是 ccui.VideoPlayer.create() 而不是 new
    static JSFunctionSpec st_funcs[] = {
        JS_FN("create", js_oppai_vp_ctor, 0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FS_END
    };

    oppai_vp_proto = JS_InitClass(cx, ccui, parentProto, oppai_vp_class,
                                  js_oppai_vp_ctor, 0, nullptr, funcs, nullptr, st_funcs);
    CCLOG("[oppai] ccui.VideoPlayer 原生绑定已注册 (proto=%p)", oppai_vp_proto);
}
#else
void register_all_oppai_videoplayer(JSContext* cx, JS::HandleObject obj)
{
    CCLOG("[oppai] VideoPlayer 仅在 Android 上绑定");
}
#endif
