"""给 ScriptingCore.cpp 打补丁：把 JS 异常内容打到 logcat。

cocos2d-js 的 JS_ReportPendingException 走默认 error reporter，
它是写 stderr 的，Android 上直接丢弃 —— 所以只看得到
`(evaluatedOK == JS_FALSE)` 但看不到具体错在哪。

这里在 runScript 失败时先把 pending exception 取出来打成日志。
"""

import io

P = r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings\bindings\manual\ScriptingCore.cpp"
s = io.open(P, encoding="utf-8").read()
orig = s

OLD = """        if (false == evaluatedOK) {
            cocos2d::log("(evaluatedOK == JS_FALSE)");
            JS_ReportPendingException(cx);
        }"""

NEW = """        if (false == evaluatedOK) {
            // [oppai] 把真实异常打出来 —— 默认 error reporter 写 stderr，Android 收不到
            JS::RootedValue exc(cx);
            if (JS_GetPendingException(cx, &exc)) {
                JS_ClearPendingException(cx);
                JS::RootedString str(cx, JS::ToString(cx, exc));
                if (str) {
                    JSAutoByteString bytes(cx, str);
                    cocos2d::log("[oppai] JS EXCEPTION in %s : %s",
                                 path, bytes.ptr() ? bytes.ptr() : "(null)");
                }
            }
            cocos2d::log("(evaluatedOK == JS_FALSE)");
            JS_ReportPendingException(cx);
        }"""

if "[oppai] JS EXCEPTION" in s:
    print("已经打过补丁了")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已给 ScriptingCore.cpp 加上异常打印")
else:
    print("!! 没找到目标片段，检查 ScriptingCore.cpp")
