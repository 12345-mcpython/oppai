// ===========================================================================
// patch.js —— 《战场双马尾》私服客户端适配层（必需）
//
// 这里放的是「少了游戏就跑不对」的补丁，根因都是引擎与游戏的约定不一致：
//
//   1. ccui.helper.seekNodeByName / seekNodeByTag
//      原版 libcocos2djs.so 有这两个绑定，vanilla cocos2d-js v3.6 没有。
//      缺了 UpdateScene._init() 抛 "seekNodeByName is not a function"，
//      热更新界面建不出来 -> 一直黑屏。
//      ⚠️ 遍历顺序必须照着原版写：**层序（BFS）**，不是深度优先。
//      原版 so 里反汇编核实过（`_ZN7cocos2d2ui6Helper14seekNodeByNameEPNS_4NodeERKSs`
//      @0xaea0fd，202 字节，层队列 + 下标递增）。写成深度优先会让
//      「同名节点先命中更深那个」—— 好友面板整页崩、情报室返回键点不动，
//      两个都真踩过（见下面 11) 和 12)）。
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
//   7. 响应派发补齐（favor 家族 + 不是 updateByServer 的那几条）
//      引擎里 `src/util/server.js` 的 `responseConfig` **一次都没被派发**
//      （实测：发一个响应里带 `favor` 块的请求，`Favor.prototype.update`
//      被调用 0 次 —— 行数据、等级、红点全靠它），而 RESP-DISPATCH 原来只补了
//      「走 updateByServer」的那批模块，favor / newFavorEvent / useGiftStatus
//      这几条一直是**死的**。后果：宿舍里好感度涨了，进度条和等级要重登才动。
//
//   8. 地址改写（URL-REWRITE）
//      jsc 里的官方地址打包时只能**等长**替换（`<host>:18080` 必须 19 字节
//      → LAN IP 必须 13 个字符），而且换 IP 时会静默跳过、整包作废。
//      这里改成运行时改写 XHR / WebSocket 的 URL，jsc 保留原始地址 ——
//      地址不再有长度约束，配合 `adb reverse` 连局域网都不需要（真机适配走这条）。
//
//   9. 队伍详情页默认落到「当前队伍」（客户端自己的 off-by-one）
//      编成 →「队伍」按钮是 `new TeamDetailLayer()`，**不带队伍下标**，于是走客户端
//      `_initData` 的兜底：`_.findIndex(this.teams, {index: DEFAULT_TEAM_IDX})`，
//      而模块常量 `DEFAULT_TEAM_IDX = 1`。本服队伍 `index` 是 0 起，所以兜底落在
//      **第 2 队** —— 症状就是「一进队伍页默认停在第二页」。
//      而队伍 index 必须 0 起是客户端自己定的：`TEAM_COUNT_LIMIT = 5`（characterconfig）、
//      `Player._setCurTeamIdx` 把 curTeamIdx 夹到 [0, TEAM_COUNT_LIMIT-1] = [0,4]、
//      `Player.getCurTeam()` 又是 `findIndex{index: curTeamIdx}`、
//      `CommonTeamItem` 传的是 `getCurTeamIdx() - 1`。改服务端 index 会让第 5 队
//      在开战前被夹到第 4 队，所以服务端没有可用杠杆，只能在客户端补。
//      详见文件末尾 TEAM-DETAIL 那段。
//
//  10. 道具数量变化后补发 `item_count_updated_<key>`（顶部货币条不刷新）
//      抽卡/领奖/买东西之后，服务端和背包里的数字都对，但**顶部货币条还是旧数字**
//      （重登或重进那一层才变）。玩家反馈「我抽卡没扣我货币」就是这个。
//      根因：货币条注册的是 `bag.addCountUpdateListener(key, cb)` →
//      `item.addPropListener("item_count_updated_" + key, cb)`，而 `Bag.updateItems`
//      走的是 `item.count = n`（setter），实测这个 setter **不派发**那个事件
//      （手动 `dispatchPropEvent` 同名事件，监听方立刻收到 → 监听侧是好的）。
//      详见文件末尾 ITEM-EVENT 那段。
//
//  11. 情报室左上角返回键（`seekNodeByName` 命中了隐藏页里的同名节点）
//      症状是「菜单 → 情报室，左上角那个返回箭头点不动」，而同一屏的类型页签、
//      「升序」都能点。根因：`illustrationscommonlayer.csb` 里有**两个**
//      `returnbutton`，`_init` 的 `seekNodeByName(_ui, "returnbutton")` 取到的是
//      `playillustrationspanel/levelpanel`（两级都不可见）里那个，
//      玩家看得见的那个（`returnbuttonpanel` 下）从来没接过回调。
//      ⚠️ 这条的**根因已经由 1) 修掉了**（polyfill 改成层序后，层序会先命中
//      浅的那个 = 可见的那个），这段现在会自己跳过（`btn === this._returnBtn`）。
//      留着当兜底，防以后 polyfill 又被写回深度优先。
//      详见文件末尾 ILLUST-RETURN 那段。
//
//  12. 好友（萌友）面板整页崩 —— 12) 其实是 1) 的后果，记在这里当案例：
//      `FriendListPanel._initButtons` 找 `sendbutton` 时命中了**好友条目**里的
//      同名按钮（条目 csb 里也有 sendbutton/chargedbutton），
//      它没有 `newreseffect2` 子节点 → `sendRedDotCase.visible = false` 抛
//      TypeError → ctor 断在 `_initButtons` → 好友列表空白、左上角返回键
//      接不到回调（有按下反馈但退不出去）。修的是 1)，这段没有单独代码。
//      证据：logcat `TypeError: sendRedDotCase is null @ friendlistpanel.js:56`
//      + `this._friendListPanel is undefined @ friendlayer.js:114`。
//
//  13. 充值直接成功（PAY-SUCCESS）
//      私服没有支付渠道：`op.pay` 走到 quicksdk/yesdk/iab 之后永远没有回调，
//      点购买只会停在「充值中」。这里把 `op.pay` 换成立刻用现造的 payInfo
//      回调成功 —— 服务端 `exchange.payment` 收到就按 `table_resource_exchange`
//      发货（充值包/礼包/月卡，含首充与月卡每日金条）。
//      ⚠️ 这条是**私服行为**，不是修 bug：原版必须真付钱。要去掉就删这一段。
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
    // 地址改写：把客户端里烘死的官方地址在**运行时**换掉
    //
    // 为什么需要它 —— 客户端那几个地址是**编译进 .jsc 的原子**（长度前缀存的），
    // 打包时只能做**等长**替换，于是：
    //   * `<host>:18080` 必须正好 19 字节，也就是 **LAN IP 必须 13 个字符**
    //     （`10.210.22.230` 可以、`192.168.1.5` 不行）
    //   * 更糟的是那几个文件是**就地改写**的：换地址时替换逻辑「找不到旧串」
    //     会静默跳过。实测踩过 —— DHCP 换了 IP，整包就废了，
    //     只能从 `game/original/zcsmw-original.apk` 把文件恢复回来
    //
    // 这里改成：jsc 里**保留原始地址**（打包时不再改），网络层统一改写到下面的 base。
    // 于是：
    //   * 没有 19/25 字节约束，地址想填什么填什么（`127.0.0.1:18080` 也行）
    //   * 换服务器只改这两个常量重打包，不需要再动 jsc
    //   * 配合 `adb reverse tcp:18080 tcp:18080`（四个端口都转发）连局域网、
    //     防火墙、真机 root/hosts 全都不需要 —— 插 USB 就能跑（真机适配推荐这条）
    //
    // 拦截点（都实测过）：
    //   ① `cc.loader.getXMLHttpRequest()` —— `httpc` 全部走它，
    //      游戏服 / 调试台 hook / 公告 的请求都能拦到
    //   ② `window.WebSocket` —— 登录握手（`wsFactory` 用 window.WebSocket||MozWebSocket）
    //
    // ⚠️ 热更新那份 `project.manifest` 走的是**原生 curl**，这里拦不到 ——
    //    那份由 `script/build_apk.py` 按 JSON 重写（它是纯文本，本来就不受等长约束）。
    // ------------------------------------------------------------------
    (function installUrlRewrite() {
        var CDN = "__OPPAI_CDN_BASE__";
        var LOGIN = "__OPPAI_LOGIN_BASE__";

        // 占位符没被替换（比如手工跑了这个文件）就什么都别做，免得把人搞坏
        if (CDN.indexOf("__OPPAI") === 0 || LOGIN.indexOf("__OPPAI") === 0) {
            emit("URL-REWRITE 占位符没被替换，跳过（build_apk.py 没带 base？）");
            return;
        }

        // 原始地址里的片段 -> 换到哪个 base；scheme（http/ws）跟着原 URL 走
        var MAP = [
            ["cdn.shuangmawei.net", CDN],
            ["www.shuangmawei.net", CDN],
            ["114.55.66.97:16840", LOGIN],     // oauth / 登录
            ["114.55.66.97:14589", LOGIN]      // 分享（用不到，一起换掉）
        ];

        function fix(url) {
            if (!url || typeof url !== "string") {
                return url;
            }
            for (var i = 0; i < MAP.length; i++) {
                var at = url.indexOf(MAP[i][0]);
                if (at < 0) {
                    continue;
                }
                var hp = MAP[i][1].replace(/^[a-z]+:\/\//i, "");
                var out = url.slice(0, at) + hp + url.slice(at + MAP[i][0].length);
                vlog("URL-REWRITE " + url + " -> " + out);
                return out;
            }
            return url;
        }
        window.__oppaiFixUrl = fix;            // 给探针/调试用

        // ① XHR
        var loader = (typeof cc !== "undefined") ? cc.loader : null;
        if (loader && typeof loader.getXMLHttpRequest === "function") {
            if (!loader.__oppaiUrlRewrite) {
                loader.__oppaiUrlRewrite = true;
                var origGet = loader.getXMLHttpRequest;
                loader.getXMLHttpRequest = function () {
                    var xhr = origGet.apply(this, arguments);
                    try {
                        var origOpen = xhr.open;
                        xhr.open = function (method, url) {
                            var a = Array.prototype.slice.call(arguments);
                            a[1] = fix(url);
                            return origOpen.apply(this, a);
                        };
                    } catch (e) {
                        emit("URL-REWRITE XHR 包装失败 " + e);
                    }
                    return xhr;
                };
                emit("URL-REWRITE 已接管 XHR -> " + CDN);
            }
        } else {
            emit("URL-REWRITE 找不到 cc.loader.getXMLHttpRequest");
        }

        // ② WebSocket
        (function () {
            var OW = window.WebSocket;
            if (typeof OW !== "function" || OW.__oppaiUrlRewrite) {
                return;
            }
            var W = function (url, proto) {
                var u = fix(url);
                return proto === undefined ? new OW(u) : new OW(u, proto);
            };
            // ⚠️⚠️ **静态常量必须抄过来**。客户端 `wsHandle.send` 判的是
            //
            //     if (this.socket.readyState === WebSocket.OPEN) { ...send... }
            //     else { cc.log("WebSocket readState:" + this.socket.readyState) }
            //
            // 而那个 `WebSocket` 是 `wsFactory` 在**模块加载时捕获**的构造函数。
            // 包出来的 W 不抄 OPEN 的话它等于 undefined，判定恒为 false ——
            // send 走 else 分支只打一条日志就返回，**登录握手一个字节都发不出去**。
            // 实测症状：WS 连上了、randomKey/dhExchange/hashKey 都算完了、
            // 服务端发完欢迎包就一直阻塞在 recv，客户端停在 "WebSocket readState:1"。
            for (var k in OW) {
                try { W[k] = OW[k]; } catch (e) { }
            }
            var CONSTS = ["CONNECTING", "OPEN", "CLOSING", "CLOSED"];
            for (var i = 0; i < CONSTS.length; i++) {
                if (OW[CONSTS[i]] !== undefined) {
                    W[CONSTS[i]] = OW[CONSTS[i]];
                }
            }
            W.prototype = OW.prototype;
            W.__oppaiUrlRewrite = true;
            window.WebSocket = W;
            emit("URL-REWRITE 已接管 WebSocket -> " + LOGIN +
                 "（OPEN=" + W.OPEN + "）");
        })();
    })();

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
    //
    // ⚠️⚠️ **必须是层序（BFS），不能是深度优先** —— 这是原版的行为，
    //   2026-09-21 从原版 APK 的 lib/armeabi/libcocos2djs.so 里反汇编核实的：
    //
    //     readelf -sW out/orig-libcocos2djs.so | grep seekNodeByName
    //       _ZN7cocos2d2ui6Helper14seekNodeByNameEPNS_4NodeERKSs   00aea0fd  202
    //     arm-linux-androideabi-objdump -d --start-address=0xaea0fc ... orig-libcocos2djs.so
    //
    //   它的结构是「一个 std::vector<Vector<Node*>*> 当层队列 + 下标 r7 递增」：
    //   先比 root 自己，再扫**当前层**所有节点（顺带把它们的孩子压进队列），
    //   扫完一层才进下一层（`_M_emplace_back_aux` 压队、aea19a 处 r7++ 后
    //   跟 vector 的实时 size 比）。seekNodeByTag（0xaea059，164 字节）同一套结构。
    //
    //   写成深度优先的后果（都真踩过）：
    //     * 好友面板（`FriendListPanel._initButtons` 找 `sendbutton`）——
    //       `_initViewLayer` 先把 5 个好友条目塞进 scrollview，条目自己的 csb
    //       （friendlistitemlayer）里**也有** `sendbutton`/`chargedbutton`，
    //       而 scrollview 是 panel 的第一个子节点 ⇒ 深度优先命中的是**条目**那个，
    //       它没有 `newreseffect2` 子节点 ⇒ `sendRedDotCase.visible = false`
    //       抛 TypeError ⇒ ctor 断在 `_initButtons` ⇒ 好友面板空白、左上角返回键
    //       没接上回调（点得动但退不出去）。
    //     * 情报室返回键（见文件末尾 11) 那段）：隐藏页里的 `returnbutton` 更深，
    //       层序会先命中浅的那个 = 玩家看得见的那个。
    // ------------------------------------------------------------------
    (function () {
        var H = (typeof ccui !== "undefined" && ccui.helper) ? ccui.helper : null;
        if (!H) { emit("POLYFILL ccui.helper 不存在，跳过"); return; }

        // 层序（BFS）遍历：先自己，再一层一层往下。children 用队列摊平，
        // 顺序 = 原版（同层按父节点的遍历顺序、同父按子节点顺序）。
        function bfs(root, match) {
            if (!root) { return null; }
            var queue = [root];
            var qi = 0;
            while (qi < queue.length) {
                var node = queue[qi++];
                if (match(node)) { return node; }
                var kids = null;
                try { kids = node.getChildren ? node.getChildren() : null; } catch (e) { }
                if (!kids) { continue; }
                for (var i = 0; i < kids.length; i++) { queue.push(kids[i]); }
            }
            return null;
        }

        if (typeof H.seekNodeByName !== "function") {
            H.seekNodeByName = function (root, name) {
                return bfs(root, function (n) {
                    try { return !!(n.getName && n.getName() === name); } catch (e) { return false; }
                });
            };
            emit("POLYFILL ccui.helper.seekNodeByName 已补（层序，和原版一致）");
        }

        if (typeof H.seekNodeByTag !== "function") {
            H.seekNodeByTag = function (root, tag) {
                return bfs(root, function (n) {
                    try { return !!(n.getTag && n.getTag() === tag); } catch (e) { return false; }
                });
            };
            emit("POLYFILL ccui.helper.seekNodeByTag 已补（层序，和原版一致）");
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
    // `src/util/server.js` 里有一张 `responseConfig` 表，本意是「响应 data 里出现
    // 哪个模块的 key，就喂给对应模块的回调」：
    //
    //     responseConfig.quest  = function (res) { ... dataManager.questCenter.updateByServer(res.data) }
    //     responseConfig.favor  = function (res) { ... dataManager.favorCenter.cb4ResFavor(res) }
    //     responseConfig.player / mail / char / gacha / ...
    //
    // 但在这套引擎上实测它**一次都没被派发**。两次实测（`server.request` 第 4 个参数
    // `isBackstageRequest` 传 true / false 都试过）：
    //
    //     发 favor.setclothes -> 回 {code:200, data:{favor:{sasm:行}}}
    //     全程 Favor.prototype.update 被调用 **0** 次、cb4ResFavor 被调用 **0** 次
    //     （Favor.update 是唯一的「把行套到本地」入口，所以这个计数是决定性的）
    //
    // 于是所有「服务端推数据给客户端」都失效：主线任务领奖后列表不刷新、
    // 好感度涨了进度条不动、宿舍事件红点推不下去。
    //
    // 这里自己补一层：包住 `server.request`，成功响应里出现下面的 key 就先派发，
    // 再走原来的回调（**顺序很重要**，回调里会立刻读这些刚被更新的数据）。
    //
    // 分三张表，照抄原版 responseConfig 的三类写法：
    //   resTargets()    收**整个 res** 的（favor / 宿舍事件 / useGiftStatus）
    //   targets()       调 `updateByServer(res.data)` 的
    //   customTargets() 调别的方法名（`bag.updateItems` 之类）
    // ------------------------------------------------------------------
    (function installResponseDispatch() {
        // ① 原版里这几条回调收的是整个 res（它们自己读 res.code / res.data）
        //
        // ⚠️ 但**不能直接把整个 res 丢过去**：这些 `cb4Res*` 的 `res.data` 要的
        // 是**那一段本身**，不是外面这层 data。以 `favor.setclothes` 的响应为例：
        //
        //     data = {charKey:"sasm", favorValue:0, favor:{sasm:{...}}}
        //                                      ^^^^^ 要的就是这个
        //
        // 直接把 res 传进去的话，`cb4ResFavor` 会 `for (var i in res.data)` 拿到
        // `charKey` / `favorValue` / `favor` 三个“角色 key”，然后
        // `this._favors["charKey"].lv` 抛 `TypeError: favor is undefined`
        // （这条异常实测过，所以这里包一层 `{code, data: res.data[key]}`）。
        //
        // key 名和 res.data 里的子键名一致，所以取哪一段由 key 自己决定。
        function resTargets() {
            var dm = window.dataManager;
            if (!dm) {
                return null;
            }
            return {
                favor: [dm.favorCenter, "cb4ResFavor"],
                newFavor: [dm.favorCenter, "cb4ResNewFavor"],
                favorAsstRefreshed: [dm.favorCenter, "cb4ResFavorAsstRefreshed"],
                newFavorEvent: [dm.favorEventCenter, "cb4ResNewFavorEvent"],
                favorEvent: [dm.favorEventCenter, "cb4ResFavorEvent"],
                removedFeEventKeys: [dm.favorEventCenter, "cb4ResRemovedFeKeys"],
                useGiftStatus: [dm.player, "cb4UseGiftStatus"]
            };
        }

        // ② 只列「响应 key -> dataManager 上的模块」能一一对上、
        //    而且模块确实有 updateByServer() 的。
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

        // ③ 原版里方法名不是 updateByServer 的那几条：[模块, 方法名]
        function customTargets() {
            var dm = window.dataManager;
            if (!dm) {
                return null;
            }
            return {
                items: [dm.bag, "updateItems"],
                updateDetectSpeedCount: [dm.detect, "updateDetectSpeedCount"],
                updatemedals: [dm.medal, "updateNewMedal"],
                updateSubareaAchievements: [dm.subareaachievement, "updateSubareaAchievements"],
                updatediarys: [dm.diary, "updateDiarys"],
                updatediarysbuyinfo: [dm.diary, "updateDiarysBuyInfo"],
                deletelevels: [dm.instance, "deleteLevels"],
                updateFriendSupportSoldiers: [dm.friendSupport, "updateSoldiers"],
                deleteFriendSupportSoldiers: [dm.friendSupport, "deleteSoldiers"],
                receiveSoldierReward: [dm.friendSupport, "updateReceiveSoldierReward"],
                updateUseRecord: [dm.friendSupport, "updateUseRecord"]
            };
        }

        function call(key, pair, arg, n) {
            var mod = pair && pair[0];
            var fn = pair && pair[1];
            if (!mod || typeof mod[fn] !== "function") {
                return n;
            }
            try {
                mod[fn](arg);
                return n + 1;
            } catch (e) {
                emit("RESP-DISPATCH " + key + " 失败: " + e);
                return n;
            }
        }

        function applyResponse(res) {
            if (!res || res.code !== 200 || !res.data) {
                return 0;
            }
            var n = 0;
            var map;
            // ①②③ 里哪张表没建出来（dataManager 还没好）就整批跳过，等下一次响应
            map = resTargets();
            if (map) {
                for (var k1 in map) {
                    if (res.data[k1] !== undefined) {
                        // 包一层：这些 cb4Res* 认的是 {code, data:<那一段>}
                        n = call(k1, map[k1], { code: res.code, data: res.data[k1] }, n);
                    }
                }
            }
            map = targets();
            if (map) {
                for (var k2 in map) {
                    if (res.data[k2] !== undefined) {
                        n = call(k2, [map[k2], "updateByServer"], res.data[k2], n);
                    }
                }
            }
            map = customTargets();
            if (map) {
                for (var k3 in map) {
                    if (res.data[k3] !== undefined) {
                        n = call(k3, map[k3], res.data[k3], n);
                    }
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
        // ⚠️ 这里**不能**设重试上限。上一版是 `++tries > 240`（2 分钟）就放弃，
        // 结果冷启动（先黑屏热更新、再登录）时 `window.server` 出现得比 2 分钟晚，
        // 整个 RESP-DISPATCH 就没装上 —— 实测到过一次，表现是所有服务端推数据又全哑。
        // 500ms 查一次 `window.server` 的成本可以忽略，装上了自己就停。
        if (!window.__oppaiDispatchTimer) {
            window.__oppaiDispatchTimer = setInterval(function () {
                if (patch()) {
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


    // ------------------------------------------------------------------
    // 队伍详情页默认「当前队伍」—— 修客户端自己的 off-by-one（见文件头第 9 条）
    //
    // 反汇编依据（`game/assets/src/ui/team/teamdetaillayer.jsc`，全部实测过）：
    //
    //   ① 入口不带参数
    //        TeamMainLayer._onClickTeamButton:
    //            cc.director.getRunningScene().push(new TeamDetailLayer(), false, true)
    //        TeamDetailLayer.ctor:  this.curTeamIdx = param.teamIdx   // undefined
    //        TeamDetailLayer.onEnter: this._initData(this.curTeamIdx)
    //
    //   ② 兜底常量 = 1
    //        _initData: if (!this.curTeamIdx) {
    //                       this.curTeamIdx = _.findIndex(this.teams, {index: DEFAULT_TEAM_IDX});
    //                       this.curTeamIdx = this.curTeamIdx < 0 ? 0 : this.curTeamIdx;
    //                   }
    //      模块级常量的槽位对应关系（`getaliasedvar hops=0 slot=N` 里的 N）：
    //        slot2=ITEM_SIZE_WIDTH=150、slot3=SCOMBATANT_LIMIT=null、slot4=ARMATURE_LIMIT=11、
    //        slot5=DEFAULT_CHAR_POS="0"、**slot6=DEFAULT_TEAM_IDX=1**、slot7=ATTACK_SELECT_MAX=5、
    //        slot8=CTM=CHAR_TYPE.MECHA、slot9=HP=clone(HERO_ROLE)、slot10=CTS=CHAR_TYPE.SOLDIER、
    //        slot11=SP=clone(SOLDIER_POSITIONING)、…、slot16=seekNodeByName、slot17/18/19=三个 lambda
    //      两个独立校验：`newCharItem`(形参 5 个)/`newCusArmature`(1 个)/`updateArmatureShader`(2 个)
    //      三个 lambda 正好占 slot17/18/19；`getaliasedvar hops=1 slot=16` 被当 2 参函数调
    //      （`seekNodeByName(node, "pitchon")`）。所以 names[i] ↔ slot(i+2)，slot6 = DEFAULT_TEAM_IDX = 1。
    //
    //   ③ team.index 是**服务端下发**的（`Team.ctor`: `this._index = team.index`），本服是 0..4
    //      ⇒ findIndex 命中下标 1 ⇒ **第 2 队**。
    //
    //   ④ 下标本来就该是 0 起：`CommonTeamItem` 打开详情页时传 `getCurTeamIdx() - 1`。
    //      也就是说兜底常量该是 0；而且就算传 0 也没用 —— `if (!this.curTeamIdx)` 把 0 当
    //      「没指定」，又跳回同一个兜底。所以这里没法靠「传 0」修，得在兜底那一步动手。
    //
    // 做法：只在「没给下标 / 给的就是 0」时接管，把结果改成玩家当前队伍的下标；
    //      显式传 1..4（CommonTeamItem 的列表点击）原样不动。
    //      当前队伍下标是 0 时，客户端那句 `if (!this.curTeamIdx)` 一定会再去找
    //      `{index: 1}`，所以在那一次调用里临时把 `_.findIndex` 拧成返回 0（同步、finally 还原）。
    //
    // 还原原版行为：整块删掉即可（症状回到「队伍页默认第 2 队」）。
    // ------------------------------------------------------------------
    (function installTeamDetailDefaultTeam() {
        // 玩家当前队伍的**下标**。player.curTeamIdx 存的是队伍的 index 值，
        // 客户端 `Player.getCurTeam()` 就是这么找的，这里照抄同一套规则。
        function curTeamPos() {
            var player = null;
            try { player = window.dataManager && window.dataManager.player; } catch (e) { }
            if (!player) { return 0; }
            var teams = player.teams;
            if (!teams || !teams.length) { return 0; }
            var cur = player.curTeamIdx;
            for (var i = 0; i < teams.length; i++) {
                if (teams[i] && teams[i].index === cur) { return i; }
            }
            return 0;
        }

        function patch() {
            if (typeof window.TeamDetailLayer === "undefined" || !window.TeamDetailLayer.prototype) {
                return false;
            }
            var proto = window.TeamDetailLayer.prototype;
            if (proto.__oppaiDefaultTeam) { return true; }
            var orig = proto._initData;
            if (typeof orig !== "function") { return false; }

            proto._initData = function (teamIdx) {
                var want;
                if (teamIdx === undefined || teamIdx === null) {
                    want = curTeamPos();                            // 没指定 → 当前队伍
                } else if (teamIdx === 0) {
                    want = 0;                                       // 显式第 1 队
                } else {
                    return orig.apply(this, arguments);              // 1..4：原样
                }
                if (want > 0) {
                    return orig.call(this, want);                   // 非 0 下标可以直接传
                }
                // want === 0：`if (!this.curTeamIdx)` 会让客户端再查一次 {index: 1}，
                // 那一次查询临时改成命中下标 0（只影响这一次同步调用）。
                var lodash = window._;
                if (!lodash || typeof lodash.findIndex !== "function") {
                    emit("TEAM-DETAIL 找不到 _.findIndex，跳过（队伍页仍会默认第 2 队）");
                    return orig.apply(this, arguments);
                }
                var teams = null;
                try { teams = window.dataManager.player.teams; } catch (e) { }
                var real = lodash.findIndex;
                lodash.findIndex = function (coll, pred) {
                    if (coll === teams && pred && pred.index === 1) {
                        return 0;
                    }
                    return real.apply(this, arguments);
                };
                try {
                    return orig.apply(this, arguments);
                } finally {
                    lodash.findIndex = real;
                }
            };
            proto.__oppaiDefaultTeam = true;
            emit("TEAM-DETAIL 队伍详情页默认改成「当前队伍」（原版兜底常量 DEFAULT_TEAM_IDX=1 → 第 2 队）");
            return true;
        }

        if (patch()) {
            return;
        }
        if (!window.__oppaiTeamDetailTimer) {
            window.__oppaiTeamDetailTimer = setInterval(function () {
                if (patch()) {
                    clearInterval(window.__oppaiTeamDetailTimer);
                    window.__oppaiTeamDetailTimer = null;
                }
            }, 500);
        }
    })();

    // -----------------------------------------------------------------------
    // 10) 道具数量变化后补发 `item_count_updated_<key>`
    //
    // 症状：抽卡（或任何服务端驱动的加/扣道具）之后，**顶部货币条不刷新** ——
    //   服务端存档和客户端背包里的数字都是对的（`bag.getItemCount("100001")`
    //   已经是新值），但货币条还显示旧数字，重登 / 重进那一层才变。
    //   玩家报「我抽卡没扣我货币」说的就是这个（实测：金条 100157→99257、
    //   好人卡 98→89，背包对、货币条还挂 100157/98）。
    //
    // 根因（活客户端实测）：
    //   * 货币条 `TopCurrencyLayer._registerListener` 是
    //     `dataManager.bag.addCountUpdateListener(key, cb.bind(this))`；
    //   * `Bag.addCountUpdateListener` = `item.addPropListener(
    //         ITEM_PROP_EVENT.ITEM_COUNT_UPDATED + key, cb)`（事件名
    //     `item_count_updated_100019`，见 config/bagconfig.jsc）；
    //   * 而 `Bag.updateItems` 改数量走的是 `item.count = n` 这个 setter ——
    //     实测**不派发**上面那个事件：手动 `it.dispatchPropEvent("item_count_updated_" + k)`
    //     时监听器立刻被调用（hits=1），走 `it.count = n+1` 时 hits 不变（=1）。
    //     base `Item._setCount` 里没有派发；`Currency._setCount` 里那段派发
    //     在这套 build 上到不了（`count` 的 getter/setter 绑的是基类实现）。
    //
    // 修法：包一层 `Bag.prototype.updateItems` —— 原逻辑跑完之后，对**这次改到的
    //   每个 key** 手动派发一次 `item_count_updated_<key>`，把货币条（以及以后任何
    //   监听这个事件的 UI）叫醒。`dispatchPropEvent` 来自 `assets/src/base/entity.jsc`，
    //   `ITEM_PROP_EVENT` 是全局常量。
    // -----------------------------------------------------------------------
    (function installItemCountEvent() {
        function patch() {
            var W = window;
            if (!W.Bag || !W.ITEM_PROP_EVENT) {
                return false;
            }
            var proto = W.Bag.prototype;
            if (proto.__oppaiCountEvent) {
                return true;
            }
            var orig = proto.updateItems;
            if (typeof orig !== "function") {
                return false;
            }
            proto.updateItems = function (items) {
                var keys = [];
                try {
                    for (var k in items) {
                        keys.push(k);
                    }
                } catch (e) { /* 不是对象就当没有 */ }
                var ret = orig.apply(this, arguments);
                var ev = W.ITEM_PROP_EVENT.ITEM_COUNT_UPDATED;
                for (var i = 0; i < keys.length; i++) {
                    try {
                        var it = this._items && this._items[keys[i]];
                        if (it && typeof it.dispatchPropEvent === "function") {
                            it.dispatchPropEvent(ev + keys[i]);
                        }
                    } catch (e) { /* 单个道具失败不影响其它 */ }
                }
                return ret;
            };
            proto.__oppaiCountEvent = true;
            emit("ITEM-EVENT 补上（updateItems 后派发 item_count_updated_<key>，"
                 + "顶部货币条才会当场刷新）");
            return true;
        }

        if (patch()) {
            return;
        }
        if (!window.__oppaiItemEventTimer) {
            window.__oppaiItemEventTimer = setInterval(function () {
                if (patch()) {
                    clearInterval(window.__oppaiItemEventTimer);
                    window.__oppaiItemEventTimer = null;
                }
            }, 500);
        }
    })();

    // -----------------------------------------------------------------------
    // 11) 情报室左上角返回键 —— `seekNodeByName` 命中隐藏页里的同名节点
    //
    // 症状：菜单 → 情报室，左上角的返回箭头**点不动**；同一屏的类型页签
    //   （全部/装甲/敢死/生化/特需）和「升序」按钮都能点（它们都是可见节点）。
    //
    // 根因（2026-09-20 活客户端实测，`out/probe_return_btn2.js`）：
    //   `illustrationscommonlayer.csb` 里有**两个**叫 `returnbutton` 的节点：
    //
    //     playillustrationspanel(vis=false) → levelpanel(vis=false)
    //         → returnbutton   ← `ccui.Layout`，150x100，世界坐标 (830,476)
    //     returnbuttonpanel(vis=true) → returnbutton
    //         → 左上角看得见的那个 `ccui.Button`，100x100，世界坐标 (8,543)
    //
    //   `Illustratedcommonlayer._init` 取的是 `seekNodeByName(_ui, "returnbutton")`，
    //   它按**深度优先取第一个**同名节点，而遍历顺序里 `playillustrationspanel`
    //   排在 `returnbuttonpanel` 前面 ⇒ `_returnBtn` 拿的是隐藏页里那个，
    //   `register()` 把 `onClickReturn`（= `getRunningScene().pop(true)`）接在它身上，
    //   玩家点的那个箭头**从来没接过任何回调**。
    //   （`ccuiManager.addMenuItemEvent` 遇到 null 会
    //   `cc.warn("ccuimanager.addMenuItemEvent error")`，logcat 里没有这条
    //   ⇒ 不是"没找到节点"，是"找到了错的节点"。）
    //
    // 做法：包一层 `_init`，原逻辑跑完之后把「可见的那个返回键」再接一次同一个
    //   `onClickReturn`，并把 `_returnBtn` 指过去。隐藏页那个保持原样 ——
    //   切到「玩法」页时它自己那套还在。
    //
    // 还原原版行为：整块删掉即可（症状回到「情报室左上角点不动」）。
    // -----------------------------------------------------------------------
    (function installIllustratedReturnButton() {
        function patch() {
            var W = window;
            if (!W.Illustratedcommonlayer || !W.Illustratedcommonlayer.prototype) {
                return false;
            }
            var proto = W.Illustratedcommonlayer.prototype;
            if (proto.__oppaiReturnBtn) {
                return true;
            }
            var orig = proto._init;
            if (typeof orig !== "function") {
                return false;
            }
            proto._init = function () {
                var ret = orig.apply(this, arguments);
                try {
                    var helper = W.ccui && W.ccui.helper;
                    if (!helper || typeof helper.seekNodeByName !== "function" || !this._ui) {
                        emit("ILLUST-RETURN 没有 ccui.helper.seekNodeByName，跳过");
                        return ret;
                    }
                    var panel = helper.seekNodeByName(this._ui, "returnbuttonpanel");
                    var btn = panel ? helper.seekNodeByName(panel, "returnbutton") : null;
                    if (!btn || btn === this._returnBtn) {
                        return ret;
                    }
                    this._returnBtn = btn;
                    var sound = W.table_view_sound && W.table_view_sound["default"];
                    W.ccuiManager.addMenuItemEvent(btn, this.onClickReturn.bind(this), true, sound);
                    emit("ILLUST-RETURN 情报室返回键改接到可见箭头上（原版接的是隐藏页里那个同名节点）");
                } catch (e) {
                    emit("ILLUST-RETURN 接返回键失败: " + e);
                }
                return ret;
            };
            proto.__oppaiReturnBtn = true;
            emit("ILLUST-RETURN 补丁已安装");
            return true;
        }

        if (patch()) {
            return;
        }
        if (!window.__oppaiIllustReturnTimer) {
            window.__oppaiIllustReturnTimer = setInterval(function () {
                if (patch()) {
                    clearInterval(window.__oppaiIllustReturnTimer);
                    window.__oppaiIllustReturnTimer = null;
                }
            }, 500);
        }
    })();

    // -----------------------------------------------------------------------
    // 13) 充值直接成功（私服没有支付渠道）
    //
    // 原版点「购买」之后是这样一条链（反汇编 `src/data/exchangecenter.jsc`
    // + `src/data/payment/*.jsc`）：
    //
    //   ExchangeCenter.payment(key)                      // 商品 key
    //     -> exchange.judgeexchangestate {key}           // state 0 才继续
    //     -> productKey = table_exchange_item[key].param_1     // "pay030" / "bundle02001"
    //     -> (月卡先 exchange.checkmonthcard)            // remainDay > 3 就中止
    //     -> clientOrderId = op.getClientOrderId()       // uuid.v4()
    //     -> _payment.payment(...)  ->  op.pay(...)      ★ 这里往下就是渠道 SDK
    //     -> payInfo 回来 -> Payment.exchangePay(payInfo) -> exchange.payment {payInfo}
    //
    // 我们这个包把渠道 SDK（QuickSDK / 百度 / …）整个删了，`op.pay` 走到
    // `quicksdk.pay` / `yesdk.pay` / `iab.pay` 之后就再也没有回调 ——
    // 界面停在「充值中」、服务端一条请求都收不到。
    //
    // 做法：把 `op.pay` 换掉，**立刻**用现造的 `payInfo` 回调成功。
    //   * 只认 `table_payment[productKey]`（不在表里就 toast 1714「购买内容不存在」，
    //     和原版一致）；
    //   * `payInfo` 里带 `clientOrderId` / `productKey` / `productId` / `price` /
    //     `receipt` / `txid` —— 服务端只按 `productKey` 认商品（116 个商品的
    //     `param_1` 唯一），`receipt`/`txid` 是给"像真的支付凭证"留的位；
    //   * 用 `setTimeout(..., 0)` 异步回调，别在同一个栈里递归下去。
    //
    // 这样客户端自己那套逻辑全都照跑：toast「充值已受理」(1701)、
    // `_addOrder` 记账、`exchange.payment` 发货、`_deleteOrder` 清单、`_isPaying`
    // 复位、成功 toast（`table_dictionary[1702]`）。
    //
    // 还原原版：整块删掉（点购买就走真渠道，私服里等于没反应）。
    // -----------------------------------------------------------------------
    (function installPaySuccess() {
        function patch() {
            var W = window;
            if (!W.op || typeof W.op.pay !== "function") {
                return false;
            }
            if (W.op.pay.__oppaiPaySuccess) {
                return true;
            }
            W.op.pay = function (productKey, clientOrderId, target, cb) {
                var tbl = W.table_payment || {};
                var row = tbl[productKey];
                if (!row) {
                    if (W.ccuiManager && W.table_dictionary) {
                        W.ccuiManager.toast(W.table_dictionary[1714]);
                    }
                    emit("PAY-SUCCESS " + productKey + " 不在 table_payment 里，已中止");
                    return;
                }
                var payInfo = {
                    clientOrderId: clientOrderId,
                    productKey: productKey,
                    productId: row.id,
                    productName: row.name,
                    price: row.price,
                    receipt: "oppai",
                    txid: clientOrderId
                };
                emit("PAY-SUCCESS " + productKey + "（" + row.name + " " + row.price + "元）"
                     + " 直接成功 —— 私服没有支付渠道");
                setTimeout(function () {
                    if (typeof cb === "function") {
                        cb(null, payInfo);
                    }
                }, 0);
            };
            W.op.pay.__oppaiPaySuccess = true;
            emit("PAY-SUCCESS 补丁已安装（点购买直接成功并发货）");
            return true;
        }

        if (patch()) {
            return;
        }
        if (!window.__oppaiPayTimer) {
            window.__oppaiPayTimer = setInterval(function () {
                if (patch()) {
                    clearInterval(window.__oppaiPayTimer);
                    window.__oppaiPayTimer = null;
                }
            }, 500);
        }
    })();

})();
