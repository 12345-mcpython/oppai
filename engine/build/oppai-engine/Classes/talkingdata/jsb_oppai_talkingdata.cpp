/*
 * jsb_oppai_talkingdata.cpp —— 统计 SDK，全是空实现
 */

#include "jsb_oppai_utilsex.h"   // oppai_attach
#include "jsb_oppai_talkingdata.h"

#include "cocos2d.h"
#include "js_manual_conversions.h"

USING_NS_CC;

// 返回值常量（照抄原版 TalkingData 的语义，避免 JS 侧拿到 undefined 出错）
static bool retUndefined(JSContext* cx, uint32_t argc, jsval* vp) { JS::CallArgs a = JS::CallArgsFromVp(argc, vp); a.rval().setUndefined(); return true; }
static bool retTrue(JSContext* cx, uint32_t argc, jsval* vp)      { JS::CallArgs a = JS::CallArgsFromVp(argc, vp); a.rval().setBoolean(true); return true; }
static bool retEmptyString(JSContext* cx, uint32_t argc, jsval* vp){ JS::CallArgs a = JS::CallArgsFromVp(argc, vp); a.rval().set(std_string_to_jsval(cx, "")); return true; }

#define OPPAI_FN(_cx, _obj, _name, _fn, _nargs) \
    JS_DefineFunction(_cx, _obj, _name, _fn, _nargs, \
                      JSPROP_READONLY | JSPROP_PERMANENT | JSPROP_ENUMERATE)

void register_all_oppai_talkingdata(JSContext* cx, JS::HandleObject obj)
{
    JS::RootedObject parent(cx);
    JS::RootedObject proto(cx);
    JSObject* ns = JS_NewObject(cx, nullptr, proto, parent);
    JS::RootedObject td(cx, ns);
    {
        JS::RootedObject rooted(cx, ns);
        oppai_attach(cx, obj, "TalkingDataAdapter", rooted);
    }

    OPPAI_FN(cx, td, "getDeviceId",       retEmptyString, 0);

    // 账号
    OPPAI_FN(cx, td, "setAccount",        retUndefined, 1);
    OPPAI_FN(cx, td, "setAccountName",    retUndefined, 1);
    OPPAI_FN(cx, td, "setAccountType",    retUndefined, 1);
    OPPAI_FN(cx, td, "setLevel",          retUndefined, 1);
    OPPAI_FN(cx, td, "setGender",         retUndefined, 1);
    OPPAI_FN(cx, td, "setAge",            retUndefined, 1);
    OPPAI_FN(cx, td, "setGameServer",     retUndefined, 1);

    // 自定义事件
    OPPAI_FN(cx, td, "onEvent",           retUndefined, 2);

    // 任务
    OPPAI_FN(cx, td, "onBegin",           retUndefined, 1);
    OPPAI_FN(cx, td, "onCompleted",       retUndefined, 1);
    OPPAI_FN(cx, td, "onFailed",          retUndefined, 2);

    // 虚拟币 / 充值
    OPPAI_FN(cx, td, "onChargeRequest",   retUndefined, 6);
    OPPAI_FN(cx, td, "onChargeSuccess",   retUndefined, 1);
    OPPAI_FN(cx, td, "onReward",          retUndefined, 2);

    // 道具
    OPPAI_FN(cx, td, "onPurchase",        retUndefined, 3);
    OPPAI_FN(cx, td, "onUse",             retUndefined, 2);

    CCLOG("[oppai] TalkingDataAdapter binding registered (all no-op)");
}
