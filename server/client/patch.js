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
//   6. 宿舍「互动（抚摸）」判定框放大
//      ⚠️ **这条不是修 bug，是私服体验改动**（原版能玩，只是反人类：
//      判定框 100×100、不可见、在角色右边，还得在 1 秒内开始搓）。
//      原因和实测见文件末尾那段。要还原原版手感就把那一段整块删掉。
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
    // 服务端推数据：客户端自己不做派发，这里补上
    //
    // src/util/server.js 里有一张 responseConfig 表，本意是「响应 data 里出现哪个
    // 模块的 key，就喂给对应模块的 updateByServer()」：
    //
    //     responseConfig.quest  = function (res) { ... dataManager.questCenter.updateByServer(res.data) }
    //     responseConfig.player / mail / char / gacha / ...
    //
    // 但在这套引擎上实测它**没有被派发**：server.request('player.getdata') 回
    // {player:...}，Player.updateByServer() 也不会被调用。于是所有「服务端推数据给
    // 客户端」都失效 —— 最直观的表现就是主线任务领奖成功、奖励也发了，但列表不刷新。
    //
    // 这里自己补一层：包住 server.request，成功响应里出现下面的 key 就先喂给对应模块
    // 的 updateByServer()，再走原来的回调（顺序很重要，回调里会立刻重绘列表）。
    // ------------------------------------------------------------------
    (function installResponseDispatch() {
        // 只列「响应 key -> dataManager 上的模块」能一一对上、
        // 而且模块确实有 updateByServer() 的。
        function targets() {
            var dm = window.dataManager;
            if (!dm) {
                return null;
            }
            return {
                player: dm.player,
                quest: dm.questCenter,
                mail: dm.mailbox,
                gacha: dm.gacha,
                char: dm.character,
                friend: dm.friend,
                sign: dm.signCenter,
                arena: dm.arenaCenter,
                score: dm.score,
                society: dm.society,
                societyclg: dm.societyClg,
                detect: dm.detect,
                boss: dm.bossCenter,
                equipment: dm.equipmentCenter,
                exchange: dm.exchangeCenter,
                talents: dm.talentCenter,
                actquest: dm.actQuestCenter
            };
        }

        function applyResponse(res) {
            if (!res || res.code !== 200 || !res.data) {
                return 0;
            }
            var map = targets();
            if (!map) {
                return 0;
            }
            var n = 0;
            for (var key in map) {
                if (res.data[key] === undefined) {
                    continue;
                }
                var mod = map[key];
                if (!mod || typeof mod.updateByServer !== "function") {
                    continue;
                }
                try {
                    mod.updateByServer(res.data[key]);
                    n++;
                } catch (e) {
                    emit("RESP-DISPATCH " + key + " 失败: " + e);
                }
            }
            return n;
        }

        function patch() {
            var s = window.server;
            if (!s || typeof s.request !== "function") {
                return false;
            }
            if (s.request.__oppaiDispatch) {
                return true;
            }
            var orig = s.request;
            var W = function () {
                var args = Array.prototype.slice.call(arguments);
                if (typeof args[2] === "function") {
                    var cb = args[2];
                    args[2] = function (err, res) {
                        if (!err) {
                            applyResponse(res);
                        }
                        return cb.apply(this, arguments);
                    };
                }
                return orig.apply(this, args);
            };
            W.__oppaiDispatch = true;
            s.request = W;
            emit("RESP-DISPATCH 已接管响应派发（responseConfig 在这套引擎上不生效）");
            return true;
        }

        if (patch()) {
            return;
        }
        var tries = 0;
        if (!window.__oppaiDispatchTimer) {
            window.__oppaiDispatchTimer = setInterval(function () {
                if (patch() || ++tries > 240) {
                    clearInterval(window.__oppaiDispatchTimer);
                    window.__oppaiDispatchTimer = null;
                }
            }, 500);
        }
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
            // ⚠️ 这个**必须带上**，而且**必须忽略参数**。
            //
            // 客户端是 `guideManager.isGuideComplete(guideId)`，按引导步骤问
            // 「这一步做完了吗」；`Player._getName` 就是拿它问「新兵起名
            // （GUIDE_NAME.GN_NEW_NAME = 2）做完了吗」，没做完就显示占位名
            // **「废材」**（table_dictionary 里那个词条）。
            //
            // 我们把引导整个跳过了，那一步永远不会真的完成，于是主界面上
            // 名字一直显示「废材」——真名其实好好地在 `player._name` 里
            // （服务端存档也是对的），只是取值器不给。
            //
            // 一开始漏了这条，症状特别容易误判成「名字被改了」。
            gm.isGuideComplete = function () { return true; };
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


    // ------------------------------------------------------------------
    // 宿舍「互动（抚摸）」判定框放大 —— **私服体验改动，不是修 bug**
    //
    // 玩法本身是两步手势，反汇编 `FavorLayer.newTouchEffect` + `CharAsstLayer`：
    //   ① 点/搓角色的头或胸口 -> `eachTalkCb` -> `touchEffect.showView()` 爱心出现
    //   ② 在**那个爱心上**按住来回搓 -> `pgState=1`，`percent += dt*60`（松手 -40）
    //   ③ 填满 100 -> `toucuFullCb` -> `favor.touchcharasst` -> 加好感度
    //
    // 卡点在 ②：那个判定框是 `favortoucheffect.csb` 里的 `touchpanel`，
    // **只有 100×100**，中心在世界坐标 (434,400) —— 在角色右边、屏幕上那颗爱心附近，
    // **不在角色身上**，而且**完全不可见**。不反汇编根本猜不到要搓哪。
    // 再加上 `showView` 之后 `PG_SHOW_STAY_TIME = 1000`：1 秒内不开始搓就自动收起。
    //
    // 实测（把框临时放大到 900×700 覆盖角色）：一次按住来回搓就能连续触发 3 次，
    // 服务端日志 `favor.touchcharasst hadf 好感 +22` 应声而来 —— 机制没问题，
    // 纯粹是「要搓对地方」这件事反人类。
    //
    // 所以这里把判定框放大到覆盖角色的两个触摸区（头/脸 + 胸口），
    // **不动 1 秒窗口**（那个常量 `PG_SHOW_STAY_TIME` 是 `newTouchEffect` 的闭包变量，
    // 从外面够不着；放大之后手感已经够了，没必要再自己发明行为）。
    //
    // 位置是相对 `favortoucheffectcase` 的（那个 case 落在屏幕中心偏右），
    // 所以用偏移量，换分辨率也不会跑偏。
    // ------------------------------------------------------------------
    (function installFavorTouchQoL() {
        var BOX_W = 560;        // 覆盖角色两个触摸区（世界 x 173~375, y 248~600）
        var BOX_H = 560;
        var OFF_X = -154;       // 相对 favortoucheffectcase 的偏移
        var OFF_Y = 20;

        function fix(layer) {
            var te = layer && layer.touchEffect;
            if (!te || te.__oppaiTouchBox) {
                return !!te;
            }
            te.__oppaiTouchBox = true;
            var kids = te.getChildren();
            for (var i = 0; i < kids.length; i++) {
                var child = kids[i];
                if (child && child.getName && child.getName() === "touchpanel") {
                    child.setContentSize(cc.size(BOX_W, BOX_H));
                    child.setPosition(cc.p(OFF_X, OFF_Y));
                    emit("FAVOR-TOUCH 互动判定框放大到 " + BOX_W + "x" + BOX_H +
                         "（原版是 100x100，在角色右边且不可见）");
                }
            }
            return true;
        }

        function patch() {
            if (typeof window.FavorLayer === "undefined" || !window.FavorLayer.prototype) {
                return false;
            }
            var proto = window.FavorLayer.prototype;
            if (proto.__oppaiTouchBoxPatched) {
                return true;
            }
            var orig = proto._initMainUi;
            if (typeof orig !== "function") {
                return false;
            }
            // touchEffect 是在 _initMainUi 里建出来的，所以得包一层再回来处理
            proto._initMainUi = function () {
                var ret = orig.apply(this, arguments);
                try {
                    fix(this);
                } catch (e) {
                    emit("FAVOR-TOUCH 放大判定框失败: " + e);
                }
                return ret;
            };
            proto.__oppaiTouchBoxPatched = true;
            emit("FAVOR-TOUCH 互动判定框补丁已安装");
            return true;
        }

        if (patch()) {
            return;
        }
        if (!window.__oppaiFavorTouchTimer) {
            window.__oppaiFavorTouchTimer = setInterval(function () {
                if (patch()) {
                    clearInterval(window.__oppaiFavorTouchTimer);
                    window.__oppaiFavorTouchTimer = null;
                }
            }, 500);
        }
    })();

})();
