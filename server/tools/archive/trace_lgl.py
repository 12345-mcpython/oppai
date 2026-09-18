"""给 LaunchGuideLayer（战斗开场引导层）加调用跟踪。

现象：视频播完后卡住。
已确认 VideoPlayer 的原生绑定没问题 —— 单独测能收到
    log=[evt=0(PLAYING), evt=0, evt=3(COMPLETED)]
所以 COMPLETED 事件是送达的，卡点在引导层自己的状态机里。

从字节码看：
    _update()  = steps[this._index] -> 没有就 _end()
                 有 gamePause 就 battlePause/battleResume
                 然后 timeline.play(...) + timeline.setLastFrameCallFunc(bind(...))
    _next()    = this._index++; this._update()
    guideStart = !arg ? jump(2) : ... new BattleGuideTutorialLayer()
    onPlayerMovieCallBack = eventType===3 && isMobile -> scheduleOnce(lambda,1)
                            lambda: _videoPlayer.removeFromParent(); guideStart(); sound.resumeMusic()

这里把这些方法都包一层日志，一次就能定位卡在哪。
"""

import io

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

BLOCK = r'''
    // ------------------------------------------------------------------
    // LaunchGuideLayer（战斗开场引导层）调用跟踪
    //
    // 视频播完卡住时用。VideoPlayer 绑定本身已验证没问题
    // （单独测能收到 COMPLETED=3），所以卡点在引导层的状态机。
    // ------------------------------------------------------------------
    (function () {
        if (typeof LaunchGuideLayer === "undefined" || !LaunchGuideLayer.prototype) {
            emit("LGL-WRAP 找不到 LaunchGuideLayer");
            return;
        }
        if (LaunchGuideLayer.prototype.__oppaiWrapped) { return; }
        LaunchGuideLayer.prototype.__oppaiWrapped = true;

        var METHODS = ["ctor", "guideStart", "jump", "_update", "_next", "_end",
                       "onPlayerMovieCallBack", "launchBegan", "launchEnd",
                       "launchCancelled", "battleLaunchEnd", "judgePlacement",
                       "_judgePlacement"];
        var n = 0;
        for (var i = 0; i < METHODS.length; i++) {
            (function (m) {
                var orig = LaunchGuideLayer.prototype[m];
                if (typeof orig !== "function") { return; }
                n++;
                LaunchGuideLayer.prototype[m] = function () {
                    var idx = "";
                    try { idx = " _index=" + this._index; } catch (e) { }
                    emit("LGL." + m + "() 进入" + idx + " args=" + arguments.length);
                    try {
                        var r = orig.apply(this, arguments);
                        emit("LGL." + m + "() 返回" + idx);
                        return r;
                    } catch (e) {
                        emit("LGL." + m + "() 抛异常!! " + e);
                        throw e;
                    }
                };
            })(METHODS[i]);
        }
        emit("LGL-WRAP LaunchGuideLayer 已包装 " + n + " 个方法");
    })();
'''

if "LGL-WRAP" in s:
    print("已经有了")
else:
    anchor = "    var tries = 0;"
    i = s.find(anchor)
    if i < 0:
        raise SystemExit("找不到锚点")
    s = s[:i] + BLOCK.lstrip("\n") + "\n" + s[i:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入 LaunchGuideLayer 跟踪")
