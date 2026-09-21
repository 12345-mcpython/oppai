# 坑速查：按症状找原因

> **一句话**：把这个项目里踩过的坑按「症状 → 真正原因 → 在哪个文件」列出来，共 18 条。
>
> **怎么用**：遇到「点了没反应 / 界面不动 / 数据不对 / 服务端明明发了客户端不认」，
> 先扫下面那张表；命中之后再看对应编号那一节的定位过程。
>
> 相关：[`overview.md`](overview.md)（全景与分层）· [`differences.md`](differences.md)（与原版的差异总账）·
> [`reverse-engineering.md`](reverse-engineering.md)（没有源码怎么查）· [`protocol.md`](protocol.md)（协议细节）

## 本节目录

- [1. SDK 桩里的「死键」——一类很容易误判成 JS 层 bug 的问题](#1-sdk-桩里的死键一类很容易误判成-js-层-bug-的问题)
- [2. 桩数据把真实数据**覆盖掉** —— 拼包顺序坑](#2-桩数据把真实数据覆盖掉--拼包顺序坑)
- [3. 客户端表：**空数组也会崩**（长度判断写在取值之后）](#3-客户端表空数组也会崩长度判断写在取值之后)
- [4. `hidden` 属性只是 UA 样式，作者样式能盖掉](#4-hidden-属性只是-ua-样式作者样式能盖掉)
- [5. 登录包里「客户端压根不读」的死键](#5-登录包里客户端压根不读的死键)
- [6. 登录块里最容易错的两种形状：map vs list](#6-登录块里最容易错的两种形状map-vs-list)
- [7. 数值算错不会报错，只会「数字不对」—— 这类逻辑必须钉自测](#7-数值算错不会报错只会数字不对-这类逻辑必须钉自测)
- [8. 「登录块」有的被读、有的没读 —— **别靠读反汇编猜，实机量一下**](#8-登录块有的被读有的没读--别靠读反汇编猜实机量一下)
- [9. 调试台自己也会坏，而且症状很像「游戏挂了」](#9-调试台自己也会坏而且症状很像游戏挂了)
- [10. 「点了/搓了没反应」——先确认那是**几步**手势](#10-点了搓了没反应先确认那是几步手势)
- [11. 「拖不动」不一定是拖的问题 —— 触摸**传播**断了](#11-拖不动不一定是拖的问题--触摸传播断了)
- [12. 「服务端改了、客户端不动」——先查**推数据**有没有派发](#12-服务端改了客户端不动先查推数据有没有派发)
- [13. 包一层**原生构造函数**时，静态常量要一起抄](#13-包一层原生构造函数时静态常量要一起抄)
- [14. 「界面点不动、服务端没请求」——**先去客户端日志里找异常**](#14-界面点不动服务端没请求先去客户端日志里找异常)
- [15. 同名节点：`seekNodeByName` 是**层序**，不是深度优先](#15-同名节点seeknodebyname-是层序不是深度优先)
- [16. 客户端会 `JSON.parse` 的字段：服务端必须发**字符串**](#16-客户端会-jsonparse-的字段服务端必须发字符串)
- [17. 「服务端发了、客户端不认」第三式：**键名差一个后缀**](#17-服务端发了客户端不认第三式键名差一个后缀)
- [18. 迁移/自检的「补齐」只该补，**绝不该删**](#18-迁移自检的补齐只该补绝不该删)

---

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
| 退出后再进游戏：**主界面一闪而过闪退**，再进一次又正常 | 退出时只 `finish()` 不杀进程 → 进程留在 cached；再进游戏**复用同一进程**，而引擎（GL 线程/native AppDelegate/JS VM）已拆一半 → 重新初始化 SIGSEGV（`libcocos2djs.so`，fault addr 0x14）。崩掉进程反而让"再进一次"变成冷启动，所以看着像"第二次才好" | `patch_smali.py` → `patch_sdk_exit()`：`finish()` **+ `Process.killProcess(myPid())`** |
| 退出弹窗**点「确定」没反应**（「取消」正常） | `Sdk.exit()` 是 `sdk_strip/gen_stubs.py` 生成的**空桩**，按钮调它等于没调 | `patch_smali.py` → `patch_sdk_exit()` |
| 关卡列表**不显示通关**、章节星级恒为 0；按通关解锁的功能（如「萌源增幅」）**永远锁着** | 登录包里 `instance` 被 `_module_stubs` 的桩覆盖成 `{"levels": []}` —— `data.update()` 排在真实进度**之后**，把整块顶掉。客户端 1142 个 Level 全停在 `_starMark = -1` | `agent.get_login_data` / `_module_stubs`（**桩里不要再出现 `instance`**）—— 见 第 2 条 |
| 用调试台「全部三星通关」作弊、甚至**重登都不生效** | 同一个根因：服务端存档早写对了，但**进度从没发到客户端**。客户端只在登录那一刻读一次关卡，所以"重登"也救不了没发出去的数据 | 同上 |
| 服务端**明明发了**数据（日志里有），客户端界面不动 | 中间那层转发（`responseConfig`）在这套引擎上不跑；`patch.js` 的 RESP-DISPATCH 补了没有 | 第 12 条 + [protocol.md §5.2](protocol.md) |
| 宿舍里好感度涨了，**进度条/等级要重登才动** | 同上：`data.favor` 一直没人派发给 `FavorCenter.cb4ResFavor`（`Favor.prototype.update` 调用 0 次） | 同上 |
| 关卡结算面板**「获得物资」永远空着**、`Exp+N` 恒为 0 | 奖励块挂在 `data.level` 上了；客户端读的是 `data.rewards.levelReward`，而 `data.level` 只走 `Level.updateLevel()` | `gamesrv/instance.py` |
| 某个界面**整页空白 + 左上角返回键有按下反馈但退不出去** | 建界面时 `seekNodeByName` 命中了**更深处的同名节点**（我们的 polyfill 写成了深度优先，原版是层序）→ ctor 中途抛 TypeError → 后面的 `_recommendationListPanel` 没建出来 → 返回键回调里 `this._recommendationListPanel.destroy()` 再抛一次，`run(new MainLayer())` 永远走不到 | `patch.js` 的 polyfill（层序 BFS）—— 见第 15 条 |
| **点了没反应**（按钮有反馈、界面一个字都不报），logcat 里 `SyntaxError: JSON.parse: unexpected character at line 1 column 2` | 客户端那个字段的 getter 是 `JSON.parse(this._x)`，服务端却发了**对象** → `JSON.parse({})` 先把对象转成 `"[object Object]"` 再解析，第 1 行第 2 列就是那个 `o` | 见第 16 条（`player.medalWear`） |
| 服务端日志里明明「发货成功」，客户端列表**没变化** | 回包块的**键名**跟客户端 `updateByServer` 读的对不上（差一个 `Add` 后缀之类）—— 它一个键一个键地 `if`，不认识的直接跳过，不报错 | 见第 17 条（`char` 块的 `soldiersAdd`） |
| 玩家**抽卡/练好的东西过一阵自己没了**（跑过自检、或版本号涨过一次之后） | 「补齐/迁移」逻辑里带了**删除**：删「不在默认名单里的 key」、或按 key 去重 —— 而抽卡得到的卡和重复卡本来就不在默认名单里 | 见第 18 条（`store.replenish_soldiers`） |
| 真机/新系统**装不上**报 `INSTALL_FAILED_DEPRECATED_SDK_VERSION` | 原版 `targetSdkVersion=23`，Android 14+ 禁装 <23、Android 15+ 禁装 <24 | **已修**：`build_apk.py` 的 `normalize_android_manifest()` 每次打包把 targetSdk 提到 **33**（并给带 intent-filter 的组件补 `android:exported`，31+ 不写同样装不上）。现在 `.\build.ps1 -Install` 直接装，不再需要 `--bypass-low-target-sdk-block`（那条开关只在装**旧**包时用） |
| 看 `abilist32` 为空就以为**跑不了** 32 位的 `armeabi` | **不一定** —— 有些 ROM 带厂商 32 位兼容层。实测一加 PLZ110（Android 16、`zygote64`、`abilist32` 空）能正常跑 | 直接装一个试；见 [`REPRODUCE.md`](../REPRODUCE.md) Step 4b |
| 编好的**队伍一直消失**（重登又是空的） | 两个原因叠在一起：①**队伍 id 对不上** —— 客户端认的 id 是「服务端 teams 数组的**下标**」（`Player.initTeams` 里 `new Team(this._teams[i], this._character, i)`，第三个参数就是 `for..in` 的 key），所以 `player.updateteams` 发的是 `"0".."4"`；而 `new_team()` 早期给的是 `id = index + 1`（1 起）→ 服务端 `未知队伍 id=0`、**整单静默跳过**（偶尔还会"撞上"另一支队 → 写错队伍）。② 跑 `selftest_game.py` 的军士升级链路会真吃掉两个军士，`handlers/char.py` 顺手把它们从队伍里摘掉 | `store.new_team()` 的 `id` = `index`（0 起）+ `store.normalize_team_ids()`（加载时对齐老存档）+ `handlers/player.py` 的 `update_teams` 按 index 找；自检脚本 `snapshot_teams()`/`restore_teams()` 收尾放回编成 |
| 分区界面**进去了但一片空白**（地图和按钮都在、一个章节都没有） | 分区左侧的章节列表来自 `getActivityChapterListOfType(SUBAREA)`，它遍历 `_activityChapters` 并用 **`table_chapter[entry.key].type == "5"`** 过滤；而 `instance.getactivityinstance` 原来回的是 `{"activityChapters": []}`（桩），且 `data` 必须是**那份 map 本身** | `gamesrv/instance.py` 的 `activity_chapters()`（认 `table_chapter.json` 里 type=="5" 的 4 个章节） |
| 分区关卡一进去就提示**「挑战次数用完啦~TuT」** | 客户端 `isCanBattle` 末尾是**裸比较** `challengeTimes >= challengeTimeLimit`，**没有** `!limit` 那层保护（那层只在 `checkLevelChallengeTimes` 里，而它没被调用）。服务端给 `challengeTimes: 0` 时 `0 >= 0` 成立 → 直接判没次数 | `gamesrv/instance.py` 的 `SUBAREA_DAILY_TIMES` 必须 **> 0**；跨天重置也得服务端做（`sync_subarea_plays`） |
| 通关后**好感度弹窗不出现/显示 +0** | 数量要回在 `rewards.levelReward.favor`（"给谁"由客户端拿自己 `table_level.favor_char_key` 算）；回了 `data.rewards.favorReward.favors` 会走到客户端一个 `.count` 写错的死分支 | `gamesrv/favor.py` + `instance.py` |
| 编成 →「队伍」**一进去就停在第二队**（点左箭头才回到第一队） | 客户端自己的 off-by-one：入口是 `new TeamDetailLayer()`（**不带下标**），于是走 `_initData` 的兜底 `this.curTeamIdx = _.findIndex(this.teams, {index: DEFAULT_TEAM_IDX})`，而模块常量 `DEFAULT_TEAM_IDX = 1`；`team.index` 是**服务端下发**的，本服 0 起 ⇒ 命中下标 1 = 第 2 队。队伍 index 必须 0 起是客户端自己定的（`TEAM_COUNT_LIMIT = 5`、`_setCurTeamIdx` 夹到 [0,4]、`getCurTeam()` = `findIndex{index: curTeamIdx}`、`CommonTeamItem` 传 `getCurTeamIdx() - 1`），改服务端 index 会让**第 5 队**开战前被夹成第 4 队 ⇒ 服务端没有杠杆 | `patch.js` 末尾 TEAM-DETAIL（运行时改成「当前队伍」）；反汇编依据：`differences.md` A3d |
| 宿舍**换完衣服整个界面点不动**（画面在动、音乐照放，屏幕上留着「着裝中…」） | **不是卡顿、也不是服务端**：客户端 `FavorLayer._playChangeClothes` 会把全局触摸闸 `op.touchEnabled = false`，而**唯一开闸的地方是 `end` 动画的最后一帧回调**；引擎 `ActionTimeline::step()` 在回调返回后又执行 `_playing = _loop`（用刚播完那段的 loop）并把新动画直接拽到最后一帧（`_currentFrame = _endFrame`）→ `end` 一帧没播、它的回调永远不响 ⇒ 闸门再也开不回来。探针实测：`touchEnabled=false`、时间轴停在新动画的 `endFrame`、trace 里只有 `FIRE …anim=began` 没有 `end`。换背景 `_replaceBg` / `LoadingLayer.show` 等同款写法都会中招 | 引擎补丁 **③b**（`engine/build/fix_lastframe_replay.py`，见 [`ENGINE_PATCHES.md`](../engine/ENGINE_PATCHES.md)）；**引擎还没重编时：重启游戏**（回到登录）即可恢复 |
| 一进游戏**整个界面点不动**（画面在动、音乐照放、服务端**一条请求都收不到**，但 `boss.getbosslist` 还照样每分钟轮询） | **客户端 JS 抛异常，把主界面初始化打断了**：奖励里出现了 `table_item.ic == ""`（`q == 0`）的道具（全表 481 条只有 `100101 卡槽购买次数` / `100102 装备槽购买次数` 两个）。`ItemIcon.updateItemIcon` 只对 `bagconfig.ITEM_QUALITY`（白/绿/蓝/紫/黄）里的品质建 `_iconCase`，品质 0 一个档都匹配不上 → `if (iconPath) this._iconCase.addChild(sprite)` 抛 `TypeError`（itemicon.js:199；前面还有一发 `bag.getItemIcon()` 的 `cc.assert`）。实机踩的是**签到第 7 天**自造奖励发了 `100101`，而 `SignRewardItem._init → rewardManager.getRewardIcon` 这条链一进游戏就跑 | 先看**客户端**日志：`adb logcat \| Select-String "JS ERROR\|JS:"`；服务端侧 `sign.SIGN_REWARDS` 已换（好人卡），`selftest_game.py` 的 `item_icon_check` 兜底；见 第 14 条 + [`differences.md`](differences.md) §F |
| 刚进游戏**连弹一堆「功能开启」**（32 个功能挨个弹） | 客户端 `MainLayer._updateAnimation()` 里有 `moduleManager.popModuleOpen()`：它遍历 `player.updateModuleState()`，把「已解锁但 `isOpened` 还是假」的模块挨个弹动画；而 `isOpened` 是 `Player.initModuleState()` 从**登录块的 `moduleOpenMark[mark_index]`** 读的（`table_function_open` 32 条，我们建号就 30 级 + 全解锁）。我们原来**没发这个字段** → 全被当"没弹过" | `handlers/agent.py` 的 `_module_open_mark()`（默认全标已弹过，`store.MODULE_OPEN_POPUP_SKIP = False` 还原）；表抽在 `table_function_open.json` |
| **点签到没用**（界面里一条签到都没有） | 登录块的 `sign.signs` 给成了**空数组**，而客户端 `SignCenter` 是 `this._signs = data.signs` + `for (k in _signs)` —— 要的是 **map**（`{signKey: 行}`）；另外 `normalSigns`/`eventSigns` 那几个键客户端**压根不读**。领奖回包还要带 `data.sign`（`updateTime` 变大）让客户端把新 `count` 合并进同一行 | `gamesrv/sign.py`（排期/奖励是自定的，见 §D）+ [protocol.md 第 7 条](protocol.md) |
| 一进游戏**又点不动**（100101 修好之后紧接着的第二发）：`TypeError: sign.rewards[(i + 1)] is undefined @ signnormallayer.js:84` | 客户端 `SignNormalLayer._updateItems` 的取数是 **1 基**的（`for (i = 0; i < rewardCount; i++) for (j = 1; sign.rewards[i + 1][j]; j++)`，event/birthday/novice 三层同款），而我发的 `rewards` 是 **0 基**二维数组 → 走到最后一天 `sign.rewards[7]` 是 undefined。`count` 是 0 基、`rewards` 是 1 基，这个错位就是它这么写的原因 | `gamesrv/sign.py` 的 `_days_1based()`（内外两层都留出下标 0）；`sign_check` 钉了形状；[protocol.md 第 7 条](protocol.md) 第 4 条 |
| 签到面板能开、但**7 个格子长得一模一样**（看不出每天给什么） | `SignRewardItem._init` 按件数分支：`_rewards.length === 1` 才画该道具真图标，`> 1` 一律用 `res/signcommonicon` 通用图标（`=== 0` 还会 `cc.warn` + `addChild(undefined)` 崩）。我一开始每天塞了 2 件 | `sign.SIGN_REWARDS` 改成**每天恰好一件**；`sign_check` 断言这一点 |
| **点派遣没用**（面板里一个任务都没有） | 登录块 `detect` 原来是 `{completeCount, allDetect, dropInfo, speedCount}` —— 客户端 `Detect.ctor` 读的是 `detect.speedInfo`（分类→已用免费加速次数，**数字**）和 `detect.detect`（章节key→`{beginTimeSec, waitTime, subCD}`，**秒**），两个都没有；6 条 `detect.*` 路由也没实现 | `gamesrv/detect.py` + [protocol.md 第 8 条](protocol.md) |
| 领了东西背包/货币条不刷新（要重登才变） | 客户端 `Bag` 是登录时缓存的；服务端改了背包却**不在响应里带 `items` 块**，界面就不会刷（`patch.js` 的 RESP-DISPATCH 里有 `items -> bag.updateItems`）。⚠️ 但**新入手的道具不能塞进去**：`Bag.updateItems` 对未知 key 是 `undefined.count = n` → TypeError | `items.changed_block(player, known_keys)`（只回客户端本来就有的 key）；见 [protocol.md §5.2](protocol.md) |
| 宿舍**送礼面板一件礼物都没有**（道具栏里也看不到礼物） | 礼物（47 种，`table_item.type == 30`）原版从抽卡/活动来，私服一件都没发 | `store.top_up_gifts()` + `GIFT_STOCK`（建号发、老存档按 `giftStockVersion` 补一次）；想还原就把 `GIFT_STOCK` 改 0 并把版本号 +1 |
| 送礼**回礼弹窗闪一下东西就没了** | 客户端 `giveAwayGift/<` 只把 `data.returnItems` 丢进 `popupRewardWithItems` **弹窗**，自己不加道具 —— 服务端算完必须自己 `add_item`，否则那个弹窗就是在撒谎 | `handlers/favor.py` 的 `use_gift`（已修，自检见 `selftest_favor.py` 的「回礼入账」段） |
| 商店里**金条换萌钞点下去弹不出东西/提示"资源不够啦……OAQ"** | 兑换的档位和「补满」规则都在客户端（`exchange_key_<次数>` + `receive_count = -1`），服务端要按同一套算：`times = todayExchangeTimes + 1` → 档位 → `table_resource_exchange[档位]`；`-1` 要补到上限而不是发 -1；失败码必须用客户端 `EXCHANGE_ERR_CODE_DICT` 里真有的（205 = 资源不够、204 = 今天次数用完），自己编的码会 `toast(undefined)` | `gamesrv/exchange.py` + [protocol.md 第 5 条](protocol.md) |
| 点**充值 / 月卡 / 礼包**一直弹提示、买不了 | **故意的**：私服没有支付渠道。客户端的 `judgeexchangestate` 先回 `state≠0` 弹提示，`exchange.payment` 也回非 200（`201` 的文案是「充值成功」，回 200 会被当成充值成功） | `gamesrv/exchange.py` 的 `judge_state` + `differences.md` §B |

| 点**演习场**没用（进去一个对手都没有、面板空的） | 登录块 `arena` 原来是桩 `{arenaInfo:{}, mechaSuperSkillCorrectOwn:{}}`，而 `ArenaCenter.ctor` 要 `{arenaInfo, rivals, resetTime, refreshTime}`——`rivals` 空就没有对手；另外 `arena.*` 那 4 条路由也没实现，点「挑战」连请求都发不出去 | `gamesrv/arena.py` + [protocol.md 第 9 条](protocol.md)（对手从 `table_friend_support_npc` 生成） |
| 演习场打了**不弹结算面板** / 积分不动 | 结算回包的字段是 `ArenaLayer._fightResult(err, data)` **平铺**读的（`success`/`rewards`/`winsRewards`/`scoreInfo`/`battleData`/`winPoints`），回非 200 它直接 `return`（用户就卡在战斗结束、什么都不弹）；另外每条 `arena.*` 回包都要带 `data.arena`，否则 RESP-DISPATCH 没法把对手列表/积分刷回界面 | 同上 |
| 演习场**失败后无法退出战斗**（`JS ERROR: js_cocos2dx_ui_Text_setString : Error processing arguments @ arenawinlayer.js:54`） | 结算面板三行是 `battleData.combatTime`（战斗用时，**秒**）/ `battleData.death`（人员伤亡）/ `battleData.rank`（**对手积分**，字段名有误导性）—— 我当时只发了 `combatTime`，另外两个是 `undefined` → `numelabed.label.string = undefined` 直接抛异常，面板构建中断、退不出去。三行的标题是从 `arenawinlayer.csb` 里读出来的（`战斗用时`/`人员伤亡`/`对手积分`） | `arena.exit_fight()` + [protocol.md 第 9 条](protocol.md) 第 6 条 |
| 演习场**「挑战」「刷新对手」按钮点不动**（toast「木有挑战次数了！」） | `arenaInfo.change` 被理解成"上一次积分变化"，输一场发成 `-10` → 客户端 `_onClickFightButton`/`_onClickRefreshButton` 开头都是 `if (_arenaInfo.change <= 0) toast(1602); return`。它其实是**今日剩余挑战次数**（`default_change` = 8，跨 05:00 重置，每场扣 1） | `arena.state()`/`info_view()`；`change` 必须是正数 |
| 演习场对手**头像画不出来**、日志刷 `JS: key is error`（8 次＝8 个对手） | `asstKey` 发成了**角色** key（`sgnw`），而客户端是 `new ItemIcon(asstKey)` → `Shop.getTypeById(key)`，它只认 `table_item`/`table_soldier`/`table_mecha`/`table_hero`/`table_equipment` 的 key —— 要发**军士卡** key（`sgnw010104`） | `arena.make_rivals()`；`arena_check` 会断言 |
| 抽卡/扭蛋界面**显示「没有卡池」**（一个池子都没有） | 客户端 176 张表里**一张 gacha 表都没有**：池子配置全在登录块下发，而 `Gacha.update(data)` 只认 `gachaData`/`gachaInfoList`/`gachaMasterList` 三个键（**都是 map**）—— 早期只发了 `gachaData`，`getGachaMasterList()` 就是空数组。另外 `saleInfoObj` 必须是「按次数索引」的折扣表（`saleInfoObj[0]` 基准价），写成对象会让价格算成 NaN、界面显示不出价钱 | `gamesrv/gacha.py` + [protocol.md 第 10 条](protocol.md)（池子 id 照 `gachaconfig.GACHA_NAMES`，内容是服务端自己造的） |
| 抽卡抽到军士**卡没了**（日志 `table_soldier 里没有 xxx，发不了这个军士`） | `items._add_soldier` 查的是 `soldier._row("table_soldier", key)`，而抽出来的 `table_soldier.json` 是**复合表**（`{card, master, constant, …}`）→ 永远查不到，**所有 SOLDIER 奖励都被静默丢掉**（抽卡/派遣/关卡奖励全中招）。正确表名是 `card`（压缩字段 `q` 品质 / `p` 站位） | `items._add_soldier`（2026-09-20 做抽卡时发现并修） |
| 抽卡**「没扣我货币」**（顶部货币条不刷新，重登才对） | **服务端没少扣，是客户端没刷新**：`Bag.updateItems` 走 `item.count = n` setter，而那个 setter **不派发** `item_count_updated_<key>`（手动派发同一个事件名，货币条的监听器立刻收到 → 监听侧是好的）。货币条只在进层那一刻读一次 → 抽卡/领奖/买东西之后它一直显示旧数字。实测：金条 100157→99257、好人卡 98→89，`bag.getItemCount()` 已经是新值，货币条还挂 100157/98 | `patch.js` 的 **ITEM-EVENT**（包一层 `updateItems` 补派发）；这条是客户端 bug，服务端没有杠杆 |

## 1. SDK 桩里的「死键」——一类很容易误判成 JS 层 bug 的问题

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

## 2. 桩数据把真实数据**覆盖掉** —— 拼包顺序坑

第 1 条 讲的是桩**没实现**（死键）；这一条是桩**实现了、但把真数据顶掉**，更隐蔽：
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

## 3. 客户端表：**空数组也会崩**（长度判断写在取值之后）

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

## 4. `hidden` 属性只是 UA 样式，作者样式能盖掉

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

## 5. 登录包里「客户端压根不读」的死键

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

## 6. 登录块里最容易错的两种形状：map vs list

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

## 7. 数值算错不会报错，只会「数字不对」—— 这类逻辑必须钉自测

好感度这套东西，加多少经验、升不升级、回不回礼、抚摸次数怎么回，
**全在服务端**（客户端只拿 `favorValue` / `favorAdd` 去播动画）。
算错了界面上不会抛异常，只是数字不对 —— 靠看画面基本发现不了。

所以 `script/selftest_favor.py` 在**进程内**把公式钉死（78 条断言，不需要模拟器、
不需要服务端在跑），`script/selftest_game.py` 那边只补形状和落盘。

⚠️ 写这类自测时注意：handler 内部是 `store.get_or_create_player()`，
**每次都从盘上重新 load**；直接改内存里的 player 是没用的，必须
`save_player` 之后再由 handler 重新读，否则测出来的是假的。

## 8. 「登录块」有的被读、有的没读 —— **别靠读反汇编猜，实机量一下**

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
   * 读了 —— `favor.favors`（map vs list，第 6 条）
   * 没读 —— `favor.isNeedAsstEff`（死键，第 5 条）
   * **读的**（我一度以为没读）—— `favorevent`
   每条都得单独确认：`script/jsc_find.py <key> --func` 看有没有人读，
   **再加一次实机量**。
3. 反汇编的 `getlocal`/`setlocal` 槽位**不要盲信** —— 那一处就是被它坑的。

## 9. 调试台自己也会坏，而且症状很像「游戏挂了」

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

## 10. 「点了/搓了没反应」——先确认那是**几步**手势

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

## 11. 「拖不动」不一定是拖的问题 —— 触摸**传播**断了

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
* 又一次印证 本文 的老话：**引擎与游戏的约定不一致，单看 JS 或单看 C++ 都发现不了。**

---

## 12. 「服务端改了、客户端不动」——先查**推数据**有没有派发

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

## 13. 包一层**原生构造函数**时，静态常量要一起抄

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

## 14. 「界面点不动、服务端没请求」——**先去客户端日志里找异常**

2026-09-20 修的：一进游戏主界面就点不动，服务端日志像**睡着了一样** ——
只有 60 秒一次的 `boss.getbosslist` 轮询，用户点哪儿都没反应。这条特别容易
往错的方向查（"是不是遮罩层吞了触摸"、"是不是触摸坐标错位"），因为
**服务端这边完全正常**，而客户端画面也在动。

**排查顺序**（照这个顺序做，别跳）：

1. **先量服务端**：`GAME route=...` 有没有？没有就说明请求根本没发出来，
   问题在客户端 —— 不要再去翻服务端 handler
2. **再看客户端日志**（关键一步，之前一直漏）：
   `adb logcat -d | Select-String "JS:|JS ERROR"` →
   这次一把就命中了：
   ```
   16:35:28.599 JS: Assert: bag.getItemIcon() error, key is 100101
   16:35:28.599 [oppai] JS ERROR: TypeError: this._iconCase is undefined
                 @ .../assets/src/ui/item/itemicon.js:199
   ```
   `JS ERROR` 那一行是**未捕获异常** —— 它会把调用方**整条初始化**打断
   （这里是登录块处理完、`guideManager.onGuide` 之后的主界面构建）
3. **再回客户端代码里找那个 key 从哪来**：`100101` 只在登录块的
   `data.sign.signs.normal.rewards` 里出现过（服务端把登录块 dump 出来数一下就知道）
4. **量一下"画不出来"的机制**（实机 REPL，不用猜）：
   ```js
   dataManager.bag._items["100101"]   // {_key:"100101", _count:0, _name:"卡槽购买次数", _quality:0, 没有 _icon}
   dataManager.bag.getItemIcon("100101")   // "res/charimage/undefined.png" + cc.assert 报错
   ```
   客户端 `Bag` 会拿 `table_item` **全表预先建行**（481 条），所以"画不出来"
   跟玩家有没有这件道具无关，只跟表里 `ic`/`q` 有关

**为什么不只是"图标空白"而是直接崩**：`ItemIcon.updateItemIcon` 是
`for (q in ITEM_QUALITY) if (q === quality) { …; this._iconCase = seekNodeByName(…, "iconcase") }`
—— 品质 0 落在五档（白/绿/蓝/紫/黄 = 10/20/30/40/50）**之外**，
循环走完 `_iconCase` 仍是 undefined，后面 `if (iconPath)` 用的时候就是 TypeError。

**教训**：
* 「服务端没收到请求」**先怀疑客户端抛异常**，而不是先怀疑触摸/遮罩/网络；
  触摸那类问题的特征是**画面停住**或**引导卡住**，而这次画面照常动
* 凡是要给客户端**显示**的东西（奖励列表、掉落、成就奖励、兑换结果），
  键必须能在 `table_item` 里查到**非空 `ic`**；能做自检就做自检
  （`item_icon_check` 就是这么加上去的，它带一个"把假登录块喂进去必须报 BAD"的
  反向验证）
* 客户端表里**没有图标的那两个键**（`100101`/`100102`）是**计数器**，不是道具：
  只能躺在背包里（`Bag.getList(ITEM_TYPE.ALL)` 会跳过 `type == CURRENCY`），
  永远不要放进奖励列表

---

## 15. 同名节点：`seekNodeByName` 是**层序**，不是深度优先

**症状**（2026-09-21，好友系统做完第一次进面板时暴露）：

* 好友（萌友）面板**整页空白** —— 页签、标题、「数量：」「今天可收取：」都在，
  但列表一条都没有；
* 左上角**返回键点不动**：有按下反馈，就是不退；
* logcat 里一串：

  ```
  [oppai] JS ERROR: TypeError: sendRedDotCase is null  @ .../ui/friend/friendlistpanel.js:56
  [oppai] JS ERROR: TypeError: this._friendListPanel is undefined  @ .../ui/friend/friendlayer.js:114
  [oppai] JS ERROR: TypeError: this._recommendationListPanel is undefined  @ .../ui/friend/friendlayer.js:214
  ```

**真正原因**：`ccui.helper.seekNodeByName` 的**遍历顺序错了**。

* 原版 `libcocos2djs.so` 里这个绑定是**层序（BFS）**；
  我们这边因为 cocos2d-js v3.6 没有这个 C++ 绑定（3.7 才加），
  在 `patch.js` 里补了一个 **深度优先** 的实现；
* 好友面板里 `sendbutton` / `chargedbutton` 这俩名字**重名**：
  panel 自己的页签按钮在 `chargedbuttonpanel` 下，而**每条好友条目**
  （`friendlistitemlayer.csb`）里也有同名的 `sendbutton` / `chargedbutton`；
* `FriendListPanel._initViewLayer` 先把条目塞进 `scrollview`（panel 的**第一个**子节点），
  然后 `_initButtons` 才 `seekNodeByName(panel, "sendbutton")` → 深度优先先钻进
  scrollview，命中的是**条目里**那个按钮 —— 它没有 `newreseffect2` 子节点，
  于是 `sendRedDotCase.visible = false` 抛 TypeError；
* ctor 断在 `_initButtons` ⇒ `_friendListPanel` / `_recommendationListPanel`
  都没赋上值 ⇒ 后面 `_updateView`（`friendlayer.js:114`）和
  `RecommendationListPanel` 回调（`:214`）接连抛；返回键的回调是
  `this._recommendationListPanel.destroy(); cc.director.getRunningScene().run(new MainLayer())`
  —— 第一句就抛，**`run(MainLayer)` 永远走不到**，所以"有反馈但不退"。

**原版是什么行为**（不是猜的，反汇编核实的）：

```powershell
# 从原版 APK 里抠出原始 .so
python -c "import zipfile,io;io.open(r'out\orig-libcocos2djs.so','wb').write(zipfile.ZipFile(r'game\original\zcsmw-original.apk').read('lib/armeabi/libcocos2djs.so'))"
$re = "engine\ndk\android-ndk-r10e\toolchains\arm-linux-androideabi-4.9\prebuilt\windows-x86_64\bin\arm-linux-androideabi-readelf.exe"
& $re -sW out\orig-libcocos2djs.so | Select-String seekNodeByName
#   _ZN7cocos2d2ui6Helper14seekNodeByNameEPNS_4NodeERKSs   00aea0fd  202
$od = "...\arm-linux-androideabi-objdump.exe"
& $od -d --start-address=0xaea0fc --stop-address=0xaea1d6 out\orig-libcocos2djs.so
```

反汇编出来的结构是「一个 `std::vector<Vector<Node*>*>` 当**层队列** + 下标 `r7` 递增」：
先比 root 自己，再扫**当前层**全部节点（顺手把它们的 `getChildren()` 压进队列），
扫完一层才进下一层（`_M_emplace_back_aux` 压队；`aea19a` 处 `r7++` 后跟 vector 的
**实时 size** 比）。`seekNodeByTag`（0xaea059，164 字节）是同一套结构。

**修法**：`patch.js` 里两个 polyfill 都改成层序（见 `seekNodeByName` 那段注释）。
改完之后：好友面板 5 条好友正常列出、返回键能退；
情报室返回键（`patch.js` 第 11 条那个 workaround）**根因也一起没了**，
它自己会跳过（`btn === this._returnBtn`）。

**教训**：

* 「同名节点」在这套客户端里**到处都有**（csb 复用的按钮名），凡是
  `seekNodeByName(某个容器, "很普通的名字")` 都有这个风险；
* 自己补的**引擎语义**（polyfill / 绑定）必须**跟原版逐条对齐** ——
  函数签名对了、能跑通，不代表语义对。这次的判据就是原版 `.so` 里那个 202 字节的函数；
* 界面上「有按下反馈但没反应」= 回调接到了，但**回调体抛异常**了
  （按下反馈是按钮自己的事，跟回调无关）—— 直接去 logcat 找 `JS ERROR`。

---

## 16. 客户端会 `JSON.parse` 的字段：服务端必须发**字符串**

**症状**（2026-09-21，勋章系统做完后点左上角玩家块时暴露）：

* 主界面**左上角那块（头像 + 名字 + EXP）点了完全没反应**，其它按钮都正常；
* 界面上不弹任何错误，logcat 里是一串（点几次就几条）：

  ```
  [oppai] JS ERROR: SyntaxError: JSON.parse: unexpected character at line 1 column 2 of the JSON data
      @ .../src/data/player.js:807
      ← MedalLayer<._initMedalInfo @ .../src/ui/medal/medallayer.js:480
      ← MedalLayer<._initSelfUI    @ .../src/ui/medal/medallayer.js:215
  ```

**真正原因**：`Player._getMedalWear` 是

```js
get medalWear() { return JSON.parse(this._medalWear); }
updateMedalWear(v) { this._medalWear = JSON.stringify(v); }
```

也就是说 **`player.medalWear` 在线上的形状是「JSON 字符串」**，而服务端发的是对象。
`JSON.parse({})` 会先把对象转成字符串 `"[object Object]"`，第 1 行第 2 列正是那个 `o`
—— 报错里的 `column 2` 就是判据。异常是在 `MedalLayer` 的 ctor 里**同步**抛的，
所以整个层建不出来，玩家看到的就是「点了没反应」。

**修法**：`agent._player_block()` 把存档那份 dict 转成字符串再发
（`json.dumps(..., separators=(",", ":"))`）；`medal.medal_wear()` 反过来两种都认
（存档里是 dict，devtools 手改过可能是串）。`medal.wearmedal` 的回包仍然是**对象**
—— `Player.updateMedalWear` 自己会 `JSON.stringify`。

**教训**：

* **凡是喂给客户端的字段，先去它的 getter 里看一眼有没有 `JSON.parse`/`JSON.stringify`**
  —— 这类字段在线上的形状是「字符串」而不是结构体。整套 `assets/src/data/*.jsc` 里
  这么干的目前只有 `Player._getMedalWear`（按 `callprop "parse"` 扫过一遍），
  但**每加一个新模块都要顺手扫一次**；
* 「点了没反应 + 界面无报错」优先怀疑**回调体抛异常**（见第 14 条）——
  这次报错只出现在 **logcat**，客户端界面一个字都没弹；
* 服务端**两种形状都收**（dict / 字符串）能省掉一次「老存档把面板打不开」的回归。

---

## 17. 「服务端发了、客户端不认」第三式：**键名差一个后缀**

**症状**（2026-09-21，玩家报「抽卡抽到的角色没进角色列表」）：

* 服务端日志一切正常：`gacha.gacha 池子 1002 x10 → 军士 [22, 23, …]`，
  存档里名单也从 18 涨到 62；
* 客户端**角色列表一个都没多**，也没有任何报错；
* 面板里实机量：`dataManager.character._soldiers` 只有 21 个（服务端 62）。

**真正原因**：`CharCenter.updateByServer(data)` 是**一个键一个键地 if**：

```js
if (data.maxSoldiersCount != null) this._maxSoldiersCount = data.maxSoldiersCount;
if (data.soldiersAdd) this.addSoldiers(data.soldiersAdd);
if (data.herosAdd)    this.addHeros(data.herosAdd);
if (data.mechasAdd)   this.addMechas(data.mechasAdd);
if (data.charManual)  for (k in data.charManual) this._charManual[k] = data.charManual[k];
```

我们回的是 `{"soldiers": [...整份名单...]}` —— **键名是 `soldiers` 而不是
`soldiersAdd`**，客户端整块跳过，既不加也不报错。`addSoldiers` 本身是
`for (…) this.addSoldier(one)` = `new Soldier(one); _soldiers[one.id] = one`，
所以正确形状是**「这次新增的那些行」**（带 `id`），不是整份名单。

**修法**：`gacha.char_block()` 改成 `soldiersAdd`/`herosAdd`/`mechasAdd`
（只发新增的，行里带 `id`），顺带带上 `charManual`（图鉴当场亮）和
`maxSoldiersCount`。自检里加了断言：抽卡回包的 `char` 块**不许**出现
`soldiers`/`heros`/`mechas` 这三个名字。

**教训**：

* 这套客户端的 `updateByServer` **全是「认键名」的**，多一个少一个字母都是
  **静默 no-op**（不抛错、不警告）。写回包前把那个类的 `updateByServer`
  反汇编看一眼，别照着字段名猜；
* 「服务端日志说发了」和「客户端收到了」之间隔着键名、形状、派发三道关
  （第 6/12/16 条是前两道，这条是键名）；
* 判据很好拿：**实机量客户端内部状态**（`dataManager.character._soldiers` 的条数），
  一眼就能分出「没发出去」和「发了没认」。

---

## 18. 迁移/自检的「补齐」只该补，**绝不该删**

**症状**（2026-09-21，我自己造成的，玩家抽卡抽到的东西被清空）：

* 玩家抽了 5 次卡，存档军士 18 → 62；
* 我跑了一次 `script/selftest_game.py` 收尾，存档变回 **18** ——
  44 个刚抽到的军士全没了。

**真正原因**：`store.replenish_soldiers()` 名义上叫「补齐」，实际还做两件删除：

```python
if key not in key_set or key in seen:      # 不在默认名单里的 key / 重复 key
    dropped_ids.add(row["id"]); continue   # → 删
```

* `SOLDIER_KEYS` 只有建号默认的 **18** 张，而卡池有 **152** 张自军卡
  —— 抽到的任何一张都不在名单里 ⇒ 全删；
* 抽到**重复卡**是正常结果，去重等于删玩家的东西。

它当初是为 `ROSTER_VERSION` 迁移写的（「名单本身改了，旧 key 留着没用」），
那个判断只对**建号默认名单**成立，对玩家自己攒的卡完全错。

**修法**：

* `replenish_soldiers` 现在**只补缺的**（默认名单里的 key 一个都没有才算缺），
  返回的 `dropped` 恒为 0，一个都不删、不去重；
* `selftest_game.py` 不再靠「补」来收尾：**跑之前把整份 `soldiers` 深拷贝拍快照，
  跑完原样写回**（`snapshot_roster()` / `restore_roster()`），
  抽卡用例新加的、升级链路吃掉的，全部回到跑之前的样子；
* 回归测试 `replenish_check` 反向验证：假名单里塞一个「不在默认名单里的 key」
  和一张重复卡，跑完必须**都还在**。

**教训**：

* 名字叫「补齐 / 迁移 / 修正」的函数里**一旦出现删除**，就要问一句
  「这些被删的行，有没有可能是玩家自己挣来的？」—— 这类 bug 的表现是
  **过一阵东西自己没了**（玩家很难复现、很容易被当成"服务器又回档了"）；
* 自检脚本**必须自己负责还原**：它跑的是真存档，不能指望「反正补齐逻辑会兜」；
* 玩家的东西（军士/道具/卡）只要没法原样恢复，就得先想清楚再动手 ——
  这次那 44 张卡的 key 没进日志，只能按同样张数重抽补上（见 commit 说明）。

---
