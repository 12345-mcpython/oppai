"""把 jsb_oppai_ccs 加进 Android.mk 和 AppDelegate.cpp。"""

import io

MK = r"E:\code\zcsmw\engine\build\oppai-engine\jni\Android.mk"
AD = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\AppDelegate.cpp"

# ---- Android.mk ----
s = io.open(MK, encoding="utf-8").read()
if "jsb_oppai_ccs.cpp" not in s:
    s = s.replace(
        "../Classes/talkingdata/jsb_oppai_talkingdata.cpp",
        "../Classes/talkingdata/jsb_oppai_talkingdata.cpp \\\n"
        "../Classes/ccs/jsb_oppai_ccs.cpp",
        1,
    )
    s = s.replace(
        "$(LOCAL_PATH)/../Classes/talkingdata",
        "$(LOCAL_PATH)/../Classes/talkingdata \\\n"
        "                    $(LOCAL_PATH)/../Classes/ccs",
        1,
    )
    io.open(MK, "w", encoding="utf-8", newline="\n").write(s)
    print("Android.mk: 已加入 jsb_oppai_ccs.cpp")
else:
    print("Android.mk: 已经有了")

# ---- AppDelegate.cpp ----
s2 = io.open(AD, encoding="utf-8").read()
if "jsb_oppai_ccs.h" not in s2:
    s2 = s2.replace(
        '#include "jsb_oppai_talkingdata.h"',
        '#include "jsb_oppai_talkingdata.h"\n#include "jsb_oppai_ccs.h"',
        1,
    )
    s2 = s2.replace(
        "sc->addRegisterCallback(register_all_oppai_talkingdata);",
        "sc->addRegisterCallback(register_all_oppai_talkingdata);\n"
        "    sc->addRegisterCallback(register_all_oppai_ccs);",
        1,
    )
    io.open(AD, "w", encoding="utf-8", newline="\n").write(s2)
    print("AppDelegate.cpp: 已加入 register_all_oppai_ccs")
else:
    print("AppDelegate.cpp: 已经有了")
