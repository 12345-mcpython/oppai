"""给 hook.js 加 ActionTimeline 回调的诊断 + 兜底。

背景：游戏的 UpdateScene 启动链是
    ctor() -> _logo() -> tl.setLastFrameCallFunc(cb) -> cb 里调 _init()
实测 Layer 节点的 action 永远不结束（actions 恒为 1），
setLastFrameCallFunc 的回调不触发，游戏就永远停在 logo 那一屏（黑屏）。

这里：
  1) 记录 setLastFrameCallFunc 到底有没有被调用（区分"没注册上"还是"没触发"）
  2) 包一层 fired 标志，防止兜底和引擎各触发一次
  3) 装一个定时器兜底：duration/60 秒 + 800ms 还没触发就自己调一次
     —— 这样即使引擎行为有差异，游戏流程也能继续往下走
"""

import io

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

BLOCK = r'''
    // ------------------------------------------------------------------
    // ActionTimeline 回调诊断 + 兜底
    //
    // 游戏 UpdateScene 的启动链：
    //     ctor() -> _logo() -> tl.setLastFrameCallFunc(cb) -> cb 调 _init()
    // 实测 Layer 节点的 action 恒为 1（永不结束），回调不触发，
    // 游戏就卡在 logo 那一屏（表现为黑屏）。
    //
    // 这里包一层：记录调用 + 加 fired 标志 + 定时器兜底。
    // ------------------------------------------------------------------
    (function () {
        var AT = (typeof ccs !== "undefined") ? ccs.ActionTimeline : null;
        if (!AT || !AT.prototype || typeof AT.prototype.setLastFrameCallFunc !== "function") {
            emit("AT-WRAP 无法包装（找不到 ccs.ActionTimeline）");
            return;
        }
        if (AT.prototype.__oppaiWrapped) { return; }
        AT.prototype.__oppaiWrapped = true;

        var origSet = AT.prototype.setLastFrameCallFunc;
        var origPlay = AT.prototype.play;

        AT.prototype.setLastFrameCallFunc = function (cb) {
            var self = this;
            var dur = 0;
            try { dur = self.getDuration ? self.getDuration() : 0; } catch (e) { }
            emit("AT.setLastFrameCallFunc 已调用 duration=" + dur);

            self.__oppaiLfFired = false;
            var wrapped = function () {
                if (self.__oppaiLfFired) { return; }
                self.__oppaiLfFired = true;
                emit("AT.lastFrame 触发（引擎）frame=" + (self.getCurrentFrame ? self.getCurrentFrame() : "?"));
                return cb.apply(self, arguments);
            };
            self.__oppaiLfOnEngine = true;

            if (dur > 0) {
                var ms = Math.round((dur / 60) * 1000) + 800;
                setTimeout(function () {
                    if (!self.__oppaiLfFired) {
                        emit("AT.lastFrame 兜底触发（引擎没触发）after " + ms + "ms");
                        wrapped();
                    }
                }, ms);
            }
            return origSet.call(self, wrapped);
        };

        // play 也记一笔，方便看时序
        AT.prototype.play = function (name, loop) {
            var r = null;
            try { r = origPlay.apply(this, arguments); } catch (e) { emit("AT.play ERR " + e); throw e; }
            emit("AT.play(" + name + "," + loop + ") endFrame=" + (this.getEndFrame ? this.getEndFrame() : "?"));
            return r;
        };

        emit("AT-WRAP ActionTimeline 已包装");
    })();
'''

if "AT-WRAP" in s:
    print("已经有了")
else:
    anchor = "    var tries = 0;"
    i = s.find(anchor)
    if i < 0:
        raise SystemExit("找不到插入锚点")
    s = s[:i] + BLOCK.lstrip("\n") + "\n" + s[i:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已插入 ActionTimeline 包装")
