"""给原生 ccui.VideoPlayer 补静态方法 create()。

游戏 launchguidelayer.js:432 用的是 ccui.VideoPlayer.create()（静态工厂），
不是 new ccui.VideoPlayer()。只注册构造函数会报：
    TypeError: ccui.VideoPlayer.create is not a function

原版 .so 的绑定里本来就有 create / createP9JSContext... ✓
通过 JS_InitClass 的 static_fs 参数挂上去。
"""

import io

P = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
s = io.open(P, encoding="utf-8").read()

OLD = '''    oppai_vp_proto = JS_InitClass(cx, ccui, parentProto, oppai_vp_class,
                                  js_oppai_vp_ctor, 0, nullptr, funcs, nullptr, nullptr);'''

NEW = '''    // 静态工厂 create() —— 游戏用的是 ccui.VideoPlayer.create() 而不是 new
    static JSFunctionSpec st_funcs[] = {
        JS_FN("create", js_oppai_vp_ctor, 0, JSPROP_PERMANENT | JSPROP_ENUMERATE),
        JS_FS_END
    };

    oppai_vp_proto = JS_InitClass(cx, ccui, parentProto, oppai_vp_class,
                                  js_oppai_vp_ctor, 0, nullptr, funcs, nullptr, st_funcs);'''

if "静态工厂 create()" in s:
    print("已经加过")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入静态 create()")
else:
    print("!! 没找到 JS_InitClass 调用")
