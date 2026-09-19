import io
import re

DIR = r"E:\code\zcsmw\engine\build\oppai-engine\Classes"

# 1) utilsex.cpp：oppai_attach 去掉 static
p = DIR + r"\utilsex\jsb_oppai_utilsex.cpp"
s = io.open(p, encoding="utf-8").read()
s = s.replace("static void oppai_attach(JSContext* cx, JS::HandleObject global,",
              "void oppai_attach(JSContext* cx, JS::HandleObject global,", 1)
s = s.replace("oppai_attach(cx, obj, \"utilsex\", ns);",
              "{\n        JS::RootedObject rooted(cx, ns);\n        oppai_attach(cx, obj, \"utilsex\", rooted);\n    }", 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("utilsex.cpp 已修")

# 2) 另外三个：ns 要包成 RootedObject
for rel, name in (("gameshare/jsb_oppai_gameshare.cpp", "GameShare"),
                  ("xg/jsb_oppai_xg.cpp", "XGAdapter"),
                  ("talkingdata/jsb_oppai_talkingdata.cpp", "TalkingDataAdapter")):
    f = DIR + "\\" + rel.replace("/", "\\")
    t = io.open(f, encoding="utf-8").read()
    old = f'    oppai_attach(cx, obj, "{name}", ns);'
    new = (f'    {{\n'
           f'        JS::RootedObject rooted(cx, ns);\n'
           f'        oppai_attach(cx, obj, "{name}", rooted);\n'
           f'    }}')
    if old in t:
        t = t.replace(old, new, 1)
        io.open(f, "w", encoding="utf-8", newline="\n").write(t)
        print(rel, "已修")
    else:
        print(rel, "没找到目标行")
