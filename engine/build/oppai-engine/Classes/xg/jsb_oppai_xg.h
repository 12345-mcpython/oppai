/*
 * jsb_oppai_xg —— 重写版（腾讯信鸽推送）
 * 原版：Classes/xg/jsb_oppai_xg.cpp（游戏私有）
 * 符号：XGAdapter.{setTag, delTag, addNotification, clearNotifications,
 *                  serviceEnabled, getDeviceToken}
 * 转发到 Java org/cocos2dx/javascript/XGAdapter
 */

#include "jsapi.h"
#include "jsfriendapi.h"

void register_all_oppai_xg(JSContext* cx, JS::HandleObject obj);
