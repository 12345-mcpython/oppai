"""在 VideoPlayer 的事件回调里加一行日志（事件很少，不吵）。

目的：确认游戏那个播放器到底收到了哪些事件。
单独测能收到 [PLAYING, PLAYING, COMPLETED]，但游戏里卡住了，
所以要看看是不是事件真的到了、还是回调没接上。
"""

import io

P = r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp"
s = io.open(P, encoding="utf-8").read()

OLD = '''        cobj->addEventListener([=](Ref* sender, VideoPlayer::EventType event) {
            JSB_AUTOCOMPARTMENT_WITH_GLOBAL_OBJCET
            jsval argv[2];'''

NEW = '''        cobj->addEventListener([=](Ref* sender, VideoPlayer::EventType event) {
            JSB_AUTOCOMPARTMENT_WITH_GLOBAL_OBJCET
            CCLOG("[oppai] VideoPlayer 事件 %d", (int)event);
            jsval argv[2];'''

if 'VideoPlayer 事件 %d' in s:
    print("已经加过")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入事件日志")
else:
    print("!! 没找到回调片段")
