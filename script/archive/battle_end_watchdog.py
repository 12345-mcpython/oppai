"""战斗结束推进看门狗。

复现出来的卡点（全部来自反汇编 + 运行时探测）：

BattleScene ctor:
    tl.setFrameEventCallFunc(op.onSoundFrameEvent);   // 帧事件只处理音效
    tl.setLastFrameCallFunc(this._next.bind(this));   // 最后一帧 -> _next

BattleScene._show1(arg0):                 // 战斗结束、EndLayer 关掉后走这里
    op.touchEnabled = false;
    this._nextCb = arg0;                  // 挂上"继续"回调
    this._battleBeganUi.visible = true;
    this._battleBeganUi.playAnimation("began3");

BattleScene._next(arg0):
    this._nextCb = arg0;
    if (this._index === 0) playAnimation("loop" + rand, true);
    else                   playAnimation("began" + rand);

battleBeganUi.playAnimation(eventName, loop):
    if (/loop\\d/.test(eventName)) { if (this._nextCb) { this._nextCb(); this._nextCb = null; } }
    else if (/began\\d/.test(eventName)) { ...进度条复位... }
    player.play(eventName, loop);

也就是说：**推进靠 began3 播放期间触发的 loop\\d 帧事件** -> _nextCb()。

实测（patch.js 的帧事件日志）began3 期间只收到
    AT.frameEvent sound_battlebegansound frame=951
没有 loop\\d —— 所以 _nextCb 永远挂着，_endType 一直是 undefined，战斗收不了尾。

这里做兜底：战斗已到最后一波（_index >= _len）且 _nextCb 挂着超过 20 秒时，
替那个缺失的帧事件调用一次 _nextCb()。

20 秒的依据：began3 是 978 帧、_frameInternal=1/60，正常约 16 秒播完；
即使因为 ARM 翻译层掉帧也不会超过 20 秒。
"""

import io

P = r"E:\code\zcsmw\server\client\patch.js"
s = io.open(P, encoding="utf-8").read()

BLOCK = r'''
    // ------------------------------------------------------------------
    // 战斗结束推进看门狗
    //
    // 战斗收尾链（反汇编 battlescene.jsc 得到）：
    //     _show1(next) -> _nextCb = next; playAnimation("began3")
    //     began3 播放期间应触发 loop\d 帧事件 -> playAnimation("loopN") -> _nextCb()
    //
    // 实测 began3 期间只有 sound_battlebegansound(frame=951)，没有 loop\d，
    // 于是 _nextCb 永远挂着、_endType 保持 undefined，战斗收不了尾。
    //
    // 兜底：到最后一波（_index >= _len）且 _nextCb 挂了超过 20 秒，
    // 就替那个缺失的帧事件调一次 _nextCb()。
    // 20s 依据：began3 共 978 帧、_frameInternal = 1/60，正常约 16 秒。
    // ------------------------------------------------------------------
    (function installBattleEndWatchdog() {
        if (typeof BattleScene === "undefined" || !BattleScene.prototype) {
            if (!window.__oppaiBEwdTimer) {
                window.__oppaiBEwdTimer = setInterval(function () {
                    if (typeof BattleScene !== "undefined" && BattleScene.prototype) {
                        clearInterval(window.__oppaiBEwdTimer);
                        window.__oppaiBEwdTimer = null;
                        installBattleEndWatchdog();
                    }
                }, 1000);
            }
            return;
        }
        if (window.__oppaiBEwdRunning) { return; }
        window.__oppaiBEwdRunning = true;

        var pendingSince = 0;
        setInterval(function () {
            try {
                var s = cc.director.getRunningScene();
                if (!(s instanceof BattleScene)) { pendingSince = 0; return; }

                // 只有到了最后一波才兜底（前面几波靠 ClearLayer 正常推进）
                if (!(typeof s._index === "number" && typeof s._len === "number" && s._index >= s._len)) {
                    pendingSince = 0;
                    return;
                }
                if (typeof s._nextCb !== "function") { pendingSince = 0; return; }

                if (!pendingSince) { pendingSince = Date.now(); return; }
                if (Date.now() - pendingSince < 20000) { return; }

                emit("BATTLE-WD 战斗收尾卡住，兜底触发 _nextCb（_index=" + s._index + "/" + s._len + "）");
                pendingSince = 0;
                try {
                    s._nextCb();
                    s._nextCb = null;
                } catch (e) {
                    emit("BATTLE-WD 兜底失败 " + e);
                }
            } catch (e) { }
        }, 1000);

        emit("BATTLE-WD 战斗结束推进看门狗已装");
    })();
'''

if "BATTLE-WD" in s:
    print("已经有了")
else:
    marker = "\n})();\n"
    i = s.rfind(marker)
    if i < 0:
        raise SystemExit("找不到 patch.js 结尾")
    s = s[:i] + "\n" + BLOCK + s[i:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入战斗结束看门狗")
