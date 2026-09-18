# 协议细节

所有结论都来自两种手段：**jsc 反汇编**（`tools/jsc_disasm.py`）和
**运行时观察**（`client/hook.js` 的探针日志 / REPL）。

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
python tools\repl.py "(function(){var out=[];function w(n,d,p){...}...})()"
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
