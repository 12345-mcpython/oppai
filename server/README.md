# 战场双马尾（Oppai / zcsmw）私服模拟器

Kuro Game《战场双马尾》v2.2.0 已经停服。这个项目用 **纯 Python（仅标准库）** 重新实现了
一份服务端，并配套一套客户端补丁，让 Android 模拟器上的**原版客户端**能重新跑起来。

> 仅供个人研究 / 存档 / 客户端逆向学习，请勿用于任何商业用途。
> 本项目不包含任何游戏素材或反编译出来的游戏代码。

---

## 目录

- [1. 成果总览](#1-成果总览)
- [2. 项目结构](#2-项目结构)
- [3. 快速开始](#3-快速开始)
- [4. 客户端补丁](#4-客户端补丁)
- [5. 协议全貌](#5-协议全貌)
- [6. jsc 反汇编器](#6-jsc-反汇编器)
- [7. 踩过的坑（重点）](#7-踩过的坑重点)
- [8. 当前状态与 TODO](#8-当前状态与-todo)

---

## 1. 成果总览

```
[客户端] 启动 → 公告 → 远程配置 → 版本校验 → 服务器列表 → 登录界面      ✅
[客户端] 点「开始游戏」→ 原生 SDK 登录（Java 层补丁，秒成功，无弹窗）    ✅
[客户端] oauth 换 token                                                ✅
[客户端] WebSocket 握手（DH + DES + HMAC）→ 登录成功                    ✅
[客户端] agent.getlogindata → 新号自动 agent.createplayer              ✅
[客户端] cb4AfterLogin → 31 个数据模块初始化                            ✅
[客户端] 切主场景 MainScene                                            ✅
[客户端] 播放新手开场动画                                               ✅
```

整条链路全自动，装上补丁版 APK 启动即可，不需要任何手动操作。

**技术栈**：cocos2d-js v3.6 + SpiderMonkey 33.1.1（`libcocos2djs.so` 里能看到
`JavaScript-C33.1.1`），客户端逻辑编译成 `.jsc`（XDR 字节码，不加密但不留源码）。

---

## 2. 项目结构

```
game_server/
├── run.py                     服务端启动入口
├── gamesrv/
│   ├── config.py              端口 / 地址 / 版本号
│   ├── logx.py                日志 + 原始报文落盘（var/capture/*.jsonl）
│   ├── httpd.py               迷你 HTTP 框架（多端口、路由、全量记录）
│   ├── wsserver.py            迷你 WebSocket 服务端（RFC6455）
│   ├── session.py             WS 登录握手（DH 单位元 + DES）
│   ├── gameproto.py           业务请求 / 响应打包解包
│   ├── accounts.py            账号（登录即注册）
│   ├── store.py               玩家 / 主角 / 队伍数据
│   ├── repl.py                下发给客户端探针的命令队列
│   ├── crypto/des.py          标准 DES（已用客户端真实密文对拍验证）
│   ├── handlers/              业务路由（agent.* / player.*）
│   └── apps.py                cdn / gate / login / game 四个端口的实现
├── client/                    客户端补丁（全部在这里）
│   ├── hook.js                明文 JS 探针（REPL / 日志 / 自动登录 / 进主场景）
│   ├── ServerLoginRunnable.smali   新增的原生登录回调类
│   └── patch_smali.py         打 Java 层登录补丁
├── tools/
│   ├── jsc_disasm.py          ★ jsc 反汇编器（SM33.1.1 XDR 字节码）
│   ├── disasm_func.py         按函数名反汇编
│   ├── gen_opcodes.py         从 Opcodes.h 生成操作码表
│   ├── patch_apk.py           客户端补丁 + 重签名（含 dex 注入）
│   ├── repl.py                在游戏进程里执行任意 JS
│   ├── probe.py               重启客户端 + 批量执行 JS
│   ├── selftest_game.py       不开游戏也能自测业务协议
│   ├── bisect_init.py         逐模块二分，找把 JS 主线程卡死的那个
│   ├── shots.py               连续截图
│   ├── trace_startup.py       跟踪启动时对远程配置的读写
│   └── dump_func.py           字符串提取（早期用的）
└── docs/
    ├── protocol.md            协议逐项细节
    └── reverse-engineering.md 反汇编器原理 + 运行时探测手法
```

---

## 3. 快速开始

### 3.1 起服务端

```powershell
cd game_server
python run.py -v
```

| 端口  | 作用 | 原来对应 |
|-------|------|----------|
| 18080 | CDN：远程配置、热更新清单、公告 | `cdn.shuangmawei.net` |
| 10001 | 网关：服务器在线状态 | `114.55.66.97:16840` 一类 |
| 8080  | 登录：`/oauth/*` + WebSocket | 客户端里硬编码的 `OAUTH_HOST` |
| 10003 | 游戏业务：加密 POST | login 返回的 `gameServUrl` |

> **端口不能随便改**：客户端 `urlconfig.jsc` / `server.jsc` 里的 URL 是**原地字节替换**
> （jsc 里字符串带长度前缀，长度必须一致）：
> `cdn.shuangmawei.net`（19 字节）→ `<host>:<port>` 必须 19 字节，
> `http://114.55.66.97:16840`（25 字节）→ `http://<host>:<port>` 必须 25 字节。
> 换机器时改 `gamesrv/config.py` 的 `PUBLIC_HOST`，并用同样的端口位数重新打包。

### 3.2 打客户端补丁

```powershell
# 0) 准备：原版 APK、apktool 解包目录、JDK、Android build-tools
#    路径可用 GS_APK_SRC / GS_APK_DIR / GS_JAVA_HOME / GS_BUILD_TOOLS 覆盖

# 1) Java 层补丁（只做一次）
python client\patch_smali.py

# 2) 重新编译 dex（只重打 dex，不动资源）
cd <apktool 解包目录>
apktool.bat b . --no-apk --no-crunch        # 产物在 <解包目录>\build\apk\classes*.dex

# 3) 打 URL 补丁 + 注入探针 + 注入 dex + 重新签名
cd game_server
python tools\patch_apk.py --host <主机IP> --port 18080 --login-port 8080

# 4) 安装
adb install -r -d E:\code\apk\work\zcsmw-mod-signed.apk
```

### 3.3 不开游戏自测服务端

```powershell
python tools\selftest_game.py
```

自己按客户端格式打包加密请求直接打服务端，验证「加解密 + 路由 + code=200」。
改服务端时不用每次都装 APK 起游戏。

---

## 4. 客户端补丁

分 **Java(smali) 层**、**JS 探针**、**现代化/精简** 三部分。

### 4.0 现代化与精简

原版 APK 是 2015 年的东西：**`minSdkVersion: 9`（Android 2.3）而且根本没有
`targetSdkVersion`**（默认取 minSdk），56 个 Activity 里绝大多数是已经停服的
百度/微博/推送/Bugly SDK 组件。

```powershell
# 0) 先备份！
copy zcsmw.apk backup\zcsmw-original.apk

# 1) 现代化（改 manifest / apktool.yml / 注入运行时权限申请）
python client\modernize.py

# 2) 重新编译 smali+资源，再打包（--strip 默认剔除广告图和百度支付插件）
apktool b <解包目录> --no-apk --no-crunch
python tools\patch_apk.py
```

`client/modernize.py` 做的事：

| 项 | 改动 |
|---|---|
| `minSdkVersion` | `9` → `21` |
| `targetSdkVersion` | 无 → **`23`**（见下面的警告） |
| `usesCleartextTraffic` | `true`（模拟服是明文 HTTP） |
| `extractNativeLibs` | `true` |
| `requestLegacyExternalStorage` | `true` |
| 运行时权限 | 新增 `client/PermissionHelper.smali`，在 `AppActivity.onCreate` 注入一次申请 |

> #### ⚠️ targetSdk 必须停在 23，不能再往上提
>
> 这个 2016 年的 QuickSDK / 百度 SDK 有两个硬性上限，都踩过：
>
> | targetSdk | 结果 |
> |---|---|
> | 9（原版） | 一切正常，但现代 Android 会拦安装 / 弹「为旧版 Android 打造」 |
> | **23** | ✅ **能用**：装得上、没有旧版警告、SDK 也正常 |
> | 27 | ❌ 能启动，但百度 SDK 初始化失败并不断重试 → **屏幕一直闪** |
> | 28+ | ❌ 起不来：隐藏 API 限制 |
>
> **24 起：`MODE_WORLD_READABLE` 会抛异常**
>
> ```
> E/channel.baidu: at com.quicksdk.apiadapter.baidu.SdkAdapter.init
> E/channel.baidu: at com.quicksdk.Sdk.init
> D/BaseLib.BIN  : =>BIN onFailed message = MODE_WORLD_READABLE no longer supported
> ```
>
> 百度 SDK 用 `MODE_WORLD_READABLE` 写文件，Android 7.0（API 24）起
> targetSdk >= 24 的应用调用它会直接抛 SecurityException。
> 初始化失败 → 不断重试 → 每次重试 show/dismiss 一次 QuickSDK 的
> 小 Loading Dialog（`com.quicksdk.utility.g`，居中 69×69 的 APPLICATION 窗口），
> 表现出来就是**屏幕一直在闪 + 中间一个转圈**。
>
> **怎么定位的**：`screencap` 连抓 10 帧，发现内容帧和空白帧交替（约各一半）；
> 再 `dumpsys window windows` 看到一个 **69×69 居中的 APPLICATION 窗口**
> （另一个同款窗口处于 `EXITING`）—— 这就是 Java 层的那个转圈。
>
> **28 起：隐藏 API 限制**
>
> QuickSDK 靠反射调隐藏 API 初始化，异常被它自己的 try/catch 吞掉：
>
> ```
> QuickSdkApplication.onCreate
>   -> com.quicksdk.utility.a.a() 返回 null
>   -> IAdapterFactory.adtActivity() 空指针
>   -> FATAL: Unable to create application ...GameApplication
> ```
>
> **为什么 23 是甜点**：`< 24` 不受 MODE_WORLD_READABLE 限制；`< 28` 不受隐藏 API 限制；
> `>= 23` 就能装在现代 Android 上、且不再弹「为旧版 Android 打造」。
> 代价是危险权限变成运行时申请 —— 已经用 `PermissionHelper.smali` 补上了。
>
> 想再往上提，只能先把 QuickSDK / 百度 SDK 整套删掉。

`tools/patch_apk.py --strip` 默认剔除：

```
assets/res/adimage/        广告图（广告服务早已下线）
assets/res/adcolumn/
assets/bdpwxpayplugin.apk  百度支付插件
```

**精简效果**：551.6 MB → 547.6 MB（-3.95 MB）。实话实说，**能安全砍的只有这么多** ——
`assets/res` 占 540 MB，里面是 `.png` 289.6 MB + `.mp3` 178.6 MB，
都是游戏必需资源。想大幅瘦身只能上有损压缩
（pngquant 量化 + mp3 降码率，预估能到 ~280 MB，但立绘会有色带、语音音质下降）。

### 4.1 Java 层：让 SDK 登录直接成功（关键）

原来的调用链：

```
JS jsb.reflection.callStaticMethod(QuickAdapter, "login")
  -> QuickAdapter.login()                       (smali)
  -> AppActivity.login() -> runOnUiThread(AppActivity$8)
  -> QuickSDK.User.login(activity)
  -> 渠道 SDK(百度) -> com.baidu.platformsdk.LoginActivity 弹窗
  -> 登录成功后原生 evalString('quicksdk.sdkLoginCallback(1, "uid", "token")')
```

QuickSDK / 百度服务器早就下线，弹窗永远登不进去。`client/patch_smali.py` 把
`QuickAdapter.login()` 换成：

```smali
.method public static login()V
    .locals 4
    sget-object v0, Lorg/cocos2dx/javascript/AppActivity;->instance:Lorg/cocos2dx/javascript/AppActivity;
    new-instance v1, Lorg/cocos2dx/javascript/ServerLoginRunnable;
    const-string v2, "emulator"
    const-string v3, "emulator-token"
    invoke-direct {v1, v2, v3}, Lorg/cocos2dx/javascript/ServerLoginRunnable;-><init>(Ljava/lang/String;Ljava/lang/String;)V
    invoke-virtual {v0, v1}, Lorg/cocos2dx/lib/Cocos2dxActivity;->runOnGLThread(Ljava/lang/Runnable;)V
    return-void
.end method
```

配套新增 `client/ServerLoginRunnable.smali`，仿照 `AppActivity$6$1`
（QuickSDK 真正登录成功时的回调）拼出并 eval：

```js
quicksdk.sdkLoginCallback(1, "emulator", "emulator-token")
```

这样点「开始游戏」走的还是游戏自己的原始流程，只是原生登录瞬间成功、弹窗不再出现。

### 4.2 JS 探针 `client/hook.js`

明文 JS，通过改 `project.json` 的 `jsList` 注入（jsc 不存在时会回退到明文，实测可行）。
它做这些事：

| 功能 | 说明 |
|------|------|
| `REQ / PACK / UNPACK / WS / GAMELOG` 日志 | 把网络层调用、加密函数十六进制输入输出、WebSocket 生命周期、游戏自己的 `cc.log` 全部转发到 logcat |
| `SCHEMA` 探测 | 用 `Proxy` 记录客户端读了响应里的哪些字段 |
| **REPL** | 轮询 `<cdn>/hook/poll` 执行返回的 JS 表达式，结果 POST 回 `/hook/result` —— 等于在游戏进程里开了个控制台 |
| 重写 `server.request` | 客户端 `reqPack` 依赖登录时写入的闭包变量 `session`/`key`，模拟环境下拿不到，用客户端自己的 `crypt` 重新实现了一遍 |
| 模块构造容错 | 某个模块数据没对齐时不让它把整个登录流程打断 |
| 登录收尾 | `agent.getlogindata` → `agent.createplayer`(新号) → `cb4AfterLogin` → `_enterMain()` |
| 登录按钮接管 | 原版「登录」走 `userLogin → requestToken`，参数对不齐会弹 JSON 提示框，改成跟「开始游戏」同一条路 |

两个开关（`hook.js` 顶部）：

```js
var AUTO_AFTER_LOGIN = true;    // 登录成功后自动跑 cb4AfterLogin + 切主场景
var AUTO_CLICK_START = false;   // 是否自动代替玩家点「开始游戏」（默认关，手动点）
```

### 4.3 其它重定向

`tools/patch_apk.py` 会做（全部是**原地等长字节替换**）：

| 文件 | 替换 |
|------|------|
| `assets/srcex/urlconfig.jsc` | `cdn.shuangmawei.net` → `<host>:18080` |
| `assets/src/util/server.jsc` | `http://114.55.66.97:16840` → `http://<host>:8080` |
| `assets/src/data/share.jsc` | 分享服务地址 → 同上 |
| `assets/src/patch/project.manifest` | 热更新地址 |

---

## 5. 协议全貌

细节（含反汇编证据）见 [`docs/protocol.md`](docs/protocol.md)，这里只列骨架。

### 5.1 远程配置（CDN）

`GET /android/v2.2.0/config/config.txt`，客户端 `getRemoteConfig(key)` 读**顶层字段**：

```jsonc
{
  "version": { "2.2.0": { /* 键必须是 op.appVersion */ } },
  "update":  { "code": 0, "msg": "" },     // code != 0 会弹窗拦下
  "servers": [ /* 服务器列表 */ ],
  "table_dictionary": { /* 覆盖本地文案 */ }
}
```

### 5.2 登录链路

```
GET  http://<gate>/get_login?key=<logindKey>      → gamedStatus / loginPort / port
GET  <OAUTH_HOST>/oauth/request_token?account=..&password=..
                                                  → {code, msg, data:{token, ...}}
WS   ws://<host>:<loginPort>                      握手（见下）
POST http://<gameServUrl>/                        业务（见下）
```

WebSocket 握手：

```
S -> C   base64(欢迎包)                      触发客户端 onmessage 开始握手
C -> S   base64(clientKey)                   dhExchange(randomKey())
S -> C   base64(serverKey)                   服务端 DH 公钥
C -> S   base64(hmac64(hashKey(seed#clientKey), secret))
C -> S   base64(desEncode(secret, utf8(登录信息 JSON)))
S -> C   base64(desEncode(secret, utf8({code,session,gameServUrl,userId,data})))
```

真实抓包：

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

#### DH 的「单位元」技巧

客户端的 `crypt.dhExchange` / `dhSecret` 是自研实现（`biPowMod` + `dhTransTo64`）。
实测它满足正常 DH 群性质，并且存在单位元：

```
X = dhExchange(00 00 00 00 00 00 00 00) = 01 00 00 00 00 00 00 00
dhSecret(X, *) == dhSecret(*, 00…00) == X
```

服务端把自己的公钥设成 `X`，**双方共享密钥必然等于 `X`**，完全不需要知道 p / g。
经典 small-subgroup 思路，对单机模拟服够用且省事。

#### DES

`crypt.desEncode(key, msg)` **就是标准 DES-ECB**，补位是 ISO/IEC 9797-1 method 2
（先补 `0x80` 再补 `0x00`）。`gamesrv/crypto/des.py` 已用标准测试向量
（`133457799BBCDFF1` / `0123456789ABCDEF` → `85E813540F0AB405`）
和真实客户端密文往返双向验证。

### 5.3 业务协议（HTTP POST）

```js
// reqPack(route, msg, reqId)
data = crypt.desEncode(key, crypt.utf16To8(JSON.stringify({route, msg, reqId})))
body = crypt.base64Encode(data)

// resUnpack(data, key)
if (data.indexOf('"code":') >= 0) return JSON.parse(data)   // 明文错误包
return JSON.parse(crypt.utf8To16(crypt.desDecode(key, crypt.base64Decode(data))))
```

**⚠️ 业务成功码是 `200`，不是 `0`。** 反汇编 `dataManager.playerLogin/</<`：

```js
var code = data.code;
if (code === 200) cb4AfterLogin(null, data.data);
else              cb4AfterLogin(data, null);   // ← 把响应当错误 → 弹「温馨提示」显示 JSON
```

`Player.updateGuideMark` 的回调同样判断 `data.code == 200`，不是 200 就会**无限重发**。

### 5.4 `agent.getlogindata` 的模块 key

**key 名不是模块名**！从 `dataManager.initUserData` 反汇编读出来的映射：

| data 里的 key | 构造的类 | dataManager 属性 |
|---|---|---|
| `player` | `Player` | `player` |
| `instance` | `Instance` | `instance` |
| `item` | `Bag` | `bag` |
| `char` | `CharCenter` | `character` |
| `gacha` | `Gacha` | `gacha` |
| `mail` | `Mailbox` | `mailbox` |
| `actquest` | `ActQuestCenter` | `actQuestCenter` |
| `quest` | `QuestCenter` | `questCenter` |
| `favor` | `FavorCenter` | `favorCenter` |
| `favorevent` | `FavorEventCenter` | `favorEventCenter` |
| `friend` | `Friend` | `friend` |
| `exchange` | `ExchangeCenter` | `exchangeCenter` |
| `talents` | `TalentCenter` | `talentCenter` |
| `sign` | `SignCenter` | `signCenter` |
| `shop` | `Shop` | `shop` |
| `arena` | `ArenaCenter` | `arenaCenter` |
| `rank` / `score` | `Rank` / `Score` | `rank` / `score` |
| `society` / `societyclg` | `Society` / `SocietyClg` | 同名 |
| `boss` | `BossCenter` | `bossCenter` |
| `chat` / `detect` / `medal` | `Chat` / `Detect` / `Medal` | 同名 |
| `equipment` | `EquipmentCenter` | `equipmentCenter` |
| `share` | `Share` | `share` |
| `subareaachievement` | `SubareaAchievement` | `subareaachievement` |
| `consumeactivity` | `ConsumeActivity` | `consumeActivity` |
| `diary` | `Diary` | `diary` |
| `friendsupport` | `FriendSupport` | `friendSupport` |
| `novicequest` | `NoviceQuestCenter` | `noviceQuestCenter` |

另外 `data.agent` **必须直接带 `timeSec` / `timezoneOffset`**（`initUserData` 第一句是
`syncTime(data.agent, ...)`，读的是 `data.agent.timeSec` 而不是 `data.agent.timeObj.timeSec`），
还要带 `msgCenterAddr` / `worldChatLimit`（给 `chat.init` 用）。

---

## 6. jsc 反汇编器

`.jsc` 是 SpiderMonkey 33.1.1 的 XDR 字节码，不加密、但不保留源码
（`Function.prototype.toString()` 只给 `[sourceless code]`）。
既然格式固定，就自己写了一个解析器：

```powershell
python tools\gen_opcodes.py                        # 从 Opcodes.h 生成操作码表
python tools\jsc_disasm.py <file.jsc> [起] [止]     # 反汇编
python tools\disasm_func.py <file.jsc> --list      # 列出所有函数
python tools\disasm_func.py <file.jsc> cb4AfterLogin
```

反汇编出来的 `dataManager.cb4AfterLogin`：

```
 0  this / 1 getarg 0 (err) / 4 not / 5 setprop "isLogin"      // this.isLogin = !err
11  getarg 0 / 14 not / 15 ifeq -> 67                          // if (err) 跳过初始化
20  name "server"    / 26 callprop "syncLastServer"
36  this / 38 callprop "initUserData" / 44 getarg 1 / 47 call 1
51  name "gameEvent" / 57 callprop "onLogin"
67  getarg 2 (cb) / 70 and -> 89                               // if (cb)
76  getarg 2 / 79 undefined / 80 getarg 0 / 83 getarg 1 / 86 call 2
90  retrval
```

原理和踩的坑写在 [`docs/reverse-engineering.md`](docs/reverse-engineering.md)。
几个要点：

1. `magic = 0xb973c02c = 0xb973c0de - 178`，和 `FIREFOX_33_1_1_RELEASE` 的
   `vm/Xdr.h` 完全一致，源码可从
   `https://hg-edge.mozilla.org/releases/mozilla-release/raw-file/FIREFOX_33_1_1_RELEASE/js/src/...` 拉。
2. 字段顺序：`nargs nblocklocals nvars length prologLength version natoms nsrcnotes
   nconsts nobjects nregexps ntrynotes nblockscopes nTypeSets funLength scriptBits`
   → `bindings → ScriptSource → sourceStart/End → lineno/column/nslots/staticLevel
   → code[length] → notes → atoms → consts → objects → ...`
3. `scriptBits & (1<<12)`（OwnSource）时中间会插一段 `ScriptSource::performXDR`。
4. **真正的代码在嵌套 lambda 里**：`data/*.jsc` 外层脚本只有 26 字节
   （`var X = (function(){...}).call()`），全部逻辑在 `objects[0]` 那个函数里，要递归解析。
5. **字节码立即数是大端序**（最坑的一点）。
6. 操作码表从 `Opcodes.h` 自动生成（229 个），注意 `JOF_TMPSLOT2/3` 里带数字。

---

## 7. 踩过的坑（重点）

### 7.1 服务端 / 协议

| 坑 | 现象 | 原因 |
|---|---|---|
| **成功码是 200 不是 0** | 引导进度无限重发、登录弹 JSON 提示框 | 客户端判断 `data.code === 200` |
| **`data.agent` 要直接带 `timeSec`** | 进登录后 JS 主线程死循环（点哪都没反应） | `syncTime(data.agent)` 读 `timeSec`，拿不到就是 `NaN` |
| **模块 key 不是模块名** | `new Bag(undefined)` → 后面全崩 | `new Bag(data.item)`、`new Mailbox(data.mail)`…… |
| **时间字段必须是字符串** | `Player.ctor` 崩在 `getTimeStr`/`getDateStr` | `actionPointTime` / `createTime` 要 `"YYYY-MM-DD HH:mm:ss"` |
| **URL 原地等长替换** | APK 打包报"长度不一致" | jsc 字符串带长度前缀，`cdn.shuangmawei.net` 必须换 19 字节 |
| **WebSocket 要回显子协议** | 客户端 `onopen` 不触发 | 请求里带 `Sec-WebSocket-Protocol`，不选一个客户端就会主动断开 |
| **`onSuccCb` 靠第一条消息触发** | 连上了但什么都不发生 | 服务端先发一条 base64 欢迎包，客户端 `onmessage` 才开始握手 |

### 7.2 客户端 / 逆向

| 坑 | 现象 | 解决 |
|---|---|---|
| **原生 `new XMLHttpRequest()` 发不出去** | 探针的 XHR 永远不回调 | 用 `cc.loader.getXMLHttpRequest()` |
| **GET 的 body 传数字会静默卡住** | 轮询请求根本没发出 | body 必须 `String(...)` |
| **`jsb.fileUtils.writeStringToFile` 不存在** | 探针写文件失败 | 改用 logcat |
| **游戏自己的 `cc.log` 不进 logcat** | 看不到客户端内部报错 | 探针包一层 `console.log`/`cc.log` 转发（`GAMELOG`） |
| **`reqPack` 依赖闭包变量** | `server.request` 静默不发 | 探针用客户端自己的 `crypt` 重新实现 `request` |
| **`window.op` 是共享命名空间** | 猜不出远程配置的 key | 给 `op` 套 `Proxy` 记录属性访问 |
| **返回值不是简单类型** | `version` 不能直接比 | 用 `Proxy` + `Error().stack` 定位到 `version[appVersion]` |
| **jsc 立即数是大端序** | 反汇编全乱 | 所有字节码立即数按大端读 |

---

## 8. 当前状态与 TODO

### 已完成

- [x] 服务端：CDN / 网关 / oauth / WebSocket 登录 / 加密业务路由
- [x] DES 用标准实现复刻并双向验证；DH 用单位元技巧绕过
- [x] Java 层去掉百度登录弹窗
- [x] 全自动登录 → 主场景 → 新手开场动画
- [x] jsc 反汇编器（含递归解析嵌套函数）

### 待办

- [ ] **业务路由**：目前只有 `agent.*` / `player.updateguidemark`，
      其余（`exchange.checkorder`、`rank.getrankinglist`、各模块的 `*.getxxx`）
      只回空 data。有了反汇编器可以直接看对应模块的 `updateByServer`/`*Cb` 读哪些 key：

      ```powershell
      python tools\disasm_func.py <assets>\src\data\rank.jsc updateByServer
      python tools\disasm_func.py <assets>\src\manager\datamanager.jsc --list
      ```

- [ ] `TalentCenter` 构造报 `this._talentTypes[v.type] is undefined`，
      需要按 `table_talent_type` 补 talent 数据（现在靠探针容错跳过）
- [ ] `hashKey` / `hmac64` 还没复刻（登录靠单位元绕过；要校验 `etoken` 才需要）
- [ ] 自研 DH (`dhExchange`/`dhSecret`) 的完整算法也可以直接反汇编还原
- [ ] `player.teams` 目前只给 5 支空队伍 + 主角 `hadf` + 机甲 `madflj`
      （key 取自 `table_hero` / `table_mecha`），战斗相关玩法需要继续补

---

## 9. 免责声明

- 本项目**不包含**任何游戏素材、`.jsc` 字节码、反编译产物或 APK。
- 所有代码均为自行编写，仅通过公开的 SpiderMonkey 源码与运行时观察还原协议。
- 请仅用于个人学习研究，不要传播游戏素材或用于商业用途。
