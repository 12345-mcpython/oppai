"""给 LaunchGuideLayer.onPlayerMovieCallBack 加重复回调守卫。

现象：视频播完停在最后一帧，不回战斗。
日志：
    TypeError: this._videoPlayer is null
      @ assets/src/battle/guide/launchguidelayer.js:372

反汇编 launchguidelayer.jsc 得到的逻辑：
    onPlayerMovieCallBack(sender, eventType) {
        if (eventType === 3 && cc.sys.isMobile) {
            this.scheduleOnce(function () {
                this._videoPlayer.removeFromParent();   // ← 372 行，这里炸
                this._videoPlayer = null;               // 它自己置的 null
                this.guideStart(true);
                sound.resumeMusic();
            }, 1);
        }
    }

_videoPlayer 是 lambda 自己置 null 的，所以 "is null" 只可能是
**回调被触发了不止一次**：第一次正常走完（移播放器 -> 置 null -> 进下一阶段），
第二次进来时 _videoPlayer 已经是 null，removeFromParent() 直接抛。

抛异常导致 audio 没恢复、流程也没继续，画面就停在视频最后一帧。

这里在 JS 层包一层：
  - 同一个 COMPLETED 只处理一次
  - _videoPlayer 已经是 null 时直接忽略（幂等）
"""

import io

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

BLOCK = r'''
    // ------------------------------------------------------------------
    // LaunchGuideLayer.onPlayerMovieCallBack 重复回调守卫
    //
    // 视频播完会调这个，回调体里第一句是 this._videoPlayer.removeFromParent()，
    // 而 _videoPlayer 恰恰是回调体自己置成 null 的 —— 所以一旦触发两次，
    // 第二次必然 TypeError: this._videoPlayer is null（launchguidelayer.js:372），
    // 异常把后面的 guideStart() / resumeMusic() 全挡掉，画面就停在视频最后一帧。
    //
    // 这里做成幂等：_videoPlayer 已经是 null 就说明这一轮已经处理过，直接忽略。
    // ------------------------------------------------------------------
    (function () {
        if (typeof LaunchGuideLayer === "undefined" || !LaunchGuideLayer.prototype) {
            emit("LGL-GUARD 找不到 LaunchGuideLayer");
            return;
        }
        if (LaunchGuideLayer.prototype.__oppaiGuard) { return; }
        LaunchGuideLayer.prototype.__oppaiGuard = true;

        var orig = LaunchGuideLayer.prototype.onPlayerMovieCallBack;
        if (typeof orig !== "function") {
            emit("LGL-GUARD onPlayerMovieCallBack 不是函数，跳过");
            return;
        }

        LaunchGuideLayer.prototype.onPlayerMovieCallBack = function (sender, eventType) {
            // 3 = COMPLETED
            if (eventType === 3 && this._videoPlayer == null) {
                emit("LGL-GUARD 忽略重复的 COMPLETED（_videoPlayer 已为 null）");
                return;
            }
            return orig.apply(this, arguments);
        };
        emit("LGL-GUARD onPlayerMovieCallBack 已加幂等守卫");
    })();
'''

if "LGL-GUARD" in s:
    print("已经有了")
else:
    anchor = "    var tries = 0;"
    i = s.find(anchor)
    if i < 0:
        raise SystemExit("找不到锚点")
    s = s[:i] + BLOCK.lstrip("\n") + "\n" + s[i:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入重复回调守卫")
