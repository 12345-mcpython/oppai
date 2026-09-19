"""修「视频播完不回到战斗」：确保视频层被真正撤掉。

实测症状（dumpsys activity 的视图树）：
    org.cocos2dx.lib.Cocos2dxGLSurfaceView  0,0-1600,1000
    org.cocos2dx.lib.Cocos2dxVideoView{6621c39  VFE......  0,49-1600,950}  <- 仍 VISIBLE
    ... 另外 11 个 VideoView 都是 0,0-0,0
画面是一片青色 = 那个还可见的 VideoView 的最后一帧盖在游戏上。

而 JS 侧 LaunchGuideLayer._videoPlayer 已经是 null，没法再 removeFromParent()，
所以视频层永远撤不掉 —— 游戏其实在后面跑（手动 _next() 能推进），只是被盖住了。

关键：我的 addEventListener 绑定会把原生播放器对象当第一个参数传给回调（sender），
所以即使 this._videoPlayer 已经丢了，也能通过 sender 把视频停掉并隐藏。

在守卫里加上这一步：
  - eventType === 3 (COMPLETED) 时，先 sender.stop() + sender.setVisible(false)
  - 然后走原来的逻辑
另外再加一个兜底：定期扫描场景，发现还有可见且不在使用的 VideoPlayer 就隐藏掉。
"""

import io

P = r"E:\code\zcsmw\server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

OLD = '''        LaunchGuideLayer.prototype.onPlayerMovieCallBack = function (sender, eventType) {
            // 3 = COMPLETED
            if (eventType === 3 && this._videoPlayer == null) {
                emit("LGL-GUARD 忽略重复的 COMPLETED（_videoPlayer 已为 null）");
                return;
            }
            return orig.apply(this, arguments);
        };
        emit("LGL-GUARD onPlayerMovieCallBack 已加幂等守卫");'''

NEW = '''        LaunchGuideLayer.prototype.onPlayerMovieCallBack = function (sender, eventType) {
            if (eventType === 3) {
                // 先把视频层撤掉 —— sender 就是那个原生 VideoPlayer。
                // 这一步很关键：_videoPlayer 可能已经丢了，但 Android 侧的
                // VideoView 还盖在界面上（实测它一直 0,49-1600,950 可见），
                // 不主动停掉的话画面永远停在视频最后一帧。
                try {
                    if (sender && sender.stop) { sender.stop(); }
                    if (sender && sender.setVisible) { sender.setVisible(false); }
                    emit("LGL-GUARD 已停掉并隐藏视频层");
                } catch (e) {
                    emit("LGL-GUARD 隐藏视频层失败 " + e);
                }
            }
            // 3 = COMPLETED
            if (eventType === 3 && this._videoPlayer == null) {
                emit("LGL-GUARD 忽略重复的 COMPLETED（_videoPlayer 已为 null）");
                return;
            }
            return orig.apply(this, arguments);
        };

        // 兜底：定期扫描场景，把还可见的 VideoPlayer 隐藏掉
        if (!window.__oppaiVideoSweepTimer) {
            window.__oppaiVideoSweepTimer = setInterval(function () {
                try {
                    var scene = cc.director.getRunningScene();
                    if (!scene) { return; }
                    var found = [];
                    (function walk(n, d) {
                        if (!n || d > 8) { return; }
                        if (n instanceof ccui.VideoPlayer) { found.push(n); }
                        var c = n.getChildrenCount ? n.getChildrenCount() : 0;
                        for (var i = 0; i < c; i++) { walk(n.getChildren()[i], d + 1); }
                    })(scene, 0);
                    for (var i = 0; i < found.length; i++) {
                        var vp = found[i];
                        var playing = false;
                        try { playing = vp.isPlaying(); } catch (e) { }
                        if (!playing) {
                            emit("VIDEO-SWEEP 隐藏残留视频层");
                            try { vp.stop(); } catch (e) { }
                            try { vp.setVisible(false); } catch (e) { }
                        }
                    }
                } catch (e) { }
            }, 1500);
        }
        emit("LGL-GUARD onPlayerMovieCallBack 已加幂等守卫 + 视频层清理");'''

if "VIDEO-SWEEP" in s:
    print("已经有了")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入视频层清理")
else:
    print("!! 没找到守卫片段")
