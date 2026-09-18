/**
 * oppai (战场双马尾) 服务端模拟器 —— 客户端探针 v2
 *
 * 通过修改 project.json 的 jsList 注入的明文 JS，用来：
 *   1. 把客户端的网络行为、加密函数调用、响应字段读取打印到 logcat
 *   2. 提供一个 REPL：轮询 __CDN_BASE__/hook/poll，执行返回的 JS 表达式，
 *      再把结果 POST 回 __CDN_BASE__/hook/result
 *
 * __CDN_BASE__ 由 tools/patch_apk.py 在打包时替换成真实的 http://host:port
 */
(function () {
    var TAG = "OPPAIHOOK";
    var VERSION = "3.0";
    var CDN_BASE = "__CDN_BASE__";

    // 模拟服的共享密钥（DH 单位元，见 README）
    var DH_IDENTITY = String.fromCharCode(1, 0, 0, 0, 0, 0, 0, 0);

    // 自动登录用的账号
    var AUTO_ACCOUNT = "emulator";
    var AUTO_PASSWORD = "emulator";

    // 登录成功后是否自动替客户端跑 cb4AfterLogin + 切主场景。
    //
    // 现在**关掉了** —— 服务端把 WebSocket 登录响应的 code 改成 200 之后
    // （反汇编 User.login: result.code !== 200 就走错误分支），
    // 客户端自己那条链（server.login -> playerLogin -> agent.getlogindata
    // -> cb4AfterLogin -> _enterMain）已经能完整跑通。
    //
    // 再让探针补一刀会变成**二次 initUserData**，把状态搞坏：
    //     CB4 ERR TypeError: this._lvEncrp is null
    //     SWITCH ERR TypeError: this._teams is null
    //
    // 排查问题时可以手动触发：__oppaiHook__.afterLogin()
    var AUTO_AFTER_LOGIN = false;

    // 是否自动代替玩家点「开始游戏」。
    // 关掉之后登录界面完全手动操作；点「开始游戏」/「登录」后照样秒登录进游戏。
    var AUTO_CLICK_START = false;

    if (window.__oppaiHook__) {
        return;
    }

    // ------------------------------------------------------------------
    // 输出
    // ------------------------------------------------------------------
    var lines = [];
    var fileText = "";
    var MAX_FILE = 300000;

    function emit(line) {
        lines.push(line);
        try {
            console.log(TAG + "|" + line);
        } catch (e) {
        }
    }

    function flush() {
        if (!lines.length) {
            return;
        }
        var chunk = lines.join("\n") + "\n";
        lines = [];
        fileText += chunk;
        if (fileText.length > MAX_FILE) {
            fileText = fileText.substring(fileText.length - MAX_FILE);
        }
        try {
            jsb.fileUtils.writeStringToFile(fileText, jsb.fileUtils.getWritablePath() + "hook.log");
        } catch (e) {
        }
    }

    // ---------------------------------------------------------------
    // 心跳：cocos2d-js 里 setInterval 不一定可用，用 scheduler 兜底
    // ---------------------------------------------------------------
    var heartbeats = [];

    function heartbeat(fn, ms) {
        heartbeats.push({fn: fn, ms: ms, acc: 0});
        if (heartbeatStarted) {
            startOne(heartbeats[heartbeats.length - 1]);
        }
    }

    var heartbeatStarted = false;

    function startOne(hb) {
        if (hb.started) {
            return;
        }
        hb.started = true;
        if (typeof setInterval === "function") {
            hb.timer = setInterval(hb.fn, hb.ms);
        }
    }

    function startHeartbeats() {
        if (heartbeatStarted) {
            return;
        }
        heartbeatStarted = true;
        for (var i = 0; i < heartbeats.length; i++) {
            startOne(heartbeats[i]);
        }
        // 无论 setInterval 是否可用，都用引擎的 scheduler 再跑一份
        try {
            if (window.cc && cc.director && cc.director.getScheduler) {
                cc.director.getScheduler().schedule(function (dt) {
                    for (var j = 0; j < heartbeats.length; j++) {
                        var hb = heartbeats[j];
                        hb.acc += dt * 1000;
                        if (hb.acc >= hb.ms) {
                            hb.acc = 0;
                            try {
                                hb.fn();
                            } catch (e) {
                                emit("HEARTBEAT ERR " + e);
                            }
                        }
                    }
                }, null, 0.1, false);
                emit("HEARTBEAT via scheduler");
            }
        } catch (e) {
            emit("HEARTBEAT scheduler failed " + e);
        }
    }

    heartbeat(flush, 1000);

    // ------------------------------------------------------------------
    // 序列化
    // ------------------------------------------------------------------
    function safeJson(value, depth, seen) {
        depth = depth === undefined ? 0 : depth;
        seen = seen || [];
        if (value === null) return "null";
        if (value === undefined) return "undefined";
        var t = typeof value;
        if (t === "number" || t === "boolean") return String(value);
        if (t === "string") return JSON.stringify(value);
        if (t === "function") return "<fn " + (value.name || "?") + ">";
        if (depth > 6) return "<deep>";
        for (var i = 0; i < seen.length; i++) {
            if (seen[i] === value) return "<cycle>";
        }
        seen = seen.concat([value]);
        var tag = Object.prototype.toString.call(value);
        if (tag === "[object Array]") {
            var parts = [];
            for (var j = 0; j < value.length && j < 300; j++) {
                parts.push(safeJson(value[j], depth + 1, seen));
            }
            return "[" + parts.join(",") + "]";
        }
        var out = [];
        var keys;
        try {
            keys = Object.keys(value);
        } catch (e) {
            return "<obj>";
        }
        for (var k = 0; k < keys.length && k < 300; k++) {
            var key = keys[k];
            var v;
            try {
                v = value[key];
            } catch (e2) {
                v = "<throw>";
            }
            out.push(key + ":" + safeJson(v, depth + 1, seen));
        }
        return "{" + out.join(",") + "}";
    }

    function hexOf(str) {
        if (typeof str !== "string") {
            return String(str);
        }
        var out = [];
        for (var i = 0; i < str.length; i++) {
            var c = str.charCodeAt(i).toString(16);
            if (c.length < 2) c = "0" + c;
            out.push(c);
        }
        return out.join("");
    }

    function brief(str, n) {
        if (typeof str !== "string") {
            str = String(str);
        }
        n = n || 800;
        return str.length > n ? str.substring(0, n) + "...(" + str.length + ")" : str;
    }

    // ------------------------------------------------------------------
    // 响应字段探测
    // ------------------------------------------------------------------
    var probeEnabled = false;
    var probeDepthLimit = 4;
    var probeLog = {};

    function noteAccess(path, key) {
        var id = path + "." + key;
        if (!probeLog[id]) {
            probeLog[id] = 0;
            emit("SCHEMA " + id);
        }
        probeLog[id]++;
    }

    function wrapProbe(value, path, depth) {
        if (!probeEnabled || depth > probeDepthLimit) {
            return value;
        }
        if (value === null || typeof value !== "object" || typeof Proxy === "undefined") {
            return value;
        }
        try {
            return new Proxy(value, {
                get: function (target, key) {
                    if (typeof key === "string") {
                        noteAccess(path, key);
                    }
                    var v;
                    try {
                        v = target[key];
                    } catch (e) {
                        return undefined;
                    }
                    if (v !== null && typeof v === "object" && !(v instanceof Function)) {
                        return wrapProbe(v, path + "." + String(key), depth + 1);
                    }
                    return v;
                },
                set: function (target, key, v) {
                    target[key] = v;
                    return true;
                }
            });
        } catch (e) {
            return value;
        }
    }

    // ------------------------------------------------------------------
    // 挂钩
    // ------------------------------------------------------------------
    var hooked = {};

    function desc(v) {
        if (typeof v === "string") {
            return "b64len=" + v.length + " hex=" + hexOf(v);
        }
        return safeJson(v);
    }

    function hookCrypt() {
        var c = window.crypt;
        if (!c || hooked.crypt) {
            return !!c;
        }
        hooked.crypt = true;
        emit("HOOK crypt=" + safeJson(Object.keys(c)));
        var names = ["hexEncode", "hexDecode", "hashKey", "randomKey", "utf16To8", "utf8To16",
            "base64Encode", "base64Decode", "desEncode", "desDecode", "dhExchange", "dhSecret",
            "hmac", "hmac64", "hexDesEncode", "hexDesDecode"];
        for (var i = 0; i < names.length; i++) {
            (function (name) {
                var orig = c[name];
                if (typeof orig !== "function") {
                    return;
                }
                c[name] = function () {
                    var args = Array.prototype.slice.call(arguments);
                    var shown = [];
                    for (var j = 0; j < args.length; j++) {
                        shown.push(desc(args[j]));
                    }
                    var result;
                    try {
                        result = orig.apply(this, arguments);
                    } catch (err) {
                        emit("CRYPT " + name + "(" + shown.join(", ") + ") THREW " + err);
                        throw err;
                    }
                    emit("CRYPT " + name + "(" + shown.join(", ") + ") => " + desc(result));
                    return result;
                };
            })(names[i]);
        }
        return true;
    }

    function hookServer() {
        var s = window.server;
        if (!s || hooked.server) {
            return !!s;
        }
        hooked.server = true;
        emit("HOOK server=" + safeJson(Object.keys(s)));

        if (typeof s.request === "function") {
            var origRequest = s.request;
            s.request = function (route, msg, cb, isBackstageRequest) {
                emit("REQ route=" + safeJson(route) + " msg=" + safeJson(msg));
                var wrapped = cb;
                if (typeof cb === "function") {
                    wrapped = function () {
                        var args = Array.prototype.slice.call(arguments);
                        var shown = [];
                        for (var i = 0; i < args.length; i++) {
                            var a = args[i];
                            if (a !== null && typeof a === "object" && !(a instanceof Function)) {
                                shown.push(safeJson(a));
                                args[i] = wrapProbe(a, "RES." + route, 0);
                            } else {
                                shown.push(safeJson(a));
                            }
                        }
                        emit("RES route=" + safeJson(route) + " args=" + shown.join(" | "));
                        return cb.apply(this, args);
                    };
                }
                return origRequest.call(this, route, msg, wrapped, isBackstageRequest);
            };
        }

        if (typeof s.resUnpack === "function") {
            var origUnpack = s.resUnpack;
            s.resUnpack = function (data, key) {
                var res = origUnpack.call(this, data, key);
                emit("UNPACK raw=" + brief(String(data)) + " key=" + brief(String(key)) + " => " + safeJson(res));
                return res;
            };
        }

        if (typeof s.reqPack === "function") {
            var origPack = s.reqPack;
            s.reqPack = function (route, msg, reqId) {
                var packed = origPack.call(this, route, msg, reqId);
                emit("PACK route=" + safeJson(route) + " msg=" + safeJson(msg) + " reqId=" + safeJson(reqId) +
                    " => " + brief(String(packed)));
                return packed;
            };
        }

        if (typeof s.oauth === "function") {
            var origOauth = s.oauth;
            s.oauth = function () {
                emit("OAUTH start " + safeJson(Array.prototype.slice.call(arguments)));
                return origOauth.apply(this, arguments);
            };
        }

        if (typeof s.login === "function") {
            var origLogin = s.login;
            s.login = function () {
                emit("LOGIN start");
                return origLogin.apply(this, arguments);
            };
        }

        return true;
    }

    function hookHttpc() {
        var h = window.httpc;
        if (!h || hooked.httpc) {
            return !!h;
        }
        hooked.httpc = true;

        // IP_URL (https://api.ipify.org/) 的证书/网络在模拟器上早已失效，
        // 客户端会无限重试，这里直接短路返回本机地址。
        var origGet = h.sendGetRequest;
        if (typeof origGet === "function") {
            h.sendGetRequest = function (host, param, cb) {
                if (String(host).indexOf("ipify") >= 0) {
                    if (typeof cb === "function") {
                        setTimeout(function () {
                            cb(null, "10.110.29.230", Date.now(), Date.now());
                        }, 20);
                    }
                    return;
                }
                return origGet.apply(this, arguments);
            };
        }

        var names = ["sendGetRequest", "sendPostRequest", "sendWebGetRequest", "sendWebPostRequest"];
        for (var i = 0; i < names.length; i++) {
            (function (name) {
                var orig = h[name];
                if (typeof orig !== "function") {
                    return;
                }
                h[name] = function () {
                    var args = Array.prototype.slice.call(arguments);
                    var shown = safeJson(args.slice(0, 3));
                    var cb = args[2];
                    if (typeof cb === "function") {
                        args[2] = function () {
                            emit("HTTPRESP " + name + " " + shown + " => " + safeJson(Array.prototype.slice.call(arguments)));
                            return cb.apply(this, arguments);
                        };
                    }
                    emit("HTTP " + name + " " + shown);
                    return orig.apply(this, args);
                };
            })(names[i]);
        }
        return true;
    }

    function hookWebSocketFactory() {
        var f = window.wsFactory;
        if (!f || hooked.ws || !f.createConnection) {
            return !!f;
        }
        hooked.ws = true;
        var orig = f.createConnection;
        f.createConnection = function (host, onSucc, onErr) {
            emit("WS connect " + safeJson(host));
            var wrappedSucc = onSucc;
            if (typeof onSucc === "function") {
                wrappedSucc = function (handle) {
                    emit("WS connected");
                    if (handle && typeof handle.send === "function") {
                        var origSend = handle.send;
                        handle.send = function (data) {
                            emit("WS SEND " + brief(String(data)));
                            return origSend.apply(this, arguments);
                        };
                    }
                    return onSucc.apply(this, arguments);
                };
            }
            var wrappedErr = onErr;
            if (typeof onErr === "function") {
                wrappedErr = function () {
                    emit("WS error " + safeJson(Array.prototype.slice.call(arguments)));
                    return onErr.apply(this, arguments);
                };
            }
            return orig.call(this, host, wrappedSucc, wrappedErr);
        };
        return true;
    }

    function hookDataManager() {
        var dm = window.dataManager;
        if (!dm || hooked.dm) {
            return !!dm;
        }
        hooked.dm = true;
        emit("HOOK dataManager=" + safeJson(Object.keys(dm)));
        var keys = Object.keys(dm);
        for (var i = 0; i < keys.length; i++) {
            (function (key) {
                var obj = dm[key];
                if (!obj || typeof obj !== "object" || typeof obj.updateByServer !== "function") {
                    return;
                }
                var orig = obj.updateByServer;
                obj.updateByServer = function (data) {
                    emit("SYNC " + key + ".updateByServer " + safeJson(data));
                    return orig.apply(this, arguments);
                };
            })(keys[i]);
        }
        return true;
    }

    function hookUi() {
        var cm = window.ccuiManager;
        if (!cm || hooked.ui || typeof cm.popup !== "function") {
            return !!cm;
        }
        hooked.ui = true;
        var origPopup = cm.popup;
        cm.popup = function () {
            emit("POPUP " + safeJson(Array.prototype.slice.call(arguments)));
            return origPopup.apply(this, arguments);
        };
        return true;
    }

    function hookLogging() {
        if (hooked.logging) {
            return true;
        }
        hooked.logging = true;
        var wrap = function (name, obj, tag) {
            if (!obj || typeof obj[name] !== "function") {
                return;
            }
            var orig = obj[name];
            obj[name] = function () {
                try {
                    emit("GAMELOG " + tag + name + ": " +
                        Array.prototype.slice.call(arguments).join(" "));
                } catch (e) {
                }
                return orig.apply(this, arguments);
            };
        };
        wrap("log", window.console, "console.");
        wrap("warn", window.console, "console.");
        wrap("error", window.console, "console.");
        if (window.cc) {
            wrap("log", cc, "cc.");
            wrap("warn", cc, "cc.");
            wrap("error", cc, "cc.");
        }
        return true;
    }

    // ------------------------------------------------------------------
    // SDK 登录绕过
    //
    // 原生侧（smali）的调用链是：
    //     JS: jsb.reflection.callStaticMethod(QuickAdapter, "login")
    //       -> QuickAdapter.login() -> AppActivity.login()
    //       -> QuickSDK.User.login(activity) -> 渠道 SDK(百度) -> LoginActivity 弹窗
    //     登录成功后原生 evalString("quicksdk.sdkLoginCallback(1, \"uid\", \"token\")")
    //
    // QuickSDK / 百度的服务器早就没了，弹窗登不进去，所以这里直接把
    // quicksdk.login 换成「立刻假装成功」，原生弹窗根本不会被拉起。
    // ------------------------------------------------------------------
    var SDK_ACCOUNT = "emulator";
    var SDK_TOKEN = "emulator-token";

    function hookSdkLogin() {
        var q = window.quicksdk;
        if (!q || hooked.sdk) {
            return !!q;
        }
        hooked.sdk = true;
        emit("HOOK quicksdk=" + safeJson(Object.keys(q)));

        // 注意：**不覆盖 q.login**。
        // 登录已经改在 Java 层（QuickAdapter.login -> ServerLoginRunnable ->
        // quicksdk.sdkLoginCallback(1, account, token)），走原生补丁即可，
        // JS 只把会连外网/弹原生界面的几个入口做掉。
        q.show = function () {
            emit("SDK show bypassed");
        };
        q.logout = function () {
            emit("SDK logout bypassed");
        };
        q.exit = function () {
            emit("SDK exit bypassed");
        };
        return true;
    }

    // ------------------------------------------------------------------
    // 重写 server.request
    //
    // 客户端 server.js 的 request -> reqPack 依赖登录时写入的闭包变量
    // session / key，这两个变量在模拟环境下拿不到（reqPack 直接返回 undefined，
    // 请求发不出去）。这里干脆用客户端自己的 crypt 重新实现一遍，
    // 绕开那两个闭包变量：
    //
    //     body = base64(desEncode(secret, utf16To8(JSON.stringify({route,msg,reqId}))))
    //     POST http://<gameServUrl>
    //     响应：明文含 "code": 就直接 JSON.parse，否则 desDecode + base64Decode
    // ------------------------------------------------------------------
    var gameState = {
        gameServUrl: null,
        session: null
    };

    function hookServerRequest() {
        var s = window.server;
        if (!s || hooked.request || typeof s.request !== "function") {
            return !!s;
        }
        hooked.request = true;

        // 先从 server.login 的返回值里抓 gameServUrl
        var origLogin = s.login;
        s.login = function (info, cb) {
            var wrapped = function (err, res) {
                if (!err && res) {
                    if (res.gameServUrl) {
                        gameState.gameServUrl = res.gameServUrl;
                    }
                    if (res.session) {
                        gameState.session = res.session;
                    }
                    emit("GAMESTATE gameServUrl=" + gameState.gameServUrl + " session=" + gameState.session);
                    // 注意：这里**默认不自动**替客户端收尾。
                    // dataManager.cb4AfterLogin() 在现在的服务端数据下会把 JS 主线程
                    // 死循环（各部分单独调用都正常，组合起来就卡住），
                    // 所以默认关掉，保证客户端不会卡死。
                    // 想试验时可以在 REPL 里执行 __oppaiHook__.afterLogin()。
                    if (AUTO_AFTER_LOGIN) {
                        setTimeout(afterLogin, 150);
                    }
                }
                return cb && cb.apply(this, arguments);
            };
            return origLogin.call(this, info, wrapped);
        };

        var reqId = 0;
        s.request = function (route, msg, cb, isBackstageRequest) {
            reqId++;
            var url = "http://" + (gameState.gameServUrl || ("10.110.29.230:" + 10003));
            var key = DH_IDENTITY;
            var body;
            try {
                var plain = crypt.utf16To8(JSON.stringify({route: route, msg: msg || {}, reqId: reqId}));
                body = crypt.base64Encode(crypt.desEncode(key, plain));
            } catch (e) {
                emit("GAME REQ PACK ERR " + e);
                return;
            }
            emit("GAME REQ " + route + " reqId=" + reqId + " -> " + url);
            try {
                httpc.sendPostRequest(url, body, function (err, data) {
                    if (err) {
                        emit("GAME REQ ERR " + safeJson(err));
                        if (typeof cb === "function") {
                            cb(err);
                        }
                        return;
                    }
                    var res;
                    try {
                        if (String(data).indexOf('"code":') >= 0) {
                            res = JSON.parse(data);
                        } else {
                            res = JSON.parse(crypt.utf8To16(crypt.desDecode(key, crypt.base64Decode(data))));
                        }
                    } catch (e) {
                        emit("GAME RESP DECODE ERR " + e + " raw=" + brief(String(data), 160));
                        if (typeof cb === "function") {
                            cb(e);
                        }
                        return;
                    }
                    emit("GAME RESP " + route + " => " + safeJson(res).substring(0, 600));
                    if (typeof cb === "function") {
                        cb(null, res, 0, 0);
                    }
                });
            } catch (e) {
                emit("GAME REQ EX " + e);
            }
        };
        return true;
    }

    // ------------------------------------------------------------------
    // 登录 UI：绕开 SDK 弹窗，直接走游戏自己的账号登录
    // ------------------------------------------------------------------
    var autoLoginTried = false;
    var afterLoginDone = false;

    // 登录成功后的收尾：拉 agent.getlogindata 并把数据喂给 dataManager
    function afterLogin() {
        if (afterLoginDone) {
            return;
        }
        afterLoginDone = true;
        emit("AFTER LOGIN -> agent.getlogindata");
        try {
            server.request("agent.getlogindata", {}, function (err, res) {
                emit("GETLOGINDATA err=" + safeJson(err) + " res=" + safeJson(res).substring(0, 240));
                if (err || !res || !res.data) {
                    afterLoginDone = false;
                    return;
                }
                if (!res.data.player) {
                    // 新号：先建角色，客户端随后会走开场 / 新手引导
                    emit("AFTER LOGIN 新号 -> agent.createplayer");
                    server.request("agent.createplayer", {activeCode: ""}, function (err2, res2) {
                        emit("CREATEPLAYER err=" + safeJson(err2) + " res=" + safeJson(res2).substring(0, 200));
                        if (err2 || !res2 || !res2.data) {
                            return;
                        }
                        finishLogin(res2.data);
                    });
                    return;
                }
                finishLogin(res.data);
            });
        } catch (e) {
            emit("AFTER LOGIN ERR " + e);
            afterLoginDone = false;
        }
    }

    function finishLogin(data) {
        try {
            dataManager.isLogin = true;
            dataManager.cb4AfterLogin(null, data);
            emit("CB4 DONE isLogin=" + dataManager.isLogin);
        } catch (e) {
            emit("CB4 ERR " + e);
        }
        // 登录收尾完成后切主场景（客户端原本是在「开始游戏」的回调里调 _enterMain）
        setTimeout(function () {
            try {
                var scene = cc.director.getRunningScene();
                if (typeof MainScene !== "undefined" && scene instanceof MainScene) {
                    return;
                }
                var layer = _findChild(scene, LoginLayer, 0);
                if (layer && typeof layer._enterMain === "function") {
                    emit("SWITCH -> MainScene");
                    layer._enterMain();
                } else {
                    emit("SWITCH 失败：找不到 LoginLayer");
                }
            } catch (e) {
                emit("SWITCH ERR " + e);
            }
        }, 400);
    }

    function autoLogin() {
        if (autoLoginTried || !window.dataManager || !dataManager.user) {
            return;
        }
        if (dataManager.isLogin) {
            return;
        }
        autoLoginTried = true;
        emit("AUTO LOGIN -> quicksdk.login()（走 Java 层补丁）");
        try {
            // 原生 QuickAdapter.login 已被改成直接回调 quicksdk.sdkLoginCallback(1, account, token)。
            // 这里只是替玩家点一下「开始游戏」，剩下交给游戏自己的登录链路。
            quicksdk.login();
        } catch (e) {
            emit("AUTO LOGIN err " + e);
            autoLoginTried = false;
        }
    }

    var origGameStart = null;

    function _findChild(node, cls, depth) {
        if (!node || (depth || 0) > 4) {
            return null;
        }
        var n = 0;
        try {
            n = node.getChildrenCount ? node.getChildrenCount() : 0;
        } catch (e) {
            return null;
        }
        for (var i = 0; i < n; i++) {
            var c = node.getChildren()[i];
            if (!c) {
                continue;
            }
            if (c instanceof cls) {
                return c;
            }
            var found = _findChild(c, cls, (depth || 0) + 1);
            if (found) {
                return found;
            }
        }
        return null;
    }

    function hookLoginUi() {
        var L = window.LoginLayer;
        if (!L || hooked.loginUi) {
            return !!L;
        }
        hooked.loginUi = true;
        emit("HOOK LoginLayer");
        // 保留原始处理逻辑：它会注册 SDK 登录回调再调 op.login。
        // 原生登录已被 Java 层改成「立刻成功」，走原流程就能直接进游戏。
        origGameStart = L.prototype._onClickGameStart;

        // 「登录」按钮走的是游戏自带的老账号体系（userLogin -> requestToken ->
        // cb4AfterLogin），在私服上参数对不齐，会把登录响应当错误弹「温馨提示」。
        // 直接让它跟「开始游戏」一样走 SDK 登录（Java 层补丁，瞬间成功）。
        L.prototype._onClickSignIn = function () {
            emit("UI 登录 -> 改走 SDK 登录（Java 层补丁）");
            if (origGameStart) {
                origGameStart.call(this, null, ccui.Widget.TOUCH_ENDED);
            }
        };
        return true;
    }

    var autoTick = 0;

    function startAutoLoginWatch() {
        if (!AUTO_CLICK_START) {
            emit("AUTO 自动点击「开始游戏」已关闭，请手动点");
            return;
        }
        heartbeat(function () {
            autoTick++;
            if (autoLoginTried || autoTick < 10) {
                return;
            }
            if (typeof dataManager === "undefined" || !dataManager.user) {
                return;
            }
            if (typeof server === "undefined" || !server.getCurServer) {
                return;
            }
            // 等服务器列表同步出 curServer，否则登录会报 "no server has been selected"
            if (!server.getCurServer()) {
                return;
            }
            var scene = null;
            try {
                scene = cc.director.getRunningScene();
            } catch (e) {
                return;
            }
            if (!scene || typeof LoginScene === "undefined" || !(scene instanceof LoginScene)) {
                return;
            }
            var layer = _findChild(scene, LoginLayer, 0);
            if (!layer || !origGameStart) {
                if (autoTick % 10 === 0) {
                    emit("AUTO 等待 LoginLayer layer=" + (layer ? "yes" : "null") +
                        " orig=" + (origGameStart ? "yes" : "null") +
                        " children=" + scene.getChildrenCount());
                }
                return;
            }
            autoLoginTried = true;
            emit("AUTO LOGIN -> 触发原始「开始游戏」处理（Java 层登录补丁）");
            try {
                origGameStart.call(layer, null, ccui.Widget.TOUCH_ENDED);
            } catch (e) {
                emit("AUTO gameStart err " + e);
                autoLoginTried = false;
            }
        }, 1000);
    }

    // ------------------------------------------------------------------
    // 模块构造容错
    //
    // dataManager.initUserData() 会 new 一堆数据模块（Bag / Mailbox / ...），
    // 只要有一个模块的数据字段没对齐就会整体抛异常，登录流程直接断掉。
    // 模拟服的数据本来就不完整，这里把构造异常吞掉让流程走下去，
    // 同时把出错信息打出来，方便继续按字段补数据。
    // ------------------------------------------------------------------
    var MODULE_CLASSES = [
        "Player", "Instance", "Bag", "CharCenter", "Gacha", "Mailbox", "QuestCenter",
        "FavorCenter", "FavorEventCenter", "Friend", "ExchangeCenter", "TalentCenter",
        "SignCenter", "Shop", "ArenaCenter", "Rank", "Score", "Society", "SocietyClg",
        "BossCenter", "Chat", "Detect", "Medal", "EquipmentCenter", "Share",
        "SubareaAchievement", "ConsumeActivity", "Diary", "FriendSupport", "NoviceQuestCenter"
    ];

    function hookModuleCtors() {
        var done = 0;
        for (var i = 0; i < MODULE_CLASSES.length; i++) {
            (function (name) {
                var C = window[name];
                if (typeof C !== "function" || C.__oppaiTolerant) {
                    return;
                }
                var W = function () {
                    try {
                        C.apply(this, arguments);
                    } catch (e) {
                        emit("CTOR " + name + " 容错跳过: " + e);
                    }
                };
                W.prototype = C.prototype;
                W.__oppaiTolerant = true;
                window[name] = W;
                done++;
            })(MODULE_CLASSES[i]);
        }
        return done;
    }

    // ------------------------------------------------------------------
    // initUserData 只跑一次
    //
    // cb4AfterLogin 里会调 dataManager.initUserData(data) 建立所有数据模块。
    // 实测它在同一次登录里被调用第二次时会把 JS 主线程卡死
    // （各部分单独调用都正常，组合起来就死循环），所以这里加个护栏。
    // ------------------------------------------------------------------
    var initUserDataRan = false;

    function hookInitUserData() {
        var dm = window.dataManager;
        if (!dm || hooked.initUserData || typeof dm.initUserData !== "function") {
            return !!dm;
        }
        hooked.initUserData = true;
        var orig = dm.initUserData;
        dm.initUserData = function (data) {
            if (initUserDataRan) {
                emit("initUserData 重复调用，已跳过");
                return;
            }
            initUserDataRan = true;
            emit("initUserData 首次执行");
            return orig.apply(this, arguments);
        };
        return true;
    }

    function hookAll() {
        hookCrypt();
        hookServer();
        hookHttpc();
        hookWebSocketFactory();
        hookDataManager();
        hookUi();
        hookLogging();
        hookSdkLogin();
        hookServerRequest();
        hookLoginUi();
        hookModuleCtors();
        hookInitUserData();
        startAutoLoginWatch();
    }

    // ------------------------------------------------------------------
    // REPL
    // ------------------------------------------------------------------
    var REPL_BASE = CDN_BASE + "/hook";
    var replSeq = 0;
    var replBusy = false;
    var replEnabled = true;
    var xhrReported = false;
    var liveXhr = [];
    var transportMode = null;

    // cocos2d-js 原生环境下直接 new XMLHttpRequest() 发不出去，
    // 必须走 cc.loader.getXMLHttpRequest()；最稳的是直接用游戏自己的 httpc。
    function makeXhr() {
        try {
            if (window.cc && cc.loader && cc.loader.getXMLHttpRequest) {
                return cc.loader.getXMLHttpRequest();
            }
        } catch (e) {
        }
        try {
            return new XMLHttpRequest();
        } catch (e) {
            return null;
        }
    }

    function rawXhr(method, url, body, cb) {
        var r = makeXhr();
        if (!r) {
            cb(-1, "no xhr");
            return;
        }
        liveXhr.push(r);
        var done = false;

        function finish(status, text) {
            if (done) {
                return;
            }
            done = true;
            var idx = liveXhr.indexOf(r);
            if (idx >= 0) {
                liveXhr.splice(idx, 1);
            }
            try {
                cb(status, text);
            } catch (e) {
                emit("XHRCB ERR " + e);
            }
        }

        try {
            r.open(method, url, true);
            r.onreadystatechange = function () {
                if (r.readyState === 4) {
                    finish(r.status, r.responseText);
                }
            };
            r.onload = function () {
                finish(r.status, r.responseText);
            };
            r.onerror = function () {
                emit("XHR onerror " + url);
                finish(-1, "error");
            };
            r.ontimeout = function () {
                emit("XHR ontimeout " + url);
                finish(-1, "timeout");
            };
            r.timeout = 8000;
            // cocos2d-js 的 XHR 只接受字符串 body，传数字会静默卡住
            var payload = (body === undefined || body === null) ? null : String(body);
            r.send(payload);
        } catch (e) {
            if (!xhrReported) {
                xhrReported = true;
                emit("XHR open/send failed: " + e);
            }
            finish(-1, String(e));
        }
    }

    function httpcXhr(method, url, body, cb) {
        var h = window.httpc;
        var fn = method === "GET" ? h.sendGetRequest : h.sendPostRequest;
        var ctx = h;
        fn.call(ctx, url, body, function () {
            var a = Array.prototype.slice.call(arguments);
            if (transportMode === null) {
                emit("HTTPC cb args=" + safeJson(a));
            }
            var err = a[0];
            var data = a.length > 1 ? a[1] : a[0];
            if (err && typeof err === "object" && err.code && !a[1]) {
                cb(-1, safeJson(err));
            } else {
                cb(200, typeof data === "string" ? data : safeJson(data));
            }
        }, true);
    }

    function xhr(method, url, body, cb) {
        if (body !== undefined && body !== null && typeof body !== "string") {
            try {
                body = JSON.stringify(body);
            } catch (e) {
                body = String(body);
            }
        }
        if (transportMode === null) {
            transportMode = (window.httpc && typeof httpc.sendGetRequest === "function") ? "httpc" : "xhr";
            emit("TRANSPORT " + transportMode);
        }
        if (transportMode === "httpc") {
            try {
                httpcXhr(method, url, body, cb);
                return;
            } catch (e) {
                emit("httpc transport failed: " + e + " -> fallback xhr");
                transportMode = "xhr";
            }
        }
        rawXhr(method, url, body, cb);
    }

    var replPolls = 0;
    var replBusySince = 0;

    function replPoll() {
        // 看门狗：请求万一丢了，别把 REPL 永久锁死
        if (replBusy) {
            if (replBusySince && (Date.now() - replBusySince) > 8000) {
                emit("REPL watchdog reset (pendingXhr=" + liveXhr.length + ")");
                liveXhr.length = 0;
                replBusy = false;
            } else {
                return;
            }
        }
        if (!replEnabled) {
            return;
        }
        replBusy = true;
        replBusySince = Date.now();
        replPolls++;
        xhr("GET", REPL_BASE + "/poll", String(replSeq), function (st, txt) {
            replBusy = false;
            replBusySince = 0;
            if (replPolls <= 3) {
                emit("REPL poll#" + replPolls + " status=" + st + " body=" + brief(String(txt), 300));
            }
            if (st !== 200 || !txt) {
                return;
            }
            var cmd;
            try {
                cmd = JSON.parse(txt);
            } catch (e) {
                return;
            }
            if (!cmd || cmd.id === undefined || cmd.id <= replSeq) {
                return;
            }
            replSeq = cmd.id;
            var out = {id: cmd.id, ok: true, value: ""};
            try {
                /* jshint evil:true */
                var v = eval(cmd.code);
                out.value = safeJson(v);
            } catch (e2) {
                out.ok = false;
                out.value = String(e2);
            }
            xhr("POST", REPL_BASE + "/result", out, function () {
            });
        });
    }

    heartbeat(replPoll, 600);

    window.__oppaiHook__ = {
        version: VERSION,
        log: emit,
        flush: flush,
        safeJson: safeJson,
        afterLogin: afterLogin,
        probe: function (on) {
            probeEnabled = !!on;
            emit("PROBE " + (probeEnabled ? "on" : "off"));
        },
        repl: function (on) {
            replEnabled = !!on;
        },
        cdn: function () {
            return CDN_BASE;
        }
    };

    emit("HOOK LOADED v" + VERSION + " cdn=" + CDN_BASE +

        " setInterval=" + (typeof setInterval) +
        " XHR=" + (typeof XMLHttpRequest) +
        " cc=" + (typeof window.cc) +
        " director=" + (window.cc && cc.director ? "yes" : "no"));


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
            emit("AT.setLastFrameCallFunc 已调用 duration=" + dur);

            self.__oppaiLfFired = false;
            var wrapped = function () {
                if (self.__oppaiLfFired) { return; }
                self.__oppaiLfFired = true;
                var scB = null;
                try { scB = cc.director.getRunningScene(); } catch (e) { }
                emit("AT.lastFrame 触发 frame=" + (self.getCurrentFrame ? self.getCurrentFrame() : "?") +
                     " sceneBefore=" + (scB ? scB.getChildrenCount() : "-"));
                // 关键：原版引擎会把动画名当第一个参数传给回调
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
                }
            };
            self.__oppaiLfOnEngine = true;

            if (dur > 0) {
                var ms = Math.round((dur / 60) * 1000) + 800;
                setTimeout(function () {
                    if (!self.__oppaiLfFired) {
                        emit("AT.lastFrame 兜底触发（引擎没触发）after " + ms + "ms");
                        wrapped();
                    }
                }, ms);
            }
            return origSet.call(self, wrapped);
        };

        // play 也记一笔，方便看时序
        AT.prototype.play = function (name, loop) {
            // 记下动画名，setLastFrameCallFunc 的回调要用
            this.__oppaiAnimName = name;
            var r = null;
            try { r = origPlay.apply(this, arguments); } catch (e) { emit("AT.play ERR " + e); throw e; }
            emit("AT.play(" + name + "," + loop + ") endFrame=" + (this.getEndFrame ? this.getEndFrame() : "?"));
            return r;
        };

        emit("AT-WRAP ActionTimeline 已包装");
    })();

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

    var tries = 0;
    var timer = setInterval(function () {
        tries++;
        try {
            hookAll();
        } catch (e) {
            emit("HOOK ERROR " + e);
        }
        if (tries > 2000) {
            clearInterval(timer);
        }
    }, 250);

    var tick = 0;
    heartbeat(function () {
        tick++;
        if (tick <= 5 || tick % 50 === 0) {
            emit("TICK " + tick + " pendingXhr=" + liveXhr.length);
        }
    }, 1000);

    try {
        hookAll();
        startHeartbeats();
        // 立刻做一次自检：写文件 + 发一次 XHR
        emit("SELFTEST writable=" + (function () {
            try {
                jsb.fileUtils.writeStringToFile("selftest\n", jsb.fileUtils.getWritablePath() + "hook.log");
                return jsb.fileUtils.isFileExist(jsb.fileUtils.getWritablePath() + "hook.log");
            } catch (e) {
                return "err:" + e;
            }
        })());
        xhr("GET", CDN_BASE + "/hook/ping", "1", function (st, txt) {
            emit("SELFTEST xhr status=" + st + " body=" + brief(String(txt), 200));
        });
        replPoll();
    } catch (e) {
        emit("HOOK ERROR " + e);
    }
})();
