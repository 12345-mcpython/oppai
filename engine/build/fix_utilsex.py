import io

P = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
s = io.open(P, encoding="utf-8").read()
orig = s

# cocos2d-x 3.6 的 js_manual_conversions.h 里没有 jsval_to_boolean
s = s.replace(
    """    bool locked = false;
    if (argc >= 1) {
        jsval_to_boolean(cx, args.get(0), &locked);
    }""",
    """    bool locked = false;
    if (argc >= 1) {
        locked = JS::ToBoolean(args.get(0));
    }""",
    1,
)

s = s.replace(
    """    int code = 0;
    if (argc >= 1) {
        jsval_to_int32(cx, args.get(0), (int32_t*)&code);
    }""",
    """    int32_t code = 0;
    if (argc >= 1) {
        jsval_to_int32(cx, args.get(0), &code);
    }""",
    1,
)

if s == orig:
    print("!! 没有替换成功")
else:
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已修正 jsval_to_boolean -> JS::ToBoolean")
