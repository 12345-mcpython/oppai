# 项目全景（资料总览）

> 这份是「一张图看懂整个项目」的总览，把散在 README / docs / 脚本注释里的结论收拢到一处。
> 遇到具体问题翻 [`pitfalls.md`](pitfalls.md)（按症状查原因）；文档地图在 [`../README.md`](../README.md)。

## 本节目录

- [1. 端到端链路：现在能跑到哪](#1-端到端链路现在能跑到哪)
- [2. 系统分层](#2-系统分层)
- [3. 关键逆向成果](#3-关键逆向成果)
- [4. 服务端的数据从哪来](#4-服务端的数据从哪来)
- [5. 客户端补丁：哪些是必须的，哪些只是诊断](#5-客户端补丁哪些是必须的哪些只是诊断)
- [6. 坑速查：按症状找原因](#6-坑速查按症状找原因)
- [7. 现状与待办](#7-现状与待办)
- [8. 工具与工作流](#8-工具与工作流)
- [9. 关键路径](#9-关键路径)

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
| **编成 → 上阵队伍 → 加号** | ✅ | 18 个初始士兵（前锋/中卫/后卫各 6，角色不重复，全是 `card_type==1` 的自军卡） |
| **副本 / 关卡（主线战斗）** | ✅ | 11 章 / 1142 关，关卡表在客户端；服务端只存进度 |
| **任务 → 主线任务** | ✅ | 12 条窗口；**进度接的是真实战斗**（打一次关卡胜利 = 一次任务进度） |
| **编成 → 军士培养 / 突破 / 技能** | ✅ | `char.upgradesoldierlv` / `improvesoldierstar` / `upgradesoldierskill`；升级公式和客户端逐字段对齐（`script/check_soldier_calc.py`） |
| **日常 / 成就任务** | ✅ | 三类任务（主线/日常/成就）共用 `quest.*`；日常一天放当前等级那一档、换日 05:00、奖励真发 |
| **好友** | ⚠️ | 9 条 `friend.*` 全实现（搜索/申请/同意/拒绝/删除/送收物资/两个列表）；好友是 **NPC**（没有别的玩家），见 §7 |
| **勋章 / 头像 / 衣柜** | ⚠️ | 9 条 `medal.*` 全实现（换头像/衣服/背景、佩戴、清 NEW、好友勋章）；衣柜与头像是**背包道具**（type 40/50/60/70），勋章进度按 `condition_kind` 反推，25 条活动勋章直接算完成，见 §7 |
| 开场/引导视频 | ✅ | 视频层清理 + 幂等守卫 |
| 扭蛋 / 抽卡 | ⚠️ | 缺运营配置（`gachaMasterList`），靠兜底不让它崩 |
| **培养（天赋）** | ✅ | 三条课题（101 军士 / 102 机甲 / 103 克制），`player.selecttalent` / `player.upgradetalent` 落盘、升级真扣材料。⚠️ 入口是编成→培养里的「**萌源增幅**」，要**通关 3-6** 才解锁（`table_function_open[100014].unlock_level_key = "100316"`）；103 还要指挥部 40 级 |
| 背包 / 道具消耗 | ✅ | `gamesrv/items.py` + 存档里的 `items`；买东西 / 抽卡 / 培养的消耗都真的扣、重登不回退 |
| 排行榜 / 交易所 / 好友 Boss 等 | ❌ | 路由只回空 data（stub） |

**"能点但没内容"** 的典型原因就是上面这些数据缺口 —— 客户端不会崩，只是列表空。

---

## 2. 系统分层

```
┌───────────────────────────────────────────────────────────────┐
│ 原版 APK（只做原地等长字节替换 + Java 层补丁 + 注入明文 JS）      │
│   ├─ Java/smali   登录弹窗绕开、targetSdk=33                      │
│   ├─ assets/*.jsc URL 重定向到私服                               │
│   └─ assets/src/patch/{patch,probe}.js  ← 明文 JS，绕过 jsc 限制  │
├───────────────────────────────────────────────────────────────┤
│ libcocos2djs.so   自己用 NDK r10e + cocos2d-js v3.6 源码重建      │
│   └─ 11 个引擎补丁（见 E:\code\zcsmw\engine\ENGINE_PATCHES.md）        │
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
  crypto/des.py   标准 DES（纯 Python 参考实现 + 可选 libcrypto 加速，两条路线都对拍过）
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

工具：`script/jsc_disasm.py`（全量反汇编）、`script/disasm_func.py`（按函数名）、
`script/jsc_strings.py`（只扒 atom 表，定位逻辑最快的一把刀）。

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

11 个引擎补丁的完整清单 + 证据链见 `E:\code\zcsmw\engine\ENGINE_PATCHES.md`，
每个改动都有一个可重复执行的脚本在 `E:\code\zcsmw\engine\build\*.py`。

---

## 4. 服务端的数据从哪来

这是这个项目最容易踩的一类坑：**客户端对数据形状的要求非常硬**，
少一个字段就是 `TypeError` 或者「列表能看但点不了」。

| 数据 | 来源 | 备注 |
|---|---|---|
| 模块开启状态 `moduleState` | 服务端生成 | key 是 `table_function_open` 的编号 100001~100032 |
| 士兵 / 主角 / 机甲 | 服务端生成 | key 必须真实存在于 `table_soldier` / `table_hero` / `table_mecha` |
| **任务表** | `script/extract_client_tables.py` 从客户端抽 | 存成 `gamesrv/data/table_quest.json` |
| 任务进度 | 服务端维护 | `players.json` 里的 `quests.done` |
| 扭蛋配置 / 副本关卡 | **缺** | 运营配置类数据，客户端表里也没有，得自己想 |

**抽表的套路**（后来加功能可以直接复用）：
客户端表编译在 `assets/src/table/table*.jsc` 里，服务端没有原始文件 →
开 REPL 让游戏自己把要用的字段 `JSON.stringify` 吐出来 → 存成服务端的 JSON。

---

## 5. 客户端补丁：哪些是必须的，哪些只是诊断

`server/client/` 下分两个文件，**打包时可以只带前一个**（`script/build_apk.py --no-probe`）：

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
| **`RESP-DISPATCH`** | 客户端 `responseConfig` 在这套引擎上根本没被派发，自己补一层响应分发。**三类写法都要补**（收整个 `res` 的 / `updateByServer` 的 / 方法名各不相同的），只补中间那批的话好感度整条是死的 —— 见 [protocol.md §5.2](protocol.md) |
| **`URL-REWRITE`** | jsc 里的官方地址只能**等长**替换（`<host>:18080` 必须 19 字节 → host 必须 13 字符），而且换 IP 会静默跳过、整包作废。改成运行时改写 `cc.loader.getXMLHttpRequest()` 与 `window.WebSocket`（**默认**；老路子是 `-JscUrlPatch`），地址不再有长度约束 —— 配合 `adb reverse` 连局域网/root/hosts 都不需要（见 [`REPRODUCE.md`](../REPRODUCE.md) Step 4b） |
| 宿舍互动判定框放大 | ⚠️ **这条是私服体验改动，不是修 bug**（原版 100×100 且不可见） |
| **`ITEM-EVENT`** | `Bag.updateItems` 改数量走 `item.count = n` setter，而这个 setter **不派发** `item_count_updated_<key>`（实测：手动派发时货币条的监听器立刻收到，走 setter 收不到）→ **顶部货币条永远是进层那一刻的旧数字**（服务端和背包里都是对的）。包一层 `updateItems`，对这次改到的每个 key 补派发一次 |

### `probe.js` —— 诊断（`--no-probe` 时不打包）

日志转发、`Proxy` 探字段、REPL、调用序列追踪、异常 stack、登录期/战斗期各种 hook。

> ⚠️ 探针的第一原则：**只包一层，不要重写**。
>
> 早先的版本把 `server.request` 整个重写了，一度被当成「服务端推数据失效」的元凶。
> 后来实测**不是它**：原生 `responseConfig` 在这套引擎上本来就不跑
> （`Favor.prototype.update` 调用 0 次，见 [protocol.md §5.2](protocol.md)）。
> 不过「只包一层」这条规矩仍然要守 —— 当初那版重写确实把响应派发绕掉了，
> 让排查多绕了一圈。

---

## 6. 坑速查：按症状找原因

**拆成独立一篇了**：[`pitfalls.md`](pitfalls.md) —— 开头一张「症状 → 真正原因 → 在哪个文件」
的表（18 条），后面每条都带现象、根因和定位过程。

> 它会独立出去，是因为**被引用最多**（README / REPRODUCE / `ENGINE_PATCHES.md` 都指过来），
> 而且排查任何「点了没反应 / 界面不动 / 数字不对」都得先翻它 —— 放在这份 700 行的全景文中间太难找。

---

## 7. 现状与待办

### 已完成

- [x] 服务端：CDN / 网关 / oauth / WebSocket 登录 / 加密业务路由（16 条）
- [x] DES 标准实现双向对拍；DH 用单位元绕过
- [x] Java 层去掉百度登录弹窗；删掉 46.6MB 没用的第三方 SDK
- [x] 全自动登录 → 主场景 → 开场动画 → 主界面
- [x] **引擎源码重建**（16 个补丁），战斗可完整跑完
- [x] `jsc` 反汇编器 + atom 表提取器 + 运行时 REPL 探针
- [x] **浏览器调试台**（`http://127.0.0.1:18080/devtools`）：
      流量重放 / JS 控制台 / 存档编辑 + 作弊 / 客户端日志流 / 表查询
      —— 见 [`docs/devtools.md`](devtools.md)
- [x] **引擎层 JS 调试器**（断点 / 单步 / 调用栈 / 暂停时求值）：
      引擎本来就带（SpiderMonkey Debugger API + Firefox 远程调试协议），
      只是被 `#if COCOS2D_DEBUG` 挡着；打开它 + 修 4 个坑
      —— 见 [`docs/engine-debug.md`](engine-debug.md)
- [x] 编成 / 上阵队伍（含士兵数据）
- [x] **军士培养 / 突破 / 技能**（升级公式和客户端逐字段对齐，材料会被真的吃掉）
- [x] 主线任务（窗口推进 + 领奖 + 刷新）
- [x] Java 层退出确认弹窗：官方包自带乱码文案 → 换成正常中文；空桩 `Sdk.exit()` →
      **`finish()` + `Process.killProcess(myPid())`**
      ⚠️ 早先只做 `finish()`（"软退"，怕 killProcess 留 signal 9 看着像崩溃）——
      那个顾虑是错的，而且**埋了个崩溃**：进程留在 cached，下次进游戏复用同一进程
      就 SIGSEGV（主界面一闪而过闪退，再进一次才好）。详见 [`pitfalls.md`](pitfalls.md) 的症状表 —— 见 [`pitfalls.md`](pitfalls.md) 第 1 条
- [x] **天赋（培养）**：三条课题 + `player.selecttalent` / `player.upgradetalent`，
      落盘、升级真扣材料（`table_talent_upgrade` 的三张表进了 `gamesrv/data/`）。
      顺带修掉「登录包 `instance` 被桩覆盖」—— 那个 bug **把整个关卡进度吞掉了**，
      不只是天赋，见 [`pitfalls.md`](pitfalls.md) 第 2 条
- [x] **装备系统**（`equipment.*` 7 条，路由 57→64）：登录块按 `EquipmentCenter.ctor`
      的形状给，7 条路由全实现（穿/脱/升级/分解/锁定/标记已读/编组）。
      ⚠️ 属性 key `firstAttrKeys` **必须由服务端算**（`<first_attr_group>+%02d(lv)+%02d(index)`）
      且**不能为空** —— 客户端 `addEquipmentAttrByKeys` 先取值后判长度，空数组也崩；
      而且不是所有 `first_attr_group` 都在属性表里，所以加了校验+回退。见 [`pitfalls.md`](pitfalls.md) 第 3 条
- [x] **调试台**：日志分级（debug/info/warning/error/fatal，服务端按行首判级）、
      页签记忆、toast 自动关闭、控制台/日志/流量自动滚动、数据面板三个视图都能翻页、
      表浏览一行省略+点开展开、`[hidden]` 兜底（见 [`pitfalls.md`](pitfalls.md) 第 4 条）
- [x] **好感度（宿舍）**（`favor.*` 5 条，路由 64→69）：登录块按 `FavorCenter._initData`
      的形状给（`favors` 是 **map**、`id` 的有无 = 已获得、缺不得 `favorInteractChance`），
      送礼 / 换装 / 换背景 / 抚摸 / 资料已读全实现。数值表 7 张进 `gamesrv/data/`。
      ⚠️ 三条坑各成一节：死键（[`pitfalls.md`](pitfalls.md) 第 5 条）、map vs list（[`pitfalls.md`](pitfalls.md) 第 6 条）、
      「算错不报错所以要自测」（[`pitfalls.md`](pitfalls.md) 第 7 条）
- [x] **宿舍事件**（`favorevent.seteventsunlock` 1 条）：好感度到级解锁剧情，
      读完发 `reward_favor` 好感度。⚠️ 事件靠**登录块**下发（实测 184 条里 19 条是我们建的），
      见 [`pitfalls.md`](pitfalls.md) 第 8 条
- [x] **守护灵**（`char.upgradedaemon`）+ **设置助战**（`player.updateasst`），路由 70→72：
      守护灵上限跟着**好感度等级**走（`table_favor_upgrade[lv].max_daemon_lv`，前 6 级是 0）、
      材料喂军士（`table_daemon_exp[品质]` 的同角色/同类型/其它三档）、军士真被吃掉；
      助战改 `player.asstKey`（重登靠登录包恢复）
- [x] **战斗结算给好感度**：数值来自客户端表 `table_level.favor` /
      `favor_char_key`（这两列**客户端一行代码都不读**，是原版留给服务端的），
      服务端只回**数量**，给谁由客户端拿自己那张表算（没有 `favor_char_key` 就全队 +
      主角一人一份）。顺带把奖励块从 `data.level` 挪到 `data.rewards` ——
      那一层才是 `_dealLevelResult` 读的地方。见 [`pitfalls.md`](pitfalls.md) 第 12 条
- [x] **修好响应派发**：`patch.js` 的 RESP-DISPATCH 原来只补了 `updateByServer` 那批，
      `favor` / `newFavorEvent` / `useGiftStatus` 这一整条一直是死的
      （宿舍好感涨了、进度条和等级要重登才动）。现在照原版 `responseConfig` 三类写法补齐，
      并且不再「重试 2 分钟就放弃」。见 [`pitfalls.md`](pitfalls.md) 第 12 条 + [protocol.md §5.2](protocol.md)
- [x] **分区关卡（分区玩法）接通**：两条链路都要给，缺一条界面就是空的
      * **章节列表**：`instance.getactivityinstance` 的 **`data` 本身就是 map**
        `{chapterId: {key, challengeTimes: -1, priority}}`（客户端按
        `table_chapter[key].type == "5"` 过滤 + 按 priority 排序）；
        `challengeTimes: -1` 是客户端自己的"不限次"约定（转成 `Number.MAX_VALUE`）。
        章节清单只在客户端表里 → 新增 `table_chapter.json` 抽表 + CHAPTER_JS
      * **关卡进度/次数**：登录块 `subareaLevels` + `instance.getsubarealevel`，
        key 是关卡 id（27 关，按 `table_level.instance_type == 5` 认，抽表带出 `it` 列）
      * **开放时间不编**：章节和关卡都不填 `deadline`/`limitDay`/`limitTime`
        —— 客户端在这几个字段全缺时直接放行（永久开放）
      * 每日上限 `SUBAREA_DAILY_TIMES = 3`（§D），跨天 05:00 由服务端清零
        （`sync_subarea_plays`）。见 [`pitfalls.md`](pitfalls.md) 的症状表里那两条「分区空白 / 次数用完」的坑
- [x] **修好「编好的队伍一直消失」**：客户端认的队伍 id 是**服务端 teams 数组的下标**
      （`Player.initTeams` 把 `for..in` 的 key 当 id 传进 `Team.ctor`），所以
      `player.updateteams` 发的是 `"0".."4"`；而服务端早期用 `id = index + 1` →
      **整单静默跳过**，编成从来没存下来过（偶尔还会写错队伍）。
      现在 `new_team().id = index`，加载时 `normalize_team_ids()` 对齐老存档，
      handler 按 index 找。顺带让自检脚本收尾时把编成放回去（它的军士升级链路
      会真吃军士、顺带摘队伍）。见 [`pitfalls.md`](pitfalls.md) 的症状表
- [x] **分区成就**（`subareaachievement.receivereward` 1 条，路由 72→73）：84 条成就 /
       84 条条件 / 232 条奖励三张表进 `gamesrv/data/`。
       ⚠️ **条件判定不用服务端做** —— 客户端 `subareaAchievementManager.formatBattleInfo()`
       在结算时按 `table_subarea_achievement_condition` 判完，把
       `subareaInfo = {victory, battleId, modifyAchievements, newAchievements}` 塞进
       `instance.finishlevel`（日志里抓到过完整样例）。服务端只做三件事：
       建行 / 合并进度（`complete` → 写 `completeTime`）、登录块给
       `{achievements: {<id>: 行}}`(**map**)、领奖时按奖励表发道具。
       行形状来自客户端 `SubareaAchievement.createAchievement`：
       `{id, progress, progressInfo, isReceiveReward}`（`completeTime` 服务端写）。
       领奖失败码照客户端 `ERROR_CODE` 回 201~206，客户端自己弹 `table_dictionary` 文案
- [x] **黑市交易所 / 充值页**（`exchange.*` 7 条全注册，路由 73→78）：客户端叫
       `ExchangeCenter`，实际是商店中枢 —— 月卡/充值礼包（IAP）+ 金条↔萌钞 +
       行动力/BP/卡槽兑换。⚠️ **档位规则在客户端**：
       `times = todayExchangeTimes + 1` → `table_exchange_item[key]["exchange_key_"+times]`，
       取不到退回 `exchange_key_default`，再拿 `table_resource_exchange[档位]` 的
       `spend_*`/`receive_*` 扣发；`receive_count = -1` 是「补满」哨兵（行动力补到
       `maxActionPoint`、BP 补到 `limit_count`）；首次兑换还要按
       `first_exchange_percentage` 折算。失败码必须用客户端
       `EXCHANGE_ERR_CODE_DICT` 里真有的（205 = 资源不够），否则 `toast(undefined)`。
       IAP 那半边**故意不通**（没有支付渠道），见 [`pitfalls.md`](pitfalls.md) 的症状表 + [protocol.md [`pitfalls.md`](pitfalls.md) 第 5 条](protocol.md)
- [x] **好感度礼物整备**：① 建号/老存档一次性发满 47 种礼物（`store.top_up_gifts()`，
       版本号做成一次性的）—— 不然宿舍送礼面板是空的；② **回礼真的进背包**（以前只把
       `returnItems` 回给客户端弹窗，服务端自己不加道具，弹出来的东西等于没给）；
       ③ 数值又核对了一遍：礼物加值全部来自 `table_item`（`favor/favor_love/favor_hate/
       gift_type/quality`），礼物的好感等级门槛照抄客户端
       `table_constant.use_gift_lv_<quality>`（四档都是 1，实际不拦人）；
       仍然是推断值的只剩生日倍数与回礼概率（`table_constant` 223 项里没有对应键），
       见 differences.md §D
- [x] **关掉「功能开启」弹窗（纯服务端）**：一进游戏原本会连弹 32 个功能开启动画 ——
      客户端 `moduleManager.popModuleOpen()` 会把「已解锁但 `isOpened` 还是假」的模块
      挨个弹一遍，而 `isOpened` 来自登录块 `moduleOpenMark[mark_index]`（`table_function_open`
      32 条，`mark_index` 1..32）。我们原来没发这个字段 → 全当没弹过。
      现在 `_module_open_mark()` 把 32 个 mark 全标成已弹过（`store.MODULE_OPEN_POPUP_SKIP = False`
      可还原），**客户端一行都没动**；`player.setmoduleopenmark` 的回写也统一存成
      `{markIndex: 1}` map（以前原样存数组，回给客户端会整体错一位）
- [x] **签到**（`sign.receivereward`，路由 78→79）：登录块 `sign` 的 `signs` 原来是**空数组**，
      而客户端 `SignCenter` 把它当 **map**（`for (k in _signs)`）用 → 一条签到都没有，
      表现就是「点签到没用」（`normalSigns`/`eventSigns`… 那几个键客户端压根不读）。
      现在给真行：`{signKey, type, count, rewardCount, rewards, canSignToday,
      beginTimeSec, endTimeSec, dialogue, soldierKey}`；`rewards` 是 **1 基的二维数组**
      （客户端 `rewards[i + 1][j]`，i 从 0、j 从 1），而且**每天恰好一件**奖励
      （`SignRewardItem._init` 只在 `length === 1` 时画真图标，多件一律用通用图标）。
      排期/奖励客户端表里**没有** → 自己定了一套 7 天循环（§D，旋钮 `sign.SIGN_REWARDS`）。
      顺带修了「领了东西背包不刷新」：回包带 `items` 块（`items.changed_block`，
      新道具不能塞进去，见 [protocol.md §5.2](protocol.md)）
- [x] **任务派遣**（`detect.*` 6 条，路由 80→86）：主界面「派遣」。登录块
      `{speedInfo: {分类: 已用免费加速次数}, detect: {章节key: {beginTimeSec, waitTime, subCD}}}`
      —— **都是秒**（`detectItem` 里 `(begin+wait-subCD)*1000 - util.time()` 就是剩余毫秒）。
      六条路由都实现，失败回 **HTTP 200** + `data.code = DETECT_ERROR_CODE`
      （204 在跑 / 206 没选人 / 207 数量上限 / 208 等级不够 / 210 没到时间 / 213 加速次数用完…）。
      上阵条件（人数/等级/兵种）照 `table_detect_chapter` 校验，兵种比的是
      `table_soldier_master[charKey].type`（新抽了 `table_soldier_type.json`）。
      ⚠️ 表里 15 个派遣全要 20/30 级军士，建号发的 18 个是 1 级 → 得先培养军士才能派（原版设计）。
      掉落内容见 §D（`gainItemGroup` 的真实道具组客户端没有）
- [x] **演习场**（`arena.*` 4 条，路由 86→90）：主界面「演习场」。登录块原来只有
      `{arenaInfo:{}, mechaSuperSkillCorrectOwn:{}}`，而 `ArenaCenter.ctor` 要
      `{arenaInfo, rivals, resetTime, refreshTime}` —— 一个对手都没有，点进去是空面板。
      现在从 `table_friend_support_npc`（101 个 NPC，自带名字/等级/5 个军士 key）生成
      8 个对手；积分/段位/连胜/刷新倒计时按 `table_arena_constant`（新抽了 4 张
      `table_arena*`）算；赢了发 `pvp_rewards`（14 演习萌币）。
      ⚠️ 两处形状坑：`refreshTime` **顶层和 `arenaInfo` 里都要发**、每条回包都要带
      `data.arena`（客户端这条路的 cb 不带参数，靠 RESP-DISPATCH 落数据）——
      见 [protocol.md [`pitfalls.md`](pitfalls.md) 第 9 条](protocol.md)
- [x] **抽卡 / 扭蛋**（`gacha.*` 3 条，路由仍是 90 —— 这 3 条以前是空桩）：客户端
      **一张 gacha 表都没有**，卡池 master 全在登录块下发，所以这块是**内容缺口**：
      池子 id 照 `gachaconfig.GACHA_NAMES`（1001 免费 / 2001·2010 碎片单抽十连 /
      4001·4010 钻石单抽十连），卡池内容从 `table_soldier`（自军卡 152 张 = 每角色
      4 档，q3=S、q4=SR）+ `table_hero`(2) + `table_mecha`(6) 里挑，消耗/概率/十连保底
      是自己定的（§B/§D）。⚠️ 三件套必须是 **map**、`saleInfoObj` 要按次数索引
      （否则价格变 NaN）—— 见 [protocol.md [`pitfalls.md`](pitfalls.md) 第 10 条](protocol.md)。顺带修了一个**老 bug**：
      `items._add_soldier` 查错表名（`table_soldier` 应为 `card`），导致**所有军士奖励
      被静默丢掉**（抽卡/派遣/关卡奖励都中招）
- [x] **日常 / 成就任务**（`type=1` / `type=3`，路由仍是 90 —— 复用 `quest.getnewquest` /
      `quest.submitquest`）：日常 246 条按 `lv` 分 24 档（1/5/10/…/116），**一天只放当前等级
      那一档**（7~11 条）。判据很硬：档内那条「完成所有的日常任务哦！」(`ct=19201`) 的 `tar`
      正好 = **档内条数 − 1**，24 个档逐一核对都对得上。换日点 **05:00**（和分区每日次数同一个
      换日点，`quests.RESET_HOUR`），`daily.day` 存「游戏内的今天」，跨天清空当日 done。
      成就 91 条按 `lv` 门槛解锁、长期累计。进度全部由计数器现算：通关次数 / 某副本各难度
      通关数 / 军士升级·突破 / 抚摸（含按角色）/ 送礼 / 演习 / 派遣 / 分解 / **累计获得道具**
      （挂在 `items.add_item` 这个唯一入账口上）/ 战役星数 / 军士数量 / 私密开启数。
      ⚠️ 三类拿不到数据的条件（12208 击杀鸭子数、12209 我方军士跪倒数、13102 技能熟练度）
      进度恒 0、不假装达成。（18201「给好友送物资」原来也卡着，好友系统做完后**已经通了**。）
      **奖励真发**：新抽的表 `table_quest_reward`（1534 行）→ `items.settle`，回包
      `data.rewards = [{type,key,count}]`（`ccuiManager.popupReward` 认的形状）——
      主线以前领奖只回空数组，这次一并补上。见 [protocol.md](protocol.md) 的「任务」一节。
- [x] **好友系统**（9 条 `friend.*`：搜索 / 申请 / 同意 / 拒绝 / 删除 / 送物资 / 收物资 /
      `getfriendlist` / `getrecommendationlist`）：位掩码、条目形状、上限全按客户端反汇编来
      （`status` 的 A/B 是 **key 里的第一/第二个人**，本服自己恒为 A；上限读客户端表
      `table_player_level_function[lv]` 的 `friend_limit` / `take_materials_limit`）。
      单机没有别的玩家，所以好友是 `table_friend_support_npc` 的 101 个 NPC：
      建号送 5 个好友 + 2 条待处理申请，申请出去 60 秒后自动同意，换日清空物资位并让
      2 个好友重新送物资；收物资给行动力（回包 `data.reward` 是 `{itemKey: count}`）。
      顺手解开了日常 **18201「给好友送物资」**（`quests.on_friend_send`）。
      见 [protocol.md](protocol.md) 的「好友」一节 + [differences.md](differences.md) §B/§D。
- [x] **勋章 / 头像 / 衣柜**（9 条 `medal.*`）：换头像 / 换衣服 / 换背景（回包是**值**）、
      佩戴勋章（回的是**整张** `medalWear`）、4 条清 NEW 标记、查好友勋章。
      衣柜/头像/勋章本体都是**背包道具**（`ITEM_TYPE` 40/50/60/70），NEW 标记靠
      `item` 块里的对象形状 `{"count": n, "isNew": true}`（`Item.ctor`/`updateByObj` 认）；
      `player.headId` 修正成 `"<道具>:<类型>"`（原来是瞎填的 `1`，客户端 `getHeadSpr`
      会 `.split` 一个数字直接抛）。勋章进度按 `table_medal.condition_kind` 反推
      （抚摸/送礼/演习/派遣/军士等级/好感度/抽卡/日常条数等 10 类），
      算不了的三类（指定关卡/被推倒/指定军士）进度恒 0 不假装完成。
      见 [protocol.md](protocol.md) 的「勋章」一节 + [differences.md](differences.md) §B/§D。
- [x] **分享 / 礼包兑换**（`share.receivesharereward` + `convert.convert`）：
      分享奖励和每天次数**照客户端表发**（`table_constant.share_reward_key = "100001@30"`
      / `share_reward_max_count = 1`），换日 05:00 清零；
      礼包按 `table_item.convert_key` → `table_convert_reward`（`consume: "800001#1#i"`）
      扣道具发奖。两个坑都记在 [protocol.md §14](protocol.md)：
      分享的 `rewards` 必须是 **map**、礼包回包的 `data` 必须是**奖励数组**。
      礼包内容原版在服务端、客户端表里没有 → 私服自己定（[differences.md](differences.md) §D）。
- [x] 文档：协议 / 逆向手法 / 打包逻辑 / 调试台 / 引擎调试 / 本总览 / **与原版的差异** / **从零复刻**

### 待办（按卡点排序）

1. **战果报告的「获得物资」还没在实机确认** —— 服务端现在把奖励块放在**对的那一层**了
   （`data.rewards.dropReward / firstComplete / appraise / levelReward`，
   日志里能看到 `掉落={'100002': 593} 首通={'100001': 20} exp=60`）。

   客户端的链路是（都是反汇编钉的）：

   ```
   Instance.finishLevel/<   ret = this._dealLevelResult(res.rewards)   // 只认 data.rewards
                            ret.rank = ...                            // 读 rewards.levelReward.playerInfo
                            cb(undefined, ret)
   LevelCompleteLayer.ctor(args)   this._result = args.result          // ← args.result = ret
   LevelWinBase._init()            this._rewardsList = this._result.rewards
   ```

   所以奖励块**必须**在 `data.rewards` 里（以前挂在 `data.level` 上，`_dealLevelResult`
   根本看不到 → 面板永远空）。⚠️ 还没实机确认的一环是
   **`args.result` 到底是不是 finishlevel 那个 `ret`**（`instancemanager` 是反汇编里
   少数对不齐的文件，`showCb` 的入参拼不出来）。如果实机面板还是空的，
   下一步就是在 `patch.js` 里包 `instanceManager` 的 `showCb` 入参，把 `ret` 塞进 `args.result`。

2. **助战（好友支援）列表渲染不出来** —— 服务端已经能正确回 NPC 名单
   （`friendsupport.getrecommendsoldiers` -> 20 个 `npcId`，客户端
   `FriendSupport._recommendList` 里也确实收到了 20 个），
   但 `SupportChoiceLayer` 那边渲染不出来。已确认的：
   `setSupportList()` 手动调是好的（会往 `_pushAsynList` 里塞 18 个
   `{item, innSize, index}`），所以卡在「层的 `_recommendList` 是 0」。
   **不影响战斗**（这个弹窗是可选的好友助战）。
3. **其余未实现的 route** —— `python script/route_gap.py --static` 能列出全部。
   当前：客户端静态候选 **161** 条，服务端 **110** 条，缺 **59** 条。按单机价值排：

   | 命名空间 | 缺 | 说明 |
   |---|---|---|
   | `diary.*` | 2 | 私密剧情购买/解锁（要 `table_story` 那套 + 行形状） |
   | `boss.*` | 5 | 好友 BOSS（`getbosslist` 已实现并回空表） |
   | `society.*` / `societyclg.*` | 33+6 | 军团——单机价值低、量最大 |

   ⚠️ **`rank.*` / `boss.getbosslist` 这类"回空表"不算缺口**：私服没有榜、没有好友，
   回空才是对的（见 `handlers/rank.py` 的论证），别当成没实现去"补"。

   ✅ 已经补完的：`equipment.*`(7)、`favor.*`(5)、`favorevent.*`(1)、
   **`char.upgradedaemon`**(1)、**`player.updateasst`**(1)、
   `player.selecttalent`/`upgradetalent`、`sign.*`(1)、`detect.*`(6)、**`arena.*`**(4)、
   **`friend.*`**(9)、**`medal.*`**(9)、**`share.*`**(1)、**`convert.*`**(1)。
4. `hashKey` / `hmac64` 还没复刻（登录靠单位元绕过）；自研 DH 的完整算法也没还原。

---

## 8. 工具与工作流

### 8.1 构建链路

**单一口径在 [`build.md`](build.md) 的「完整重建命令」** —— 六步
（`modernize` → `sdk_strip/strip` → `gen_native_stubs` → `merge_dex` → `patch_smali` →
`patch_js_debugger` → `build_apk`），那边逐条写了为什么顺序不能换。

日常改完客户端只跑 `.uild.ps1 -Install` 就够：它内部会自动打 smali 补丁、
写 `patch.js` / `probe.js`、打包签名。

### 8.2 调试工作流

**首选浏览器调试台**（服务端自己托管，不用改 APK）：`http://127.0.0.1:18080/devtools`。
流量（含「客户端调了但服务端没实现」的高亮 + 一键重放）/ JS 控制台 / 存档编辑 + 作弊 /
客户端日志流 / 表查询 / 引擎调试器。见 [`devtools.md`](devtools.md)。

命令行那套用于批量和脚本化，最常用的三条：

```powershell
python script\serve.py                    # 起服务端（守护脚本；别用 Start-Process 直接拉 run.py，会被回收）
python script\repl.py "..."               # 在游戏进程里执行任意 JS（前提：probe 版 APK + 服务端在跑）
python script\jsc_find.py useGiftStatus --func    # 这个 key/route/方法名在哪个 .jsc 的哪个函数里用过
```

其余（反汇编、抽表、自测、可达性分析…）在 [`../script/README.md`](../script/README.md)
里按用途列全了；**排查顺序**见 [`../REPRODUCE.md`](../REPRODUCE.md) §5
（按复用性排序：先看服务端日志和调试台流量，再分层，最后才上引擎级断点）。

两个实测技巧 —— 点击/截图类工具靠不住的时候用：

✅ **验原生弹窗（Java 层 AlertDialog）别靠截图，导 View 层级** —— GL 层截不到时它照样能拿到文字：

```powershell
adb shell uiautomator dump /sdcard/ui.xml
adb pull /sdcard/ui.xml
# 弹窗内容就是 TextView 的 text 属性，按 id 取：
#   android:id/alertTitle  android:id/message  android:id/button1  android:id/button2
```

实测：`screencap` 连抓 4 张全白，同一时刻 `uiautomator` 里弹窗的标题/正文/两个按钮原文一个不缺。

💡 想触发某个原生弹窗来验，用 `script\repl.py` 调**静态**入口：

```powershell
python script\repl.py "jsb.reflection.callStaticMethod('org/cocos2dx/javascript/QuickAdapter','exit','()V')"
```

⚠️ `jsb.reflection.callStaticMethod` **调不到实例方法**（会报
`CCJavascriptJavaBridge: Failed to find method id of ...`，logcat 里能看到）；
`AppActivity.exit()` 是实例方法，得走它的静态包装 `QuickAdapter.exit()`。

> ⚠️ `adb shell input tap` 在 MEmu 上**不可靠**（注入的事件不一定到得了 App），
> `adb screencap` 有时抓不到 GL 层（截出来一片白）。别用它俩下结论 ——
> 以用户看到 / 服务端日志 / `uiautomator` 为准。

---

## 9. 关键路径

```
E:\code\zcsmw\game\                  apktool 解包目录（smali / assets / lib）—— 工作副本
E:\code\zcsmw\game\original\        原版 APK 备份（唯一的一份，别删）
E:\code\zcsmw\out\                   打包产物 + 缓存（哪些能删见 script/README.md 附 2）
E:\code\zcsmw\engine\                引擎移植工作区（cocos2d-js 源码 + NDK + 构建脚本）
E:\code\zcsmw\docs\                  本目录（项目文档）
```

**文档地图只有一份是全的**：在 [`../README.md`](../README.md) 的「文档地图」一节。
免责声明也在那里（本项目只包含自己写的代码，不含游戏素材 / `.jsc` 字节码 / 反编译产物 / APK）。
