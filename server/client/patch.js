// ===========================================================================
// patch.js —— 《战场双马尾》私服客户端适配层（必需）
//
// 这里放的是「少了游戏就跑不对」的补丁，根因都是引擎与游戏的约定不一致：
//
//   1. ccui.helper.seekNodeByName / seekNodeByTag
//      原版 libcocos2djs.so 有这两个绑定，vanilla cocos2d-js v3.6 没有。
//      缺了 UpdateScene._init() 抛 "seekNodeByName is not a function"，
//      热更新界面建不出来 -> 一直黑屏。
//
//   2. ActionTimeline 回调补动画名
//      v3.6 的自动绑定是 func->invoke(0, ...)，而游戏回调写的是
//      function (eventName) { if (eventName === "default") this._init(); }
//      少了这个参数，UpdateScene 永远走不到 _init()（表现为黑屏卡死）。
//
//   3. ccui.WebView
//      原版有 experimental_webView_WebView 绑定，v3.6 仓库没有。
//      公告层靠它显示私服公告（内容由服务端 var/notice.html 提供）。
//
//   4. ccui.VideoPlayer 让路
//      引擎侧已提供原生绑定（register_all_oppai_videoplayer），
//      这里只在原生不存在时才装占位实现。
//
//   5. LaunchGuideLayer 战斗开场引导
//      视频播完后回调会因为 _videoPlayer 已为 null 而抛异常；而且 Android 侧
//      残留的 VideoView 会一直盖在画面上（表现为画面停在视频最后一帧）。
//
// 探针/诊断部分在 probe.js —— release 可以不打包那个文件。
// 两个文件互相独立，这个文件不依赖 probe.js 的任何东西。
// ===========================================================================

(function () {
    "use strict";

    var TAG = "OPPAIPATCH";

    // 自带极简日志（不依赖 probe.js 的 emit）
    function emit(line) {
        try { console.log(TAG + "|" + line); } catch (e) { }
    }
    function vlog(line) {
        if (typeof __OPPAI_VERBOSE__ !== "undefined" && __OPPAI_VERBOSE__) {
            emit(line);
        }
    }

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
            vlog("AT.setLastFrameCallFunc 已调用 duration=" + dur);

            self.__oppaiLfFired = false;
            var wrapped = function (engineName) {
                if (self.__oppaiLfFired) { return; }
                self.__oppaiLfFired = true;
                var scB = null;
                try { scB = cc.director.getRunningScene(); } catch (e) { }
                vlog("AT.lastFrame 触发 frame=" + (self.getCurrentFrame ? self.getCurrentFrame() : "?") +
                     " sceneBefore=" + (scB ? scB.getChildrenCount() : "-"));
                // 关键：引擎会把当前动画名当第一个参数传给回调（见 jsb_cocos2dx_studio_auto.cpp
                // 里 oppai 的改动），游戏代码依赖它，例如：
                //     function (eventName) { if (eventName === "default") this._init(); }
                //     function (eventName) { if (/began\d/.test(eventName)) playAnimation("loop"+N, true); }
                // 万一引擎没传（绑定补丁没生效），退回 play 时记下的名字，再退回 "default"。
                var animName = engineName || self.__oppaiAnimName || "default";
                vlog("AT.lastFrame 传参 anim=" + animName);
                try {
                    var r = cb.call(self, animName);
                    var scA = null;
                    try { scA = cc.director.getRunningScene(); } catch (e) { }
                    vlog("AT.lastFrame cb 正常返回 sceneAfter=" + (scA ? scA.getChildrenCount() : "-"));
                    return r;
                } catch (e) {
                    vlog("AT.lastFrame cb 抛异常!! " + e);
                    throw e;
                }
            };
            self.__oppaiLfOnEngine = true;

            if (dur > 0) {
                var ms = Math.round((dur / 60) * 1000) + 800;
                setTimeout(function () {
                    if (!self.__oppaiLfFired) {
                        vlog("AT.lastFrame 兜底触发（引擎没触发）after " + ms + "ms");
                        wrapped();
                    }
                }, ms);
            }
            return origSet.call(self, wrapped);
        };

        // 帧事件（setFrameEventCallFunc）—— 战斗加载/结束的推进靠它
        var origSetFrame = AT.prototype.setFrameEventCallFunc;
        if (typeof origSetFrame === "function") {
            AT.prototype.setFrameEventCallFunc = function (cb) {
                var self = this;
                var wrapped = function (frame) {
                    var ev = "?";
                    try { ev = (frame && frame.getEvent) ? frame.getEvent() : String(frame); } catch (e) { ev = "ERR"; }
                    vlog("AT.frameEvent " + ev + " frame=" + (self.getCurrentFrame ? self.getCurrentFrame() : "?"));
                    return cb.apply(self, arguments);
                };
                vlog("AT.setFrameEventCallFunc 已注册");
                return origSetFrame.call(self, wrapped);
            };
        }

        // play 也记一笔，方便看时序
        AT.prototype.play = function (name, loop) {
            // 记下动画名，setLastFrameCallFunc 的回调要用
            this.__oppaiAnimName = name;
            var r = null;
            try { r = origPlay.apply(this, arguments); } catch (e) { vlog("AT.play ERR " + e); throw e; }
            vlog("AT.play(" + name + "," + loop + ") endFrame=" + (this.getEndFrame ? this.getEndFrame() : "?"));
            return r;
        };

        emit("AT-WRAP ActionTimeline 已包装");
    })();


    // ------------------------------------------------------------------
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
                vlog("WEBVIEW.loadURL " + this._url);
                var self = this;
                try {
                    var xhr = cc.loader.getXMLHttpRequest();
                    xhr.open("GET", this._url, true);
                    xhr.onreadystatechange = function () {
                        if (xhr.readyState !== 4) { return; }
                        if (xhr.status >= 200 && xhr.status < 300) {
                            var text = htmlToText(xhr.responseText);
                            vlog("WEBVIEW 内容 " + text.length + " 字");
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

    // ------------------------------------------------------------------
    // ccui.VideoPlayer polyfill
    //
    // 原版 .so 有 experimental_video_VideoPlayer 绑定，v3.6 仓库里没有。
    // 战斗引导层 launchguidelayer.js:432 用 new ccui.VideoPlayer() 播开场视频，
    // 缺了就抛 "ccui.VideoPlayer is undefined"，引导流程中断。
    //
    // 这里做占位实现：能建、能设文件名、play() 后异步回调 COMPLETED，
    // 让流程继续。（真播视频需要原生绑定。）
    // ------------------------------------------------------------------
    (function () {
        if (typeof ccui === "undefined") { return; }
        // 原生绑定已经注册了就别覆盖（引擎侧 register_all_oppai_videoplayer）
        if (typeof ccui.VideoPlayer === "function") {
            emit("VIDEO ccui.VideoPlayer 已存在（原生绑定），跳过 polyfill");
            return;
        }

        var VP = ccui.Widget.extend({
            ctor: function () {
                this._super();
                this._file = "";
                this._url = "";
                this._playing = false;
                this._fullscreen = false;
                this._keepAspect = true;
                this._listeners = [];
                this._timer = null;
            },
            setFileName: function (f) { this._file = f || ""; },
            getFileName: function () { return this._file; },
            setURL: function (u) { this._url = u || ""; },
            getURL: function () { return this._url; },
            setFullScreenEnabled: function (b) { this._fullscreen = !!b; },
            isFullScreenEnabled: function () { return this._fullscreen; },
            setKeepAspectRatioEnabled: function (b) { this._keepAspect = !!b; },
            isKeepAspectRatioEnabled: function () { return this._keepAspect; },
            addEventListener: function (cb) {
                if (typeof cb === "function") { this._listeners.push(cb); }
            },
            _emit: function (type) {
                for (var i = 0; i < this._listeners.length; i++) {
                    try { this._listeners[i](this, type); }
                    catch (e) { emit("VIDEO 回调出错 " + e); }
                }
            },
            play: function () {
                this._playing = true;
                emit("VIDEO play(占位) " + (this._file || this._url));
                var self = this;
                // ccui.VideoPlayer 的事件常量：0=COMPLETED 1=PAUSED 2=STOPPED 3=PLAYING
                setTimeout(function () {
                    self._playing = false;
                    self._emit(0);
                }, 300);
            },
            pause: function () { this._emit(1); },
            resume: function () { this._playing = true; },
            stop: function () { this._playing = false; this._emit(2); },
            seekTo: function (s) { },
            isPlaying: function () { return this._playing; },
            onPlayEvent: function (type) { this._emit(type); },
            currentTime: function () { return 0; },
            getDuration: function () { return 0; }
        });
        VP.__oppaiFake = true;

        ccui.VideoPlayer = VP;
        emit("VIDEO ccui.VideoPlayer polyfill 已装（占位，不真正播放）");
    })();


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
    (function installGuard() {
        // LaunchGuideLayer 是懒加载的，hook 执行时可能还没有 —— 轮询等待
        if (typeof LaunchGuideLayer === "undefined" || !LaunchGuideLayer.prototype) {
            if (!window.__oppaiLglGuardTimer) {
                window.__oppaiLglGuardTimer = setInterval(function () {
                    if (typeof LaunchGuideLayer !== "undefined" && LaunchGuideLayer.prototype) {
                        clearInterval(window.__oppaiLglGuardTimer);
                        window.__oppaiLglGuardTimer = null;
                        installGuard();
                    }
                }, 500);
            }
            return;
        }
        if (window.__oppaiLglGuardTimer) {
            clearInterval(window.__oppaiLglGuardTimer);
            window.__oppaiLglGuardTimer = null;
        }
        if (LaunchGuideLayer.prototype.__oppaiGuard) { return; }
        LaunchGuideLayer.prototype.__oppaiGuard = true;

        var orig = LaunchGuideLayer.prototype.onPlayerMovieCallBack;
        if (typeof orig !== "function") {
            emit("LGL-GUARD onPlayerMovieCallBack 不是函数，跳过");
            return;
        }

        LaunchGuideLayer.prototype.onPlayerMovieCallBack = function (sender, eventType) {
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
        emit("LGL-GUARD onPlayerMovieCallBack 已加幂等守卫 + 视频层清理");
    })();

    // ------------------------------------------------------------------
    // 引导层视频看门狗
    //
    // 视频能满屏正常播放，但播完后引擎的 COMPLETED(3) 事件没送到 JS，
    // 于是 onPlayerMovieCallBack 永不触发、游戏卡在视频最后一帧。
    //
    // 兜底：轮询 _videoPlayer.isPlaying()，停播超过 2 秒就替引擎调一次
    // onPlayerMovieCallBack(this, 3)。重复调用由幂等守卫挡掉。

    // ------------------------------------------------------------------
    (function installMovieWatchdog() {
        if (typeof LaunchGuideLayer === "undefined" || !LaunchGuideLayer.prototype) {
            if (!window.__oppaiMovieWdTimer) {
                window.__oppaiMovieWdTimer = setInterval(function () {
                    if (typeof LaunchGuideLayer !== "undefined" && LaunchGuideLayer.prototype) {
                        clearInterval(window.__oppaiMovieWdTimer);
                        window.__oppaiMovieWdTimer = null;
                        installMovieWatchdog();
                    }
                }, 500);
            }
            return;
        }
        if (window.__oppaiMovieWdRunning) { return; }
        window.__oppaiMovieWdRunning = true;

        var stoppedSince = 0;
        setInterval(function () {
            try {
                var g = window.__oppaiGuideRef;
                if (!g || !g._videoPlayer) { stoppedSince = 0; return; }

                var playing = true;
                try { playing = g._videoPlayer.isPlaying(); } catch (e) { playing = false; }

                if (playing) { stoppedSince = 0; return; }

                if (!stoppedSince) { stoppedSince = Date.now(); return; }
                if (Date.now() - stoppedSince < 2000) { return; }

                emit("MOVIE-WD 视频已停播，兜底触发 COMPLETED");
                stoppedSince = 0;
                try {
                    g.onPlayerMovieCallBack(g, 3);
                } catch (e) {
                    emit("MOVIE-WD 兜底回调出错 " + e);
                }
            } catch (e) { }
        }, 500);

        // 记录当前引导层实例（ctor 时挂上）
        var origCtor = LaunchGuideLayer.prototype.ctor;
        if (typeof origCtor === "function") {
            LaunchGuideLayer.prototype.ctor = function () {
                var r = origCtor.apply(this, arguments);
                window.__oppaiGuideRef = this;
                return r;
            };
        }
        emit("MOVIE-WD 引导层视频看门狗已装");
    })();

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

})();
