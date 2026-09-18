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
    // ActionTimeline 最后一帧回调：补动画名（引擎未传时的兜底）
    //
    // 游戏多处依赖「回调的第一个参数是动画名」，例如：
    //     UpdateScene._logo : function (eventName) { if (eventName === "default") this._init(); }
    //     BattleScene      : function (eventName) { if (/began\d/.test(eventName)) playAnimation("loop"+N, true); }
    // 而 vanilla cocos2d-js v3.6 的自动绑定是 func->invoke(0, ...)，不传参数。
    //
    // 根治在引擎层（jsb_cocos2dx_studio_auto.cpp 已改成 invoke(1, argv, ...)，
    // argv[0] = ActionTimeline::getCurrentAnimationName()）。这里只做兜底：
    //     引擎传的 engineName  >  play 时记下的 self.__oppaiAnimName  >  "default"
    //
    // 注意：**不能**加"只触发一次"的守卫 —— 游戏复用同一个 ActionTimeline 播
    // 整段序列（began1→loop1→end1→…→end3），每段推进都靠这个回调，挡掉就卡死。
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

            // 注意：这里**不能**加"只触发一次"的守卫。
            // 游戏复用同一个 ActionTimeline 播整段序列（began1→loop1→end1→…→end3），
            // 每一段的推进都依赖最后一帧回调，挡掉第二次就会卡死收尾。
            var wrapped = function (engineName) {
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

            // 原来这里还有一个「dur/60 秒后兜底再触发一次」的 setTimeout，
            // 是配合上面那个「只触发一次」守卫用的。守卫去掉后它会无条件
            // 多触发一次回调，反而把游戏的状态机搞乱（同一段动画的收尾跑了两次），
            // 所以一起删掉。引擎现在会把动画名正确传进来，不需要兜底。
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
    // 新手引导：直接跳过
    //
    // 客户端所有菜单点击都走 src/ex/uiloader.js 的 op.uiLoader.addTouchEventListener：
    //
    //     op.uiLoader.addTouchEventListener = function (node, event, target) {
    //         node.addTouchEventListener(function (sender, type) {
    //             if (type == ccui.Widget.TOUCH_ENDED) {
    //                 if (GuideLayer.getInstance() && GuideLayer.getInstance().isGuide())
    //                     return GuideLayer.getInstance().nextStep(sender);   // ← 把点击吃掉
    //             }
    //             event.call(target, sender, type);
    //         });
    //     };
    //
    // 私服没有完整的引导数据（引导条件里还有「扭蛋」这种依赖运营配置的项），
    // 引导会永远卡在某一step（例如「去点任务」），于是编成 / 商店 / 抽卡 这些
    // 按钮全都没反应 —— 点下去只会喂给 GuideLayer.nextStep()。
    // 这里让引导直接「已全部结束」，并把已经挂在场景上的引导层隐藏、停掉它的
    // 触摸监听，菜单就恢复可点。
    // ------------------------------------------------------------------
    (function installGuideSkip() {
        function patchManager() {
            var gm = window.guideManager;
            if (!gm) {
                return false;
            }
            if (gm.__oppaiSkip) {
                return true;
            }
            gm.__oppaiSkip = true;
            gm.isGuideEnded = function () { return true; };
            gm.isNeedGuide = function () { return false; };
            gm.isFinishAllGuide = function () { return true; };
            gm.isFinishAllGuideFit = function () { return true; };
            gm.checkGuide = function () { return true; };
            gm.checkGuideFit = function () { return true; };
            gm.isCanEnterGuide = function () { return false; };
            emit("GUIDE-SKIP guideManager 已改成「引导全部结束」");
            return true;
        }

        function patchLayer() {
            if (typeof GuideLayer === "undefined" || !GuideLayer.prototype) {
                return false;
            }
            if (!GuideLayer.prototype.__oppaiSkip) {
                GuideLayer.prototype.__oppaiSkip = true;
                GuideLayer.prototype.isGuide = function () { return false; };
                GuideLayer.prototype.isGuideWaiting = function () { return false; };
                GuideLayer.prototype.isPauseGuide = function () { return true; };
                GuideLayer.prototype.startGuide = function () { };
                GuideLayer.prototype.playGuide = function () { };
                GuideLayer.prototype.playGuideWithPrevLayer = function () { };
                emit("GUIDE-SKIP GuideLayer.isGuide 恒为 false");
            }
            var l = null;
            try { l = GuideLayer.getInstance(); } catch (e) { }
            if (l) {
                try {
                    if (l.isVisible()) {
                        l.setVisible(false);
                    }
                } catch (e) { }
                try {
                    if (l._touchListener && l._touchListener.setEnabled) {
                        l._touchListener.setEnabled(false);
                    }
                } catch (e) { }
                try {
                    if (l._touchListener && l._touchListener.setSwallowTouches) {
                        l._touchListener.setSwallowTouches(false);
                    }
                } catch (e) { }
            }
            return true;
        }

        patchManager();
        patchLayer();
        if (!window.__oppaiGuideSkipTimer) {
            // 引导层是懒加载/可重建的，定期再抹一遍
            window.__oppaiGuideSkipTimer = setInterval(function () {
                patchManager();
                patchLayer();
            }, 1000);
        }
    })();


    // ------------------------------------------------------------------
    // 数据模块构造容错
    //
    // dataManager.initUserData() 会 new 三十来个数据模块，模块构造函数里只要有一个
    // 字段没对齐就整体抛异常，后面所有模块、以及 player.initModuleState() 全都不会
    // 执行 —— 主界面必然黑屏。模拟服的数据不可能和原服完全一致，这里统一兜住，
    // 出错信息照样打到 logcat（OPPAIPATCH|CTOR ...），方便继续按字段补数据。
    // ------------------------------------------------------------------
    (function installModuleCtorTolerance() {
        var MODULE_CLASSES = [
            "Player", "Instance", "Bag", "CharCenter", "Gacha", "Mailbox", "QuestCenter",
            "FavorCenter", "FavorEventCenter", "Friend", "ExchangeCenter", "TalentCenter",
            "SignCenter", "Shop", "ArenaCenter", "Rank", "Score", "Society", "SocietyClg",
            "BossCenter", "Chat", "Detect", "Medal", "EquipmentCenter", "Share",
            "SubareaAchievement", "ConsumeActivity", "Diary", "FriendSupport", "NoviceQuestCenter"
        ];
        var wrapped = {};

        function wrapOne(name) {
            var C = window[name];
            if (typeof C !== "function") {
                return false;
            }
            var W = function () {
                try {
                    C.apply(this, arguments);
                } catch (e) {
                    var dbg = "";
                    try { dbg = " args=" + JSON.stringify(arguments[0]); } catch (e2) { dbg = " args=<unserializable>"; }
                    if (dbg.length > 400) { dbg = dbg.slice(0, 400) + "..."; }
                    emit("CTOR " + name + " 容错跳过: " + e + dbg);
                }
            };
            W.prototype = C.prototype;
            W.__oppaiTolerant = true;
            window[name] = W;
            wrapped[name] = true;
            return true;
        }

        function patch() {
            var missing = 0;
            for (var i = 0; i < MODULE_CLASSES.length; i++) {
                var name = MODULE_CLASSES[i];
                if (wrapped[name]) {
                    continue;
                }
                if (!wrapOne(name)) {
                    missing++;
                }
            }
            return missing;
        }

        if (patch() === 0) {
            emit("CTOR-GUARD 数据模块构造容错已装齐");
            return;
        }
        var tries = 0;
        if (!window.__oppaiCtorTimer) {
            window.__oppaiCtorTimer = setInterval(function () {
                if (patch() === 0 || ++tries > 240) {
                    clearInterval(window.__oppaiCtorTimer);
                    window.__oppaiCtorTimer = null;
                    emit("CTOR-GUARD 数据模块构造容错安装结束，缺 " + patch() + " 个");
                }
            }, 500);
        }
    })();


    // ------------------------------------------------------------------
    // 扭蛋配置兜底
    //
    // 私服没有扭蛋的运营配置：data.gachaMasterList 是空的，_gachaMasterObj 也就是
    // 空对象。而 guideManager.init() -> _loadGuideFlag() -> checkGuide() 会去算
    // 「抽卡类」引导条件，链路是
    //     Gacha.getGachaFullInfo(key) -> getGachaMaster(key) 返回 undefined
    //     -> 接着读 master.name，抛
    //     TypeError: master is undefined @ src/data/gacha.js:745
    // 这个异常会把 dataManager.initUserData 的后半段整个挡掉（player.initModuleState
    // 根本没跑到），主界面随后就死在
    //     TypeError: modules is undefined @ src/ui/main/mainlayer.js:188  → 黑屏
    // 所以这里给 getGachaFullInfo 兜一个空壳，让引导条件判断能正常算完。
    // ------------------------------------------------------------------
    (function installGachaGuard() {
        function patch() {
            if (typeof Gacha === "undefined" || !Gacha.prototype) {
                return false;
            }
            if (Gacha.prototype.__oppaiGachaGuard) {
                return true;
            }
            Gacha.prototype.__oppaiGachaGuard = true;
            var orig = Gacha.prototype.getGachaFullInfo;
            if (typeof orig !== "function") {
                return true;
            }
            Gacha.prototype.getGachaFullInfo = function (masterKey, times) {
                var master = null;
                try { master = this.getGachaMaster(masterKey); } catch (e) { }
                if (!master) {
                    return {
                        key: masterKey,
                        masterKey: masterKey,
                        times: times || 1,
                        name: "",
                        form: 0,
                        resIdx: 0,
                        totalTimes: 0,
                        todayTimes: 0,
                        remainTimes: 0,
                        itemKey: "",
                        itemCount: 0,
                        voucherKey: "",
                        voucherCount: 0,
                        useVoucher: 0,
                        sale: {},
                        saleObj: {},
                        master: {},
                    };
                }
                return orig.apply(this, arguments);
            };
            emit("GACHA-GUARD getGachaFullInfo 已加空壳兜底");
            return true;
        }

        if (patch()) {
            return;
        }
        if (!window.__oppaiGachaGuardTimer) {
            window.__oppaiGachaGuardTimer = setInterval(function () {
                if (patch()) {
                    clearInterval(window.__oppaiGachaGuardTimer);
                    window.__oppaiGachaGuardTimer = null;
                }
            }, 500);
        }
    })();


    // ------------------------------------------------------------------
    // initUserData 兜底
    //
    // dataManager.initUserData() 顺序是「先 new 三十来个数据模块，再依次调
    // player.setCharacter / initTeams / initAsst / guideManager.init /
    // uiLayoutManager.init / player.initModuleState / initXgNotifications」。
    // 中间任何一步抛异常，后面的就全都不执行；其中 player.initModuleState()
    // 一旦没跑到，主界面 _initModuleButtons 立刻
    //     TypeError: modules is undefined @ mainlayer.js:188  → 黑屏。
    // 私服数据本来就不可能和原服一模一样，所以这里无论如何都保证
    // _moduleState 被建出来。
    // ------------------------------------------------------------------
    (function installInitUserDataGuard() {
        function patch() {
            var dm = window.dataManager;
            if (!dm || typeof dm.initUserData !== "function") {
                return false;
            }
            if (dm.__oppaiInitGuard) {
                return true;
            }
            dm.__oppaiInitGuard = true;
            var orig = dm.initUserData;
            dm.initUserData = function (data) {
                var err = null;
                try {
                    orig.apply(this, arguments);
                } catch (e) {
                    err = e;
                    emit("INITUSERDATA-GUARD initUserData 抛异常: " + e);
                }
                try {
                    var p = dm.player;
                    if (p && !p._moduleState && typeof p.initModuleState === "function") {
                        p.initModuleState();
                        emit("INITUSERDATA-GUARD 补跑 player.initModuleState()");
                    }
                } catch (e2) {
                    emit("INITUSERDATA-GUARD 补跑 initModuleState 失败: " + e2);
                }
                if (err) {
                    // 抛出去让上层照旧走失败分支
                    throw err;
                }
            };
            emit("INITUSERDATA-GUARD 已安装");
            return true;
        }

        if (patch()) {
            return;
        }
        if (!window.__oppaiInitGuardTimer) {
            window.__oppaiInitGuardTimer = setInterval(function () {
                if (patch()) {
                    clearInterval(window.__oppaiInitGuardTimer);
                    window.__oppaiInitGuardTimer = null;
                }
            }, 500);
        }
    })();

})();
