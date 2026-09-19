import io
import os

DIR = r"E:\code\zcsmw\engine\build\oppai-engine\Classes"
H = os.path.join(DIR, "utilsex", "jsb_oppai_utilsex.h")

h = io.open(H, encoding="utf-8").read()
if "oppai_attach" not in h:
    h = h.replace(
        "void register_all_oppai_utilsex(JSContext* cx, JS::HandleObject obj);",
        "void register_all_oppai_utilsex(JSContext* cx, JS::HandleObject obj);\n\n"
        "// 把命名空间同时挂到 全局 和 cc 上（定义在 jsb_oppai_utilsex.cpp）\n"
        "void oppai_attach(JSContext* cx, JS::HandleObject global,\n"
        "                  const char* name, JS::HandleObject ns);",
        1,
    )
    io.open(H, "w", encoding="utf-8", newline="\n").write(h)
    print("utilsex.h: 已声明 oppai_attach")

# 另外三个文件要 include 这个头
for rel in ("gameshare/jsb_oppai_gameshare.cpp",
            "xg/jsb_oppai_xg.cpp",
            "talkingdata/jsb_oppai_talkingdata.cpp"):
    p = os.path.join(DIR, rel)
    s = io.open(p, encoding="utf-8").read()
    if "jsb_oppai_utilsex.h" not in s:
        # 在第一个 #include 行前插入
        i = s.find("#include ")
        s = s[:i] + '#include "jsb_oppai_utilsex.h"   // oppai_attach\n' + s[i:]
        io.open(p, "w", encoding="utf-8", newline="\n").write(s)
        print(rel, ": 已加入 include")
    else:
        print(rel, ": 已有 include")
