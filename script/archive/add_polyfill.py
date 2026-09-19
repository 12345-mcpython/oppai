"""往 hook.js 里加 ccui.helper.seekNodeByName / seekNodeByTag 的 polyfill。

为什么不在原生层注册：
  ccui.helper 是 jsb_boot.js 里用 JS 创建的对象（实际上是个 function），
  原生 register callback 在 sc->start() 之前执行，注册进去的会被 JS 覆盖掉。
  hook.js 是在 jsb_boot.js 之后加载的，所以在这里补最稳。

游戏 UpdateScene._init() 就是卡在 ccui.helper.seekNodeByName 上
（原版 .so 有 js_cocos2dx_ui_Helper_seekNodeByName，vanilla 3.6 没有）。
"""

import io

P = r"E:\code\zcsmw\server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

POLY = r'''
    // ------------------------------------------------------------------
    // ccui.helper.seekNodeByName / seekNodeByTag polyfill
    //
    // 原版 libcocos2djs.so 里有 js_cocos2dx_ui_Helper_seekNodeByName，
    // 但 cocos2d-js v3.6 的 ui::Helper 只有 seekWidgetByName/ByTag（收 Widget*），
    // 收 Node* 的版本是 cocos2d-x 3.7 才加的。
    // 少了它，游戏的 UpdateScene._init() 会直接抛：
    //     TypeError: seekNodeByName is not a function
    // 热更新界面就建不出来，一直黑屏。
    //
    // 原生层注册不了 —— ccui.helper 是 jsb_boot.js 用 JS 建的，
    // 原生 callback 跑在它之前会被覆盖。所以在这里补。
    // ------------------------------------------------------------------
    (function () {
        var H = (typeof ccui !== "undefined" && ccui.helper) ? ccui.helper : null;
        if (!H) { emit("POLYFILL ccui.helper 不存在，跳过"); return; }

        if (typeof H.seekNodeByName !== "function") {
            H.seekNodeByName = function (root, name) {
                if (!root) { return null; }
                try {
                    if (root.getName && root.getName() === name) { return root; }
                } catch (e) { }
                var kids = null;
                try { kids = root.getChildren ? root.getChildren() : null; } catch (e) { }
                if (!kids) { return null; }
                for (var i = 0; i < kids.length; i++) {
                    var hit = H.seekNodeByName(kids[i], name);
                    if (hit) { return hit; }
                }
                return null;
            };
            emit("POLYFILL ccui.helper.seekNodeByName 已补");
        }

        if (typeof H.seekNodeByTag !== "function") {
            H.seekNodeByTag = function (root, tag) {
                if (!root) { return null; }
                try {
                    if (root.getTag && root.getTag() === tag) { return root; }
                } catch (e) { }
                var kids = null;
                try { kids = root.getChildren ? root.getChildren() : null; } catch (e) { }
                if (!kids) { return null; }
                for (var i = 0; i < kids.length; i++) {
                    var hit = H.seekNodeByTag(kids[i], tag);
                    if (hit) { return hit; }
                }
                return null;
            };
            emit("POLYFILL ccui.helper.seekNodeByTag 已补");
        }
    })();
'''

if "seekNodeByName" in s:
    print("hook.js 里已经有了")
else:
    # 插到 hook 启动标记之后 —— 找一个稳定的锚点
    anchor = None
    for cand in ("emit(\"HOOK LOADED", "HOOK LOADED"):
        i = s.find(cand)
        if i >= 0:
            anchor = i
            break
    if anchor is None:
        raise SystemExit("找不到锚点")

    # 找到该行末尾
    j = s.find("\n", anchor)
    s = s[:j + 1] + POLY + s[j + 1:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已把 polyfill 插到 HOOK LOADED 之后")
