APP_STL := gnustl_static

# NDK r10e 的默认 APP_PLATFORM 是 android-3，那个版本没有 GLES2 头，
# 会报 `GLES2/gl2platform.h: No such file or directory`。必须显式指定。
APP_PLATFORM := android-9

# 先编 armeabi 验证工具链（和原版一致），跑通后再改 x86 做性能对比
APP_ABI := armeabi

APP_CPPFLAGS := -frtti -DCC_ENABLE_CHIPMUNK_INTEGRATION=1 -std=c++11 -fsigned-char
APP_LDFLAGS := -latomic

ifeq ($(NDK_DEBUG),1)
  APP_CPPFLAGS += -DCOCOS2D_DEBUG=1
  APP_OPTIM := debug
else
  APP_CPPFLAGS += -DNDEBUG
  APP_OPTIM := release
endif
