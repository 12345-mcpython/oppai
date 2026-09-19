import io

P = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
s = io.open(P, encoding="utf-8").read()

OLD = '''    JS::RootedObject obj(cx, JS_NewObject(cx, oppai_vp_class, proto, parent));
    jsb_new_proxy(cobj, obj);
    args.rval().setObject(*obj);
    CCLOG("[oppai] new ccui.VideoPlayer()");
    return true;'''

NEW = '''    JS::RootedObject obj(cx, JS_NewObject(cx, oppai_vp_class, proto, parent));
    js_proxy_t* p = jsb_new_proxy(cobj, obj);
    // 标准写法：把 JS 对象挂进 root 集合，防止它被提前 GC
    // （少了这句，JS 对象可能先被回收，原生对象就成了野指针 -> 渲染线程 SIGSEGV）
    if (p) {
        JS::AddNamedObjectRoot(cx, &p->obj, "cocos2d::experimental::ui::VideoPlayer");
    }
    args.rval().setObject(*obj);
    return true;'''

if "AddNamedObjectRoot" in s:
    print("已经有了")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("ctor 已加 AddNamedObjectRoot")
else:
    print("!! 没找到")
