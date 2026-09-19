/*
 * jsb_oppai_utilsex —— 重写版
 *
 * 原版是游戏自研的 bindings/manual/Classes/utilsex/jsb_oppai_utilsex.cpp，
 * 源码不随 APK 发布。这里按从 libcocos2djs.so 里挖出来的符号表重写：
 *
 *   utilsex.getPlatform / getPlatformName / getExtraPlatform /
 *           getAppVersion / getBuildVersion / getDeviceId / getDeviceUId /
 *           getUdid / getVid / getMacAddress / getSystemVersion /
 *           getPhoneModel / setScreenLock / isScreenLock / openURL / exit
 *
 * 能转发到 Java 的一律转发（那些实现 APK 里都还在），
 * 纯原生实现的（getPlatform 等）按 appconfig.jsc 里的常量返回。
 */

#ifndef __JSB_OPPAI_UTILSEX_H__
#define __JSB_OPPAI_UTILSEX_H__

#include "jsapi.h"
#include "jsfriendapi.h"

void register_all_oppai_utilsex(JSContext* cx, JS::HandleObject obj);

// 把命名空间同时挂到 全局 和 cc 上（定义在 jsb_oppai_utilsex.cpp）
void oppai_attach(JSContext* cx, JS::HandleObject global,
                  const char* name, JS::HandleObject ns);
// 也在这个编译单元里实现（见 .cpp 末尾）
void register_all_oppai_ccs(JSContext* cx, JS::HandleObject obj);
void register_all_oppai_csloader(JSContext* cx, JS::HandleObject obj);
void register_all_oppai_uihelper(JSContext* cx, JS::HandleObject obj);
void register_all_oppai_bugly(JSContext* cx, JS::HandleObject obj);
void register_all_oppai_touch(JSContext* cx, JS::HandleObject obj);
void register_all_oppai_videoplayer(JSContext* cx, JS::HandleObject obj);

#endif
