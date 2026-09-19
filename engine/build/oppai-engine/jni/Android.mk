LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)

LOCAL_MODULE := cocos2djs_shared

LOCAL_MODULE_FILENAME := libcocos2djs

# 注意：LOCAL_PATH 是 <app>/jni，Classes 在 <app>/Classes，
# 所以是 ../Classes（runtime 模板里多一层 proj.android，写的是 ../../Classes）
#
# 源文件清单照抄 js-template-runtime（游戏的 main.cpp 就是它，
# 里面实现了 AppActivity 的 nativeIsLandScape / nativeIsDebug），
# 末尾追加 4 个自研绑定。
LOCAL_SRC_FILES := \
../Classes/protobuf-lite/google/protobuf/io/coded_stream.cc \
../Classes/protobuf-lite/google/protobuf/stubs/common.cc \
../Classes/protobuf-lite/google/protobuf/extension_set.cc \
../Classes/protobuf-lite/google/protobuf/generated_message_util.cc \
../Classes/protobuf-lite/google/protobuf/message_lite.cc \
../Classes/protobuf-lite/google/protobuf/stubs/once.cc \
../Classes/protobuf-lite/google/protobuf/stubs/atomicops_internals_x86_gcc.cc \
../Classes/protobuf-lite/google/protobuf/repeated_field.cc \
../Classes/protobuf-lite/google/protobuf/wire_format_lite.cc \
../Classes/protobuf-lite/google/protobuf/io/zero_copy_stream.cc \
../Classes/protobuf-lite/google/protobuf/io/zero_copy_stream_impl_lite.cc \
../Classes/protobuf-lite/google/protobuf/stubs/stringprintf.cc \
../Classes/runtime/ConnectWaitLayer.cpp \
../Classes/runtime/ConsoleCommand.cpp \
../Classes/runtime/FileServer.cpp \
../Classes/runtime/Landscape_png.cpp \
../Classes/runtime/PlayDisable_png.cpp \
../Classes/runtime/PlayEnable_png.cpp \
../Classes/runtime/Portrait_png.cpp \
../Classes/runtime/Protos.pb.cc \
../Classes/runtime/Runtime.cpp \
../Classes/runtime/Shine_png.cpp \
../Classes/VisibleRect.cpp \
../Classes/AppDelegate.cpp \
../Classes/ConfigParser.cpp \
hellojavascript/Runtime_android.cpp \
hellojavascript/main.cpp \
../Classes/utilsex/jsb_oppai_utilsex.cpp \
../Classes/gameshare/jsb_oppai_gameshare.cpp \
../Classes/xg/jsb_oppai_xg.cpp \
../Classes/talkingdata/jsb_oppai_talkingdata.cpp

LOCAL_C_INCLUDES := \
$(LOCAL_PATH)/../Classes/protobuf-lite \
$(LOCAL_PATH)/../Classes/runtime \
$(LOCAL_PATH)/../Classes \
$(LOCAL_PATH)/../Classes/utilsex \
$(LOCAL_PATH)/../Classes/gameshare \
$(LOCAL_PATH)/../Classes/xg \
$(LOCAL_PATH)/../Classes/talkingdata

# 注意：不要在 LOCAL_C_INCLUDES 里加 cocos/editor-support，
# 会让 deprecated/CCString.h 那一串头文件解析出错（__String::create / StringMake）。
# 当前 ccs 绑定是最小实现，不需要 cocostudio 头。

# cocostudio 的头文件（CSLoader.h / CCActionTimeline.h）不在 bindings 模块的导出路径里
OPPAI_MOVE := $(LOCAL_PATH)/../../..
LOCAL_C_INCLUDES += \
$(OPPAI_MOVE)/src/cocos2d-js/frameworks/js-bindings/cocos2d-x/cocos/editor-support \
$(OPPAI_MOVE)/src/cocos2d-js/frameworks/js-bindings/cocos2d-x/cocos/editor-support/cocostudio

LOCAL_STATIC_LIBRARIES := cocos_jsb_static

LOCAL_EXPORT_CFLAGS := -DCOCOS2D_DEBUG=2 -DCOCOS2D_JAVASCRIPT

include $(BUILD_SHARED_LIBRARY)


$(call import-module,bindings)
