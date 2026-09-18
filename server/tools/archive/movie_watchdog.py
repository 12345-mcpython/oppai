"""给引导层的视频加看门狗兜底。

现象：视频满屏正常播放（播放器是活的），但播完后游戏不回到战斗。
守卫已确认装上了，而 onPlayerMovieCallBack 从没被调用过 ——
说明引擎的 COMPLETED(3) 事件没送到 JS。

不再纠结事件为什么没到，直接兜底：
  定时检查 _videoPlayer，如果它已经停止播放（isPlaying() 为 false）
  且持续了一小会儿，就按"播放完成"处理 —— 也就是替引擎调一次
  onPlayerMovieCallBack(this, 3)。

isPlaying() 来自 VideoPlayer 的原生绑定（PLAYING 事件置 true，
其它事件置 false），所以停播后为 false 是可靠信号。

重复调用由已有的幂等守卫挡住，不会重复推进。
"""

import io

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

BLOCK = r'''
    // ------------------------------------------------------------------
    // 引导层视频看门狗
    //
    // 视频能满屏正常播放，但播完后引擎的 COMPLETED(3) 事件没送到 JS，
    // 于是 onPlayerMovieCallBack 永不触发、游戏卡在视频最后一帧。
    //
    // 兜底：轮询 _videoPlayer.isPlaying()，停播超过 2 秒就替引擎调一次
    // onPlayerMovieCallBack(this, 3)。重复调用由幂等守卫挡掉。
    // ------------------------------------------------------------------
    (function installMovieWatchdog() {
        if (typeof LaunchGuideLayer === "undefined" || !LaunchGuideLayer.prototype) {
            if (!window.__oppaiMovieWdTimer) {
                window.__oppaiMovieWdTimer = setInterval(function () {
                    if (typeof LaunchGuideLayer !== "undefined" && LaunchGuideLayer.prototype) {
                        clearInterval(window.__oppaiMovieWdTimer);
                        window.__oppaiMovieWdTimer = null;
                        installMovieWatchdog();
                    }
                }, 500);
            }
            return;
        }
        if (window.__oppaiMovieWdRunning) { return; }
        window.__oppaiMovieWdRunning = true;

        var stoppedSince = 0;
        setInterval(function () {
            try {
                var g = window.__oppaiGuideRef;
                if (!g || !g._videoPlayer) { stoppedSince = 0; return; }

                var playing = true;
                try { playing = g._videoPlayer.isPlaying(); } catch (e) { playing = false; }

                if (playing) { stoppedSince = 0; return; }

                if (!stoppedSince) { stoppedSince = Date.now(); return; }
                if (Date.now() - stoppedSince < 2000) { return; }

                emit("MOVIE-WD 视频已停播，兜底触发 COMPLETED");
                stoppedSince = 0;
                try {
                    g.onPlayerMovieCallBack(g, 3);
                } catch (e) {
                    emit("MOVIE-WD 兜底回调出错 " + e);
                }
            } catch (e) { }
        }, 500);

        // 记录当前引导层实例（ctor 时挂上）
        var origCtor = LaunchGuideLayer.prototype.ctor;
        if (typeof origCtor === "function") {
            LaunchGuideLayer.prototype.ctor = function () {
                var r = origCtor.apply(this, arguments);
                window.__oppaiGuideRef = this;
                return r;
            };
        }
        emit("MOVIE-WD 引导层视频看门狗已装");
    })();
'''

if "MOVIE-WD" in s:
    print("已经有了")
else:
    anchor = "    var tries = 0;"
    i = s.find(anchor)
    if i < 0:
        raise SystemExit("找不到锚点")
    s = s[:i] + BLOCK.lstrip("\n") + "\n" + s[i:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入视频看门狗")
