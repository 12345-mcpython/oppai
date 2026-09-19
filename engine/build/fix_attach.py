"""把 4 个自研绑定同时注册到 全局 和 cc 上。

实测 JS 里用的是 `cc.utilsex`（update.js:71 报 cc.utilsex is undefined），
但 XGAdapter 之前测出来是全局函数 —— 所以两边都挂上最稳。
"""

import io

P = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
s = io.open(P, encoding="utf-8").read()

HELPER = r'''
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

static void oppai_attach(JSContext* cx, JS::HandleObject global,
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
'''

if "oppai_attach" not in s:
    # 插到 error reporter 前面
    anchor = "static void oppaiJsErrorReporter"
    assert anchor in s
    s = s.replace(anchor, HELPER.strip() + "\n\n" + anchor, 1)

    # utilsex
    s = s.replace(
        """    JS::RootedValue nsVal(cx);
    nsVal = OBJECT_TO_JSVAL(ns);
    JS_SetProperty(cx, obj, "utilsex", nsVal);""",
        """    oppai_attach(cx, obj, "utilsex", ns);""",
        1,
    )

    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("utilsex.cpp: 已改成 全局 + cc 双挂载")
else:
    print("utilsex.cpp: 已经改过")

# ---- 另外三个绑定也一起改 ----
for f, name in (
    (r"E:\code\zcsmw\engine\build\oppai-engine\Classes\gameshare\jsb_oppai_gameshare.cpp", "GameShare"),
    (r"E:\code\zcsmw\engine\build\oppai-engine\Classes\xg\jsb_oppai_xg.cpp", "XGAdapter"),
    (r"E:\code\zcsmw\engine\build\oppai-engine\Classes\talkingdata\jsb_oppai_talkingdata.cpp", "TalkingDataAdapter"),
):
    t = io.open(f, encoding="utf-8").read()
    old = f'''    JS::RootedValue nsVal(cx);
    nsVal = OBJECT_TO_JSVAL(ns);
    JS_SetProperty(cx, obj, "{name}", nsVal);'''
    if old in t:
        t = t.replace(old, f'''    oppai_attach(cx, obj, "{name}", ns);''', 1)
        io.open(f, "w", encoding="utf-8", newline="\n").write(t)
        print(f"{name}: 已改成双挂载")
    else:
        print(f"{name}: 没找到目标片段（可能写法不同）")
