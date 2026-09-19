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
| 培养（天赋） | ⚠️ | `TalentCenter` 构造抛异常（被容错吞掉），界面大概率是空的 |
| 背包 / 道具消耗 | ⚠️ | `data.item` 还是硬编码的平铺映射，买东西/消耗**不落盘** |
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
- [x] 文档：协议 / 逆向手法 / 打包逻辑 / 调试台 / 引擎调试 / 本总览

### 待办（按卡点排序）

1. **扭蛋 / 抽卡** —— 缺 `gachaMasterList` 运营配置；现在只保证不崩。
   `GUIDE_GACHA_KEY = 1002`（`GACHA_KEYS.GEM`）。
2. **背包 / 道具要落盘** —— `data.item` 现在是 `agent._module_stubs` 里硬编码的
   平铺映射，所以抽卡消耗、商店购买、培养花掉的萌钞都**不会真的扣**。
   要先在 `store` 里给玩家加一份 `items`，再把 `_module_stubs` 改成读它。
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

# 抽客户端表
python script\extract_client_tables.py

# 反汇编
python script\jsc_strings.py  <assets>\src\ui\main\mainlayer.jsc _initModuleButtons
python script\disasm_func.py  <assets>\src\data\questcenter.jsc _createQuest
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
| **`docs/overview.md`**（本文） | 全景：成果、分层、逆向结论、坑速查、待办 |
| `docs/devtools.md` | 浏览器调试台：六个面板怎么用、架构取舍、怎么加面板 |
| `docs/engine-debug.md` | **引擎层调试**：引擎自带的远程 JS 调试器怎么打开、协议、4 个坑、复现清单 |
| `docs/protocol.md` | 协议逐项细节 + 反汇编证据（含 quest 协议、session 前缀） |
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
