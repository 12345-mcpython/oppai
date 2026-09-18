"""给 hook.js 加 ccui.VideoPlayer polyfill。

原版 .so 有 cocos2dx_experimental_video_VideoPlayer 的绑定（17 个方法），
vanilla cocos2d-js v3.6 没有。缺了它：
    TypeError: ccui.VideoPlayer is undefined
  @ assets/src/battle/guide/launchguidelayer.js:432

游戏用法：
    var vp = new ccui.VideoPlayer();
    vp.setFileName("res/video/newplayer.mp4");
    vp.addEventListener(function (sender, eventType) { ... });   // 播完继续
    vp.play();

这里做成「能建、能播（占位）、播完回调 COMPLETED」的实现，
保证战斗引导流程能往下走。

注：真·视频播放需要原生绑定（UIVideoPlayer-android.cpp 已经编进引擎，
    只是链接器因为没人引用丢掉了），要做的话得写整套 JSB 类注册。
"""

import io

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

BLOCK = r'''
    // ------------------------------------------------------------------
    // ccui.VideoPlayer polyfill
    //
    // 原版 .so 有 experimental_video_VideoPlayer 绑定，v3.6 仓库里没有。
    // 战斗引导层 launchguidelayer.js:432 用 new ccui.VideoPlayer() 播开场视频，
    // 缺了就抛 "ccui.VideoPlayer is undefined"，引导流程中断。
    //
    // 这里做占位实现：能建、能设文件名、play() 后异步回调 COMPLETED，
    // 让流程继续。（真播视频需要原生绑定。）
    // ------------------------------------------------------------------
    (function () {
        if (typeof ccui === "undefined") { return; }
        if (typeof ccui.VideoPlayer === "function" && ccui.VideoPlayer.__oppaiFake) {
            return;
        }

        var VP = ccui.Widget.extend({
            ctor: function () {
                this._super();
                this._file = "";
                this._url = "";
                this._playing = false;
                this._fullscreen = false;
                this._keepAspect = true;
                this._listeners = [];
                this._timer = null;
            },
            setFileName: function (f) { this._file = f || ""; },
            getFileName: function () { return this._file; },
            setURL: function (u) { this._url = u || ""; },
            getURL: function () { return this._url; },
            setFullScreenEnabled: function (b) { this._fullscreen = !!b; },
            isFullScreenEnabled: function () { return this._fullscreen; },
            setKeepAspectRatioEnabled: function (b) { this._keepAspect = !!b; },
            isKeepAspectRatioEnabled: function () { return this._keepAspect; },
            addEventListener: function (cb) {
                if (typeof cb === "function") { this._listeners.push(cb); }
            },
            _emit: function (type) {
                for (var i = 0; i < this._listeners.length; i++) {
                    try { this._listeners[i](this, type); }
                    catch (e) { emit("VIDEO 回调出错 " + e); }
                }
            },
            play: function () {
                this._playing = true;
                emit("VIDEO play(占位) " + (this._file || this._url));
                var self = this;
                // ccui.VideoPlayer 的事件常量：0=COMPLETED 1=PAUSED 2=STOPPED 3=PLAYING
                setTimeout(function () {
                    self._playing = false;
                    self._emit(0);
                }, 300);
            },
            pause: function () { this._emit(1); },
            resume: function () { this._playing = true; },
            stop: function () { this._playing = false; this._emit(2); },
            seekTo: function (s) { },
            isPlaying: function () { return this._playing; },
            onPlayEvent: function (type) { this._emit(type); },
            currentTime: function () { return 0; },
            getDuration: function () { return 0; }
        });
        VP.__oppaiFake = true;

        ccui.VideoPlayer = VP;
        emit("VIDEO ccui.VideoPlayer polyfill 已装（占位，不真正播放）");
    })();
'''

if "ccui.VideoPlayer polyfill" in s:
    print("已经有了")
else:
    anchor = "    var tries = 0;"
    i = s.find(anchor)
    if i < 0:
        raise SystemExit("找不到锚点")
    s = s[:i] + BLOCK.lstrip("\n") + "\n" + s[i:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入 ccui.VideoPlayer polyfill")
