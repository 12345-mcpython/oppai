"""给 utilsex 绑定加一个 JS error reporter，把所有 JS 报错打到 logcat。

cocos2d-js 默认的 error reporter 写 stderr，Android 会丢弃，
所以只看得到 (evaluatedOK == JS_FALSE) 却看不到原因。
"""

import io

P = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
s = io.open(P, encoding="utf-8").read()

CODE = r'''

// ---------------------------------------------------------------------------
// JS error reporter
//
// 默认的 reporter 把错误写 stderr，Android 直接丢掉，
// 于是只看到 (evaluatedOK == JS_FALSE) 不知道错在哪。
// 这里换成写 logcat。
// ---------------------------------------------------------------------------
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
'''

if "oppaiJsErrorReporter" not in s:
    s = s.replace(
        "void register_all_oppai_utilsex(JSContext* cx, JS::HandleObject obj)\n{",
        CODE.strip() + "\n\nvoid register_all_oppai_utilsex(JSContext* cx, JS::HandleObject obj)\n{\n"
        "    oppai_install_error_reporter(cx);\n",
        1,
    )
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入 JS error reporter")
else:
    print("已经有了")
