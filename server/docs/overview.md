# 项目全景（资料总览）

> 这份是「一张图看懂整个项目」的总览，把散在 README / docs / 脚本注释里的结论收拢到一处。
> 遇到具体问题再按最后一节的**文档地图**跳过去看细节。

---

## 0. 一句话

《战场双马尾》v2.2.0 已经停服。这个项目用**纯 Python（仅标准库）**重写了一份服务端，
配一套客户端补丁，让 Android 模拟器上的**原版客户端**重新跑起来 ——
包括登录、主界面、编成、主线任务、战斗。

客户端一行源码都没有，全部结论来自：

1. `.jsc` 字节码反汇编（自己写的反汇编器）
2. 在游戏进程里开 REPL，把客户端当 oracle 反复试
3. logcat + tombstone + 自己编译引擎复现

---

## 1. 端到端链路：现在能跑到哪

```
启动 → 公告（私服自己的 HTML） → 远程配置 → 版本校验 → 服务器列表
     → 登录界面 → 点「开始游戏」→ 原生 SDK 登录（Java 补丁，秒成功）
     → oauth 换 token → WebSocket 握手（DH 单位元 + DES + HMAC）
     → agent.getlogindata → 新号自动建号 → 31 个数据模块初始化
     → 切主场景 MainScene → 开场动画 → 主界面                ✅ 全自动
```

进了主界面之后：

| 模块 | 状态 | 说明 |
|---|---|---|
| **编成 → 上阵队伍 → 加号** | ✅ | 18 个初始士兵（前锋/中卫/后卫各 6，角色不重复） |
| **副本 / 关卡（主线战斗）** | ✅ | 11 章 / 1142 关，关卡表在客户端；服务端只存进度 |
| **任务 → 主线任务** | ✅ | 12 条窗口；**进度接的是真实战斗**（打一次关卡胜利 = 一次任务进度） |
| 编成 → 军士升级 | ⚠️ | `char.upgradesoldierlv` 还没接（任务 `13203 首次升级` 做不了） |
| 日常 / 成就任务 | ⚠️ | 数据是空的（只做了主线，`type=2`） |
| 开场/引导视频 | ✅ | 视频层清理 + 幂等守卫 |
| 扭蛋 / 抽卡 | ⚠️ | 缺运营配置（`gachaMasterList`），靠兜底不让它崩 |
| 培养（天赋） | ⚠️ | `TalentCenter` 构造抛异常（被容错吞掉），界面大概率是空的 |
| 排行榜 / 交易所 / 好友 Boss 等 | ❌ | 路由只回空 data（stub） |

**"能点但没内容"** 的典型原因就是上面这些数据缺口 —— 客户端不会崩，只是列表空。

---

## 2. 系统分层

```
┌───────────────────────────────────────────────────────────────┐
│ 原版 APK（只做原地等长字节替换 + Java 层补丁 + 注入明文 JS）      │
│   ├─ Java/smali   登录弹窗绕开、targetSdk=23                      │
│   ├─ assets/*.jsc URL 重定向到私服                               │
│   └─ assets/src/patch/{patch,probe}.js  ← 明文 JS，绕过 jsc 限制  │
├───────────────────────────────────────────────────────────────┤
│ libcocos2djs.so   自己用 NDK r10e + cocos2d-js v3.6 源码重建      │
│   └─ 11 个引擎补丁（见 E:\code\apk\move\ENGINE_PATCHES.md）        │
├───────────────────────────────────────────────────────────────┤
│ 服务端（Python 标准库）                                          │
│   cdn:18080  静态资源 + 公告 + 远程配置 + REPL 控制接口            │
│   gate:10001 /get_login    login:8080 oauth    game:10003 业务    │
└───────────────────────────────────────────────────────────────┘
```

服务端模块：

```
gamesrv/
  config.py       端口 / 对外地址 / 版本号
  httpd.py        迷你 HTTP 框架（多端口 + 全量报文落盘）
  wsserver.py     迷你 WebSocket（RFC6455）
  session.py      WS 登录握手（DH 单位元 + DES + 回显子协议 + 主动发欢迎包）
  gameproto.py    业务请求/响应打包（含请求体 session 前缀解析）
  accounts.py     账号（登录即注册）
  store.py        玩家存档（players.json）+ 字段迁移
  quests.py       主线任务（窗口推进、领奖状态机）
  repl.py         下发给探针的命令队列
  crypto/des.py   标准 DES（用客户端真实密文对拍验证过）
  handlers/       agent.*  player.*  quest.*（16 条路由）
  data/           table_quest.json（从客户端抽出来的表）
  apps.py         cdn / gate / login / game 四个端口的实现
```

---

## 3. 关键逆向成果

### 3.1 `.jsc` = SpiderMonkey 33 XDR 字节码

* 不加密，但**不含源码**：`Function.prototype.toString()` 只会给 `[sourceless code]`。
* 文件 = `uint32 magic` + `XDRScript`；真正的业务代码全在**嵌套 lambda**（对象字面量里的方法）里。
* **字节码立即数是大端序** —— 这一条不搞对，反汇编全乱。

工具：`tools/jsc_disasm.py`（全量反汇编）、`tools/disasm_func.py`（按函数名）、
`tools/jsc_strings.py`（只扒 atom 表，定位逻辑最快的一把刀）。

`jsc_strings.py` 的原理：SM33 对每个函数脚本按**源码顺序**写一组 atom，
编码是 `<uint32 (2*len+1)> <len 字节 ASCII>`。于是能恢复出

```
MainLayer<._initModuleButtons     ← 函数（debug name）
mainUiLayer modules               ← 局部变量（按声明顺序）
key row node module ...
_mainUiLayer dataManager player moduleState ...   ← 函数体里用到的属性，按出现顺序
```

一眼就能看出 `var modules = dataManager.player.moduleState;`。

### 3.2 协议

| 环节 | 关键点 |
|---|---|
| 远程配置 | `getRemoteConfig(key)` 读**顶层字段**；`update.code != 0` 会弹窗拦下 |
| 网关 | `GET /get_login?key=<logindKey>` |
| oauth | 登录即注册；返回 `code` 必须对 |
| WebSocket | 服务端**必须先发一条** base64 欢迎包，否则客户端 `onopen` 不触发；必须**回显 `Sec-WebSocket-Protocol`** |
| DH | 自研实现，但存在单位元 `dhExchange(0) = 01 00 00 00 00 00 00 00`，服务端公钥设成它即可 |
| 加密 | 标准 DES-ECB + ISO/IEC 9797-1 method-2 补位（`0x80` + 0）+ base64 |
| 业务请求 | `base64(" " + sessionId)` **+** `base64(des({route,msg,reqId}))` 两段拼 |
| 业务响应 | `base64(des({code,msg,data}))`；**成功码是 200 不是 0** |
| 错误响应 | 可以是明文 JSON（客户端只要看到 `"code":` 就直接当 JSON 解析） |

### 3.3 引擎：游戏那版**不是** vanilla cocos2d-js 3.6

游戏当年的引擎是打过补丁的更新版本，已证实的三处差异：

| 差异 | vanilla 3.6 | 游戏那版 |
|---|---|---|
| `ui::Helper::seekNodeByName(Node*, name)` | 不存在（只收 `Widget*`） | 存在（3.7+ 才加） |
| `RotationSkewFrame::onApply` 运算符优先级 | 有 bug（4 处） | 已修 |
| `setLastFrameCallFunc` 回调 | `invoke(0, …)` 不传参 | 传**动画名** |

第三条是**战斗收尾卡住**和**过场黑屏**的共同根因：
vanilla 不传参 → 游戏代码 `function (eventName) { if (/began\d/.test(eventName)) … }`
永远拿不到名字 → 链条断在第一步。

11 个引擎补丁的完整清单 + 证据链见 `E:\code\apk\move\ENGINE_PATCHES.md`，
每个改动都有一个可重复执行的脚本在 `E:\code\apk\move\build\*.py`。

---

## 4. 服务端的数据从哪来

这是这个项目最容易踩的一类坑：**客户端对数据形状的要求非常硬**，
少一个字段就是 `TypeError` 或者「列表能看但点不了」。

| 数据 | 来源 | 备注 |
|---|---|---|
| 模块开启状态 `moduleState` | 服务端生成 | key 是 `table_function_open` 的编号 100001~100032 |
| 士兵 / 主角 / 机甲 | 服务端生成 | key 必须真实存在于 `table_soldier` / `table_hero` / `table_mecha` |
| **任务表** | `tools/extract_client_tables.py` 从客户端抽 | 存成 `gamesrv/data/table_quest.json` |
| 任务进度 | 服务端维护 | `players.json` 里的 `quests.done` |
| 扭蛋配置 / 副本关卡 | **缺** | 运营配置类数据，客户端表里也没有，得自己想 |

**抽表的套路**（后来加功能可以直接复用）：
客户端表编译在 `assets/src/table/table*.jsc` 里，服务端没有原始文件 →
开 REPL 让游戏自己把要用的字段 `JSON.stringify` 吐出来 → 存成服务端的 JSON。

---

## 5. 客户端补丁：哪些是必须的，哪些只是诊断

`client/` 下分两个文件，**打包时可以只带前一个**（`tools/build_apk.py --no-probe`）：

### `patch.js` —— 必须的适配（进正式包）

| 补丁 | 为什么 |
|---|---|
| `ccui.helper.seekNodeByName/ByTag` polyfill | 游戏那版引擎有，v3.6 没有 |
| `ActionTimeline.setLastFrameCallFunc` 包装 | 同上（引擎层已经根治，这层是保险） |
| `ccui.WebView` polyfill | v3.6 没这个绑定；公告是私服自己的 HTML |
| 新手引导跳过 | 引导会吃掉所有菜单点击，私服数据下它会永远卡住 |
| `Gacha.getGachaFullInfo` 空壳兜底 | 缺扭蛋配置时会抛异常，把 `initUserData` 后半段全打断 |
| 数据模块构造容错 | 某个模块数据没对齐时不连累整体 |
| `initUserData` 兜底 | 无论如何保证 `player._moduleState` 建出来（否则主界面黑屏） |
| **`RESP-DISPATCH`** | 客户端 `responseConfig` 在这套引擎上根本没被派发，自己补一层响应分发 |

### `probe.js` —— 诊断（`--no-probe` 时不打包）

日志转发、`Proxy` 探字段、REPL、调用序列追踪、异常 stack、登录期/战斗期各种 hook。

> ⚠️ 探针的第一原则：**只包一层，不要重写**。
> 早先的版本把 `server.request` 整个重写了，结果把客户端原生的响应派发整条路绕掉了，
> 害得「服务端推数据」全部失效，查了很久才反应过来。

---

## 6. 坑速查：按症状找原因

| 症状 | 真正原因 | 在哪 |
|---|---|---|
| 点开始游戏弹 `温馨提示 {"code":1,"msg":"bad request"}` | 请求体前面的 `base64(" " + sessionId)` 没摘掉，DES 解不出来 | `gameproto.split_session_field` |
| 登录后**黑屏**，日志 `modules is undefined @ mainlayer.js:188` | `initUserData` 中途抛异常 → `player.initModuleState()` 没跑到 → `_moduleState` 是 undefined | §7 表 + `INITUSERDATA-GUARD` |
| 主界面按钮**全都点不动**，引导一直让你点某个按钮 | 引导层把菜单点击吃了（`op.uiLoader.addTouchEventListener`） | `patch.js` GUIDE-SKIP |
| 编成 → 加号**点了没人** | 士兵列表为空 / 全是同一角色 / 默认站位页没兵 | `store.SOLDIER_KEYS` |
| 任务**领不了** | 没回 `id`（客户端本地就 return，服务端收不到请求） | `quests._quest_entry` |
| 任务进度条不显示 / 领奖按钮是灰的 | `schedule` 回了数组，实际要**对象** | 同上 |
| 领了奖**列表不刷新** | 领过的任务要再回一次 `state:"4"`（`updateByServer` 只覆盖不清理） | `quests.block` |
| **反复刷新还是同两条任务** | 领奖进度没写回文件（`get_or_create_player` 返回的是临时副本） | `store.save_player` |
| 战斗结束卡住 | `setLastFrameCallFunc` 不传动画名 | 引擎补丁 ① |
| 战斗中原生崩溃（SIGSEGV） | `RotationSkewFrame::onApply` 运算符优先级 | 引擎补丁 ② |
| 编好队点战斗弹**「队伍数据异常，请重新登陆」**然后闪退 | 客户端 `Soldier._originData` 防篡改快照比对失败 —— 少发 `skillLv`（`_mainSkill.lv = args.skillLv \|\| 1`） | `store.new_soldier` |
| 进关卡弹**「没有甜甜圈了 是否需要补充行动力」** | 行动力是背包道具（`100003`），不是 `player.actionPoint`；而 `data.item` 必须是**平铺映射** | `agent._module_stubs` |
| 屏幕被青色的视频层盖住 | Android 侧 `VideoView` 还 VISIBLE | `patch.js` LGL-GUARD |
| 日志刷屏（每帧一条） | 引擎自己的 LOGD | 引擎补丁 ③ + `vlog()` |

---

## 7. 现状与待办

### 已完成

- [x] 服务端：CDN / 网关 / oauth / WebSocket 登录 / 加密业务路由（16 条）
- [x] DES 标准实现双向对拍；DH 用单位元绕过
- [x] Java 层去掉百度登录弹窗；删掉 46.6MB 没用的第三方 SDK
- [x] 全自动登录 → 主场景 → 开场动画 → 主界面
- [x] **引擎源码重建**（11 个补丁），战斗可完整跑完
- [x] `jsc` 反汇编器 + atom 表提取器 + 运行时 REPL 探针
- [x] 编成 / 上阵队伍（含士兵数据）
- [x] 主线任务（窗口推进 + 领奖 + 刷新）
- [x] 文档：协议 / 逆向手法 / 打包逻辑 / 本总览

### 待办（按卡点排序）

1. **编成 → 军士升级（`char.upgradesoldierlv`）** —— 主线 210001「首次升级」
   就等这个；同时 210002+「某军阶军士等级达到 N 级」也要它。
   `char.improvesoldierstar`（突破）同理。
2. **扭蛋 / 抽卡** —— 缺 `gachaMasterList` 运营配置；现在只保证不崩。
   `GUIDE_GACHA_KEY = 1002`（`GACHA_KEYS.GEM`）。
3. **培养（天赋）** —— `TalentCenter` 构造抛 `this._talentTypes[v.type] is undefined`，
   已试过 7~8 种形状都没在 REPL 里复现，怀疑 `initUserData` 传进去的不是 `data.talents`，
   需要在探针里把构造参数打出来再登一次才能确定。
4. **日常 / 成就任务** —— 只做了 `type=2`（主线）；日常 246 条 / 成就 91 条。
5. **战果报告的「获得物资」还是空的** —— 服务端已经把通关奖励算出来了
   （`level.dropReward / firstComplete / appraise / levelReward`，日志里能看到
   `掉落={'100002': 593} 首通={'100001': 20} exp=60`），但客户端面板读的不是这里：

   `LevelWinBase._init(args)` 读的是 **`args.rewards / args.firstComplete /
   args.appraise / args.rank / args.favorReward`**，而 `args` 是
   `instanceManager.onBattle/</showCb` 拼的那个对象 —— 那个对象里只有
   `againCb / backCb / battleInfo / result`（+`starMark` / `id`），**根本没有 rewards**。
   下一步要么找出原版是从哪儿补进去的，要么在 patch.js 里包一层
   `showCb` 的入参（把服务端回来的奖励塞进 `args`）。

6. **助战（好友支援）列表渲染不出来** —— 服务端已经能正确回 NPC 名单
   （`friendsupport.getrecommendsoldiers` -> 20 个 `npcId`，客户端
   `FriendSupport._recommendList` 里也确实收到了 20 个），
   但 `SupportChoiceLayer` 那边渲染不出来。已确认的：
   `setSupportList()` 手动调是好的（会往 `_pushAsynList` 里塞 18 个
   `{item, innSize, index}`），所以卡在「层的 `_recommendList` 是 0」。
   **不影响战斗**（这个弹窗是可选的好友助战）。
7. **其余 stub 路由** —— `rank.*` / `exchange.*` / `boss.*` / `shop.*` / `mail.*` 等，
   照着对应模块的 `updateByServer` 反汇编补 key 即可。
8. `hashKey` / `hmac64` 还没复刻（登录靠单位元绕过）；自研 DH 的完整算法也没还原。

---

## 8. 工具与工作流

### 8.1 构建链路（每一步都是幂等的）

```powershell
cd E:\code\python\game_server
python client\patch_smali.py          # Java 层补丁
python client\modernize.py            # manifest / targetSdk=23 / 运行时权限
python tools\sdk_strip\strip.py       # 删第三方 SDK + 装桩
python tools\gen_native_stubs.py      # .so 硬依赖的类
# 引擎：ndk-build（见 E:\code\apk\move\build\build.ps1）
python tools\build_apk.py [--no-probe]   # 改 assets + apktool 打包 + 对齐 + 签名
adb install -r -d E:\code\apk\work\zcsmw-mod-signed.apk
```

### 8.2 调试工作流

```powershell
# 服务端（用守护脚本，别用 Start-Process 直接拉 run.py，会被回收）
python tools\serve.py

# 在游戏进程里执行任意 JS（前提：probe 版本 + 服务端在跑）
python tools\repl.py "dataManager.player._moduleState ? Object.keys(dataManager.player._moduleState).length : 'none'"

# 抽客户端表
python tools\extract_client_tables.py

# 反汇编
python tools\jsc_strings.py  <assets>\src\ui\main\mainlayer.jsc _initModuleButtons
python tools\disasm_func.py  <assets>\src\data\questcenter.jsc _createQuest
```

**排障顺序**（按复用性排序）：

1. 先分清是 Java 层 / 引擎层 / JS 层 —— `dumpsys activity top`、tombstone
2. 抓 logcat，`OPPAIPATCH|` / `OPPAIHOOK|` 前缀的日志是补丁和探针打的
3. JS 异常先看 stack；`initUserData` 抛异常会连累一大片（见 §6）
4. 拿不准的数据形状，直接 `new Xxx(candidate)` 在 REPL 里试 —— 几秒钟一个
5. 实在不行就包一层打日志，别猜

> ⚠️ `adb shell input tap` 在 MuMu 上**不可靠**，注入的事件不一定到得了 App。
> 别用它判断"点击坏了"。同理 `adb screencap` 有时抓不到 GL 层（截出来一片白），
> 以用户看到 / 服务端日志为准。

---

## 9. 文档地图

| 文档 | 内容 |
|---|---|
| `README.md` | 上手：项目结构、快速开始、补丁清单、协议骨架 |
| **`docs/overview.md`**（本文） | 全景：成果、分层、逆向结论、坑速查、待办 |
| `docs/protocol.md` | 协议逐项细节 + 反汇编证据（含 quest 协议、session 前缀） |
| `docs/reverse-engineering.md` | jsc 反汇编器原理、运行时探测手法、排障套路 |
| `docs/build.md` | 打包逻辑（为什么这么做） |
| `tools/README.md` | 工具索引（哪个脚本干什么、加新模块的推荐流程） |
| `E:\code\apk\move\ENGINE_PATCHES.md` | 11 个引擎补丁的证据链与复现脚本 |

仓库外的关键路径：

```
E:\code\apk\zcsmw\             apktool 解包目录（smali / assets / lib）
E:\code\apk\work\              打包中间产物 + zcsmw-mod-signed.apk
E:\code\apk\backup\            原版 APK 备份（唯一的一份，别删）
E:\code\apk\move\              引擎移植工作区（cocos2d-js 源码 + NDK + 构建脚本）
E:\code\apk\shots\             截图存档
```

---

## 10. 免责声明

- 本项目**不包含**任何游戏素材、`.jsc` 字节码、反编译产物或 APK。
- 所有代码均为自行编写，仅通过公开的 SpiderMonkey / cocos2d-x 源码与运行时观察还原协议。
- 仅供个人学习研究，请勿传播游戏素材或用于商业用途。
