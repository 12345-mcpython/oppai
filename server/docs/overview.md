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
| 关卡列表**不显示通关**、章节星级恒为 0；按通关解锁的功能（如「萌源增幅」）**永远锁着** | 登录包里 `instance` 被 `_module_stubs` 的桩覆盖成 `{"levels": []}` —— `data.update()` 排在真实进度**之后**，把整块顶掉。客户端 1142 个 Level 全停在 `_starMark = -1` | `agent.get_login_data` / `_module_stubs`（**桩里不要再出现 `instance`**）—— 见 §6.2 |
| 用调试台「全部三星通关」作弊、甚至**重登都不生效** | 同一个根因：服务端存档早写对了，但**进度从没发到客户端**。客户端只在登录那一刻读一次关卡，所以"重登"也救不了没发出去的数据 | 同上 |

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

### 6.8 「登录块」不一定是给客户端读的 —— 有的模块是**只认响应推送**

宿舍事件（`favorevent`）就是这种。反汇编 `FavorEventCenter._initData(data)`：

    this._eventsInTable = {};
    this._favorEvents   = {};                       // ← 全程空着
    for (var i in table_favor_random_event) {
        this._eventsInTable[i] = table_favor_random_event[i];
        this._eventsInTable[i].table_id = i;
        data[i] = new FavorEvent(data[i] || {eventKey: i}, this._eventsInTable[i]);
        //      ^^^^^^^ 写回的是**登录块那个对象**，不是 _favorEvents
    }

也就是说 `data.favorevent` 在客户端眼里只是一个**临时容器**：给什么都不影响界面，
`_favorEvents`（界面真正读的那张表）只在响应键 `newFavorEvent` 里填。

推论 + 一个必须实机确认的点：响应派发那道闸

    if (dataManager.isLogin) { for (k in responseConfig) responseConfig[k](e.data); }

`dataManager.isLogin` 是登录成功**之后**才置 true 的（否则首次登录时
`dataManager.favorCenter` 还是 null，`cb4ResFavor` 直接抛），
所以**登录响应里的 `newFavorEvent` 很可能派发不进去**。

现在的做法是**两处都发**（登录响应 + 好感度涨了的响应），哪条通都能用；
实机验证方法：登录后看

    Object.keys(dataManager.favorEventCenter._favorEvents).length

是 0 就说明登录那条路不通，只能靠好感度涨了才推。

教训：**别假设「登录包里给了客户端就会用」**。每个模块都要回字节码确认
「这个 key 到底被谁读了」—— 同一个登录包里，三种情况都真实存在过：
读了（`favor.favors`）、没读（`favor.isNeedAsstEff`，§6.5）、
读进去但立刻丢掉（`favorevent`，本节）。

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
- [x] **宿舍事件**（`favorevent.seteventsunlock` 1 条，路由 69→70）：好感度到级解锁剧情，
      读完发 `reward_favor` 好感度。⚠️ 这一块的**形状是反汇编钉死的、触发时机是推断的**，
      推断的部分单独列在 `gamesrv/favor.py` 的「宿舍事件」那一段 —— 关键是
      **登录块 `favorevent` 是个 scratch 对象，客户端不拿它填界面**，
      事件只能靠响应键 `newFavorEvent` 推（见 §6.8）
- [x] 文档：协议 / 逆向手法 / 打包逻辑 / 调试台 / 引擎调试 / 本总览

### 待办（按卡点排序）

1. **扭蛋 / 抽卡** —— 缺 `gachaMasterList` 运营配置；现在只保证不崩。
   `GUIDE_GACHA_KEY = 1002`（`GACHA_KEYS.GEM`）。
2. **日常 / 成就任务** —— 只做了 `type=2`（主线）；日常 246 条 / 成就 91 条。
3. **战果报告的「获得物资」还是空的** —— 服务端已经把通关奖励算出来了
   （`level.dropReward / firstComplete / appraise / levelReward`，日志里能看到
   `掉落={'100002': 593} 首通={'100001': 20} exp=60`），但客户端面板读的不是这里：

   `LevelWinBase._init(args)` 读的是 **`args.rewards / args.firstComplete /
   args.appraise / args.rank / args.favorReward`**，而 `args` 是
   `instanceManager.onBattle/</showCb` 拼的那个对象 —— 那个对象里只有
   `againCb / backCb / battleInfo / result`（+`starMark` / `id`），**根本没有 rewards**。
   下一步要么找出原版是从哪儿补进去的，要么在 patch.js 里包一层
   `showCb` 的入参（把服务端回来的奖励塞进 `args`）。

4. **助战（好友支援）列表渲染不出来** —— 服务端已经能正确回 NPC 名单
   （`friendsupport.getrecommendsoldiers` -> 20 个 `npcId`，客户端
   `FriendSupport._recommendList` 里也确实收到了 20 个），
   但 `SupportChoiceLayer` 那边渲染不出来。已确认的：
   `setSupportList()` 手动调是好的（会往 `_pushAsynList` 里塞 18 个
   `{item, innSize, index}`），所以卡在「层的 `_recommendList` 是 0」。
   **不影响战斗**（这个弹窗是可选的好友助战）。
5. **其余未实现的 route** —— `python script/route_gap.py --static` 能列出全部。
   当前：客户端静态候选 **161** 条，服务端 **70** 条，缺 **99** 条。按单机价值排：

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

   ✅ `equipment.*`(7)、`favor.*`(5)、`favorevent.*`(1)、
   `player.selecttalent`/`upgradetalent` 已经补完。
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
