"""关键修复：让 setLastFrameCallFunc 的回调收到动画名。

游戏 UpdateScene._logo 的回调是：
    tl.setLastFrameCallFunc(function (eventName) {
        if (eventName === "default") { this._init(); }
    });

但 cocos2d-js v3.6 的自动绑定里是 func->invoke(0, nullptr, &rval) —— 不传参数，
于是 eventName 永远是 undefined，_init() 永远不调用，
热更新界面建不出来，游戏就卡在 logo（黑屏）。

原版引擎显然是带参数调用的（传动画名）。这里在 JS 层补齐：
  - 包 play() 记录动画名
  - 包 setLastFrameCallFunc()，触发时把动画名当第一个参数传进去
"""

import io

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

# 1) 回调触发处：改成传动画名
OLD_CB = '''                try {
                    var r = cb.apply(self, arguments);
                    var scA = null;
                    try { scA = cc.director.getRunningScene(); } catch (e) { }
                    emit("AT.lastFrame cb 正常返回 sceneAfter=" + (scA ? scA.getChildrenCount() : "-"));
                    return r;
                } catch (e) {
                    emit("AT.lastFrame cb 抛异常!! " + e);
                    throw e;
                }'''

NEW_CB = '''                // 关键：原版引擎会把动画名当第一个参数传给回调
                // 游戏代码写的是 function (eventName) { if (eventName === "default") this._init(); }
                // 而 v3.6 的绑定是 invoke(0, ...) 不传参数，导致 _init() 永不调用。
                var animName = self.__oppaiAnimName || "default";
                emit("AT.lastFrame 传参 anim=" + animName);
                try {
                    var r = cb.call(self, animName);
                    var scA = null;
                    try { scA = cc.director.getRunningScene(); } catch (e) { }
                    emit("AT.lastFrame cb 正常返回 sceneAfter=" + (scA ? scA.getChildrenCount() : "-"));
                    return r;
                } catch (e) {
                    emit("AT.lastFrame cb 抛异常!! " + e);
                    throw e;
                }'''

# 2) play() 记录动画名
OLD_PLAY = '''        AT.prototype.play = function (name, loop) {
            var r = null;
            try { r = origPlay.apply(this, arguments); } catch (e) { emit("AT.play ERR " + e); throw e; }
            emit("AT.play(" + name + "," + loop + ") endFrame=" + (this.getEndFrame ? this.getEndFrame() : "?"));
            return r;
        };'''

NEW_PLAY = '''        AT.prototype.play = function (name, loop) {
            // 记下动画名，setLastFrameCallFunc 的回调要用
            this.__oppaiAnimName = name;
            var r = null;
            try { r = origPlay.apply(this, arguments); } catch (e) { emit("AT.play ERR " + e); throw e; }
            emit("AT.play(" + name + "," + loop + ") endFrame=" + (this.getEndFrame ? this.getEndFrame() : "?"));
            return r;
        };'''

n = 0
if OLD_CB in s:
    s = s.replace(OLD_CB, NEW_CB, 1); n += 1
if OLD_PLAY in s:
    s = s.replace(OLD_PLAY, NEW_PLAY, 1); n += 1

if n == 0:
    print("!! 没找到目标片段")
else:
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已修改 %d 处" % n)
