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
| **编成 → 上阵队伍 → 加号** | ✅ | 18 个初始士兵（前锋/中卫/后卫各 6，角色不重复，全是 `card_type==1` 的自军卡） |
| **副本 / 关卡（主线战斗）** | ✅ | 11 章 / 1142 关，关卡表在客户端；服务端只存进度 |
| **任务 → 主线任务** | ✅ | 12 条窗口；**进度接的是真实战斗**（打一次关卡胜利 = 一次任务进度） |
| **编成 → 军士培养 / 突破 / 技能** | ✅ | `char.upgradesoldierlv` / `improvesoldierstar` / `upgradesoldierskill`；升级公式和客户端逐字段对齐（`script/check_soldier_calc.py`） |
| 日常 / 成就任务 | ⚠️ | 数据是空的（只做了主线，`type=2`） |
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
│   ├─ Java/smali   登录弹窗绕开、targetSdk=23                      │
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
| **`URL-REWRITE`** | jsc 里的官方地址只能**等长**替换（`<host>:18080` 必须 19 字节 → host 必须 13 字符），而且换 IP 会静默跳过、整包作废。改成运行时改写 `cc.loader.getXMLHttpRequest()` 与 `window.WebSocket`（**默认**；老路子是 `-JscUrlPatch`），地址不再有长度约束 —— 配合 `adb reverse` 连局域网/root/hosts 都不需要（见 [`REPRODUCE.md`](../../REPRODUCE.md) Step 4b） |
| 宿舍互动判定框放大 | ⚠️ **这条是私服体验改动，不是修 bug**（原版 100×100 且不可见） |

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

| 症状 | 真正原因 | 在哪 |
|---|---|---|
| 点开始游戏弹 `温馨提示 {"code":1,"msg":"bad request"}` | 请求体前面的 `base64(" " + sessionId)` 没摘掉，DES 解不出来 | `gameproto.split_session_field` |
| 登录后**黑屏**，日志 `modules is undefined @ mainlayer.js:188` | `initUserData` 中途抛异常 → `player.initModuleState()` 没跑到 → `_moduleState` 是 undefined | §7 表 + `INITUSERDATA-GUARD` |
| 主界面按钮**全都点不动**，引导一直让你点某个按钮 | 引导层把菜单点击吃了（`op.uiLoader.addTouchEventListener`） | `patch.js` GUIDE-SKIP |
| 编成 → 加号**点了没人** | 士兵列表为空 / 全是同一角色 / 默认站位页没兵 | `store.SOLDIER_KEYS` |
| 编成 → **培养**按钮点不了，弹「指挥部等级不足哦~OAQ」 | 「培养系统」在 `table_function_open[100005].unlock_lv = 6`，玩家等级不够 | `store.MIN_PLAYER_LV` |
| 编成 → 培养里**选不出材料** | 军士的 `card_type` 不是 1（`table_soldier_master[char_key].card_type`），敌方单位不进军士卡列表 | `store.SOLDIER_KEYS` |
| 培养点一下**直接顶到等级上限** | 材料的 `table_soldier[key].base_cost` 是 `undefined`（敌方行没有这个字段），加法变 `NaN` | 同上 |
| 培养**预览 +3 级、点完跳 +8 级** | 服务端没复刻客户端的经验曲线 | `gamesrv/soldier.py` + `script/check_soldier_calc.py` |
| 培养升完**重登又变回去了** | 军士没落盘（`soldiers` 是 `null` / 升级后没 `save_player`） | `store._migrate` / `store.save_player` |
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
| 调试台打开是**白板** | `devtools.js` 抛异常（最常见的是引用了 HTML 里没有的 id） | 页面顶部红条会写出来；也跑 `python script\check_devtools.py` |
| 调试台**流量面板不动** | 事件总线的长轮询断了（服务端刚重启） | 刷新页面；`/devtools/api/overview` 里看 `bus.seq` 有没有在涨 |
| 客户端 `console.log` 在日志面板里**看不到** | JSB 里 `console.log` 是 `writable:false, configurable:false`，**客户端没法包一层**（赋值静默失败），探针一直没转发到；只有 `cc.log` 被包上了 | 调试台改成从 logcat 收 `cocos2d-x debug info` tag 的原文，单独一档「console.log」；要转发请用 `cc.log` —— 见 §11.4 |
| `console.log('a', b)` 抛 `js_console_log : wrong number of arguments` | 原生 `console.log` **只接受一个参数** | 自己 `[a, b].join(' ')` |
| Java 层退出确认弹窗**文字是乱码**（一片"盒子问号"，偶尔漏出正常汉字） | **官方包自带的**：原版 `classes2.dex` 里这 4 个串本来就有 26 个 U+FFFD（同一个 dex 里别的「确定」「取消」是正常 UTF-8），字节已被替换符抹掉 | `patch_smali.py` → `patch_exit_dialog()` |
| 退出弹窗**点「确定」没反应**（「取消」正常） | `Sdk.exit()` 是 `sdk_strip/gen_stubs.py` 生成的**空桩**，按钮调它等于没调 | `patch_smali.py` → `patch_sdk_exit()` |
| 关卡列表**不显示通关**、章节星级恒为 0；按通关解锁的功能（如「萌源增幅」）**永远锁着** | 登录包里 `instance` 被 `_module_stubs` 的桩覆盖成 `{"levels": []}` —— `data.update()` 排在真实进度**之后**，把整块顶掉。客户端 1142 个 Level 全停在 `_starMark = -1` | `agent.get_login_data` / `_module_stubs`（**桩里不要再出现 `instance`**）—— 见 §6.2 |
| 用调试台「全部三星通关」作弊、甚至**重登都不生效** | 同一个根因：服务端存档早写对了，但**进度从没发到客户端**。客户端只在登录那一刻读一次关卡，所以"重登"也救不了没发出去的数据 | 同上 |
| 服务端**明明发了**数据（日志里有），客户端界面不动 | 中间那层转发（`responseConfig`）在这套引擎上不跑；`patch.js` 的 RESP-DISPATCH 补了没有 | §6.12 + [protocol.md §5.2](protocol.md) |
| 宿舍里好感度涨了，**进度条/等级要重登才动** | 同上：`data.favor` 一直没人派发给 `FavorCenter.cb4ResFavor`（`Favor.prototype.update` 调用 0 次） | 同上 |
| 关卡结算面板**「获得物资」永远空着**、`Exp+N` 恒为 0 | 奖励块挂在 `data.level` 上了；客户端读的是 `data.rewards.levelReward`，而 `data.level` 只走 `Level.updateLevel()` | `gamesrv/instance.py` |
| 通关后**好感度弹窗不出现/显示 +0** | 数量要回在 `rewards.levelReward.favor`（"给谁"由客户端拿自己 `table_level.favor_char_key` 算）；回了 `data.rewards.favorReward.favors` 会走到客户端一个 `.count` 写错的死分支 | `gamesrv/favor.py` + `instance.py` |

### 6.1 SDK 桩里的「死键」——一类很容易误判成 JS 层 bug 的问题

`strip.py` / `gen_stubs.py` 删掉第三方 SDK 后，是按 `needed.json` **补空桩**：
方法签名齐全、方法体是 `return-void`。于是**任何"点了应该有反应"的 SDK 调用都会
变成死键** —— 界面正常、弹窗正常，点下去无声无息。

退出弹窗就是这么中招的：弹窗文字（我们已修）和按钮是两件独立的事，
`AppActivity$12$1.onClick → Sdk.exit()` 落在空桩上，所以「确定」不退出。

排查只要两步：

```powershell
# 1) 谁在调它（拿方法名 + 描述符去搜）
grep -rn "Lcom/quicksdk/Sdk;->exit(Landroid/app/Activity;)V" game\smali
# 2) 打开桩看方法体：`.method ...` 之后直接 return-void 就是空桩
```

> 判据：**弹窗/界面是对的、按钮却毫无反应，先怀疑空桩**，别急着往 JS 层查。

> ⚠️ 改空桩前必须 grep 出**全部**调用点。`Sdk.exit()` 全工程只有 `AppActivity$12`
> 和 `$12$1` 两处，所以把桩改成"真的退出"是安全的；换一个被到处调的桩
> （比如 `init` / `onResume`）就可能把启动流程直接搞崩。

### 6.2 桩数据把真实数据**覆盖掉** —— 拼包顺序坑

§6.1 讲的是桩**没实现**（死键）；这一条是桩**实现了、但把真数据顶掉**，更隐蔽：
接口返回 200、字段名也对，只是值是空的 —— 客户端不崩，只是"什么都没发生"。

`get_login_data` 的写法是：

    data = {..., "instance": instance.login_block(player)}   # 真实关卡进度（1142 关）
    data.update(_module_stubs(player))                       # 把桩并进去

`dict.update()` **只覆盖、不合并**。而 `_module_stubs` 早期返回的第一项就是
`"instance": {"levels": []}` —— 于是精心拼好的真进度**在同一个函数里当场被丢掉**，
客户端拿到的 `instance.levels` 是一个空**数组**。

后果链条（这就是「萌源增幅」一直锁着的真正原因）：

    instance.levels = []  →  Instance._updateLevels() 一个都没更新
                          →  1142 个 Level 全停在 _starMark = -1
                          →  getStarsCount() 返回 -1
                          →  layerjumpmanager.checkLevel() 要求 > 0 → 永远不过

**判据**：同一份响应里既有"真数据"又有"桩数据"、且两边可能撞 key 时，
必须确认 `update` 的方向和顺序；桩只该提供**真数据没有的** key。

**排查姿势**：直接调 handler 看拼出来的包，别猜（也不用起客户端）——

```powershell
python -c "import sys; sys.path.insert(0,'server'); from gamesrv import handlers; handlers.load_all(); from gamesrv.handlers import agent; d=agent.get_login_data({'info':{'account':'test'}},{},1)['data']; print(type(d['instance']['levels']).__name__, len(d['instance']['levels']))"
# 修之前 -> list 0     修之后 -> dict 1142
```

### 6.3 客户端表：**空数组也会崩**（长度判断写在取值之后）

装备的 `firstAttrKeys` / `secondAttrKeys` 一开始给的是空数组，结果「强化」点了没反应。
logcat 里只有一行 `JS ERROR: TypeError: config is undefined @ equipmentstrengelayer.js:129`。

根因在 `equipmentManager.addEquipmentAttrByKeys(keys)`：

    while (true) {
        var attr = table_equipment_attr[keys[i]];      // ← keys 为空时 keys[0] = undefined
        if (resultAttr[attr.attr_key] == null) ...     // ← attr.attr_key → TypeError
        i++;
        if (!(i < keys.length)) break;                 // ← 长度判断在取值**之后**！
    }

**它是先取值、后判长度**，所以"空数组"根本不安全（我们习惯的 `for (i=0;i<n;i++)` 思维会踩）。

教训：**客户端要的数组字段，先确认它是不是"至少得有一个元素"**。这类字段宁可给一个
合法的默认值，也不要给空数组。

### 6.4 `hidden` 属性只是 UA 样式，作者样式能盖掉

```css
[hidden] { display: none }        /* UA 样式，优先级最低 */
.chk    { display: inline-flex }  /* 作者样式 —— 盖掉上面那条 */
```

于是 `<label class="chk" hidden>` **照样显示**，而同一批操作里 `<button hidden>` 藏得掉
（button 没写显式 display）—— 表现就是"有的藏了有的没藏"，看着自相矛盾。

`devtools.css` 里加了兜底（**任何用 JS 切显隐的地方都受益**）：

```css
[hidden] { display: none !important; }
```

同类坑还有：`display: flex` 的容器里，子元素的 `hidden` 也常常失效。

### 6.5 登录包里「客户端压根不读」的死键

`favor` 块以前写的是 `{"favors": [], "isNeedAsstEff": 0, "favorExpAdd": 0}`，
其中**后两个键客户端从来不读**。反汇编 `FavorCenter._initData`：

    this._isNeedAsstEff = false;   // ← 写死
    this._favorExpAdd   = 0;       // ← 写死，**不是** data.xxx

它们只由响应键 `favorAsstRefreshed`（`cb4ResFavorAsstRefreshed`）驱动。
也就是说登录包里挂那两个字段纯属自己骗自己 —— 写错了、写少了都不会报错，
排查时却会让你以为"这里已经处理过了"。**已经删掉。**

同类死键还有一批，判据是「这个 key 在 `dataManager.initUserData` / 各模块 ctor 里
到底有没有被读」：

    # 查某个 key 有没有被读（.jsc 是二进制，裸 grep 搜不到，必须走原子表）
    python script\jsc_find.py isNeedAsstEff --func

教训：**登录包的字段不能凭"名字看着合理"往里塞**。要么反汇编确认读取点，
要么用 `jsc_find.py` 全库搜一遍；搜不到就是没人读。

### 6.6 登录块里最容易错的两种形状：map vs list

`favor` 块真正的形状是 `{favors: {charKey: 行}, favorInteractChance, favorInteractUpdateTimeSec}`
—— `favors` 是 **map**，不是数组。判断依据在 `FavorCenter._initData`：

    var favorsData = data.favors;
    for (var i in favorsData) existedKeys.push(favorsData[i].charKey);
    ...
    var favorData = existedKeys.indexOf(charKey) === -1 ? {charKey: charKey}
                                                       : favorsData[charKey];   // ← 直接拿 charKey 索引

回数组的话 `favorsData["sasm"]` 恒为 undefined，**63 个角色全部退化成「未获得」**，
而且不报任何错 —— 表现只是宿舍里一片灰。

同类：军士用 `char_key`（`sasm`）索引，不是 `table_soldier` 的 key（`sasm010104`）；
`id` 的有无就是客户端的 `isAcquired`（`typeof favorData.id != "undefined"`），
所以没获得的角色**不能**带 `id`。

### 6.7 数值算错不会报错，只会「数字不对」—— 这类逻辑必须钉自测

好感度这套东西，加多少经验、升不升级、回不回礼、抚摸次数怎么回，
**全在服务端**（客户端只拿 `favorValue` / `favorAdd` 去播动画）。
算错了界面上不会抛异常，只是数字不对 —— 靠看画面基本发现不了。

所以 `script/selftest_favor.py` 在**进程内**把公式钉死（78 条断言，不需要模拟器、
不需要服务端在跑），`script/selftest_game.py` 那边只补形状和落盘。

⚠️ 写这类自测时注意：handler 内部是 `store.get_or_create_player()`，
**每次都从盘上重新 load**；直接改内存里的 player 是没用的，必须
`save_player` 之后再由 handler 重新读，否则测出来的是假的。

### 6.8 「登录块」有的被读、有的没读 —— **别靠读反汇编猜，实机量一下**

> ⚠️ **这一节我第一版写错了，留在这里当反面教材。**
> 我当初读 `FavorEventCenter._initData` 的反汇编，把末尾那个 `setelem` 的目标
> 认成了参数 `data`，于是断言「登录块 `favorevent` 是个 scratch 对象、
> 客户端不拿它填界面，事件只能靠响应键 `newFavorEvent` 推」。
> **实机一量就打脸了。**

宿舍事件（`favorevent`）实机数据 —— 重启客户端（走一次完整登录）之后：

    Object.keys(dataManager.favorEventCenter._favorEvents).length   ->  184
    其中 _id 有值的                                                 ->  19

`184` = `table_favor_random_event` 的**整张表**；`19` = 服务端在登录块里建的那 19 条。
而且那 19 条的 `createTimeSec` 是**服务端的时间戳**，客户端编不出来。

结论：`_initData` 就是**往 `_favorEvents` 里写**的
（`this._favorEvents[i] = new FavorEvent(data[i] || {eventKey: i}, table[i])`），
所以**登录块 `favorevent` 本来就会被读**，事件不依赖响应推送。
没建的那些用 `{eventKey: i}` 占位（`_id` / `_status` / `_isToBeUnlocked` 全是 undefined）。

登录响应和好感度涨了的响应里各带一份 `newFavorEvent`，现在看是**冗余的**
（留着无害，路径都通）。

**这一节真正的教训**（比上面那个结论值钱）：

1. **分不清 `setelem` 的目标时，别硬读字节码 —— 去实机量。**
   这个页面本来就是自己写的，加一行 `cc.log` / 用 `/devtools` 控制台
   敲个 `Object.keys(...).length`，成本远低于反复反汇编。
2. **同一个登录包里三种情况都真实存在过**，所以「登录块字段」这件事**没有通则**：
   * 读了 —— `favor.favors`（map vs list，§6.6）
   * 没读 —— `favor.isNeedAsstEff`（死键，§6.5）
   * **读的**（我一度以为没读）—— `favorevent`
   每条都得单独确认：`script/jsc_find.py <key> --func` 看有没有人读，
   **再加一次实机量**。
3. 反汇编的 `getlocal`/`setlocal` 槽位**不要盲信** —— 那一处就是被它坑的。

### 6.9 调试台自己也会坏，而且症状很像「游戏挂了」

现象：**控制台页里没有东西、流量页也不再动了**，`/devtools/api/events?...`
那条长轮询要等满 25 秒才回来。看着像服务端卡了，其实是两个独立 bug：

**① 长轮询的游标不能只往前推。** 前端的 `state.since = data.seq` 是**无条件采纳**的，
而服务端老代码回的是

    cursor = events[-1]["seq"] if events else max(since, latest)

`max(since, latest)` 的注释写的是「别让前端卡在永远拿不到事件的区间里」，
但它恰好**把这个状态焊死了**：服务端重启后 `_seq` 从 1 重新数，前端还抱着
上一次进程的 `since=570`，而 `latest` 才 298 —— `max(570, 298) = 570`，
于是永远问 570、永远答「没有新事件」，面板永久空白。

正确做法是**服务端认得出「游标超前」并回退到 0 重放整个缓冲**，而且**立刻返回**
（不能还把 25 秒的长轮询等满）：

    stale = since > latest
    events = bus.since(0, limit) if stale else ...
    cursor = events[-1]["seq"] if events else latest      # ← 不再 max(since, ...)

**② 单条日志 131KB，浏览器渲染直接卡死。** 客户端探针把**整个登录响应** dump 成 hex：

    CRYPT base64Decode(b64len=131136 hex=436b794f4e4a7276...)

一条 131KB × 一次 2000 条 → 标签页卡住。修法是入库前统一过 `devbus._clip()`：
字符串按 kind 截断（日志/流量 4000，控制台 20000 —— 那是人主动要看的输出），
数组封顶 100 项，被截的字段上打 `clipped` 标记。

**③ `/favicon.ico` 落到 fallback，于是日志面板自己刷自己。**
它每次请求都打一条 `CDN 未处理请求: GET /favicon.ico` warning，而那条 warning
**就显示在 devtools 的日志面板里**；页面卡住时浏览器请求得特别勤，
于是「日志面板被自己的 favicon 警告刷屏」——看着像别的东西也坏了。
现在直接回 204。

教训：**调试工具自己出问题时的现象，会和被调试对象出问题的现象长得一模一样**
（都是"页面不动了"）。所以它也必须有自测 —— 见
`script/check_devtools.py` 的 `event_stream_checks()`（超前游标 / 字段长度 / favicon）。

**④ `setInterval(asyncFn)` + 只增不减的兜底红条 = 页顶挂一条关不掉的红条。**
页面顶上有个兜底报错条 `#fatal`（`position:fixed; top:0; z-index:999`），
原本是 `box.textContent += ...`、**没有关闭按钮、没有行数上限**。而初始化时写的是

    setInterval(refreshOverview, 5000);      // refreshOverview 是 async 的

`setInterval` **不管返回值** —— 服务端一重启，`fetch` 每次都 reject，
于是每 5 秒产生一个 **unhandledrejection**，被 `window.addEventListener('unhandledrejection')`
记进那条红条。结果就是页顶一条红条越堆越长、**一直挂着挡工具栏，还关不掉**。

两条一起修：后台轮询一律走 `poll(fn, ms)`（内部 try/catch，reject 是预期内的，
不当 fatal）；兜底红条可关闭、最多留 `FATAL_MAX` 行、同一条只累加次数。

`check_devtools.py` 里加了条**不变量**：剔掉注释后 `setInterval(` 全文只能出现一次
（就是 `poll()` 里那次）。这样以后谁再直接 `setInterval(asyncFn)` 会被自测拦下来。

### 6.10 「点了/搓了没反应」——先确认那是**几步**手势

宿舍的「互动（抚摸）」我查了很久，最后发现**代码一直是对的**，是我在错的地方搓。
它的真身是**两步手势**（反汇编 `FavorLayer.newTouchEffect` + `CharAsstLayer`）：

    ① 点/搓角色的头或胸口  -> eachTalkCb -> touchEffect.showView()  爱心出现
    ② 在**那颗爱心上**按住来回搓 -> pgState=1，percent += dt*60（松手 -40）
    ③ 填满 100 -> toucuFullCb -> favor.touchcharasst

坑在 ②：判定框是 `favortoucheffect.csb` 里的 `touchpanel`，**只有 100×100、
完全不可见**、中心在世界坐标 (434,400)——在角色**右边**，不在角色身上。
而且 `showView` 之后 `PG_SHOW_STAY_TIME = 1000`：**1 秒**内不开始搓就自动收起。

`curMode` 也是陷阱：`touchEffect.showView()` 的条件是
`curMode == DETAIL && decorateMode == SHOW && interActivePanel.getCount() > 0`，
而这两项在**列表模式和详情模式下都成立**，光看它判断不出你在哪一屏。

**排查顺序**（我绕了好几圈才走对）：

1. **先看服务端有没有收到请求**（存档里的 `curExp` / 次数有没有动）——
   收到了就是客户端表现问题，没收到才是链路问题
2. 再逐层挂探针：触摸监听器 -> 领域判定 -> 回调 -> 进度
3. **别凭"这个日志刷屏了"就去认领它当根因** —— 我一开始把
   `createExpSprite error, clothes item not found` 当成触摸失败的原因，
   其实那是 `CharAsstLayer.talk`（立绘说话表情），两条路互不相干。
   查法：`jsc_find.py createExpSpriteEx` 看**调用点**，只有两个，都不在触摸路径上

**挂探针本身也有坑**：`cc.EventListener.create(config)` 会把 **`config.event` 删掉**，
真正的监听器是它**复制出来的另一个对象**。所以

    layer.asstLayer.touchListener.onTouchBegan = 我的包装   // ← 永远不会被调用

现象是「探针装上了、`__probed` 也是 true，但一条日志都没有」。
两个特征可以认出这件事：`touchListener.constructor.name === "Object"`（不是
`EventListenerTouchOneByOne`）、`touchListener.event === undefined`。
要挂就得**先把 `event` 补回去、包好、再 `disableFavorTouch()` + `enableFavorTouch()` 重新注册**。

**这条已经改成私服体验改动了**（判定框放大到覆盖角色），见
[`differences.md`](differences.md) 和 `server/client/patch.js` 末尾那段。

### 6.11 「拖不动」不一定是拖的问题 —— 触摸**传播**断了

宿舍的 RoomList（`FavorListLayer` + `ccui.ScrollView`）**在角色条目上按住拖动完全没反应**，
只在条目之间的空隙起手才能滚。条目自己的点击是好的。

**排查路径**（一路从 JS 查到 C++，每一步都有实机数字）：

1. **先量 ScrollView 本身**：`getInnerContainerSize()` 2435 vs 视口 465、19 个条目、
   `isTouchEnabled()=true`、方向 `VERTICAL`、命中矩形 `(603,21)-(1150,486)`
   —— **ScrollView 一切正常，就是收不到事件**
2. 给它 `addEventListener` 挂探针，**一条事件都没有** → 不是"滚到头了"
3. 排掉几个嫌疑：`touchSwallower`（`visible=false`）、`shieldPanel`（`visible=false`）
   —— 注意 `Widget::onTouchBegan` **是判 `isVisible()` 的**（vanilla 3.6 源码
   `UIWidget.cpp:749`），所以"不可见的挡板在吞触摸"这个直觉是**错的**，
   别顺着它查下去
4. 看条目：`items[0].constructor.name === "Node"` —— **裸 Node**！
5. 回 C++：`Widget::getWidgetParent()` 是 `dynamic_cast<Widget*>(getParent())`，
   **只看直接父节点**；`propagateTouchEvent` 拿它当唯一一跳。
   裸 Node 让这一跳返回 `nullptr` → **传播到此为止** → ScrollView 永远收不到
   BEGAN/MOVED → 拖动被条目自己的 `_touchListener`（`swallowTouches=true`）吃掉

**修法是引擎补丁**（`engine/build/fix_scrollview_propagate.py`）：`propagateTouchEvent`
改成沿裸父链往上找第一个 Widget。它是**严格超集** —— 直接父节点是 Widget 时找到的还是
同一个，而 `Widget::interceptTouchEvent` 本来就会逐层递归。

**教训**：
* **「子控件的触摸会往上传播给父滚动容器」是 cocos 的设计**，但它走的是
  `dynamic_cast<Widget*>`，**普通 `Node` 会把它切断**。游戏用 `csb` 拼 UI 时
  很容易在中间夹一层裸 Node。
* 排查这类问题要**先量容器自己的尺寸/命中区**，别一上来就怀疑坐标或方向 ——
  这次两者的数字都是对的，问题在"事件根本没传过来"。
* 又一次印证 §6 的老话：**引擎与游戏的约定不一致，单看 JS 或单看 C++ 都发现不了。**

---

### 6.12 「服务端改了、客户端不动」——先查**推数据**有没有派发

给战斗结算加好感度时撞上的：服务端 `instance.finishlevel` 回了 `rewards.levelReward.favor`
和 `data.favor`，客户端**一点反应都没有**。原因不在数据、也不在形状，而在中间那一层
转发（`responseConfig`）压根没跑，见 [protocol.md §5.2](protocol.md)。

排查办法（照抄即可，别靠读反汇编猜）：

```js
// 1) 把「唯一入口」包一层计数。Favor.update 是把服务端那一行套到本地的唯一入口
var f = dataManager.favorCenter.getFavorByKey('sasm');
var p = Object.getPrototypeOf(f), o = p.update;
p.update = function () { p.__n = (p.__n || 0) + 1; return o.apply(this, arguments); };
// 2) 发一个**幂等**的请求（把衣服换成现在这件，状态不变但响应里带 favor 块）
server.request('favor.setclothes', {charKey:'sasm', itemKey:f.curClothes}, cb, false);
// 3) 读计数：0 = 没派发
```

⚠️ 探针的两个坑，都真踩过：

* **第 4 个参数 `isBackstageRequest` 要和真实调用一致**。`Favor.submitSetClothes` 传的是
  `false`；先传 `true` 测出「没派发」，结论作废，得重测（两次结果一样，但过程不严谨）。
* **别只包 `cb4ResFavor`**：万一派发方持有的是方法引用（构造时就取好了），包它是看不见的。
  包 `Favor.prototype.update` 这种「下游唯一入口」才与实现无关。

修法同样分两层，缺一层都不生效：

1. **客户端**：`patch.js` 的 RESP-DISPATCH 把 `responseConfig` 那三类写法补齐
   （收整个 `res` 的 / `updateByServer` 的 / 方法名各不相同的）——
   细节和「`{code, data: res.data[key]}` 为什么要包一层」见 protocol.md §5.2。
2. **服务端**：把块放对位置。以关卡结算为例，**奖励块必须在 `data.rewards` 里**，
   `data.level` 只走 `Level.updateLevel()`（只认星级/次数/时间三个字段）——
   以前 `dropReward` / `levelReward` 挂在 `data.level` 上，表现就是结算面板
   「获得物资」永远空着、`Exp+N` 恒为 0。

**教训**：这类问题的症状是「服务端明明发了」，很容易反向怀疑数据形状，
于是把形状改来改去都没用。**先量转发层有没有到**，再谈形状。

---

### 6.13 包一层**原生构造函数**时，静态常量要一起抄

给「真机不用 root」做 URL 改写时踩的，症状极具误导性：

```
客户端：WS connect "ws://127.0.0.1:8080" → WS connected
        randomKey / dhExchange / hashKey / base64Encode 全算完了
        GAMELOG cc.log: WebSocket readState:1        ← 就停在这
服务端：WS 会话开始 → 发欢迎包 →（一直阻塞在 recv，一个帧都没收到）
```

看起来像「网络不通」或「加密算错」，其实两边都没问题 —— 是**根本没调 send**。

根因：客户端 `wsFactory` 是在**模块加载时**把 `window.WebSocket` **捕获**下来的
（`var WebSocket = window.WebSocket || window.MozWebSocket`），而
`wsHandle.send` 的判定是：

```js
if (this.socket.readyState === WebSocket.OPEN) { ...this.socket.send(data)... }
else { cc.log("WebSocket readState:" + this.socket.readyState); }
```

我包出来的 `W` **没有抄静态常量**，于是 `WebSocket.OPEN === undefined`，
`1 === undefined` 恒假 → 走 else 分支，只打一条日志就返回。
（那行 `readState:1` 就是 else 分支打的，看着像"状态正常"，实际是"没发"。）

**规矩**：包装原生构造函数时，除了 `prototype`，静态成员也要照抄 ——
至少 `CONNECTING/OPEN/CLOSING/CLOSED` 这类常量，稳妥点
`for (var k in orig) W[k] = orig[k];` 再补一遍白名单（JSB 的原生构造函数
不一定可枚举）。

**教训**：同一个「单点拦截」的设计，XHR 侧只是包一个实例方法（没事），
WS 侧要替换构造函数（就出事）。定位靠的是**脱离游戏逻辑的最小复现**：
直接用客户端环境手动 `new WebSocket(...)` + `send(...)`，服务端立刻收到
`WS <- #1` —— 一步就把「包装坏了」和「游戏逻辑坏了」分开了。

---



## 7. 现状与待办

### 已完成

- [x] 服务端：CDN / 网关 / oauth / WebSocket 登录 / 加密业务路由（16 条）
- [x] DES 标准实现双向对拍；DH 用单位元绕过
- [x] Java 层去掉百度登录弹窗；删掉 46.6MB 没用的第三方 SDK
- [x] 全自动登录 → 主场景 → 开场动画 → 主界面
- [x] **引擎源码重建**（13 个补丁），战斗可完整跑完
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
- [x] Java 层退出确认弹窗：官方包自带乱码文案 → 换成正常中文；空桩 `Sdk.exit()` → 软退
      （`finish`，回桌面但进程进 cached，不留 signal 9）——见 §6.1
- [x] **天赋（培养）**：三条课题 + `player.selecttalent` / `player.upgradetalent`，
      落盘、升级真扣材料（`table_talent_upgrade` 的三张表进了 `gamesrv/data/`）。
      顺带修掉「登录包 `instance` 被桩覆盖」—— 那个 bug **把整个关卡进度吞掉了**，
      不只是天赋，见 §6.2
- [x] **装备系统**（`equipment.*` 7 条，路由 57→64）：登录块按 `EquipmentCenter.ctor`
      的形状给，7 条路由全实现（穿/脱/升级/分解/锁定/标记已读/编组）。
      ⚠️ 属性 key `firstAttrKeys` **必须由服务端算**（`<first_attr_group>+%02d(lv)+%02d(index)`）
      且**不能为空** —— 客户端 `addEquipmentAttrByKeys` 先取值后判长度，空数组也崩；
      而且不是所有 `first_attr_group` 都在属性表里，所以加了校验+回退。见 §6.3
- [x] **调试台**：日志分级（debug/info/warning/error/fatal，服务端按行首判级）、
      页签记忆、toast 自动关闭、控制台/日志/流量自动滚动、数据面板三个视图都能翻页、
      表浏览一行省略+点开展开、`[hidden]` 兜底（见 §6.4）
- [x] **好感度（宿舍）**（`favor.*` 5 条，路由 64→69）：登录块按 `FavorCenter._initData`
      的形状给（`favors` 是 **map**、`id` 的有无 = 已获得、缺不得 `favorInteractChance`），
      送礼 / 换装 / 换背景 / 抚摸 / 资料已读全实现。数值表 7 张进 `gamesrv/data/`。
      ⚠️ 三条坑各成一节：死键（§6.5）、map vs list（§6.6）、
      「算错不报错所以要自测」（§6.7）
- [x] **宿舍事件**（`favorevent.seteventsunlock` 1 条）：好感度到级解锁剧情，
      读完发 `reward_favor` 好感度。⚠️ 事件靠**登录块**下发（实测 184 条里 19 条是我们建的），
      见 §6.8
- [x] **守护灵**（`char.upgradedaemon`）+ **设置助战**（`player.updateasst`），路由 70→72：
      守护灵上限跟着**好感度等级**走（`table_favor_upgrade[lv].max_daemon_lv`，前 6 级是 0）、
      材料喂军士（`table_daemon_exp[品质]` 的同角色/同类型/其它三档）、军士真被吃掉；
      助战改 `player.asstKey`（重登靠登录包恢复）
- [x] **战斗结算给好感度**：数值来自客户端表 `table_level.favor` /
      `favor_char_key`（这两列**客户端一行代码都不读**，是原版留给服务端的），
      服务端只回**数量**，给谁由客户端拿自己那张表算（没有 `favor_char_key` 就全队 +
      主角一人一份）。顺带把奖励块从 `data.level` 挪到 `data.rewards` ——
      那一层才是 `_dealLevelResult` 读的地方。见 §6.12
- [x] **修好响应派发**：`patch.js` 的 RESP-DISPATCH 原来只补了 `updateByServer` 那批，
      `favor` / `newFavorEvent` / `useGiftStatus` 这一整条一直是死的
      （宿舍好感涨了、进度条和等级要重登才动）。现在照原版 `responseConfig` 三类写法补齐，
      并且不再「重试 2 分钟就放弃」。见 §6.12 + [protocol.md §5.2](protocol.md)
- [x] 文档：协议 / 逆向手法 / 打包逻辑 / 调试台 / 引擎调试 / 本总览 / **与原版的差异** / **从零复刻**

### 待办（按卡点排序）

1. **扭蛋 / 抽卡** —— 缺 `gachaMasterList` 运营配置；现在只保证不崩。
   `GUIDE_GACHA_KEY = 1002`（`GACHA_KEYS.GEM`）。
2. **日常 / 成就任务** —— 只做了 `type=2`（主线）；日常 246 条 / 成就 91 条。
3. **战果报告的「获得物资」还没在实机确认** —— 服务端现在把奖励块放在**对的那一层**了
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

4. **助战（好友支援）列表渲染不出来** —— 服务端已经能正确回 NPC 名单
   （`friendsupport.getrecommendsoldiers` -> 20 个 `npcId`，客户端
   `FriendSupport._recommendList` 里也确实收到了 20 个），
   但 `SupportChoiceLayer` 那边渲染不出来。已确认的：
   `setSupportList()` 手动调是好的（会往 `_pushAsynList` 里塞 18 个
   `{item, innSize, index}`），所以卡在「层的 `_recommendList` 是 0」。
   **不影响战斗**（这个弹窗是可选的好友助战）。
5. **其余未实现的 route** —— `python script/route_gap.py --static` 能列出全部。
   当前：客户端静态候选 **161** 条，服务端 **72** 条，缺 **97** 条。按单机价值排：

   | 命名空间 | 缺 | 说明 |
   |---|---|---|
   | `exchange.*` | 6 | 黑市交易所（`checkorder` 已实现）；要抽兑换表 |
   | `detect.*` | 6 | 侦查玩法 |
   | `diary.*` / `sign.*` / `subareaachievement.*` / `convert.*` / `share.*` | 1+1+1+1+1 | 零散领奖类，工作量最小，适合热身 |
   | `boss.*` | 5 | 好友 BOSS（`getbosslist` 已实现并回空表） |
   | `society.*` / `societyclg.*` | 33+6 | 军团——单机价值低、量最大 |
   | `friend.*` / `medal.*` / `arena.*` | 9+9+4 | 社交类，同上 |

   ⚠️ **`rank.*` / `boss.getbosslist` 这类"回空表"不算缺口**：私服没有榜、没有好友，
   回空才是对的（见 `handlers/rank.py` 的论证），别当成没实现去"补"。

   ✅ 已经补完的：`equipment.*`(7)、`favor.*`(5)、`favorevent.*`(1)、
   **`char.upgradedaemon`**(1)、**`player.updateasst`**(1)、
   `player.selecttalent`/`upgradetalent`。
6. `hashKey` / `hmac64` 还没复刻（登录靠单位元绕过）；自研 DH 的完整算法也没还原。

---

## 8. 工具与工作流

### 8.1 构建链路（每一步都是幂等的）

```powershell
cd E:\code\zcsmw
python server\client\modernize.py            # manifest / targetSdk=23 / 运行时权限
python script\sdk_strip\strip.py             # 删第三方 SDK + 装桩
python script\sdk_strip\gen_native_stubs.py  # .so 硬依赖的类
python server\client\patch_smali.py          # Java 层补丁（⚠️ 必须最后，见 docs/build.md）
# 引擎：ndk-build（见 E:\code\zcsmw\engine\build\build.ps1）
python script\build_apk.py [--no-probe]      # 改 assets + apktool 打包 + 对齐 + 签名
adb install -r -d E:\code\zcsmw\out\zcsmw-mod-signed.apk
```

### 8.2 调试工作流

**首选浏览器调试台**（服务端自己托管，不用改 APK）：

```
http://127.0.0.1:18080/devtools
```

流量（含「客户端调了但服务端没实现」的高亮 + 一键重放）/ JS 控制台 /
存档编辑 + 作弊 / 客户端日志流 / 表查询，五个面板。见
[`docs/devtools.md`](devtools.md)。

命令行那套仍然有用（批量、脚本化）：

```powershell
# 服务端（用守护脚本，别用 Start-Process 直接拉 run.py，会被回收）
python script\serve.py

# 在游戏进程里执行任意 JS（前提：probe 版本 + 服务端在跑）
python script\repl.py "dataManager.player._moduleState ? Object.keys(dataManager.player._moduleState).length : 'none'"

# 抽客户端表。**只补新表时务必加 --only**：每张表都是一次 /control/eval，
# eval 跑在游戏主线程上、返回值还要 base64+DES 回传，整轮全抽（含 table_soldier /
# table_equipment 那种几十万字的）会把模拟器压到卡死
python script\extract_client_tables.py --only favor
python script\extract_client_tables.py --only char_desc

# 反汇编
python script\jsc_strings.py  <assets>\src\ui\main\mainlayer.jsc _initModuleButtons
python script\disasm_func.py  <assets>\src\data\questcenter.jsc _createQuest

# 反查：这个 key / route / 方法名在哪个 .jsc 的哪个函数里用过
#（.jsc 是二进制，裸 grep 搜不到，必须走原子表）
python script\jsc_find.py useGiftStatus --func
python script\jsc_find.py "favor\..*" --regex

# 自测
python script\selftest_favor.py    # 好感度公式，**不需要模拟器也不需要服务端**
python script\selftest_game.py     # 走 HTTP 的协议/路由/落盘冒烟（要服务端在跑）
```

**排障顺序**（按复用性排序）：

1. 先看调试台的**流量**面板 —— 「客户端调了但服务端没实现」会直接标黄，
   请求 msg 和回包都能看到原文，还能一键重放
2. 再分清是 Java 层 / 引擎层 / JS 层 —— `dumpsys activity top`、tombstone
3. 抓 logcat，`OPPAIPATCH|` / `OPPAIHOOK|` 前缀的日志是补丁和探针打的
   （调试台的**日志**面板就是这条流，不用手动 `adb logcat`）
4. JS 异常先看 stack；`initUserData` 抛异常会连累一大片（见 §6）
5. 拿不准的数据形状，直接在调试台控制台里试 —— 几秒钟一个
6. **要看「这个函数被谁调的 / 某个变量到底是多少」**：调试台的**调试器**页签
   （或 `python script\jsd.py repl`）下断点 —— 那是引擎级的断点，游戏会真的停住，
   能拿调用栈、能在栈帧里求值。见 [`docs/engine-debug.md`](engine-debug.md)
7. 实在不行就包一层打日志，别猜

> ⚠️ `adb shell input tap` 在 MuMu 上**不可靠**，注入的事件不一定到得了 App。
> 别用它判断"点击坏了"。同理 `adb screencap` 有时抓不到 GL 层（截出来一片白），
> 以用户看到 / 服务端日志为准。

✅ **要验原生弹窗（Java 层 AlertDialog）别靠截图，导 View 层级**——比截图可靠得多，
GL 层截不到的时候它照样能拿到文字：

```powershell
adb shell uiautomator dump /sdcard/ui.xml
adb pull /sdcard/ui.xml
# 弹窗内容就是 TextView 的 text 属性，按 id 取：
#   android:id/alertTitle  android:id/message  android:id/button1  android:id/button2
```

实测：`screencap` 连抓 4 张全白，同一时刻 `uiautomator` 里弹窗的标题/正文/两个按钮
原文一个不缺。**判断"弹窗到底显示成什么样"以它为准。**

💡 想触发某个原生弹窗来验，用 `script\repl.py` 调**静态**入口：

```powershell
python script\repl.py "jsb.reflection.callStaticMethod('org/cocos2dx/javascript/QuickAdapter','exit','()V')"
```

⚠️ `jsb.reflection.callStaticMethod` **调不到实例方法**，会报
`CCJavascriptJavaBridge: Failed to find method id of ...`（logcat 里能看到）。
`AppActivity.exit()` 是实例方法，得走它的静态包装 `QuickAdapter.exit()`。

---

## 9. 文档地图

| 文档 | 内容 |
|---|---|
| `README.md` | 上手：项目结构、快速开始、补丁清单、协议骨架 |
| **`../REPRODUCE.md`** | ★ **从零复刻**：环境、要自备的外部资源、逐步操作 + 每步验证点 |
| **`docs/overview.md`**（本文） | 全景：成果、分层、逆向结论、坑速查、待办 |
| `docs/devtools.md` | 浏览器调试台：六个面板怎么用、架构取舍、怎么加面板 |
| `docs/engine-debug.md` | **引擎层调试**：引擎自带的远程 JS 调试器怎么打开、协议、4 个坑、复现清单 |
| `docs/protocol.md` | 协议逐项细节 + 反汇编证据（含 quest 协议、session 前缀） |
| **`docs/differences.md`** | ★ **与原版的差异总账**：A 不得不改 / B 私服取舍 / C 还没做 / D **数值是猜的** |
| `docs/reverse-engineering.md` | jsc 反汇编器原理、运行时探测手法、排障套路 |
| `docs/build.md` | 打包逻辑（为什么这么做） |
| `script/README.md` | 工具索引（哪个脚本干什么、加新模块的推荐流程） |
| `E:\code\zcsmw\engine\ENGINE_PATCHES.md` | 13 个引擎补丁的证据链与复现脚本 |

仓库外的关键路径：

```
E:\code\zcsmw\game\             apktool 解包目录（smali / assets / lib）
E:\code\zcsmw\out\              打包中间产物 + zcsmw-mod-signed.apk
E:\code\zcsmw\game\original\            原版 APK 备份（唯一的一份，别删）
E:\code\zcsmw\engine\              引擎移植工作区（cocos2d-js 源码 + NDK + 构建脚本）
E:\code\zcsmw\out\shots\             截图存档
```

---

## 10. 免责声明

- 本项目**不包含**任何游戏素材、`.jsc` 字节码、反编译产物或 APK。
- 所有代码均为自行编写，仅通过公开的 SpiderMonkey / cocos2d-x 源码与运行时观察还原协议。
- 仅供个人学习研究，请勿传播游戏素材或用于商业用途。
