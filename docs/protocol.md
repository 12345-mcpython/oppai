# 协议细节

所有结论都来自两种手段：**jsc 反汇编**（`script/jsc_disasm.py`）和
**运行时观察**（`server/client/hook.js` 的探针日志 / REPL）。

## 本节目录

- [1. 远程配置（CDN）](#1-远程配置cdn)
- [2. 网关状态](#2-网关状态)
- [3. 账号 oauth](#3-账号-oauth)
- [4. WebSocket 登录握手](#4-websocket-登录握手)
- [5. 业务协议（HTTP POST）](#5-业务协议http-post)
- [6. `agent.getlogindata`](#6-agentgetlogindata)
- [7. 切主场景](#7-切主场景)
- [7.5 新号起名（player.naming）](#75-新号起名playernaming)
- [8. 客户端内部数据模型（从反汇编读出来的接口）](#8-客户端内部数据模型从反汇编读出来的接口)
- [9. 任务（quest.*）](#9-任务quest)
- [10. 关卡 / 副本（instance.*）](#10-关卡--副本instance)
- [11. 军士养成（char.*）](#11-军士养成char)
- [12. 好友（friend.*）](#12-好友friend)
- [13. 勋章 / 头像 / 衣柜（medal.*）](#13-勋章--头像--衣柜medal)
- [14. 分享 / 礼包兑换（share.* / convert.*）](#14-分享--礼包兑换share--convert)
- [15. 助战（friendsupport.*）](#15-助战friendsupport)
- [16. 私密剧情（diary.*）](#16-私密剧情diary)

---

## 1. 远程配置（CDN）

**请求**

```
GET http://<cdn>/android/v2.2.0/config/config.txt?data=null
```

路径来自 `assets/srcex/urlconfig.jsc`：

```js
URL_PATH      = "android/v2.2.0"
APP_CONFIG_URL = "http://cdn.shuangmawei.net/" + URL_PATH + "/config/config.txt"
AD_CONFIG_URL  = ... + "/ad/ad.txt"
AD_DIR_URL     = ... + "/ad/"
NOTICE_URL     = ... + "/notice/index.html"
```

**响应**：客户端 `ex/remoteconfig.js` 的 `getRemoteConfig(key)` 直接读**顶层字段**。

```jsonc
{
  "version": { "2.2.0": { /* 键必须是 op.appVersion */ } },
  "update":  { "code": 0, "msg": "" },
  "servers": [ /* 服务器列表 */ ],
  "table_dictionary": { /* 覆盖本地文案 */ }
}
```

**怎么确定的**：给全局 `op`（util 和 remoteConfig 的共享命名空间）套 `Proxy`
记录属性访问，再用返回值 `Proxy` + `Error().stack` 定位。日志：

```
OPGET appVersion = "2.2.0"
RCFG get(version) => "2.2.0"
VERPROP .2.2.0   stk=UpdateScene<._startUpdate@update.js:251:35
RC   key=update  val=undefined
POPUP ["获取更新信息错误，请重新打开游戏",...,"校验版本",4]
```

`VERPROP .2.2.0` 说明代码在 `version[appVersion]` 取值 —— 所以 `version`
必须是**以 appVersion 为键的字典**，不是字符串。

服务器列表每项用到的字段（`server.js`）：
`id / name / host / gatePort / loginPort / port / logindKey / status / gamedStatus / playerInfo`。

---

## 2. 网关状态

```
GET http://<host>:<gatePort>/get_login?key=<logindKey>
```

响应：

```json
{"code":0,"msg":"","gamedStatus":0,"loginPort":8080,"port":10003}
```

客户端把 `gamedStatus` / `loginPort` / `port` 写回服务器列表项。
`code != 0` 会被判定为失败（日志 `[sync serv status] failed, msg: ...`）。

---

## 3. 账号 oauth

客户端 `server.oauth(routh, msg, cb)`：

```
GET <OAUTH_HOST>/oauth/<routh>?<key=value&...>
```

`<OAUTH_HOST>` 是**硬编码**在 `src/util/server.jsc` 里的
`http://114.55.66.97:16840`，所以要打包时原地替换（25 字节）。

| routh | 说明 |
|-------|------|
| `request_token` | 登录换 token |
| `register` | 注册（带 `active_code`） |

响应 `{code, msg, data:{account, password, token, userId}}`。

反汇编 `data/user.js` 的 `User.requestToken`：

```js
requestToken: function (account, password, cb) {
    var that = this;
    server.oauth('request_token', {account: account, password: password}, function (err, data) {
        if (err) { cb(err); return; }
        if (data.code != 0) { cb(data.code); return; }
        that._account = ...; that._password = ...; that._token = ...;
        that._save();
        cb(...);
    });
}
```

---

## 4. WebSocket 登录握手

```
S -> C   base64(欢迎包)
C -> S   base64(clientKey)
S -> C   base64(serverKey)
C -> S   base64(hmac64(hashKey(seed#clientKey), secret))
C -> S   base64(desEncode(secret, utf8(loginInfoJson)))
S -> C   base64(desEncode(secret, utf8({code,session,gameServUrl,userId,data})))
```

### 4.1 服务端要主动先发一条

客户端 `util/websocketc.js` 的 `wsHandle.init` 只挂了
`onmessage` / `onerror` / `onclose`（**没有 onopen**），
`onSuccCb` 是由第一条消息触发的。所以服务端必须先发一条 base64 欢迎包。

另外请求头里有 `Sec-WebSocket-Protocol: default-protocol`，
按 RFC6455 服务端必须回显一个子协议，否则客户端会主动断开。

### 4.2 真实抓包

```
CRYPT base64Decode(e30=)                            => 7b7d              "{}"
CRYPT randomKey()                                   => 7f813b99976732bd
CRYPT dhExchange(7f813b99976732bd)                  => 6e58ce797a29f3f4
CRYPT hashKey(7b7d23 6e58ce797a29f3f4)              => d784c7c8e1d79a2c
CRYPT base64Decode(AQAAAAAAAAA=)                     => 0100000000000000
CRYPT dhSecret(0100000000000000, 7f813b99976732bd)  => 0100000000000000
CRYPT hmac64(d784c7c8e1d79a2c, 0100000000000000)    => d3684ec0fa8d0f4a
CRYPT desEncode(0100000000000000, <319字节登录JSON>) => <320字节密文>
```

注意 `hashKey` 的入参是 `"{}#" + clientKey`（`7b7d23` = `{` `}` `#`）。

### 4.3 DH 单位元

`crypt.dhExchange` / `dhSecret` 是自研实现，但满足正常 DH 群性质：

```
X = dhExchange(00 00 00 00 00 00 00 00) = 01 00 00 00 00 00 00 00
dhSecret(X, *) == dhSecret(*, 00…00) == X
```

服务端公钥设成 `X`，则共享密钥必然等于 `X`，不需要知道 p / g。

### 4.4 登录信息（客户端 → 服务端）

```json
{
  "deviceId": "...", "os": "Android", "platform": 234592,
  "platformName": "ye", "channel": 14, "extra": "unknown",
  "appVersion": "2.2.0", "buildVersion": "51", "patchVersion": "2.2.0",
  "ip": "...", "xgDeviceToken": "0", "server": "gamed_1",
  "data": { "uk": null, "token": null, "productCode": "..." }
}
```

### 4.5 登录响应

```json
{
  "code": 200, "msg": "",
  "session": "<hex>", "gameServUrl": "10.110.29.230:10003", "userId": 1,
  "data": { "session": "...", "gameServUrl": "...", "userId": 1 }
}
```

`session` / `gameServUrl` 顶层和 `data` 里各给一份，兼容两种读法。
`gameServUrl` 必须匹配 `^((25[0-5]|...)\.){3}(...):\d{3,5}$`（必须是 IP:PORT）。

> #### ⚠️ `code` 必须是 **200**
>
> 反汇编 `User.login` 的回调：
>
> ```js
> if (result == undefined) { cb(table_dictionary[622]); return; }
> if (result.code !== 200) {
>     cc.log('error on login, code :' + result.code);
>     cb({code: 299, data: table_dictionary[623] + result.code}, result);
>     return;
> }
> ```
>
> 回 0、或者压根不带 `code`，都会走错误分支。表现是：
> **点「开始游戏」先弹一个「温馨提示」，然后才进游戏** ——
> 弹窗是这条链报的错，进游戏是探针那条链干完的活。
>
> 改成 200 之后，客户端自己那条链就完全跑通了：
>
> ```
> server.login → playerLogin → agent.getlogindata → cb4AfterLogin
>              → initUserData → LoginLayer._enterMain → MainScene
> ```
>
> 探针里的 `afterLogin`（手动补登录收尾）也就可以关掉了 ——
> 否则两条链都跑 `initUserData`，会二次初始化把状态搞坏：
>
> ```
> CB4 ERR    TypeError: this._lvEncrp is null
> SWITCH ERR TypeError: this._teams is null
> ```

---

## 5. 业务协议（HTTP POST）

```js
// server.js
// reqPack(route, msg, reqId)
data = crypt.desEncode(key, crypt.utf16To8(JSON.stringify({route, msg, reqId})))
body = crypt.base64Encode(data)

// resUnpack(data, key)
if (data.indexOf('"code":') >= 0) return JSON.parse(data)   // 明文错误包
return JSON.parse(crypt.utf8To16(crypt.desDecode(key, crypt.base64Decode(data))))
```

* 请求体 = `base64(desEncode(secret, utf8(JSON.stringify({route, msg, reqId}))))`
* 成功响应 = `base64(desEncode(secret, utf8(JSON.stringify(payload))))`
* **错误响应可以是明文 JSON**（只要含 `"code":`，客户端就直接当 JSON 解析）

### 5.0 请求体前面还有一段 session（容易被探针掩盖）

真实请求体是**两段 base64 拼起来的**：

```
body = base64(" " + sessionId)  +  base64(desEncode(secret, {route, msg, reqId}))
        └─ 33 字节 → 44 个 base64 字符，没有 '=' ─┘
```

看到的现象是服务端 `unpack_request` 解密失败，客户端弹
`温馨提示 {"code":1,"msg":"bad request"}`。

这段以前一直没被发现，因为 `server/client/probe.js` 早先的版本**把 `server.request` 整个
重写了**（自己拿 `httpc` 重发），压根不带这段前缀；探针改成「只包一层」之后真实
格式才暴露出来。

服务端处理见 `gamesrv/gameproto.py::split_session_field`：

```python
head, rest = text[:44], text[44:]
sid = base64.b64decode(head)      # b" " + 32 位 hex
payload = unpack_request(rest)    # 再用该 session 的 secret 解
```

### 5.2 响应派发（responseConfig）在这套引擎上不生效
`src/util/server.js` 里有一张 `responseConfig`，本意是「响应 `data` 里出现哪个模块的
key，就喂给对应模块的回调」：

```js
responseConfig.quest  = function (res) { ... dataManager.questCenter.updateByServer(res.data) }
responseConfig.favor  = function (res) { ... dataManager.favorCenter.cb4ResFavor(...) }
responseConfig.player / mail / char / gacha / favorEvent / useGiftStatus / ...
```

但在这套引擎上**它一次都没被派发**。两次独立实测：

```
① server.request('player.getdata') -> {player:{...}}
     └─ 包了 Player.updateByServer 打日志，一次都没进

② server.request('favor.setclothes', {charKey, itemKey}, cb, false)   // 第 4 个参数
     -> {code:200, data:{charKey, favorValue, favor:{sasm:行}}}        // true / false 都试过
     └─ Favor.prototype.update 调用 0 次、cb4ResFavor 调用 0 次
```

②是决定性的：`Favor.update()` 是「把服务端那一行套到本地」的**唯一**入口，
它没被调用就说明行数据（等级、经验、衣服、已读位）根本没进客户端。

后果是**所有「服务端推数据给客户端」都失效**：主线任务领奖后列表不刷新、
好感度涨了进度条和等级要重登才动、宿舍事件红点推不下去。

客户端 `server/client/patch.js` 里的 `RESP-DISPATCH` 补了这一层：包住 `server.request`，
成功响应里出现已知 key 就先派发，再走原来的回调（**顺序关键**：回调里会立刻读这些
刚更新的数据，例如 `Instance.finishLevel/<` 里 `rank = this._updatePlayer(...)`）。

⚠️ 补的时候有两个坑，都不是靠「照着注释抄」能发现的：

1. **不能只补 `updateByServer` 那一批**。原版 `responseConfig` 里有三条写法完全不同：
   收**整个 `res`** 的（`favor` / `newFavor` / `favorAsstRefreshed` / `newFavorEvent` /
   `favorEvent` / `removedFeEventKeys` / `useGiftStatus`）、调 `updateByServer(res.data)` 的、
   以及方法名各不相同的（`bag.updateItems` / `medal.updateNewMedal` / `diary.updateDiarys`
   / `friendSupport.updateSoldiers` …）。只补中间那批，好感度这一整条就是死的。

2. **收 `res` 的那批不能把整个 `res` 直接丢过去**。它们要的 `res.data` 是**里面那一段**：

   ```js
   // favor.setclothes 的响应
   data = {charKey:"sasm", favorValue:0, favor:{sasm:{...}}}
   //                                  ^^^^^ 才是 cb4ResFavor 要的 data
   ```

   直接传 `res` 的话，`cb4ResFavor` 会 `for (var i in res.data)` 把 `charKey` /
   `favorValue` / `favor` 当成三个角色 key，然后
   `this._favors["charKey"].lv` 抛 `TypeError: favor is undefined`
   （实测到过这条异常）。patch.js 里因此统一包一层 `{code: res.code, data: res.data[key]}`。

3. 顺带：RESP-DISPATCH 原来有「重试 240 次（2 分钟）就放弃」的上限。冷启动
   （先黑屏热更新、再登录）时 `window.server` 可能比 2 分钟更晚出现，整个补丁就**没装上**
   —— 实测踩到过一次（表现是所有推数据又全哑、但引导跳过/WebView 这些照样生效）。
   现在不设上限，装上自己停。

4. **改了背包就要在响应里带 `items` 块**（`patch.js` 的 customTargets：
   `items -> dm.bag.updateItems(res.data.items)`）。客户端 `Bag` 是**登录时缓存**的，
   不带这个块的话东西进了存档但界面不动（背包/顶部货币条要重登才刷新）。

   ⚠️ 但**新入手的道具不能塞进去**：`Bag.updateItems` 是
   `this._items[key].count = n`，key 不在客户端那份 bag 里就是 `undefined.count` → TypeError。
   服务端用 `items.changed_block(player, known_keys)` —— `known_keys` 传这次改动**之前**
   的 key 集合，只回那些（新道具照常进存档 + 进奖励弹窗，重登才会出现在背包列表里）。

### 5.1 成功码是 200

反汇编 `dataManager.playerLogin/</<`：

```
58  getarg 1 (data) / 61 getprop "code" / 66 setlocal 0
71  getlocal 0 / 75 uint16 200 / 78 stricteq / 79 ifeq -> 119
84  cb4AfterLogin(null, data.data)
119 cb4AfterLogin(data, null)      // ← 把响应当错误
```

```js
function (err, data, reqTime, reqFinishTime) {
    this.doLoginReqTime = reqTime;
    this.doLoginReqFinishTime = reqFinishTime;
    if (err) { cb4AfterLogin(err, null); return; }
    var code = data.code;
    if (code === 200) cb4AfterLogin(null, data.data);
    else              cb4AfterLogin(data, null);   // 弹「温馨提示」显示 JSON
}
```

`Player.updateGuideMark` 的回调同样判断 `data.code == 200`：

```
10  getarg 1 (data) / 13 getprop "code" / 18 uint16 200 / 21 eq / 22 ifeq -> 74
27  _this._setGuideMark(mark)
```

不是 200 就永远不更新本地 mark，于是**无限重发**同一个请求。

---

## 6. `agent.getlogindata`

反汇编 `dataManager.initUserData`（984 字节）读出来的完整逻辑：

```js
dataManager.initUserData = function (data) {
    syncTime(data.agent, this.doLoginReqTime, this.doLoginReqFinishTime);
    this.player           = new Player(data.player);
    this.instance         = new Instance(data.instance);
    this.bag              = new Bag(data.item);
    this.character        = new CharCenter(data.char);
    this.gacha            = new Gacha(data.gacha);
    this.mailbox          = new Mailbox(data.mail);
    this.actQuestCenter   = new ActQuestCenter(data.actquest);
    this.questCenter      = new QuestCenter(data.quest);
    this.favorCenter      = new FavorCenter(data.favor);
    this.favorEventCenter = new FavorEventCenter(data.favorevent);
    this.friend           = new Friend(data.friend);
    this.exchangeCenter   = new ExchangeCenter(data.exchange);
    this.talentCenter     = new TalentCenter(data.talents);
    this.signCenter       = new SignCenter(data.sign);
    this.shop             = new Shop(data.shop);
    this.arenaCenter      = new ArenaCenter(data.arena);
    this.rank             = new Rank(data.rank);
    this.score            = new Score(data.score);
    this.society          = new Society(data.society);
    this.societyClg       = new SocietyClg(data.societyclg);
    this.bossCenter       = new BossCenter(data.boss);
    this.chat             = new Chat(data.chat);
    this.detect           = new Detect(data.detect);
    this.medal            = new Medal(data.medal);
    this.equipmentCenter  = new EquipmentCenter(data.equipment);
    this.share            = new Share(data.share);
    this.subareaachievement = new SubareaAchievement(data.subareaachievement);
    this.consumeActivity  = new ConsumeActivity(data.consumeactivity);
    this.diary            = new Diary(data.diary);
    this.friendSupport    = new FriendSupport(data.friendsupport);
    this.noviceQuestCenter = new NoviceQuestCenter(data.novicequest);

    this.player.setCharacter(this.character);
    this.player.initTeams();
    this.player.initAsst();

    guideManager.init();
    uiLayoutManager.init();
    this.player.initModuleState();
    this.player.initXgNotifications();

    this.chat.init(data.agent.msgCenterAddr, data.agent.worldChatLimit);

    if (cc.sys.os == cc.sys.OS_ANDROID) {
        this.share.getWechatAppId();
    }
};
```

### 6.1 `data.agent`

**必须直接带 `timeSec` / `timezoneOffset`**，因为 `syncTime` 读的是：

```js
syncTime = function (timeObj, reqTime, reqFinishTime) {
    var now = Date.now();
    if (reqTime && reqFinishTime) now += Math.round((reqFinishTime - reqTime) / 2);
    util.setClientTimeDifference(now - timeObj.timeSec * 1000);   // ← 直接 .timeSec
    util.setServerTimezoneOffset(timeObj.timezoneOffset);
};
```

`timeSec` 拿不到就是 `NaN`，后面会把 JS 主线程跑死（表现为「点哪都没反应」）。

### 6.2 队伍

`Player.initTeams` 用 `data.teams` 建 `Team`：

```js
new Team(teamData, this._character, index)
```

`Team._initData` 会读 `dataManager.character.mechas` 和 `charType`
（`CHAR_TYPE = {HERO:"h", SOLDIER:"s", MECHA:"m"}`），
所以 `char` 里必须有对应的 hero / mecha，否则 `this._hero is undefined`。

`TEAM_COUNT_LIMIT = 5`。

主角/机甲 key 取自客户端配置表（可用 REPL 查）：

```
Object.keys(table_hero)    -> ["hadf","haysdn"]        // hadf = 阿呆芙·洛玛
Object.keys(table_mecha)   -> ["madfewt","madflj",...] // madflj = 六酱
```

### 6.3 时间字段是字符串

`Player.ctor` 会用 `util.getTimeByDateStr()` 解析，所以
`actionPointTime` / `createTime` / `worldChatTime` 等必须是
`"YYYY-MM-DD HH:mm:ss"` 字符串，不能是时间戳。

### 6.4 分区成就（`subareaachievement.*`）

**条件判定全在客户端** —— 这一条很重要，不然会想当然地在服务端复刻那 84 条条件。
`game/assets/src/manager/subareaachievementmanager.jsc` 里：

```js
// 普通战斗（不是分区关也走这里）
formatBattleInfo(battleResult, battleId, team):
    newParam = battleResult.battleInfo            // 补 victory / battleId / missleName
    checkAchievements(newParam)                   // 条件类型就是方法名：
                                                  // 1003 击杀 / 1004 耗时 / 1005 机甲炮弹 /
                                                  // 1008 全程血量 / 1020 全区勘察 / 1021 掉落
    return {time, battleId, victory,
            modifyAchievements: {<id>: {progress, progressInfo, countKey, complete}},
            newAchievements:    ["100101", ...]}  // 玩家本地**还没有**的成就行
```

返回值就是 `instance.finishlevel` 请求体里的 `subareaInfo`（实测日志）：

```json
"subareaInfo": {"time": 22966.67, "battleId": "100102", "victory": true,
                "modifyAchievements": {}, "newAchievements": ["100101", "..."]}
```

所以服务端协议只有三块：

| 方向 | key / route | 形状 |
|---|---|---|
| 登录块 | `data.subareaachievement` | `{"achievements": {"<id>": 行}}` —— **map**（客户端 `_initData` 直接赋值给 `_achivevements`） |
| 结算推送 | `instance.finishlevel` 回包 `data.updateSubareaAchievements` | `[行, ...]`（客户端 RESP-DISPATCH 的 customTargets 按 `list[i].id` 覆盖本地行） |
| 领奖 | `subareaachievement.receivereward` `{achievementId}` | 成功 → `data` = 「道具key → 数量」map（直接进 `popupReward`）；失败 → `code` = 201~206 |

行形状（客户端 `SubareaAchievement.createAchievement`）：

```js
{id: "100101", progress: 0, progressInfo: {}, isReceiveReward: 0}
// completeTime 由服务端在收到 modifyAchievements.complete 时写
```

界面完全由这几个字段驱动（`SubareaChapterRewardLayer._getSortIdx`）：
没有 `completeTime` → 未完成（显示 progress/times 进度）；`isReceiveReward` → 已领；
其余 → 可领（显示领取按钮）。

⚠️ 两个坑：

1. **登录块里的行只能包含 `table_subarea_achievement` 里有的 id** ——
   客户端 `_initSubareaAchievementInfo()` 是「遍历玩家行 → 查表拿 `sub_area`」，
   表里查不到的 id 会 `info.sub_area` 抛 TypeError，整个成就页打不开。
2. **领奖回包的 `data` 只能是奖励 map** ——
   `SubareaChapterRewardLayer._receiveRewardSucc(achievementId, res.data)` 直接
   `ccuiManager.popupReward(util.objectToArray(res.data))`，掺别的键会被当成道具。

失败码（客户端 `SubareaAchievement.ERROR_CODE`，反汇编模块体）：

```
201 PARAM_ERROR            202 UPDATA_DB_ERROR       203 ACHIEVEMENT_NOT_EXIST
204 REWARD_RECEIVED        205 REWARD_NOT_EXIST       206 RECEIVE_REWARD_ERROR
```

客户端按码弹 `table_dictionary`：201→10000、202→10001、203/205→4200、
204→4201、206→4202。

### 6.5 黑市交易所 / 充值页（`exchange.*`）

客户端里叫 `ExchangeCenter`，是**一整个商店中枢**：月卡 / 充值礼包（IAP）、
金条↔萌钞、行动力 / BP / 卡槽兑换，全走它。

**登录块**：`data.exchange` = 一整张 map，值是玩家那一段的行：

```js
_exchangeData["300001"] = {
    exchangeKey: "300001",          // 当前档位（见下）
    todayExchangeTimes: 2,          // 今天换了几次 —— 决定下一次用哪一档
    totalExchangeTimes: 5,
    lastExchangeTimeSec: 1789885973 // 客户端拿它跟 table_constant.common_reset_time 比，自己清天
}
```

**档位规则**（`ExchangeCenter.getExchangeInfoKey`，服务端必须照抄）：

```js
var times      = 行 ? 行.todayExchangeTimes + 1 : 1;   // 这一次是第几次
var exchangeKey = "exchange_key_" + times;             // EXCHANGE_KEY_PRE
if (!table_exchange_item[key][exchangeKey])             // 表里没有这一档
    exchangeKey = "exchange_key_default";               // 就一直用默认档
var info = table_resource_exchange[exchangeKey];        // 这一档的消耗/产出
```

`table_resource_exchange[档位]` 的字段：

```
spend_key / spend_count                  花什么、花多少
receive_key_<i> / receive_count_<i>      给什么、给多少（i = 1..15，表里最多用到 6）
presented_key / presented_count          额外赠送
```

⚠️ 两个坑：

1. `receive_count = -1` 是「**补满**」哨兵（400001 的说明就是「每次可回满行动力。」，
   700001 同理补 BP）。补满上限取玩家自己的 `maxActionPoint`（行动力）或
   `table_item[<key>].limit_count`（BP = 6）—— 直接按 -1 发道具会变成"倒扣"。
2. 客户端显示的数值是 `floor(数值 * percentage / 100)`，`percentage` 默认 100，
   **只有第一次兑换且表里有 `first_exchange_percentage`** 时才变
   （`getInfoByKey` 里那段）。服务端要按同一条规则折算，否则界面显示的和实际给的对不上。

**路由**：

| route | 请求 | 回包 |
|---|---|---|
| `exchange.getexchangebycategory` | `{category}`（只有礼包页发 `"80"`） | `data` = 该分类的商品 key 列表 |
| `exchange.exchange` | `{key}`（**只带 key，档位服务端算**） | `data` = 新的那一段行（带 `exchangeKey`），客户端 `update(data)` 合并 |
| `exchange.exchangecrystal` | `{count}`（扭蛋钻石直购） | 私服扭蛋未开放 → 非 200 |
| `exchange.checkmonthcard` | `{}` | `data = {remainDay}`（注意不是 `days`） |
| `exchange.judgeexchangestate` | `{key}`（IAP 下单前） | `data = {state, item}`，`state≠0` 客户端按 1/2/3 弹 `table_dictionary` |
| `exchange.payment` / `exchange.checkorder` | IAP 回执 | 私服**故意非 200**（`201` 的文案就是「充值成功」，回 200 会被当成充值成功） |

**失败码**（客户端 `EXCHANGE_ERR_CODE_DICT`，实测取的值）：

```
201 充值成功   202 未知兑换类型   203 购买卡槽次数已达上限
204 今天购买次数已用完   205 资源不够啦……OAQ   405 更新数据错误
```

非 200 时客户端 `ccuiManager.toast(EXCHANGE_ERR_CODE_DICT[code])`，
所以码必须是表里这几个，**不能自己编**（编了 `toast(undefined)`）。

### 6.6 功能开启标记（`moduleOpenMark`）

进主界面时客户端会弹「功能开启」动画，触发链是：

```js
MainLayer._updateAnimation()  →  moduleManager.popModuleOpen()
popModuleOpen():  list = dataManager.player.updateModuleState()   // 见下
                  if (!list) return;                              // 空 = 不弹
                  op.touchEnabled = false;
                  for (each) ccuiManager.createModuleUnlockEffect(module.unlockDesc);

Player.updateModuleState():
    for (k in this._moduleState) {
        var module = this._moduleState[k];
        if (!module.isOpened) {                       // ★ 要不要弹就看它
            if (等级/关卡/引导条件都满足) {
                if (module.unlockDesc) updateList.push(module);
                requestList.push(module.markIndex);
                module.isOpened = true;
            }
        }
    }
    if (requestList.length) this.requestUpdateModuleOpenMark(requestList);
    return updateList;

Player.initModuleState():   module.isOpened = moduleOpenMark[module.markIndex];
                            // ↑ 这个 moduleOpenMark 是**登录块**里的字段
```

也就是说弹窗由**服务端的一个字段**决定：登录块 `moduleOpenMark` 里
`mark_index`（`table_function_open[key].mark_index`，这张表 32 条、1..32 连续）
对应的值是不是真。

* 私服默认把 32 个 mark 全标成已弹过 → **一个都不弹**
  （`store.MODULE_OPEN_POPUP_SKIP = False` 还原原版：每个系统第一次开启弹一次；
  建号就 30 级 + 全解锁，所以原版行为是一进游戏连弹 32 个）。
* 客户端弹完会把这一批 `markIndex` 用 `player.setmoduleopenmark` 回写，
  请求体是一个**裸数组** `[1,2,3,…]`（`server.request(route, arg, null, true)`）。
  服务端存成 `{markIndex: 1}` 的 **map** —— 直接回数组的话客户端
  `moduleOpenMark[module.markIndex]` 会整体错一位。

顺带：`table_function_open.unlock_lv` 是「XX 系统几级开」的唯一出处
（如 `100005` 培养系统 = 6 级、`100029` 分区战场 = 25 级），
私服建号直接给 30 级就是为了这个（见 `store.MIN_PLAYER_LV`）。

### 6.7 签到（`sign.receivereward`）

客户端只有**一条**路由，其余全靠登录块：

```js
SignCenter.ctor(data):   this._signs = data.signs;      // ★ 是 {signKey: 行} 的 map
                         this._updateTime = data.updateTime;
SignCenter._init():      for (k in _signs) 按 row.type 分到 4 个列表 + 算红点
SignCenter.updateByServer(data):
                         if (data.updateTime <= _updateTime) return;   // ← 时间必须变大
                         for (k in data.signs)
                             if (!_signs[k]) 整条塞进去
                             else            **逐字段合并**进同一条对象
SignNormalLayer._update():   读 sign.{signKey, count, rewardCount, rewards, canSignToday,
                                   beginTimeSec, endTimeSec, dialogue, soldierKey}
SignNormalLayer._updateItems(): rewards 是 **1 基的二维数组**（见下面第 4 条），
                                把 `i < sign.count` 的那些天标成已领取
SignNormalLayer.receiveRewards(): if (!sign.canSignToday) return;   // 签过了根本不发请求
                                  requestReceiveRewards({key: signKey})
```

⚠️ 五个坑：

1. **`signs` 必须是 map**（`{signKey: 行}`）。以前这里给的是 `signs: []`
   → 客户端 `for (k in [])` 一条都拿不到 → **「点签到没用」**
   （`normalSigns`/`eventSigns`/… 那几个键客户端**压根不读**，只有 `signs`+`updateTime` 有用）。
2. **回包要带 `data.sign` 且 `updateTime` 比上次大**：客户端按 key 逐字段合并进
   **同一个行对象**，界面上的「第 N 天 / 已领取」靠它当场刷新。
3. 非 200 会被客户端当成 `isTimeout` → 弹 `table_dictionary[3002]`「已经过期」。
4. **`rewards` 内外两层都是 1 基**（`[null, 第1天, …]`，每天 `[null, item1, …]`）。
   `SignNormalLayer._updateItems` 的取数是（event/birthday/novice 三层同款）：

   ```js
   for (i = 0; i < sign.rewardCount; i++)          // i 从 0 数
       for (j = 1; sign.rewards[i + 1][j]; j++)    // ← 外层 i+1、内层从 1
           rewards.push(sign.rewards[i + 1][j]);
   ```

   发 0 基二维数组的后果：走到最后一天 `sign.rewards[7]` 是 undefined →
   `TypeError: sign.rewards[(i + 1)] is undefined`（signnormallayer.js:84）。
   实机验证的取数脚本：`out/probe_sign_shape.py`（只走数据层，不开界面）。
   奖励里**不能放没有图标的道具**（`100101`/`100102`）—— 见 differences.md §F。
5. **每天必须恰好 1 件奖励**。`SignRewardItem._init` 按件数分支：

   ```js
   if (_rewards.length === 1)      item = rewardManager.getRewardIcon(_rewards[0]);  // 真图标
   else if (_rewards.length > 1)   item = new cc.Sprite(res.signcommonicon);         // 通用图标
   else                            cc.warn("signNormalLayer._update() rewards error!");
   ```

   给 2 件 → 7 个格子全长一样（都是 `signcommonicon`），看不出给什么；
   给 0 件 → `addChild(undefined)` 直接崩。

排期和奖励**客户端表里没有**（`jsc_find table_sign*` 0 命中）→ 服务端自己定，
见 `gamesrv/sign.py` 的 `SIGN_REWARDS`（7 天循环）与 differences.md §D。

### 6.8 任务派遣（`detect.*`）

主界面「派遣」按钮（模块 `100025`）。客户端 `src/data/detect.jsc` + `src/ui/detect/*`。

**登录块** `data.detect`：

```js
{"speedInfo": {"10001": 已用免费加速次数, ...},        // 值是**数字**（不是对象）
 "detect":    {"<章节key>": {beginTimeSec, waitTime, subCD, speedCount}}}
```

`Detect.ctor` 读 `detect.speedInfo` 与 `detect.detect`；
`getSpeedCountInfo(type) = (table_detect[type].speed_count - speedInfo[type]) + "/" + speed_count`。

**计时口径**（`detectItem._getGoDetectTime`）：

```js
剩余毫秒 = (beginTimeSec + waitTime - subCD) * 1000 - util.time()
```

⇒ `beginTimeSec` / `waitTime` / `subCD` **都是秒**；`subCD` 是「已经跳过多少秒」，
加速就是把它加上去（不用改 `beginTimeSec`）。

**六条路由 + 错误码**：失败也回 **HTTP 200**，客户端看的是 `data.code`
（`DETECT_ERROR_CODE`）—— 回非 200 反而会走网络失败分支。

| route | 请求 | 回包 |
|---|---|---|
| `detect.getdetectlist` | `{id}`（分类 10001/10002/10003） | `{code, detectList: [{index: 章节key, data: {beginTimeSec,…}}]}` |
| `detect.checkdetectmain` | `{idlist}` | `{code, detectMainList: [{id, isOpen}]}` |
| `detect.selectsolders` | `{detectkey, solders:[军士id]}` | `{code}` |
| `detect.godetect` | `{detectkey}` | `{code, detect:{…}}` |
| `detect.godetectcomplete` | `{detectkey}` | `{code, complateDetectCount, detect, rewards, items}` |
| `detect.subtime` | `{detectkey, timesed}` | `{code, speedInfo, detect:{…}}` |

```
200 OK / 202 PARAM_ERROR / 203 TIME_CLOSE / 204 GODETECT_RUNNING / 205 GODETECT_COMPLETE
206 GODETECT_NOTSELECT / 207 GODETECT_COUNTMAX / 208 GODETECT_SOLV / 209 GODETECT_NOTGO
210 GODETECT_NOTCOMPLETE / 211 GODETECT_SOISGO / 212 GODETECT_SOTYPE / 213 SPEED_MAX
214 SPEED_ITEM_MAX
```

**上阵条件**来自 `table_detect_chapter`：`soldierNum`（人数）、`soldierLv`（等级门槛）、
`soldierType`（兵种 = `table_soldier_master[charKey].type`：4/8/16，0 = 任意）。
⚠️ 表里 15 个派遣**全要 20/30 级**军士，而私服建号发的 18 个军士是 1 级 ——
所以刚建号时点进去会看到「需求 3 个 20 级以上任意军士」而派不了，得先培养军士
（这是原版设计，不是 bug；想放宽就改 `gamesrv/detect.py` 的 `_check_soldiers`）。

**掉落**：`table_detect_chapter_reward_group[gainItemGroup<i>]` 给 `group1..4` + 权重，
但**「组里到底有哪些道具」那张表客户端没有**（把 `"101111"` 当 key 扫遍所有 `table_*`
都 0 命中）→ 服务端用表里就有的 `gainIcon2`（界面上「可能掉落」那排图标）当奖池，
按权重挑档位取一件。见 differences.md §D。

### 6.9 演习场（`arena.*`）

主界面「演习场」按钮（模块 `100012`，解锁等级 27）。客户端 `src/data/arenacenter.jsc`
+ `src/ui/arena/*`。**只有 4 条路由**，其余全靠登录块。

**登录块** `data.arena`（`ArenaCenter.ctor(data)` 原样读）：

```js
{arenaInfo:  {points, change, wins, rating, refreshTime},   // 我的数据
 rivals:     [{index, name, lv, rating, points, headId, asstKey, state, soldier1..5}, …],
 resetTime:  秒级时间戳,       // 赛季重置（table_arena_constant：14 天一轮，起算 2016-01-01）
 refreshTime:秒级时间戳}       // 上一次刷对手的时刻
```

`arenaInfo` 客户端**只读这 5 个键**（没有刷新次数/重置次数之类的计数）：
`points` 积分、**`change` = 今日剩余挑战次数**（不是积分变化！`<= 0` 时客户端
「挑战」「刷新对手」两个按钮直接 `toast(table_dictionary[1602])`「木有挑战次数了！」退出，
整个界面像哑掉）、`wins` 连胜、`rating` 段位 1..5、`refreshTime`。

⚠️ **六个坑**：

1. **`refreshTime` 要发两份**：`ctor` 读顶层 `data.refreshTime`，而
   `updateByServer` 读 **`data.arenaInfo.refreshTime`**
   （`_refreshTime = data.arenaInfo.refreshTime + table_arena_constant.update_interval_by_player`）。
   只发一处，另一条路径上就是 `undefined + 7200 = NaN`（刷新倒计时直接乱）。
   两个时间字段都是**秒级 unix 时间戳**（客户端自己 `* 1000` 再进倒计时）。
2. **每条回包都要带 `data.arena`**：`requestGetArenaInfo(cb)` 的 cb **不带参数**，
   数据是靠 `patch.js` 的 RESP-DISPATCH（`arena -> arenaCenter.updateByServer`）落回去的。
   回包里没有 `arena` 块 = 界面不刷新（和 §5.2 那类「服务端发了但客户端不动」同一个根因）。
3. **`rival.soldier<i>` 是「军士编码串」**：`"<table_soldier key>#<星级>#<等级>#<技能等级>[#daemonLv][#装备串]"`
   —— 客户端 `ArenaCenter._init` 拿 `charManager.decodeSoldier(str)` 解出来挂到
   `rival.soldiers[i]`（**1 基**：`soldier1` → `soldiers[1]`，wire 上**不要**发 `soldiers`）。
   `decodeSoldier` 就是 `str.split("#")`，**少于 4 段直接 `cc.warn` + 返回 undefined**；
   只发裸 key 的话 8 个对手的军士**一个都解不出来**（实机量过）。
   服务端直接从 `table_friend_support_npc`(101 个 NPC) 抄：
   行形如 `{general:"sasm010103#1#30#1", brave:…, armor:…, biological:…, agent:…,
   player_name:"大阪小松子", player_lv:10}`，那 5 个字段的值正好是编码串。
4. **`asstKey` 是军士卡 key**（`"sgnw010104"`），**不是角色 key**（`"sgnw"`）：
   `headId` 为 0 时客户端走 `new ItemIcon(asstKey)` → `Shop.getTypeById(key)`，
   而它只认 `table_item` / `table_soldier` / `table_mecha` / `table_hero` / `table_equipment`
   的 key；给角色 key 会 `cc.log("key is error")`（实机 8 个对手各一次）且头像画不出来。
5. **`state === 1` 表示「已经战胜过他」**（`_setDekaroned(rival.state === 1)`：
   隐藏挑战按钮、显示成功图；`table_dictionary[1603]` =「已经战胜过他了呢~」）。
   所以**输了不能置 1**，否则那个对手再也点不动。其它任何值都算"可挑战"。
6. **结算回包要平铺字段**：`ArenaLayer._fightResult(err, data)` 直接读
   `data.success / rewards / winsRewards / scoreInfo / battleData / winPoints`
   （`rewards` 是标准 `[{type,key,count}]`，客户端按 `REWARD_TYPE` 拆成 `cards`/`items`；
   `type 2 = ITEM` 和客户端的 `REWARD_TYPE.ITEM` 是同一个值），**同时**还要带 `data.arena`。
   其中 `battleData` 必须是**结算面板的三行**（`arenawinlayer.csb` 里的标题）：

   | 键 | 面板标题 | 值 |
   |---|---|---|
   | `combatTime` | 战斗用时 | **秒**（客户端会 `* 1000` 再喂 `op.getCountdownStr`） |
   | `death` | 人员伤亡 | 我方阵亡数（请求体里的 `ownSoldierDiedCount`） |
   | `rank` | **对手积分**（名字有误导性） | 该对手的 `points` |

   少任何一个 → `numelabed.label.string = undefined` → `js_cocos2dx_ui_Text_setString :
   Error processing arguments` 抛异常，**整个结算面板停在半路、玩家退不出战斗**
   （2026-09-20 实机踩过，只发了 `combatTime`）。`scoreInfo` 是这三行各自的评价字母
   （`a|b|c|d|s|ss|sss`，对应 `res/icon/arenascore/*.png`，客户端**赢了才画**）。
   ⚠️ 回非 200 时 `_fightResult` 直接 return（同样什么都不弹）——所以别拿非 200
   当「不能打」的挡箭牌，重复上报照发。

**四条路由**：

| route | 请求 | 回包 |
|---|---|---|
| `arena.getrivallist` | `{}` | `{code:200, data:{arena: 块}}` |
| `arena.resetrivals` | `{useGold}` | `{code:200, data:{arena: 块}}`；失败 `{code≠200}`（客户端弹的是 `table_dictionary[1609]`「冷却未结束」，**不看回包 `msg`**） |
| `arena.enterfight` | `{index}` | `{code:200, data:{arena: 块}}` |
| `arena.exitfight` | `{index, success, battleInfo:{combatTime, ownSoldierDiedCount}}` | `{code:200, data:{arena, success, rewards, winsRewards, scoreInfo, battleData, winPoints}}` |

`arena.enterfight` / `exitfight` 的 `checkEnterFight` / `checkExitFight` 在客户端里都是
`return true` 的**空钩子**，不是门禁 —— 门槛全在按钮上（`arenaInfo.change > 0`）。

**战斗在客户端算**（`BattleScene.combat({id, team, enemyTeams, …})`，`id` 是
`table_arena_constant.level_rating_<rating>` 里随机取的关卡 key，如 `800001`），
打完把 `success` + `battleInfo` 报给服务端 —— 和关卡结算 `instance.finishlevel` 一个套路。
敌方队伍由 `Team.getArenaEnemyBattleTeam(rival)` 用 `rival.soldiers[1..5]` 现组
（不吃本机装备，属性修正走 `table_arena_attr_correct_enemy`），**不读 rival 的其它字段**。

**数值**（`table_arena_constant`，44 项，抽在 `data/table_arena_constant.json`）：
`rival_count`=8 个对手、`update_interval_by_player`=7200 秒自动换一批、
`refresh_cost`="5" + `refresh_cooldown`=120 秒（冷却内弹 `table_dictionary[1611]`
问「是否消耗 N 金条立刻刷新」；客户端查的其实是 **萌钞** `ITEM_KEY.MONEY`，
文案和判定不一致是原版客户端自己的小矛盾，服务端跟着判定扣萌钞）、
`rating_1..5`=100/1000/2000/3000/4000（积分段位）、`default_change`=8（每日挑战次数）、
`default/min/max_arena_points`、`pvp_rewards`="100019#14"（赢了给 14 演习萌币，
客户端自己解析这个串显示奖励）、`fail_coins`=14（输了也给，**推断**）。
积分增减公式原版无从考证，见 differences.md §D。

`mechaSuperSkillCorrectOwn`（旧登录块里的那个键）**客户端压根不读**：全库 797 个 `.jsc`
搜不到这个 atom。演习场机甲超必杀走的是客户端**本地静态表**
`table_arena_mecha_super_skill_correct_own`（唯一消费者 `Mecha.loadArenaSuperSkill`，
键 `"<mechaKey>#<superSkillLv>"`），所以服务端不用发。

### 6.10 抽卡 / 扭蛋（`gacha.*`）
`assets/src/table/` 里 176 张表**一张 gacha 的都没有** ——
「有哪些池子、消耗什么、概率多少、能出哪些卡」是**运营配置**，原版由服务端下发，
随停服一起没了。所以这一块是**内容缺口**，服务端得自己造（见 `gamesrv/gacha.py` 与
differences.md §B/§D），但所有 id/枚举都照客户端：

```js
config/gachaconfig.jsc:
  GACHA_TYPE  = {10:FREE, 20:GEM, 30:FRAGMENT, 40:TIME_LIMIT, 50:WELFARE, 60:COMMON}
  GACHA_KEYS  = {FREE:"1001", GEM:"1002", FRAGMENT:"1003"}   // ★ 池子 id 就是这三个
  GACHA_FORM  = {DEFAULT:"0", TIMES_CHANGEABLE:"1"}
  GACHA_SALE_TYPE   = {t:TOTAL_TIMES, d:DAILY_TIMES, w:WEEKLY_TIMES, m:MONTHLY_TIMES}
  GACHA_TIMES_LIMIT = {d:DAILY, a:ACTIVITY, t:TOTAL}
  SOLDIER_S_QUALITY = 3 / SOLDIER_SR_QUALITY = 4
  GUIDE_GACHA_KEY   = 1002                     // 新手引导指向钻石池
  GACHA_ERROR_DICT  = {201 参数错误 / 202 找不到抽卡信息 / 203 军士库满员 / 204 次数用完 /
                       205 倒计时 / 206 资源不够 / 207 资源错误 / 210 非活动期 /
                       211~213 次数用完 / 405 更新数据错误}
```

⚠️ `GACHA_NAMES`（`{"1001":"免费抽卡","2001":"碎片单抽","2010":"碎片十连","4001":"钻石单抽",
"4010":"钻石十连"}`）在客户端**全库零引用**（`jsc_find --exact GACHA_NAMES` 0 命中）——
是张**死表**，只反映原始命名习惯。**别照它做池子**：真正的模型是
**3 个 4 位池子 key（1001/1002/1003），每个池子带「×1 / ×10」两个按钮**
（`master.infoKeysObj = {"1": …, "10": …}`；`Gacha.getGachaTimes(key)` 就是
`_.keys(infoKeysObj).sort()`，按钮 1/2 分别取 `[0]`/`[1]`）。
（第一版照 `GACHA_NAMES` 做了 5 个池子 + 单 key，实机表现是**点进抽卡层卡在
`LoadingLayer#loadinglayer`** —— 见下面第 6 条。）

**入口**：主界面「黑市」按钮（`table_main_layer` 里 `module_key=100007`，`name=heishibutton`，
`node_name=heishi`）→ `MODULE_FUN.heishi` → `GachaLayer`。它属于「按关卡解锁」的 6 个功能之一
（`table_function_open["100007"].unlock_level_key = "100105"`），没通关那一关按钮不出现 ——
服务端 `instance.unlock_module_levels()` 会把这 6 关直接标成通关（私服取舍）。

**登录块 `data.gacha`**（`Gacha.update(data)` 是直接赋值，所以这三份必须是 **map**）：

```js
{gachaData:      {"<dataKey>": {masterKey, todayTimes, totalTimes, lastGachaTimeSec, lastFreeTimeSec}},
 gachaInfoList:  {"<infoKey>": {itemKey, itemCount, voucherKey, voucherCount, useVoucher,
                                receiveKey, receiveCount, limitTimesObj, saleInfoObj,
                                saleTypeObj, freeInterval}},
 gachaMasterList:{"<poolKey>": {key, name, type, form, resIdx, showPriority, infoKeysObj}}}
```

⚠️ **六个坑**：

1. **`gachaInfoList` / `gachaMasterList` 缺了就是「没有卡池」**：早期只发 `gachaData`，
   `getGachaMasterList()` 返回空数组 → 界面显示没有卡池（`Gacha.update` 只认这三个键，
   而 `getGachaMasterList()` 是遍历 `_gachaMasterObj` 再按 `_checkGachaVaild` 过滤）。
   `_checkGachaVaild` **只对 `type == 40`（TIME_LIMIT）校验 `startTime`/`endTime`** ——
   普通池（10/20/30）不填时间也永远有效。
2. **`saleInfoObj` 是「按次数索引」的折扣表**：客户端
   `getGachaSale = saleInfoObj[第几次] || saleInfoObj[0] || 100`，
   `getGachaConsume = info.itemCount * sale / 100`。写成 `{saleTimes, sale, defaultSale}`
   那种对象会让 `saleInfoObj[次数]` 是 undefined → 价格算成 **NaN**（实机量到 `consume: null`，
   界面上价格显示不出来）。正确形状如 `{"0": 100}`。
3. **`dataKey` 怎么拼**：`Gacha.getGachaDataKey(masterKey, times)` 是
   `form == TIMES_CHANGEABLE ? masterKey : masterKey + ("0" + times)`（times < 10 补 0）
   ⇒ 钻石池 ×1 = `100201`、×10 = `100210`。
4. **`infoKeysObj` 的键只能是字符串 `"1"` / `"10"`**：客户端按它建按钮、
   还会喂给 `getGachaTimesIconUrl(times)`（`times<1 || times>10` 直接报错，
   而且 `"1","2","10"` 会被 `_.keys().sort()` 排成 `["1","10","2"]` → 第 2 个按钮变十连、
   第 3 个不存在）。
5. **免费池的判定是「没有消耗」**：`isFreeGacha()` = `info.itemCount` 和
   `info.voucherCount` 都是 0；每日次数走 `info.limitTimesObj.d`
   （`GACHA_TIMES_LIMIT.DAILY`），客户端 `getRemainTimes` 拿它减 `gachaData[key].todayTimes`。
6. **`master.resIdx` 必须给（1..54）**：客户端拿它选
   `res/ui/gacha/src/gachatypelayer<resIdx>.csb`、`res/icon/gacha/gachabtnidx<resIdx>.png`
   （按钮图标是**硬 assert**）、`res/bg/gachabg/gachabgidx<resIdx>.png`。
   **不给 `resIdx` 的实机表现是**：点「黑市」进得去 `GachaLayer`，但一直卡在
   `LoadingLayer#loadinglayer` 上（`getGachaTypeLayerUrl(undefined)` 加载不出来）。
   `showPriority` 决定轮盘顺序（**降序**）。

**三条路由**：

| route | 请求 | 回包 |
|---|---|---|
| `gacha.getgacha` | `{}` | **扁平**三件套 `gachaData/gachaInfoList/gachaMasterList`（⚠️ 不是 `{"gacha":…}`，它不走响应派发）；可另带一个顶层 `gacha: {freeGachaTip: bool}` 用 RESP-DISPATCH 点亮「黑市」红点 |
| `gacha.getlibraryshow` | `{key, isShowAll?}` | `{cards: {"<角色key>": 1, …}, upRate: "<字符串>"}` |
| `gacha.gacha` | `{key, times}`（`times` 是 `"1"`/`"10"`，可能是字符串） | `{gachaData: <那一行>, cards: ["<角色key>", …], extraReward: {}, gemGachaTimes}`；失败用 `GACHA_ERROR_DICT` 的码 |

⚠️ 三个字段级细节（反汇编 + 活客户端实测）：

* **`gacha.gacha` 的 `gachaData` 是「那一行」不是 map**：客户端是
  `updateGachaData(dataKey, result.gachaData)`（`_gachaDataObj[key] = data`），
  而**登录块**里的 `gachaData` 才是 map（整个赋给 `_gachaDataObj`）。
* **`cards` 的元素是「角色 key 字符串」**（如 `"lfcz01"`），不是对象：
  客户端 `new NewCardEffect(cards[0])` / `GachaBeganEffect.getEffectFile(cards)` /
  `TenGachaShow._updateCardHeadList` 都拿 key 去 `charManager.getCharType(key)` /
  `getImageFullPath(key)` / `createCharCardNode(key)` 现查表。发对象会直接崩。
* **`getlibraryshow` 的 `cards` 必须是 map**（`{角色key: 1}`）：客户端
  `updateLibCards` 是 `for (var k in cards) list.push(k)` —— 发**数组**时 `k` 是**下标**
  （"0".."160"），随后 `getCharType(k)` / `getSoldierQuality(k)` /
  `createCharCardNodeByKey(k)` 全拿下标查表 → 日志刷
  `charManager.getCharType() error, key is 96`，最后 `charcardnode.js:53
  TypeError: ui is undefined`，**图鉴层直接崩**（实机踩过）。
* **`getlibraryshow` 的 `upRate` 必须是字符串**：`GachaLibraryShowLayer._initUpRate` 是
  `if (!upRate) { 隐藏概率面板; return; }` 然后 `upRate.split("#")` —— 发 `{}` 会
  **TypeError 让整个图鉴层起不来**。没有 UP 就发 `""`；要显示概率的话格式是
  `"1@30#2@25#3@25#4@20"`（1..4 = 白/绿/紫/金，1..4 对应 `UP_RATE_NAME` 里那四个面板）。
* ⚠️⚠️ **回包里捎给客户端的 `char` 块，键名是 `soldiersAdd` / `herosAdd` / `mechasAdd`**
  （2026-09-21 踩：发 `soldiers` 客户端整块忽略，玩家"抽到的角色没进角色列表"）。
  `CharCenter.updateByServer(data)` 是逐个 if 的：

  ```js
  if (data.maxSoldiersCount != null) this._maxSoldiersCount = data.maxSoldiersCount;
  if (data.soldiersAdd) this.addSoldiers(data.soldiersAdd);   // addSoldier: _soldiers[row.id] = row
  if (data.herosAdd)    this.addHeros(data.herosAdd);
  if (data.mechasAdd)   this.addMechas(data.mechasAdd);
  if (data.charManual)  for (k in data.charManual) this._charManual[k] = data.charManual[k];
  ```

  所以 `*Add` 里放的是**这次新增的行**（军士行必须带 `id`），不是整份名单；
  `charManual` 是按 key 合并（图鉴当场亮），`maxSoldiersCount` 顺带刷新栏位上限。
  见 `docs/pitfalls.md` 第 17 条。

**卡池内容**（服务端从客户端表里挑，见 `gacha.card_pool()`）：自军卡看
`table_soldier_master[charKey].card_type == 1`，一共 **152 张 = 每个角色的 4 档卡**
（`quality` 1/2/3/4，`template` 就是档位；q3 = S、q4 = SR）；大奖是
`table_hero`（2 个，带 `gacha_name`）+ `table_mecha`（6 个，同样带 `gacha_name`）。
抽到的英雄/机甲进 `player["heros"]`/`player["mechas"]`，随登录块 `char.heros`/`char.mechas`
下发（`CharCenter._initHeros/_initMechas` 按 `{key, ownFlag}` 建对象）。
⚠️ 抽卡前客户端还有一道自己的 `gachaJudge()`：军士库满、池子不存在、次数用完、
资源不够（`isConsumeEnough` 查**本地背包**）都会**直接不发请求** —— 所以服务端给的
消耗道具必须玩家真的持有。

---

## 7. 切主场景

反汇编 `LoginLayer._enterMain`：

```js
LoginLayer.prototype._enterMain = function () {
    op.touchEnabled = false;
    cc.director.runScene(new MainScene());
    op.touchEnabled = true;
};
```

正常流程里它由「开始游戏」的回调触发；补丁里由探针在
`cb4AfterLogin` 之后调用。

---

## 7.5 新号起名（player.naming）

进主场景后，新号会弹出 `NamingLayer`。反汇编 `NamingLayer`：

```js
// 模块作用域
var MAX_NAME_LENGTH = table_constant.name_max_length;          // 10
var ILLEGAL_CHARACTER_REG = /.../;
var NICKNAME_REG = new RegExp("^[a-zA-Z0-9\u4e00-\u9fa5\u0800-\u4e00]{1," + MAX_NAME_LENGTH + "}$");

// 确认按钮
NamingLayer.prototype._onClickOk = function () {
    var str = this._nameField.getString();
    if (str.length == 0)                { ccuiManager.toast(table_dictionary[105]); return; }
    if (str.length > MAX_NAME_LENGTH)   { ccuiManager.toast(table_dictionary[101]); return; }
    if (ILLEGAL_CHARACTER_REG.test(str)){ ccuiManager.toast(table_dictionary[106]); return; }
    if (!NICKNAME_REG.test(str))        { ccuiManager.toast(table_dictionary[106]); return; }
    if (dataManager.chat.checkSensitive(str)) { ccuiManager.toast(table_dictionary[108]); return; }

    if (this._skipGuide) { if (this._cb) this._cb(str); }
    else { dataManager.player.naming(str, function (msg) { ... }.bind(this)); }
};
```

`Player.naming`：

```js
Player.prototype.naming = function (name, cb) {
    this._pendingName = name;
    server.request('player.naming', {name: name}, function (err, data) {
        if (err) return;
        if (data.code == 200) { _this._setName(name); if (cb) cb(); gameEvent.onRoleCreate(); }
        else                  { if (cb) cb(PLAYER_ERR_DICT[data.code] || data.data); }
    });
};
```

服务端必须回 **`code: 200`**。回 0 的话客户端走错误分支，**弹窗永远不关**，
玩家看到的就是「点确认没反应 + 弹窗里有个东西一直闪」。

那个「一直闪的东西」是 `NamingLayer.ctor` 里跑的 `res.cursoreffect` 时间轴
（输入框的闪烁光标特效），是正常装饰，不是加载圈。

**怎么确认「闪的东西」到底是谁**（这套排障手法值得复用）：

```powershell
# 1) 先排除 Java 层
adb shell dumpsys activity top | findstr "ProgressBar Dialog GLSurfaceView"
#    -> 只有 Cocos2dxGLSurfaceView + Cocos2dxEditText，Java 层是干净的

# 2) 扫 JS 场景里所有「可见 + opacity>0 + 有正在跑的动作」的节点
python script\repl.py "(function(){var out=[];function w(n,d,p){...}...})()"
#    -> TopLayer(时间轴) 和 NamingLayer(光标特效)，其余都是 opacity=0 的遮罩

# 3) 用 instanceof 反查节点是哪个 JS 类
for (var k in window) if (typeof window[k]==='function' && node instanceof window[k]) ...
#    -> TopLayer / NamingLayer
```

---

## 8. 客户端内部数据模型（从反汇编读出来的接口）

### 8.1 jsc 字节码涉及的调用约定

* `crypt.desEncode(key, msg)` —— 标准 DES-ECB，`0x80` 补位
* `crypt.base64Encode/Decode` —— 标准 base64
* `crypt.utf16To8/utf8To16` —— JS 字符串（UTF-16）与 UTF-8 互转
* `crypt.dhExchange(x)` / `dhSecret(a, b)` —— 自研 DH，有单位元
* `crypt.hashKey(a, b)` / `hmac64(a, b)` —— 8 字节摘要（未复刻）

### 8.2 模块 key 映射

见 README 5.4 节的表格。

## 9. 任务（quest.*）

### 9.1 数据形状

登录的 `data.quest` 和 `quest.getnewquest` / `quest.submitquest` 响应里的 `quest`
是**同一个形状**，客户端 `QuestCenter.ctor` 与 `updateByServer` 都吃它：

```json
{
  "quests": {
    "209001": {
      "id": "209001",
      "questKey": "209001",
      "state": "3",
      "schedule": {"1": 10}
    }
  },
  "finishQuests": {"209001": 1},
  "finishQuestsId": [],
  "dailyQuestsTime": "2026-09-22 05:00:00",   // 下次日常换日时刻（客户端只存不读）
  "updateTime": "2026-09-18 20:15:33"
}
```

三个坑（都是「列表能看但点不了/不刷新」的直接原因）：

1. **`id` 必须给。** `_createQuest` 结尾 `quest.id = data.id`，
   而 `QuestItem.updateState` 和 `QuestLayer._submitQuest` 都拿 `quest.id`
   去查表和提交（`requestReceiveRewards({id: quest.id})`）。不给的话客户端在本地
   就 `return`，**服务端连 `quest.submitquest` 都收不到**。
2. **`schedule` 是对象不是数组。** key 取自 `table_quest.schedule_i` 的第一段
   （`"1#1"` → `"1"`），value 是当前进度。回数组的话 `data.schedule["1"]` 取到
   `undefined`，`scheduleCur` 算不出来，「领奖」按钮一直是灰的。
3. **领过的任务要再回一次 `state:"4"`（FINISHED）。**
   `updateByServer` 只覆盖不清理，不在回包里那条会一直停在 ACHIEVED，
   表现就是「奖励领了、列表没刷新」。

状态机：`0 未激活 / 1 已激活 / 2 已接受 / 3 已达成(可领) / 4 已完成(已领) / 5 无效`
类型：`1 日常 / 2 主线 / 3 成就 / 4 公会 / 5 活动 / 6 新手`

⚠️ **三类任务回在同一个 `quests` map 里**：任务界面三个页签各自调
`QuestCenter.getQuests(QUEST_TYPE.DAILY / NORMAL / ACHIEVEMENT)`，而 getQuests 是从
同一个 map 按 `type` 筛 —— 服务端不用分通道，少放一类那个页签就是空的。

### 9.2 任务表是客户端静态配置

`table_quest` / `table_quest_condition` / `table_quest_reward` 编译在
`assets/src/table/tablequest*.jsc` 里，服务端没有原始文件。两条拿表的路：

* **常规**：`script/extract_client_tables.py` —— 让游戏自己把要用的字段吐成 JSON；
* **离线**（游戏没装 / 没探针时用，2026-09-21 加的奖励表就是这么来的）：
  `jsc_decompile.py` 反编译 → node 求值 → 落 JSON，配方见
  [`build.md`](build.md) 的「离线抽表」。

服务端实际用两份：

| 文件 | 内容 | 谁在用 |
|---|---|---|
| `data/table_quest.json` | **merge 过的**（508 条）：`type/rank/lv(=activate_lv)/ak(=activate_quest_key)` + 条件 `ct/cp1/cp2` + 达成值 `tar` + schedule key `sk` | `quests.py` 全部逻辑 |
| `data/table_quest_reward.json` | 每条任务的奖励行：`"<questKey>#N" -> {type, param_1, param_2}`（1534 行） | 领奖（§9.5） |
| `data/table_quest_condition.json` | 条件原文：`"<questKey>#1" -> {type, param_1..param_5}` | 留档：`ct` 的 `param_3+`（如 13102 的技能等级）只有这份表有 |

> ⚠️ 客户端自己那份 `table_quest` 字段是 `{title, desc, icon, jump_view, activate_lv, …}`
> —— **标题 / 描述 / 图标 / 跳转都是客户端渲染的**，服务端一个都不用发（两份表 key 集合一样）。
> `ct` 的含义靠客户端 `desc` 反推，逐条记在 §9.4 的表里。

### 9.3 三类任务的推进方式

| 类型 | 窗口（一次放给客户端多少条） | 进度存在哪 |
|---|---|---|
| **2 主线**（135 条） | 按 `activate_quest_key` 前序遍历，一次 20 条；领一条滑一格 | `quests.done` |
| **1 日常**（246 条） | **只放当前等级所在的那一档** | `quests.daily = {day, done}` |
| **3 成就**（91 条） | 所有 `lv ≤ 玩家等级` 的都放（多数 `lv=0` 无门槛） | `quests.achievement.done` |

**日常分档**：表里 type=1 按 `lv` 分 24 档（1 / 5 / 10 / … / 116），每档 7~11 条。
判据很硬 —— 档内那条「完成所有的日常任务哦！」（`ct=19201`）的 `tar` 正好等于
**档内条数 − 1**（24 档逐一核对都对得上）。所以窗口取「不超过玩家等级的最大档」，
而不是把所有 ≤ 等级的档都铺出来（30 级会变成 40 多条，也不符合 `tar` 的语义）。

**换日**：`quests.RESET_HOUR = 5`（和 `instance.SUBAREA_RESET_HOUR` 同一个换日点 ——
同一个游戏里两处「每日」用不同换日点会让玩家觉得计数坏了）。`daily.day` 存
「游戏内的今天」（`day_str()`，按换日点算日期），`ensure_daily_reset()` 在 block /
submit 之前调用：跨天就清空当日 `done` 并**自己落盘**（`quest.getnewquest` 这类
只读 handler 不会 save，不落盘的话磁盘上永远停在昨天、`updateTime` 反复变）。

**成就**不分档，只按 `lv` 门槛解锁；进度是长期累计（不重置）。

### 9.4 任务进度是**服务端**算的（条件码总表）

`QuestCenter.checkQuestFinish(key)` 只有一句 `return this._finishQuests[key]`
（「这条领过没有」），客户端**不算**条件。所以 ACHIEVED 完全由服务端决定，
客户端只负责把 `schedule[cur]` 显示成进度条。

`ct` 的含义不是猜的：`table_quest_condition` 只有参数，文案在客户端 `table_quest.desc`
里（例：`100001 ct=12204 cp1=5` → 「通关任意关卡5次！」；`100007 ct=13232 cp1=5` →
「抚摸妹纸达到5次！」；`301001 ct=11201 cp2=100002` → 「累计获得萌抄达到5000000大元！」）。
`gamesrv/quests.py:progress_of()` 按码分派，下表就是它的全部映射：

| `ct` | 含义（日常/成就用到的） | 服务端怎么算 |
|---|---|---|
| `12204` | 通关任意关卡 / 战胜 N 次 | `wins` |
| `12207` | 通关 cp2 里那些关卡（同一副本的各难度）共 N 次 | `clearsByLevel` 求和 |
| `13203` | 提升军士等级 N 次 | `soldierUpgrades` |
| `13232` | 抚摸 N 次（成就里 cp2 指定角色） | `touches` / `touchesByChar` |
| `13231` | 赠送 N 个礼物 | `gifts` |
| `18201` | 给好友发送物资 N 次 | `friendSends` —— **好友系统没做，恒 0** |
| `19201` | 完成所有日常任务 | 当天本档已领条数（不含自己） |
| `15202` | 完成演习 N 次 | `arenaFights` |
| `16202` | 完成任务派遣 N 次 | `detects` |
| `12201` / `12205` | 战斗次数 / 失败次数 | `battles` / `losses` |
| `11201` | 累计获得 cp2 道具 N 个 | `itemGained`（挂在 `items.add_item` 这个唯一入账口） |
| `12101` | 战役获得的星星数量 | 现算：关卡记录的 `starMark` 合计 ⚠️ 未按难度区分 |
| `13103` | 不同 cp2 军阶的军士数量 | 现算：名单里去重 |
| `13105` | 角色私密开启数量 | 现算：`favor` 行里 `desc == -1`（全解锁）的个数 |
| `13201` | 累计军士退伍（分解）个数 | `soldierSells` |
| `12210` | 队伍中存在 cp1 角色通关过 cp2 那个关卡 | 关卡结算时记 `clearsWithChar` |
| `12212` / `12216` | 只上阵 cp2 人胜利 / 队伍里有 cp2 兵种胜利（**主线**） | `winsByTeamSize` / 简化成任意胜场 |
| `13204` / `13102` | 首次突破 / 某军阶军士达到 N 级（**主线**） | `soldierStars` / `soldierMaxLv` |
| `12208` / `12209` / `13102`(成就) | 击杀鸭子数 / 我方军士跪倒数 / 技能熟练度 | **拿不到数据 → 恒 0**，不假装达成 |

计数器都挂在 `player.questStats`（2026-09-21 从 5 个扩到 17 个）：

```json
{"wins": 1, "winsByTeamSize": {"3": 1}, "soldierUpgrades": 0, "soldierStars": 0,
 "soldierMaxLv": {}, "battles": 0, "losses": 0, "clears": 0, "clearsByLevel": {},
 "clearsWithChar": {}, "touches": 0, "touchesByChar": {}, "gifts": 0,
 "arenaFights": 0, "detects": 0, "soldierSells": 0, "itemGained": {}, "friendSends": 0}
```

写入点：关卡结算（`instance.finish_level`）、军士升级·突破·分解（`char.*`）、
抚摸·送礼（`favor.*`）、演习（`arena.exitfight`）、派遣（`detect.godetectcomplete`）、
道具入账（`items.add_item`）。

### 9.5 领奖与奖励形状

```
quest.submitquest {questKey}  ->  data.rewards = [{type, key, count}, ...]
```

* 形状是客户端定的：`ccuiManager.popupReward(rewards)` 会把它塞进
  `RewardTipsLayer(rewards, cb, type, receiveState)`，而 `popupRewardWithItems` 拼的
  就是 `{type, key, count}`（`manager/ccuimanager.jsc` 反汇编）。
* `type` 用客户端 `REWARD_TYPE` 的字符串值：`1` 玩家经验（**key 给空串**）、`2` 道具、
  `5` 军士、`6` 公会经验、`8` 装备（无数量，恒 1）。
* 发放走 `items.settle()`：能发的只有 ITEM / PLAYER_EXP / SOLDIER；公会经验、装备
  记进 `player.pendingRewards`（**不假装发成功**）。
* 回包同时带 `items`（`items.changed_block`，只回客户端本来就认识的 key）让背包当场刷新；
  道理和 §5 里讲的 `items.changed_block` 一样：只回客户端本来就认识的 key。
* 主线以前 `submit` 只回 `rewards: []`（领了等于没领），现在三类统一发真奖励。

上阵人数来自 `player.teams[curTeamIdx].soldierKeys` —— 也就是必须实现
`player.updateteams`（见 §10.3），否则服务端永远以为队伍是空的。

私服想少打几场就把环境变量 `GS_QUEST_MULT` 调大（一次胜利按 N 次算）。

## 10. 关卡 / 副本（instance.*）

### 10.1 关卡表在客户端，服务端只给进度

`Instance.ctor(data)`：

```js
this._initLevel();                  // 用客户端自己的 table_level 建 1142 个关卡
this._updateLevels(data.levels);    // 服务端只回「进度」
this._chapters = data.chapters;
```

`Level.updateLevel(level)` 只读三个字段，所以登录的 `data.instance.levels` 就是

```json
{"100102": {"starMark": 7, "challengeTimes": 3, "lastUpdateTimeSec": 1789739147}}
```

关卡结构（章节、每章有哪些关）客户端自己表里有，服务端**不需要**给。

### 10.2 战斗链路

```
instanceManager.onBattle(levelId, cb, immCb)
  ├─ 第 1 步：curTeam.isNormalData()  —— 见 10.4，不通过就弹「队伍数据异常，请重新登陆」
  ├─ 检查上阵人数 / 行动力
  └─ BattleScene.combat({id, team, rewards, resultCb, showCb, endCb, controlParams})
       打完 → resultCb(args)
       → Instance.finishLevel(levelId, starMark, rewards, battleInfo, cb, ...)
       → POST instance.finishlevel {levelId, starMark, rewards, battleInfo,
                                    curTeamIdx, curExp, lv, actionPoint}
       回包 data.level → Instance._updateResult() → level.updateLevel()
```

`starMark` 是客户端算的（`calLevelStarMark`）：`1` 通关 / `+2` 英雄击杀达标 /
`+4` 己方阵亡达标，所以 `7` = 三星。服务端只管记下来并取 max。

服务端在 `instance.finishlevel` 里顺手做一件事：**把这次胜利记进任务进度**。

### 10.3 `player.updateteams`

```js
paramTeams.push({id: team.id, team: team.getReqUpdateParam()});
// getReqUpdateParam() -> {heroKey, mechaKey, soldierKeys}
// soldierKeys 实际是 getSoldierIds()（军士的 id）
server.request('player.updateteams', {teams: paramTeams}, cb);   // code 必须是 200
```

不实现的话服务端不知道玩家上阵了谁，主线的「只上阵 N 个军士」永远做不完。

### 10.4 客户端那个防篡改快照（踩过的坑）

`Soldier._init` 第一句是

```js
this._originData = util.encodeOriginData(args);   // ← 存的是**服务端原始数据**
```

`encodeOriginData` 把对象深拷贝、所有数字 `<< 5`、再 `JSON.stringify`。
之后 `Soldier.isNormalData()` 拿它跟本地值逐项比对：

```js
originData.key      != this._key        -> false
originData.quality  != this._quality    -> false
originData.star     != this._star       -> false
originData.lv       != this._lv         -> false
originData.skillLv  != this._mainSkill.lv -> false      // _mainSkill.lv = args.skillLv || 1
```

`Team.isNormalData()` 只是遍历队员逐个查；**空队伍必然通过**。

所以我们少发任何一个字段，都会在「编好队第一次点战斗」时炸出来：
服务端必须发 `skillLv`（不发就是 `undefined != 1`），数字字段必须是数字
（字符串会绕过 `<<5` 的还原）。表现是弹「队伍数据异常，请重新登陆」然后闪退。

### 10.5 行动力是背包道具，不是 player 字段

进关卡前客户端查的是 `bag.getItemCount(ITEM_KEY.ACTION_POINT)`（`100003`，玩家说的「甜甜圈」），
不是 `player.actionPoint`。为 0 就弹「没有甜甜圈了 是否需要补充行动力」。

而 `data.item` 是**平铺映射**，不是嵌套结构：

```js
// dataManager.initUserData
this.bag = new Bag(data.item);
// Bag.ctor(items)
for (var key in table_item) this._items[key] = this._createItem(key, items[key] || 0);
```

所以 `data.item` 要直接是 `{"100003": 999, "100002": 10000000, "100001": 100000}`
—— 早先按 `{items:{...}, package:{}, limitTimeItems:[]}` 给，所有道具都是 0。

## 11. 军士养成（char.*）

### 11.1 功能是按「指挥部等级」解锁的

`table_function_open` 里每个系统都带解锁条件，`unlock_lv` 是**玩家等级**：

| key | 系统 | 条件 |
|-----|------|------|
| 100004 | 编成系统 | `unlock_lv: 1` |
| **100005** | **培养系统** | **`unlock_lv: 6`** |
| 100012 | 演习场 | `unlock_lv: 27` |
| 100020 | 第 4 个上阵位置 | `unlock_lv: 11` |
| 100021 | 第 5 个上阵位置 | `unlock_lv: 25` |
| 100025 / 100027 / 100028 / 100029 | 任务派遣 / 战术模块 / 模块一览 / 分区战场 | `unlock_lv: 20` / 25 / 25 / 25 |

等级不够时客户端按钮是灰的、点了弹 `table_dictionary` 里的
「指挥部等级不足哦~OAQ」/「指挥部达到25级开启哦~加油升级吧」。

所以 `store.new_player()` 直接给 `lv = MIN_PLAYER_LV(30)`，不是 1 —— 私服没必要从 1 级刷起。
`_migrate()` 会把老存档低于 30 的补上去（否则「编成 -> 培养」永远点不开）。

### 11.2 军士必须是 `card_type == 1` 的自军卡

`Soldier._loadMasterAttr` 就一句：

```js
this._cardType = table_soldier_master[this._charKey].card_type;
```

`CARD_TYPE = {TEAMMATE: 1, ENEMY: 2, EXP: 3, SKILL: 4}`。
**key 是 `char_key`（角色），不是士兵 key**（`sasm010104` 的 char_key 是 `sasm`）。

踩过大坑：早期初始名单是「实测能 `new` 出来」才挑的，结果挑中一堆 `card_type == 2`
的敌方单位（`sfog` 是先代巫女 BOSS）。后果：

1. 「编成 -> 培养」的材料列表只收 `cardType == TEAMMATE`，18 个里只剩 7 个；
2. `CharCenter.calcSoldierUpgrade` 里 `gainExp` 会变成 `NaN` ——
   敌方行没有 `base_cost` 字段，`undefined` 参与加法就是 `NaN`，
   `while (tarExp < maxExp)` 永远为假，直接顶到等级上限。

挑名单用：

```js
table_soldier[k].quality === 4 && table_soldier_master[table_soldier[k].char_key].card_type === 1
```

### 11.3 培养：升级是客户端先算、服务端复刻

`SoldierDetailLayer._updateUpgradeData` 在**面板刷新时**就本地算出目标等级显示出来：

```js
var res = dataManager.character.calcSoldierUpgrade(this._upgradeMaterials, this._soldier);
this._needExp   = this._soldier.maxExp - this._soldier.curExp;
this._needMoney = res.needMoney;
this._ownMoney  = dataManager.bag.getItemCount(ITEM_KEY.MONEY);   // 萌钞 100002
```

点「升级」才把材料发给服务端，服务端回什么等级面板就显示什么等级
（`Soldier.requestUpgradeCb` -> `upgrade(data.soldier)`）。
所以服务端把 `calcSoldierUpgrade` 逐行复刻了一份（`gamesrv/soldier.py`），
不然会出现「预览 +3 级，点完跳 +8 级」。

请求 / 响应：

```
char.upgradesoldierlv   {id, key, materials: [军士id, ...]}
  -> {"code":200,"data":{"soldier":{"lv":新等级,"curExp":经验,"skillLv":技能等级}}}

char.improvesoldierstar {id}
  -> {"code":200,"data":{"soldier":{"star":新星级}}}

char.upgradesoldierskill{id, materialId}
  -> 只要 code==200

char.sellsoldiers       {materials: [军士id, ...]}
  -> {"code":200,"data":{"rewards":[]}}
```

⚠️ **`materials` 是军士 id 的数字数组，不是对象数组**。
`SoldierCheckBoxListLayer._targets` 是以 id 为下标的稀疏数组，确认时
`for (k in _targets) if (_targets[k]) out.push(k)`。
`char.sellsoldiers` 用的也是同一个字段名 `materials`（不是 `keys`）。

材料会被**真的吃掉**：客户端 `requestUpgradeCb` 立刻调 `character.removeSoldier(materials)`，
服务端不删的话重登材料就复活了。

数值来源（`table_soldier` 系列表，见 `script/extract_client_tables.py` 的 `SOLDIER_JS`）：

```
gainExp   += table_soldier_to_exp[材料.lv]["quality_<品质>_<星级>"]
needMoney += table_soldier[材料.key].base_cost            // 是 base_cost，不是 base_exp
           + table_soldier_to_cost_for_upgrade[材料.lv]["quality_<品质>_<星级>"]

tarLv = 目标.lv; tarExp = 目标.curExp + gainExp
while (true) {
    if (tarLv >= 目标.maxLv) { tarLv = 目标.maxLv; tarExp = 0; break; }
    need = table_soldier_upgrade_exp[tarLv]["quality_<品质>"];    // 注意没有星级的维度
    if (tarExp < need) break;
    tarExp -= need; tarLv++;
}
```

等级上限 `table_soldier_lv_limit[星级]["quality_<品质>"]`（1 星 30、2 星 40 … 5 星 70），
星级上限 / 技能上限在 `table_soldier_constant` 的 `max_star_<品质>` / `max_skill_lv_<品质>`。

对齐验证：`python script/check_soldier_calc.py`（44 个用例，和服务端算的逐字段比对）。
链路验证：`python script/selftest_game.py`（喂两个材料 -> 重登确认等级落盘、材料没复活）。

---

## 12. 好友（friend.*）

服务端实现见 `server/gamesrv/friends.py` + `handlers/friend.py`。
下面每条都是 `assets/src/data/friend.jsc`（逐函数反汇编）和
`assets/src/config/friendconfig.jsc`（常量表）里读出来的，不是猜的。

### 12.1 九条路由

| route | 请求 | 成功 `data` | 客户端回调 |
| --- | --- | --- | --- |
| `friend.getfriendlist` | `{}` | `{friendMapList, recommendationList, takeMaterialsCount}` | `Friend.update(res.data)` |
| `friend.getrecommendationlist` | `{}` | `{recommendationList}` | `Friend.update({recommendationList})` |
| `friend.searchplayer` | `{numberId}` | `{playerInfo}` | 回给搜索面板（当推荐条目用） |
| `friend.applyfor` | `{numberId}` | `{friendMap}` | `updateFriendMap(data.friendMap)` |
| `friend.agreeapplication` | `{numberId}` | `{friendMap}` | 同上 |
| `friend.refuseapplication` | `{numberId}` | `{friendMap}` | 同上 |
| `friend.deletefriend` | `{numberId}` | `{friendMap}` | 同上 |
| `friend.sendmaterials` | `{numberId}` | `{friendMap}` | 同上 |
| `friend.takematerials` | `{numberId}` | `{friendMap, takeMaterialsCount, reward}` | 同上 + 弹奖励 |

⚠️ **`getfriendlist` 的字段必须在 `data` 顶层**：回调是 `this.update(res.data)`，
而 `Friend.update` 读 `data.friendMapList` / `data.recommendationList` /
`data.takeMaterialsCount`。套一层 `{friend: {...}}` 的话三个全是 `undefined`，
`update` 直接 return —— 表现是「面板打开是空的，但服务端日志里请求是 200」。

### 12.2 两种条目形状（不一样，别混）

好友记录（`friendMapList` 的值 / `friendMap` 回包）：

```json
{"numberId1": 100001, "numberId2": 900001, "status": 40, "updateTimeSec": 1789968251,
 "player": {"numberId": 900001, "name": "大阪小松子", "lv": 10,
            "headId": "601001:2", "lastLoginTimeSec": 1789961051}}
```

* key = `"<numberId1>#<numberId2>"`（`Friend.getFriendMapKey` 就是 `a + "#" + b`）；
* **`player` 必给**：好友列表每条都走
  `FriendItem._updateFriendLabel -> _updateInfo(this._info.player)`，
  `player` 是 `undefined` 的话 `info.lv` 当场 TypeError（整页列不出来）；
* `updateTimeSec` 是**申请列表的排序键**（`getApplicationList` 按它倒序）。

推荐条目（`recommendationList` 的元素 / `data.playerInfo`）是**平铺**的：

```json
{"numberId": 900008, "name": "鬼龍院皋月", "lv": 10,
 "headId": "601009:2", "lastLoginTimeSec": 1789967231}
```

`FriendItem._updateRecommendationLabel` 直接 `_updateInfo(this._info)`，
`getNumberId()` 也直接读 `_info.numberId` —— 多套一层 `player` 就全 `undefined`。

### 12.3 `status` 位掩码（`FRIEND_MAP_STATUS`）

```
DELETE 1   A_APPLY_FOR_B 2   B_APPlY_FOR_A 4   BE_FRIEND 8
A_SEND_MATERIALS_B 16   B_SEND_MATERIALS_A 32   A_TAKE_MATERIALS_B 64   B_TAKE_MATERIALS_A 128
```

⚠️ **名字里的 A/B 是「key 里的第一/第二个人」，不是「我 / 他」。**
客户端 `isBeApplyFor` / `isGetMaterials` / `isSendMaterials` / `isTakeMaterials`
都是先看「我在 numberId1 还是 numberId2」，再查对应的那一位。
本服的约定是**自己永远当 A**（key = `"<我的numberId>#<对方numberId>"`），
所以每条分支都落在 A 那一支：

| 事件 | 位 |
| --- | --- |
| 我申请加他 | `A_APPLY_FOR_B` |
| 他申请加我（我能同意/拒绝） | `B_APPlY_FOR_A` |
| 成为战友 | `BE_FRIEND` |
| 我给他送了物资 | `A_SEND_MATERIALS_B` |
| 他给我送了物资（我能收） | `B_SEND_MATERIALS_A` |
| 我收过了 | `A_TAKE_MATERIALS_B` |
| 他收过了 | `B_TAKE_MATERIALS_A` |

组状态（都已实测）：

* 好友列表 = `isFriend(status)` = **有 `BE_FRIEND` 且没有 `DELETE`**；
* 申请列表 = `isBeApplyFor(status)` = 有 `B_APPlY_FOR_A` —— **不看 `DELETE`**，
  所以「拒绝」必须把申请位清掉，只标 `DELETE` 的话这条会一直赖在申请页签里；
* 可收物资 = `isGetMaterials` && !`isTakeMaterials`；「已送」「已收」位要**各自留着**，
  界面就是靠它们显示「已送 / 已收」（清了「已送」位，重复收取会回 234「还没收到物资」
  而不是客户端文案里那句 213「已经收过啦」）。

### 12.4 上限来自客户端表

`Friend.isFriendListFull()` / `isTakeMaterialsFull()` 读的是

```
table_player_level_function[玩家等级].friend_limit        // 1 级 18，每 10 级 +2，120 级 42
table_player_level_function[玩家等级].take_materials_limit // 1 级 4， 每 10 级 +1，120 级 16
```

（抽表：`python script/decompile_table.py tableplayerlevelfunction` ->
`server/gamesrv/data/table_player_level_function.json`，1~120 级全有。）
服务端用**同一张表**算上限，界面上「3/18」「4/4」才对得上。

### 12.5 `player.numberId`

`Player.ctor` 读 `data.numberId`，好友那一套全拿它跟 key 两端比
（`Friend.getFriendNumberId` 甚至会在两边都不等时 `cc.error` 并返回 `undefined`）。
本服在建号 / 迁移时分配一个稳定号（`store._next_number_id`，100001 起，
NPC 好友占 900001 起），存在存档里 —— 每次现算的话一重登好友就全对不上了。

### 12.6 错误码

`FRIEND_ERROR_CODE`（`friendconfig.jsc`）把码映射到 `table_dictionary` 的文案，
回调里是 `FRIEND_ERROR_CODE[code] || data`，所以**回对应的码就行**，文案客户端自己弹：

| 码 | 文案 | 什么时候 |
| --- | --- | --- |
| 201 | 参数错误 | `numberId` 缺失 / 自己加自己 |
| 210 | 伦家已经是你的战友啦~ | 对已经是战友的人再申请 / 再同意 |
| 211 | 申请重复啦~ | 重复申请 |
| 212 | 已经送过啦~ | 同一天给同一个人送第二次 |
| 213 | 已经收过啦~ | 同一份物资收第二次 |
| 221 | 找不到申请哦~ | 同意/拒绝一条不存在的申请 |
| 222 | 你确定他在这片大陆上吗~ | 搜不到这个号 |
| 223 | 找不到这位指挥官哦~ | 操作对象不是好友 / 不在表里 |
| 231 | 你的萌友达到上限了~ | 我这边好友满了 |
| 232 | 收取物资次数达到上限了 | 今天收的次数到 `take_materials_limit` |
| 234 | 你还没收到物资哦~ | 对方没送就点收 |

### 12.7 收物资的奖励形状

`friend.takematerials` 的 `data.reward` 是 **`{itemKey: count}`**（不是数组）：
客户端 `FriendItem.showTakeMaterialsPanel` 用 `for (key in reward)` 迭代，
包成 `{key, count, type: REWARD_TYPE.ITEM}` 再 `ccuiManager.popupReward`。

请求体里**只有 `numberId`**，没有任何道具参数 —— 给什么是服务端说了算
（本服给行动力 `100003` ×2，见 `friends.TAKE_REWARD_*` 两个环境变量）。

### 12.8 红点（`recevieMaterialsTip` / `applicationTip`）

`util/server.jsc` 的 `responseConfig` 里有 `friend` 这一项，响应 `data` 里出现
`friend` 就会派发给 `Friend.updateByServer(data.friend)`，它只认这两个提示键
（只看真值、从不置回 false）。所以有可收物资 / 有待处理申请时带上它们是红点的来源。

### 12.9 私服的取舍

原版这些数据在别的玩家身上，单机没有别的玩家，所以：
好友全是 `table_friend_support_npc` 的 101 个 NPC（numberId = 900001 + 表内下标），
建号第一次送 5 个好友 + 2 条待处理申请，申请出去 60 秒后 NPC 自动同意，
换日（`quests.RESET_HOUR` = 05:00）清空四个物资位并让 2 个好友重新送物资。
细节和"哪些是猜的"见 `docs/differences.md` §B/§D。

链路验证：`python script/selftest_game.py --only 好友`（形状 / 上限 / 申请自动同意 /
送物资 + 日常 18201 / 收物资进背包 / 重复与上限 / 同意拒绝删除 / 换日，收尾还原）。

⚠️ **客户端必须打 `patch.js` 的层序 polyfill 才能显示这个面板**：
`ccui.helper.seekNodeByName` 原版是**层序**遍历，我们补成深度优先的话，
`FriendListPanel._initButtons` 会命中**好友条目里**的同名 `sendbutton`
（条目 csb 里也有），ctor 直接抛 `sendRedDotCase is null` ——
症状是「面板整页空白 + 左上角返回键有反馈但退不出去」。
详见 `docs/pitfalls.md` 第 15 条。

---

## 13. 勋章 / 头像 / 衣柜（medal.*）

服务端实现：`server/gamesrv/medal.py` + `handlers/medal.py`。
下面每条都来自 `assets/src/data/medal.jsc` 的反汇编 + `ui/medal/*.jsc` 的读法。

### 13.1 九条路由

| route | 请求 | 成功 `data` |
| --- | --- | --- |
| `medal.changehead` | `{headId, headType}` | 新的 headId **字符串** `"<itemKey>:<type>"` |
| `medal.changeclothes` | `{clothesId}` | 新的 clothesId（**值**，不是对象） |
| `medal.changebg` | `{bgId}` | 新的 bgId（值） |
| `medal.wearmedal` | `{medalId, wearIdx}` | **整张** medalWear（`{勋章id: 位}`） |
| `medal.setclothesorbgold` | `{id}` | 任意真值 |
| `medal.setheadold` | `{headId, headType}` | 任意真值 |
| `medal.setmedalold` | `{medalId}` | 任意真值 |
| `medal.clearallheadnew` | `{}` | 任意真值 |
| `medal.getfriendmedalinfo` | `{friendId}` | 整个勋章信息对象（见 13.5） |

⚠️ 三条 `change*` 的回包是**值**（回调直接 `player.updateMedalClothesId(res.data)`），
`wearmedal` 回的是**整张** `medalWear`（`player.updateMedalWear(res.data)`）——
回错形状不会报错，只会静默不生效。

### 13.2 `data.medal`（登录块）

```json
{"medals": {"10010": {"id": "10010", "progress": 0, "progressInfo": {}, "completeTime": 0}, ...},
 "newMedalIds": []}
```

* `Medal._initData` 把 `medals` 原样存进 `_medals`，完成与否看
  **`completeTime` 真值**（`isMedalCompleteByGroup`），`progress` 只是显示进度；
* ⚠️ **`medals` 必须覆盖 `table_medal` 每一条**（59 条）——
  `isMedalCompleteByGroup` 是 `_medals[id].completeTime`，缺一条就是
  `undefined.completeTime` TypeError（和任务窗口那个坑同源）；
* 行的形状来自 `createTempNewMedal`：`{id, progress, progressInfo: {}, completeTime?}`。

### 13.3 三个「穿着」的字段都在 `player` 上

* `player.headId` = `"<itemKey>:<HEAD_TYPE>"`（`HEAD_TYPE = {SOLDIER:"1", OTHER:"2"}`）。
  `Medal.getHeadSpr` 自己 `split(":")`：`SOLDIER` 走 `new HeadIcon(key)`、
  `OTHER` 走 `new ItemIcon(key)`。⚠️ 老存档里那个 `headId: 1` 是瞎填的，
  `getHeadSpr` 会 `.split` 一个数字 → 直接抛；`medal.ensure()` 会按
  `table_constant.default_head_id`(`601001`) 修正成 `"601001:2"`。
* `player.medalClothesId` / `medalBgId` = type 40 / 50 的道具 key，
  默认值分别是 `table_char_clothes[table_constant.lead_char_key].item_key`
  （`401001`）和 `table_constant.default_medal_bg`（`501001`）。
* `player.medalWear` = `{勋章id: 佩戴位下标}`。客户端 `isWearMedal` 是
  「**同一 group 只能戴一个**」的语义，所以服务端戴上新的会先把同组的摘掉。
  ⚠️⚠️ **登录块里这个字段是 JSON 字符串**，不是对象：客户端
  `Player._getMedalWear` 是 `JSON.parse(this._medalWear)`、`updateMedalWear(v)`
  是 `this._medalWear = JSON.stringify(v)`。发对象的话 `JSON.parse({})` 会先被转成
  `"[object Object]"` 再解析 → `SyntaxError: JSON.parse: unexpected character at
  line 1 column 2`，而这个异常是在 `MedalLayer` ctor 里同步抛的 ⇒
  整个「玩家信息/勋章」层建不出来，表现是**左上角点了没反应**。
  转换点只有两处：`agent._player_block()`（发字符串）、`medal.wearmedal` 的回包（发对象）。
  详见 `docs/pitfalls.md` 第 16 条。

### 13.4 衣柜 / 头像 / 勋章本体都是**背包道具**

`ITEM_TYPE`：CLOTHES 40 / BG_IMG 50 / HEAD 60 / MEDAL 70（`config/bagconfig.jsc`）。
勋章 ↔ 道具的对应是 `table_medal[key].icon_id`（700000 起，`table_item` 里 `t == 70`）。
`hasNewClothes` / `hasNewBg` / `hasNewByItemType` / `isNewMedal` 全部走
`bag.getItemsByType(type)` 再看 `item.isNew`。

**NEW 标记怎么给**：`Item.ctor(key, itemOrCount)` 和 `Item.updateByObj(item)` 里都有
`typeof arg === "object"` 分支，`updateByObj` 最后一句是 `this._isNew = item.isNew`
—— 所以登录块的 `item` 块把该道具写成 **`{"count": n, "isNew": true}`** 就点亮了
（`medal.item_block()` 干这个）。普通道具保持「key -> 数字」不变。
4 条 `set*old` / `clearallheadnew` 就是客户端点过之后回来让服务端把标记去掉的。

### 13.5 `medal.getfriendmedalinfo`

`ui/friend/deletefriendpanel._getFriendMedalInfoSuccCb(data)`：

```js
data.lv = friend.lv; data.name = friend.name; data.numberId = friend.numberId;
cc.director.getRunningScene().push(new MedalLayer(data), true);   // 整个 data 当勋章信息画
```

所以回包 = **好友的** `{name, lv, numberId, headId, completeCount, medals, medalWear}`。
好友是 NPC（`friends.py`），勋章按 numberId 给一份确定性的（前 K 条达成 + 每组戴一个）。

### 13.6 进度：按 `condition_kind` 反推（这是猜的部分，见 differences §D）

`condition_id` 指向的 `table_medal_condition` 里只有 `condition_ids` —— 那是原版
**服务端**的 condition 对象 id（客户端表里没有对应的表），所以语义只能从
`table_medal.desc`（人话）+ `times`（目标值）反推：

| kind | 语义（desc） | 数据来源 |
| --- | --- | --- |
| 1002 | 战斗失败 N 次 | `questStats.losses` |
| 2001 | 演习 N 次 | `questStats.arenaFights` |
| 3001 | 派遣 N 次 | `questStats.detects` |
| 4001 | N 个军士达到 X 级 | 军士名单（X 从 desc 抠） |
| 5001 | N 个角色好感度到 X 级 | `favors`（X 从 desc 抠） |
| 5002 | 送 N 个礼物 | `questStats.gifts` |
| 5003 | 抚摸 N 次 | `questStats.touches` |
| 6001 | 金条/碎片抽卡 N 次 | `player["gacha"]` 的 `totalTimes`（按池子） |
| 7002 | 获得 desc 点名的浴衣 | 背包 type 40 且名字命中的 |
| 8001 | 完成 N 次日常任务 | `questStats.dailyQuests`（本模块新加的累计计数） |

**算不了的三类**（进度恒 0，不假装完成）：`1001` 通关指定关卡（desc 点名关卡名，
要 `table_level` 的「关卡名 → key」表，还没抽）、`1003` 我方军士被推倒次数
（战斗内部统计）、`4002` 获得指定军士（没有「军士卡 key → 名字」的表）。

`condition_kind` 为空的 **25 条是运营活动名次 / 预约人数**类勋章，活动早没了
—— 私服直接算完成（否则永远拿不到），见 differences §B。

验证：`python script/selftest_game.py --only 勋章`（形状 / 默认值 / NEW 标记 /
换装落盘 / 佩戴同组替换 / 清标记 / 好友勋章 / 计数器驱动的达成与发本体，收尾还原）。

---

## 14. 分享 / 礼包兑换（`share.*` / `convert.*`）

两条零散领奖路由，形状都小但**各有一个坑**。

### 14.1 `share.receivesharereward`

| 项 | 值 |
| --- | --- |
| 请求 | `{shareSuccess: true, platform: "<平台>"}`（客户端硬编码 `shareSuccess: true`） |
| 成功 `data` | `{shareCount: <新的次数>, rewards: {"<道具key>": <数量>}}` |
| 登录块 `data.share` | `{shareCount, isCanShare}`（客户端 `Share._initData` **只读 `shareCount`**） |

* ⚠️ **`rewards` 必须是 map**：回调是 `ccuiManager.popupRewardWithItems(data.rewards)`，
  而它是 `for (k in items) push({type: ITEM, key: k, count: items[k]})` —— 发数组的话
  `k` 会变成下标 `"0"`，弹出来的奖励会是空的/错的。
* 次数上限和奖励内容**都在客户端表里**：`table_constant.share_reward_max_count`（= 1，
  客户端自己拿来挡按钮）、`table_constant.share_reward_key`（= `"100001@30"`，
  `<itemKey>@<count>`）—— 服务端照表发，不要自己编。
* `shareCount` 按换日点（05:00）清零。⚠️ 换日检查要在
  `agent.getlogindata` 里**显式调 + 落盘**（`share.ensure()`），
  只在 `share.block()` 里改内存的话，重登看到还是旧次数（同 pitfalls 第 16 类）。

### 14.2 `convert.convert`（用礼包）

| 项 | 值 |
| --- | --- |
| 请求 | `{key: "<convert_key>", count: <个数>}` |
| 成功 `data` | **奖励数组** `[{type, key, count}, …]` |

* 客户端：`Package._loadTable` 把 `table_item[key].convert_key` 存成 `_convertKey`，
  `Bag._package` 发 `convert.convert`；回调把 **`res.data` 整个当奖励数组**
  塞进 `RewardBoxLayer.popReward({reward: res.data}, 9999)`，那一层是
  `_.map(params.reward, …)` 逐项读 `.type`（过 `REWARD_TYPE_SWITCH`）
  —— ⚠️ **`data` 是数组，不是 `{rewards: […]}`**；失败时客户端只 `cc.log("error")`，
  界面上**没有任何提示**。
* 消耗和奖励 key 在 `table_convert_reward`（**客户端一行都不读**，是原版服务端数据）：

  ```
  10300001: {consume: "800001#1#i", reward_key: "10300001"}   // 消耗 800001 x1
  ```

  `consume` 是 `"<道具key>#<数量>#i"`；`reward_key` 的**内容**客户端表里没有
  （全库 0 命中）→ 私服自己定，见 differences §D。
* ⚠️ 抽出来的 `table_item.json` 是**压缩字段**（`ck`/`ic`/`t`…），**没有 `convert_key` 列**
  —— 三个礼包的 key 是照客户端全表抄的（`handlers/convert.py` 的 `CONVERT_KEYS`），
  表里补上这一列之后那段可以删。

验证：`python script/selftest_game.py --only 分享`（rewards 是 map、一天一次、换日清零、
礼包扣道具且 `data` 是数组、没道具不给兑，收尾还原）。

---

## 15. 助战（`friendsupport.*`）

| route | 请求 | 成功 `data` |
| --- | --- | --- |
| `friendsupport.getrecommendsoldiers` | `{levelid}` | **`{recommendList: [ … ]}`**（⚠️ 不是裸数组） |
| `friendsupport.setfriendsupport` | `{soldiers, userecord, …}` | 任意真值（登记出战助战） |
| `friendsupport.delfriendsupport` | 同上 | 任意真值 |

⚠️⚠️ **`getrecommendsoldiers` 的 `data` 必须是对象、里面放 `recommendList`**：

```js
// src/data/friendsupport.js
server.request('friendsupport.getrecommendsoldiers', {levelid}, function (err, res) {
    this._recommendList = res.data;      // ← 整个 data 存下来
    succCb && succCb(res.data);          // ← 回调也只传 data 本身
});
// src/ui/friendsupport/supportchoicelayer.js
function (data) { this._initUI(data.recommendList); }        // ← 层读的是 .recommendList
```

发裸数组的话 `data.recommendList` 是 `undefined` →
`_initUI(undefined)` → `setSupportList(undefined)` 第一句 `if (!list) return;` 直接退出
—— 症状就是**弹窗打得开、里面一个军士都没有**（2026-09-21 之前一直是这样；
`_recommendList = res.data` 那句存的是整个 data，所以查数据类会以为"收到了 20 个"，
容易被误判成"data 本身就是列表"）。

条目里的字段（`SupportChoiceLayer.setSupportList` 逐个读）：

* `npcId` —— `SupportChoiceItem.createSolider` 拿它查 `table_friend_support_npc`，
  NPC 的名字/等级/各兵种军士全在客户端表里，服务端**只需要回 id**；
* `playerId` —— 层拿它查 `friendSupport.userecord`（借出记录，登录块
  `data.friendsupport.userecord`），没有就按 NPC 分支走。

验证：`python script/selftest_game.py --only 助战`；实机量过客户端拿到
`count=20 first=ai001/ai001`（在运行中的客户端里直接调 `getRecommendList` 的回调）。

---

## 16. 私密剧情（`diary.*`）

| route | 请求 | 成功 `data` |
| --- | --- | --- |
| `diary.getdiarybuyinfo` | `{}` | `{diarysBuyInfo: {cid: 1}, updateDiarys: {cid: 行}}` |
| `diary.buyunlockstory` | `{chapterId, levelId}` | **该章的新行**（客户端拿 `data.chapterId` 当 key） |

### 16.1 登录块 `data.diary`

```js
// src/data/diary.js
Diary.ctor(data) {
    this._storyDiarys   = data.storyDiarys;      // { chapterId: 行 }
    this._diarysBuyInfo = data.diarysBuyInfo;    // { chapterId: 1 }
}
isUnlock(cid, lid)       { return !!this._storyDiarys[cid].unlockLevels[lid]; }
isStoryCanShow(cid, lid) { return this.isUnlock(cid, lid)
                                || (!!this._storyDiarys[cid].lockLevels[lid]
                                    && this._diarysBuyInfo[cid] == 1); }
```

行里**只有两个字段会被读**，且都当下标用，所以必须是 **map**（给数组/`null` 会在
`unlockLevels[lid]` 那一步炸）：

| 字段 | 含义 |
| --- | --- |
| `unlockLevels` | 已解锁：`{levelId: 1}` |
| `lockLevels` | 可**购买**的：`{levelId: 1}` |

⚠️ `_diarysBuyInfo[cid] == 1` 是**松散相等**，所以服务端必须给**整数 `1`**
（`true`/`"1"` 在这里能过，但 `{"a":1}` 之类会假；统一给 1 最稳）。

### 16.2 购买链路

```js
// src/ui/illustrated/diarylevelitem.jsc
_clickLayer() { // 未解锁
    BuyPopBox.pop(table_dictionary[3401].replace('%d',
                  table_story_review[chapterId].price), cb, ITEM_KEY.GEM);
}
cb() { dataManager.diary.unlockStory({chapterId, levelId}, function (err, res) {
    if (err) { toast(table_dictionary[<按 err 码>]); return; }
    this._storyDiarys[res.data.chapterId] = res.data;
}); }
```

* 失败码是 `tableswitch low=201 high=204` → 我们按字典顺序对应
  `201 物品不足（3402）/ 202 该章节已解锁（3403）/ 203 标签错误（3404）/
  204 章节错误（3405）`。⚠️ 这个对应是**推的** —— 跳转表没展开（见 differences §D）。
* ⚠️ **`table_story_review[*].price` 客户端表里没有**（本服从客户端抽出来的 38 行
  只有 `image`）。原版价格是**服务端**塞进那张表的，所以：
  * 确认弹窗的文案默认会是「…花费 **undefined** 金条解锁该剧情？」；
  * 登录块的 `data.diary.prices`（`{cid: 价}`，客户端不读）就是给 `patch.js` 用的
    —— 想修文案就在补丁里把价格填回 `table_story_review[*].price`（要重打包 APK）。

### 16.3 私服的取舍

* 可买信息：`table_story_review` 的 **38 章全部可买**（原版按活动天数逐步开，
  客户端表里那个 `activity_chapter_unlock_diary_days = 7` 就是干这个的）。
* 解锁状态：**通关过的关卡算已解锁**（玩家自己看过的剧情；通关记录来自
  `gamesrv/instance.py` 的存档 `levels[key].starMark/playCount`），
  加上**买过的**（存档 `player["diary"]["bought"] = {chapterId: [levelId, …]}`）。
* 价格：`30 + 5 × 章节序号` 金条（`STORY_PRICE` 可覆盖单章）—— 私服自己定的，见
  differences §D。

验证：`python script/selftest_game.py --only 私密剧情`（登录块形状、值是整数 1、
`getdiarybuyinfo` 形状、解锁按章节价扣金条、重复 202 / 错章 204 / 错关 203 /
金条不足 201，收尾还原存档与道具）。

