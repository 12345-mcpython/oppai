/*
 * jsb_oppai_gameshare —— 重写版
 * 原版：Classes/gameshare/jsb_oppai_gameshare.cpp（游戏私有）
 * 符号：GameShare.shareToWeChat / shareToSina
 * 转发到 Java org/cocos2dx/javascript/GameShare
 */

#include "jsapi.h"
#include "jsfriendapi.h"

void register_all_oppai_gameshare(JSContext* cx, JS::HandleObject obj);
