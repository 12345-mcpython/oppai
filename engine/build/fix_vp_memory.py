"""修 VideoPlayer 绑定的内存管理（崩溃根因）。

崩溃：
    Fatal signal 11 (SIGSEGV), fault addr 0x0 in tid GLThread 307
    Cause: null pointer dereference

我原先写的：
  ctor:     cobj->retain();  jsb_new_proxy(cobj, obj);        // 少了 AddNamedObjectRoot
  finalize: jsb_remove_proxy(p, nullptr);                     // 参数反了！

cocos2d-js 的标准写法（见 jsb_cocos2dx_ui_auto.cpp 的 Widget）：
  ctor:     _ccobj->autorelease();
            jsb_new_proxy(cobj, obj);
            JS::AddNamedObjectRoot(cx, &p->obj, typeName);    // 防止 JS 对象被提前 GC
  finalize: jsproxy = jsb_get_js_proxy(obj);
            if (jsproxy) {
                nproxy = jsb_get_native_proxy(jsproxy->ptr);
                jsb_remove_proxy(nproxy, jsproxy);            // (nativeProxy, jsProxy)
            }

jsb_remove_proxy(jsProxy, nullptr) 会把映射表搞坏，导致渲染线程
通过代理拿原生指针时得到空指针 -> SIGSEGV。
"""

import io

P = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
s = io.open(P, encoding="utf-8").read()

# ---- 1) finalize 改成标准写法 ----
OLD_FIN = '''static void js_oppai_vp_finalize(JSFreeOp* fop, JSObject* obj)
{
    js_proxy_t* p = jsb_get_js_proxy(obj);
    if (p) {
        VideoPlayer* vp = (VideoPlayer*)p->ptr;
        if (vp) { vp->release(); }
        jsb_remove_proxy(p, nullptr);
    }
}'''

NEW_FIN = '''static void js_oppai_vp_finalize(JSFreeOp* fop, JSObject* obj)
{
    // 标准写法：jsb_remove_proxy(nativeProxy, jsProxy)
    // 之前写成 jsb_remove_proxy(jsProxy, nullptr) 会把代理映射表搞坏，
    // 渲染线程拿到空指针 -> SIGSEGV
    js_proxy_t* jsproxy = jsb_get_js_proxy(obj);
    if (jsproxy) {
        js_proxy_t* nproxy = jsb_get_native_proxy(jsproxy->ptr);
        jsb_remove_proxy(nproxy, jsproxy);
    }
}'''

if 'jsb_remove_proxy(nproxy, jsproxy)' in s:
    print("finalize 已经是对的")
elif OLD_FIN in s:
    s = s.replace(OLD_FIN, NEW_FIN, 1)
    print("finalize 已修")
else:
    print("!! 没找到 finalize")

# ---- 2) ctor 加上 AddNamedObjectRoot ----
OLD_CTOR = '''    JS::RootedObject obj(cx, JS_NewObject(cx, oppai_vp_class, proto, parent));
    jsb_new_proxy(cobj, obj);
    args.rval().setObject(*obj);
    return true;'''

NEW_CTOR = '''    JS::RootedObject obj(cx, JS_NewObject(cx, oppai_vp_class, proto, parent));
    js_proxy_t* p = jsb_new_proxy(cobj, obj);
    // 标准写法：把 JS 对象挂进 root 集合，防止它被提前 GC
    // （少了这句，JS 对象可能先被回收，原生对象就成了野指针）
    if (p) {
        JS::AddNamedObjectRoot(cx, &p->obj, "cocos2d::experimental::ui::VideoPlayer");
    }
    args.rval().setObject(*obj);
    return true;'''

if 'AddNamedObjectRoot' in s:
    print("ctor 已经有 AddNamedObjectRoot")
elif OLD_CTOR in s:
    s = s.replace(OLD_CTOR, NEW_CTOR, 1)
    print("ctor 已加 AddNamedObjectRoot")
else:
    print("!! 没找到 ctor 片段")

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("完成")
