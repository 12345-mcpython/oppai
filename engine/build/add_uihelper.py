"""补上 ccui.helper.seekNodeByName / seekNodeByTag。

原版 .so 里有 js_cocos2dx_ui_Helper_seekNodeByName / seekNodeByTag，
但 cocos2d-x 3.6 的 ui::Helper 只有 seekWidgetByName/ByTag（收 Widget*），
没有收 Node* 的 seekNodeByName（那是 3.7+ 加的）。
游戏 UpdateScene._init() 就卡在这个调用上：
    TypeError: seekNodeByName is not a function
自己写一份递归查找即可。
"""

import io

UTIL = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
HDR = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.h"
AD = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\AppDelegate.cpp"

CODE = r'''

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
'''

# 1) utilsex.cpp
s = io.open(UTIL, encoding="utf-8").read()
if "register_all_oppai_uihelper" not in s:
    s = s.rstrip() + "\n" + CODE
    io.open(UTIL, "w", encoding="utf-8", newline="\n").write(s)
    print("utilsex.cpp: 已加入 ccui.helper")
else:
    print("utilsex.cpp: 已有")

# 2) 头文件
h = io.open(HDR, encoding="utf-8").read()
if "register_all_oppai_uihelper" not in h:
    h = h.replace(
        "void register_all_oppai_csloader(JSContext* cx, JS::HandleObject obj);",
        "void register_all_oppai_csloader(JSContext* cx, JS::HandleObject obj);\n"
        "void register_all_oppai_uihelper(JSContext* cx, JS::HandleObject obj);",
        1,
    )
    io.open(HDR, "w", encoding="utf-8", newline="\n").write(h)
    print("utilsex.h: 已声明")

# 3) AppDelegate
a = io.open(AD, encoding="utf-8").read()
if "register_all_oppai_uihelper" not in a:
    a = a.replace(
        "sc->addRegisterCallback(register_all_oppai_csloader);",
        "sc->addRegisterCallback(register_all_oppai_csloader);\n"
        "    sc->addRegisterCallback(register_all_oppai_uihelper);",
        1,
    )
    io.open(AD, "w", encoding="utf-8", newline="\n").write(a)
    print("AppDelegate: 已加入")
