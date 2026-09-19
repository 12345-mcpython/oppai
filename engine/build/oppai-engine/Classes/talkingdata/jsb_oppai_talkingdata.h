/*
 * jsb_oppai_talkingdata —— 重写版（TalkingData 统计）
 * 原版：Classes/talkingdata/jsb_oppai_talkingdata.cpp（游戏私有）
 *
 * 注意：**JS 侧一处都没调用**（全量扫 jsc 的结果），纯粹是历史遗留。
 * 所以这里全部做成空实现 —— 连 Java 都不用转。
 */

#include "jsapi.h"
#include "jsfriendapi.h"

void register_all_oppai_talkingdata(JSContext* cx, JS::HandleObject obj);
