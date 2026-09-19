import io

H = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.h"
AD = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\AppDelegate.cpp"

s = io.open(H, encoding="utf-8").read()
if "register_all_oppai_ccs" not in s:
    s = s.replace(
        "void register_all_oppai_utilsex(JSContext* cx, JS::HandleObject obj);",
        "void register_all_oppai_utilsex(JSContext* cx, JS::HandleObject obj);\n"
        "// 也在这个编译单元里实现（见 .cpp 末尾）\n"
        "void register_all_oppai_ccs(JSContext* cx, JS::HandleObject obj);",
        1,
    )
    io.open(H, "w", encoding="utf-8", newline="\n").write(s)
    print("jsb_oppai_utilsex.h: 已声明 register_all_oppai_ccs")

s2 = io.open(AD, encoding="utf-8").read()
if '#include "jsb_oppai_ccs.h"' in s2:
    s2 = s2.replace('\n#include "jsb_oppai_ccs.h"', "", 1)
    io.open(AD, "w", encoding="utf-8", newline="\n").write(s2)
    print("AppDelegate.cpp: 已移除 jsb_oppai_ccs.h")
