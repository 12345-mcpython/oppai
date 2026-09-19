"""包装 UpdateScene 的方法，记录调用顺序和异常。"""

import io

P = r"E:\code\zcsmw\server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

BLOCK = r'''
    // ------------------------------------------------------------------
    // UpdateScene 方法调用跟踪
    //
    // AT.lastFrame 的回调确实触发了、也正常返回了，但场景不变（2->2），
    // 说明它不是 _init 的触发者。这里把 UpdateScene 的方法都包一层，
    // 直接看热更新的启动顺序卡在哪一步。
    // ------------------------------------------------------------------
    (function () {
        if (typeof UpdateScene === "undefined" || !UpdateScene.prototype) {
            emit("US-WRAP 找不到 UpdateScene");
            return;
        }
        if (UpdateScene.prototype.__oppaiWrapped) { return; }
        UpdateScene.prototype.__oppaiWrapped = true;

        var METHODS = ["onEnter", "onExit", "_init", "_loadRemoteConfig", "_loadJs",
                       "_downloadTips", "_unzipTips", "_showTips", "_updateProgression",
                       "_initUpdateView", "_logo"];
        var n = 0;
        for (var i = 0; i < METHODS.length; i++) {
            (function (m) {
                var orig = UpdateScene.prototype[m];
                if (typeof orig !== "function") { return; }
                n++;
                UpdateScene.prototype[m] = function () {
                    emit("US." + m + "() 进入");
                    try {
                        var r = orig.apply(this, arguments);
                        emit("US." + m + "() 返回");
                        return r;
                    } catch (e) {
                        emit("US." + m + "() 抛异常!! " + e);
                        throw e;
                    }
                };
            })(METHODS[i]);
        }
        emit("US-WRAP UpdateScene 已包装 " + n + " 个方法");
    })();
'''

if "US-WRAP" in s:
    print("已经有了")
else:
    anchor = "    var tries = 0;"
    i = s.find(anchor)
    if i < 0:
        raise SystemExit("找不到锚点")
    s = s[:i] + BLOCK.lstrip("\n") + "\n" + s[i:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入 UpdateScene 跟踪")
