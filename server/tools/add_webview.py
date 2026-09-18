"""给 hook.js 加 ccui.WebView polyfill。

背景：原版 libcocos2djs.so 里有 cocos2dx_experimental_webView_WebView（约 30 个函数），
但 cocos2d-js v3.6 仓库里**没有这个绑定**（游戏当年自己加的）。
缺了它，公告层报：
    TypeError: ccui.WebView is not a constructor
  @ assets/src/ui/menu/noticelayer.js:35

游戏用到的接口（从 noticelayer.jsc 的 atom 表反汇编出来的）：
    new ccui.WebView()
    loadURL / reload / stopLoading
    setJavascriptInterfaceScheme / setScalesPageToFit
    setOnDidFinishLoading / setOnDidFailLoading / setOnJSCallback / setOnShouldStartLoading
    canGoBack / canGoForward / goBack / goForward / evaluateJS
    （其余 setContentSize / setAnchorPoint / setPosition / setLocalZOrder / addChild 继承自 Widget）

注意：公告内容来自远端 URL（服务器早停了），所以这里做成"能建、能加载、
会回调 onDidFinishLoading"的占位实现 —— 公告框会显示为空，
但不影响主流程。真正的原生 WebView 绑定可以作为后续优化。
"""

import io

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

BLOCK = r'''
    // ------------------------------------------------------------------
    // ccui.WebView polyfill
    //
    // 原版 .so 有 experimental_webView_WebView 绑定，v3.6 仓库里没有。
    // 游戏公告层 noticelayer.js 用 new ccui.WebView()，缺了就抛
    // "ccui.WebView is not a constructor"。
    //
    // 公告内容来自远端 URL（服务器已停），所以做占位实现：
    // 能构建、能 loadURL、会异步回调 onDidFinishLoading，让流程能继续。
    // ------------------------------------------------------------------
    (function () {
        if (typeof ccui === "undefined") { return; }
        if (typeof ccui.WebView === "function") { emit("WEBVIEW 已存在，跳过"); return; }

        var WV = ccui.Widget.extend({
            ctor: function () {
                this._super();
                this._url = "";
                this._scheme = "";
                this._scales = false;
                this._cbFinish = null;
                this._cbFail = null;
                this._cbJS = null;
                this._cbShouldStart = null;
            },
            loadURL: function (url) {
                this._url = url || "";
                emit("WEBVIEW.loadURL " + this._url);
                var self = this;
                // 让公告层的 onDidFinishLoading 能收到，流程不至于卡住
                setTimeout(function () {
                    try {
                        if (self._cbFinish) { self._cbFinish(self, self._url); }
                    } catch (e) { emit("WEBVIEW cbFinish ERR " + e); }
                }, 50);
            },
            loadFile: function (p) { this.loadURL(p); },
            loadHTMLString: function (html, base) { this._url = base || ""; },
            loadData: function () { },
            reload: function () {
                if (this._url) { this.loadURL(this._url); }
            },
            stopLoading: function () { },
            setJavascriptInterfaceScheme: function (s) { this._scheme = s; },
            setScalesPageToFit: function (b) { this._scales = !!b; },
            setOnDidFinishLoading: function (cb) { this._cbFinish = cb; },
            setOnDidFailLoading: function (cb) { this._cbFail = cb; },
            setOnJSCallback: function (cb) { this._cbJS = cb; },
            setOnShouldStartLoading: function (cb) { this._cbShouldStart = cb; },
            canGoBack: function () { return false; },
            canGoForward: function () { return false; },
            goBack: function () { },
            goForward: function () { },
            evaluateJS: function (js) { },
            getURL: function () { return this._url; }
        });

        ccui.WebView = WV;
        emit("WEBVIEW ccui.WebView polyfill 已装");
    })();
'''

if "WEBVIEW" in s:
    print("已经有了")
else:
    anchor = "    var tries = 0;"
    i = s.find(anchor)
    if i < 0:
        raise SystemExit("找不到锚点")
    s = s[:i] + BLOCK.lstrip("\n") + "\n" + s[i:]
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已加入 ccui.WebView polyfill")
