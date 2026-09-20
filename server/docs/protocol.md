# 协议细节

所有结论都来自两种手段：**jsc 反汇编**（`script/jsc_disasm.py`）和
**运行时观察**（`server/client/hook.js` 的探针日志 / REPL）。

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
  "dailyQuestsTime": "2026-09-18 20:15:33",
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

### 9.2 任务表是客户端静态配置

`table_quest` / `table_quest_condition` / `table_quest_reward` 编译在
`assets/src/table/tablequest*.jsc` 里，服务端没有原始文件。
用 `script/extract_client_tables.py` 让游戏自己把要用的字段吐出来，
存成 `gamesrv/data/table_quest.json`（508 条，只留 type/rank/activate_lv/
activate_quest_key/条件个数/达成值/schedule key）。客户端换版本重跑一次即可。

### 9.3 主线推进

`gamesrv/quests.py` 只维护主线（type=2，共 135 条）：
按 `activate_quest_key` 前序遍历出推进顺序，一次给客户端一个 12 条的窗口，
领掉一条之后窗口往后滑。

`sync.syncupclient` 也实现了：客户端上报 `actquest.questUpdateTime`，
和服务端的 `quests.update_time(player)` 不一致时把整个 quest 块推回去
（这是 `SyncManager.updateByServer` 唯一认的通道）。

### 9.4 任务进度是**服务端**算的

`QuestCenter.checkQuestFinish(key)` 只有一句 `return this._finishQuests[key]`
（「这条领过没有」），客户端**不算**条件。所以 ACHIEVED 完全由服务端决定，
客户端只负责把 `schedule[cur]` 显示成进度条。

条件类型从 `table_quest.desc` 反推（已抽进 `table_quest.json` 的 `ct/cp1/cp2`）：

| type | 含义 | 服务端怎么算 |
|---|---|---|
| `12212` | 队伍中只上阵 cp2 个军士获得胜利 cp1 次 | 关卡胜利时按「上阵人数」分桶计数 |
| `12216` | 队伍中存在 cp2 兵种的军士获得胜利 cp1 次 | 兵种表在客户端 char 表里，简化成「任意胜场」 |
| `13203` | 进行首次军士升级 | `char.upgradesoldierlv` 计数 |
| `13102` | 1 位 cp2 军阶的军士等级达到 cp1 级 | 升到时记该军阶的最高等级 |
| `13204` | 1 位军士进行首次突破 | `char.improvesoldierstar` 计数 |

玩家身上只存几个**计数器**（`player.questStats`），某条任务的 `cur` 现算：

```json
{"wins": 1, "winsByTeamSize": {"3": 1}, "soldierUpgrades": 0, "soldierMaxLv": {}}
```

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
