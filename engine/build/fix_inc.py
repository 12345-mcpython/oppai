import io

P = r"E:\code\zcsmw\engine\build\oppai-engine\jni\Android.mk"
s = io.open(P, encoding="utf-8").read()

BLOCK = """
# cocostudio 的头文件（CocoStudio.h 等）不在 bindings 模块的导出路径里，得自己加。
# $(LOCAL_PATH) 是 <app>/jni，所以 ../../ 就是 E:\\code\\apk\\move
OPPAI_MOVE := $(LOCAL_PATH)/../..
LOCAL_C_INCLUDES += \\
$(OPPAI_MOVE)/src/cocos2d-js/frameworks/js-bindings/cocos2d-x/cocos/editor-support \\
$(OPPAI_MOVE)/src/cocos2d-js/frameworks/js-bindings/cocos2d-x/cocos/editor-support/cocostudio
"""

if "OPPAI_MOVE" in s:
    print("已经加过了")
else:
    anchor = "LOCAL_STATIC_LIBRARIES := cocos_jsb_static"
    assert anchor in s, "找不到锚点"
    s = s.replace(anchor, BLOCK.strip() + "\n\n" + anchor, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入 cocostudio 头文件路径")
