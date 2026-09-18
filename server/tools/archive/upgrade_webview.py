"""升级 ccui.WebView polyfill：真的把公告内容取回来显示。

原来的 polyfill 只是占位（能建、能 loadURL、回调一下），公告框是空白的。
现在改成：
  1) loadURL 之后用 XMLHttpRequest 把页面抓回来
  2) 粗略剥掉 HTML 标签，拿到纯文本
  3) 用 ccui.Text 渲染在 WebView 的位置上（自动换行、可滚动区域样式）

这样公告内容就能跟着私服走 —— 服务端改 NOTICE_HTML 或 var/notice.html，
客户端公告框里就会显示对应内容。

（真·原生 WebView 绑定需要动整套 JSB 类注册机制（~150 行），
  而公告内容本来就是我们自己控制的，用文本渲染更简单也更可控。）
"""

import io
import re

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

START = "    // ------------------------------------------------------------------\n    // ccui.WebView polyfill"
i = s.find(START)
if i < 0:
    raise SystemExit("找不到原 polyfill")
# 找到该 IIFE 的结尾
j = s.find("    })();\n", i)
if j < 0:
    raise SystemExit("找不到 polyfill 结尾")
j += len("    })();\n")

NEW = r'''    // ------------------------------------------------------------------
    // ccui.WebView polyfill（会真的把内容显示出来）
    //
    // 原版 .so 有 experimental_webView_WebView 绑定，v3.6 仓库里没有。
    // 游戏公告层 noticelayer.js 用 new ccui.WebView() 加载 NOTICE_URL。
    //
    // NOTICE_URL 已经被重定向到私服（cdn.shuangmawei.net -> 10.110.29.230:18080），
    // 所以这里：抓页面 -> 剥 HTML 标签 -> 用 ccui.Text 渲染出来。
    // 服务端改公告，客户端就能看到。
    // ------------------------------------------------------------------
    (function () {
        if (typeof ccui === "undefined") { return; }
        if (typeof ccui.WebView === "function" && ccui.WebView.__oppaiReal) {
            emit("WEBVIEW 已经装过真实现");
            return;
        }

        // ---- HTML -> 纯文本（够用就行）----
        function htmlToText(html) {
            var t = String(html || "");
            t = t.replace(/<script[\s\S]*?<\/script>/gi, "");
            t = t.replace(/<style[\s\S]*?<\/style>/gi, "");
            t = t.replace(/<br\s*\/?>/gi, "\n");
            t = t.replace(/<\/(p|div|h[1-6]|li|tr)>/gi, "\n");
            t = t.replace(/<li[^>]*>/gi, "  · ");
            t = t.replace(/<[^>]+>/g, "");
            t = t.replace(/&nbsp;/g, " ").replace(/&lt;/g, "<")
                 .replace(/&gt;/g, ">").replace(/&amp;/g, "&").replace(/&quot;/g, '"');
            t = t.replace(/\r/g, "");
            t = t.replace(/\n{3,}/g, "\n\n");
            return t.replace(/^[ \t]+|[ \t]+$/gm, "").replace(/^\n+|\n+$/g, "");
        }

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
                this._label = null;
            },

            _render: function (text) {
                if (this._label) {
                    try { this._label.removeFromParent(); } catch (e) { }
                    this._label = null;
                }
                var size = null;
                try { size = this.getContentSize(); } catch (e) { }
                var w = (size && size.width > 40) ? (size.width - 40) : 640;
                var h = (size && size.height > 40) ? (size.height - 40) : 400;

                var label = new ccui.Text();
                try {
                    label.setString(text);
                    label.setFontSize(20);
                    label.setTextColor(cc.color(91, 74, 47));
                    label.ignoreContentAdaptWithSize(false);
                    label.setTextAreaSize(cc.size(w, h));
                    label.setTextHorizontalAlignment(cc.TEXT_ALIGNMENT_LEFT);
                    label.setTextVerticalAlignment(cc.VERTICAL_TEXT_ALIGNMENT_TOP);
                } catch (e) {
                    emit("WEBVIEW 建 label 出错 " + e);
                }
                var sz = null;
                try { sz = this.getContentSize(); } catch (e) { }
                if (sz) { label.setPosition(cc.p(sz.width / 2, sz.height / 2)); }
                label.setLocalZOrder(1);
                this.addChild(label);
                this._label = label;
            },

            loadURL: function (url) {
                this._url = url || "";
                emit("WEBVIEW.loadURL " + this._url);
                var self = this;
                try {
                    var xhr = cc.loader.getXMLHttpRequest();
                    xhr.open("GET", this._url, true);
                    xhr.onreadystatechange = function () {
                        if (xhr.readyState !== 4) { return; }
                        if (xhr.status >= 200 && xhr.status < 300) {
                            var text = htmlToText(xhr.responseText);
                            emit("WEBVIEW 内容 " + text.length + " 字");
                            self._render(text || "（公告为空）");
                            try { if (self._cbFinish) { self._cbFinish(self, self._url); } } catch (e) { }
                        } else {
                            emit("WEBVIEW 加载失败 status=" + xhr.status);
                            self._render("公告加载失败（HTTP " + xhr.status + "）");
                            try { if (self._cbFail) { self._cbFail(self, self._url); } } catch (e) { }
                        }
                    };
                    xhr.send();
                } catch (e) {
                    emit("WEBVIEW xhr 出错 " + e);
                    self._render("公告加载失败");
                }
            },

            loadFile: function (p) { this.loadURL(p); },
            loadHTMLString: function (html, base) {
                this._render(htmlToText(html));
                var self = this;
                setTimeout(function () { try { if (self._cbFinish) { self._cbFinish(self, base || ""); } } catch (e) { } }, 30);
            },
            loadData: function () { },
            reload: function () { if (this._url) { this.loadURL(this._url); } },
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
        WV.__oppaiReal = true;

        ccui.WebView = WV;
        emit("WEBVIEW ccui.WebView 已装（会渲染公告内容）");
    })();
'''

s = s[:i] + NEW + s[j:]
io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("已替换 WebView polyfill")
