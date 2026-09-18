"""给 ActionTimeline 的 setFrameEventCallFunc 加日志。

战斗结束/加载卡住的排查线索：
  BattleScene 的 _nextCb 一直挂着没触发。
  看 battlescene.jsc 里的回调：
      battleBeganUi.playAnimation = function (eventName) {
          if (eventName.match(/loop\\d/)) { if (this._nextCb) { this._nextCb(); this._nextCb = null; } }
          else if (eventName.match(/began\\d/)) { ... }
      };
  这是 setFrameEventCallFunc 的帧事件回调 —— 帧事件不送达就会一直等。

所以这里把帧事件也打出来，同时记录 setFrameEventCallFunc 的注册。
"""

import io

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

OLD = '''        // play 也记一笔，方便看时序
        AT.prototype.play = function (name, loop) {'''

NEW = '''        // 帧事件（setFrameEventCallFunc）—— 战斗加载/结束的推进靠它
        var origSetFrame = AT.prototype.setFrameEventCallFunc;
        if (typeof origSetFrame === "function") {
            AT.prototype.setFrameEventCallFunc = function (cb) {
                var self = this;
                var wrapped = function (frame) {
                    var ev = "?";
                    try { ev = (frame && frame.getEvent) ? frame.getEvent() : String(frame); } catch (e) { ev = "ERR"; }
                    emit("AT.frameEvent " + ev + " frame=" + (self.getCurrentFrame ? self.getCurrentFrame() : "?"));
                    return cb.apply(self, arguments);
                };
                emit("AT.setFrameEventCallFunc 已注册");
                return origSetFrame.call(self, wrapped);
            };
        }

        // play 也记一笔，方便看时序
        AT.prototype.play = function (name, loop) {'''

if "AT.frameEvent" in s:
    print("已经有了")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入帧事件日志")
else:
    print("!! 没找到锚点")
