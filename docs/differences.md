# 与原版的差异（私服改动总账）

> 这份文档回答一个问题：**「这跟原版《战场双马尾》v2.2.0 不一样的地方有哪些？」**
>
> 立项前提：游戏已停服，**原版服务端不存在**，客户端只有编译过的 `.jsc`
> （没有源码）。所以本项目 = 原版客户端 + 重建的引擎 + 全新写的服务端。
>
> 差异分四类，**排查问题时先确认是哪一类** —— A 类是"不改跑不起来"，
> D 类是"我猜的、可能和原版不一样"，两者的可信度完全不同：

| 类别 | 含义 |
|---|---|
| **A** | 不得不改（引擎/系统/服务端缺失） |
| **B** | 私服取舍（原版靠运营活动或在线服务，私服给不了就换个给法） |
| **C** | 还没做（缺口，见 §C） |
| **D** | **数值是猜的**（原版行为无从考证，见 §D） |

---

## A. 不得不改

| # | 改了什么 | 为什么 | 在哪 |
|---|---|---|---|
| A1 | **整个服务端自研** | 原版服务端已随停服消失。纯标准库 Python（无 pip 依赖；DES 会顺带用 `ctypes` 加载设备上的 OpenSSL 加速，验不过就退回纯 Python，见 A8），CDN/gate/login/game 四个端口 | `server/` |
| A2 | **重建 `libcocos2djs.so`** | 原版 `.so` 是用**改过的** cocos2d-js 3.6 编的，仓库里那版对不上。按 v3.6 重建 + 14 个补丁 | `engine/`、[`engine-debug.md`](engine-debug.md) |
| A3 | **客户端适配 11 条**（`patch.js`） | 引擎换了，几个绑定名对不上，不改直接黑屏；另外引擎里 `responseConfig` 不派发、少了几个绑定；**另外三条是客户端自己的坑**：队伍详情页 off-by-one（A3d）、道具数量事件不派发（A3e）、情报室返回键接到隐藏节点上（A3f） | `server/client/patch.js` 头部 |
| A3b | **响应派发自己补一层** | 原版靠 `src/util/server.js` 的 `responseConfig` 把响应里的模块块推给各中心，这套引擎上**一次都没跑**（实测 `Favor.prototype.update` 调用 0 次）。`patch.js` 的 RESP-DISPATCH 照它的三类写法补齐才生效 | [protocol.md §5.2](protocol.md) |
| A3c | **地址改成运行时改写**（`URL-REWRITE`，默认行为） | jsc 里的地址只能等长替换（`<host>:18080` 必须 19 字节 → **host 必须 13 字符**），而且那几个文件是就地改写的，换地址时替换逻辑找不到旧串会**静默跳过**、整包作废。现在 jsc 保留原始地址，改由 `patch.js` 在运行时改写 XHR / WebSocket；`project.manifest` 按 JSON 重写（它走原生 curl，拦不到）。代价：包内仍带官方地址 | [`REPRODUCE.md`](../REPRODUCE.md) Step 4b、`script/build_apk.py`（老路子 `--patch-jsc-urls`） |
| A3d | **队伍详情页默认落到「当前队伍」** | 客户端自己的 off-by-one：入口 `new TeamDetailLayer()` 不带下标 → 走兜底常量 `DEFAULT_TEAM_IDX = 1` → `_.findIndex(teams, {index:1})` 命中**第 2 队**。队伍 `index` 必须 0 起是客户端自己定的（`TEAM_COUNT_LIMIT=5`、`_setCurTeamIdx` 夹 [0,4]、`getCurTeam()` = `findIndex{index: curTeamIdx}`、`CommonTeamItem` 传 `getCurTeamIdx()-1`），改服务端 index 会让第 5 队开战前被夹错 → 只能在客户端补 | `patch.js` 末尾 TEAM-DETAIL（**删掉即可还原原版第 2 队**）；反汇编依据见 [`reverse-engineering.md`](reverse-engineering.md) 的 aliased 槽位那节 |
| A3e | **道具数量变化后补发 `item_count_updated_<key>`** | 客户端自己的死链：货币条注册的是 `bag.addCountUpdateListener(key, cb)` → `item.addPropListener("item_count_updated_"+key, cb)`，而 `Bag.updateItems` 改数量走 `item.count = n` setter，**这个 setter 不派发那个事件**（实测：手动 `dispatchPropEvent` 同名事件监听器立刻收到，走 setter 收不到）→ 服务端驱动的加/扣道具之后**顶部货币条一直是旧数字**（背包里其实已经变了）。玩家报「抽卡没扣我货币」就是这个 | `patch.js` 末尾 ITEM-EVENT（包一层 `updateItems` 补派发） |
| A3f | **情报室左上角返回键改接到"可见的那个"同名节点** | `illustrationscommonlayer.csb` 里有**两个** `returnbutton`：可见的在 `returnbuttonpanel` 下（`ccui.Button`，世界坐标 8,543），另一个在 `playillustrationspanel(不可见) → levelpanel(不可见)` 里（`ccui.Layout`，830,476）。`_init` 用 `ccui.helper.seekNodeByName(_ui, "returnbutton")`——**深度优先取第一个**——拿到的是隐藏页那个，于是 `onClickReturn` 接到了玩家点不到的地方；同屏类型页签/升序都是可见节点所以正常。**服务端没有杠杆，只能客户端补**（实机证据 `out/probe_return_btn2.js`） | `patch.js` 末尾 ILLUST-RETURN（包 `_init`，把可见箭头再接一次 `onClickReturn`） |
| A4 | **Java 层绕开渠道登录** | 原版走 QuickSDK→百度登录，那两个服务器早下线了，弹窗永远登不进去。改成原生直接回调「登录成功」 | `server/client/patch_smali.py`、`ServerLoginRunnable.smali` |
| A5 | **删掉第三方 SDK（46.6MB + 后续两轮）** | 统计/推送/广告/渠道全下线了，留着只是体积。第一轮删的是**"没人引用"**的大块（`android/support`、`R$*`、支付宝自带的 org.json…）；2026-09-20 又清了**"游戏自己的代码还吊着"**的那批：微信分享 8 类、信鸽推送 3 类、微博分享 4+1 类、TalkingData 1 类 —— 做法是先把 `GameShare`/`XGAdapter` 改写成保留原方法签名的桩、删掉 `AppActivity` 里三处 TalkingData 调用，再删包（**签名不能动**：JS 的 `jsb.reflection` 和两个 `.so` 的字符串表都按名字点名这两个类）。dex 144,404 → **134,276 字节**，smali 151 → **134 个类**；两台设备冷启动 + `jsb.reflection` 实调都验过 | `script/sdk_strip/`、`script/build_apk.py`（`DROP_SMALI`）、[`build.md`](build.md) §2b-2b、原件备份 `out/removed-smali-20260920/` |
| A6 | **登录不走真实 DH** | 原版握手用自研 DH + `hashKey`/`hmac64`，算法没还原。私服用 **DH 单位元**当共享密钥 | [`protocol.md`](protocol.md) |
| A7 | **`.ps1` 全部加 UTF-8 BOM** | PowerShell 5.1 对无 BOM 的 `.ps1` 按 GBK 读，中文注释会吃掉引号 → **解析失败 = 一行都不执行** | [`build.md`](build.md) |
| A8 | **DES 走 libcrypto 加速**（可选，不装也能跑） | 业务响应是 `base64(des(JSON))`，**每个请求都要把整包加密一遍**：登录包明文 219.8 KB，纯 Python 查表版 157 KB/s → **光加密 1.4 秒**（占该响应 98%，组包/JSON/base64 加起来不到 2%）；一轮 `selftest_game.py` 146 秒，基本全是等 DES。改用 `ctypes` 调 `libcrypto` 的 `DES_ecb_encrypt` 后同机 14 MB/s：登录包 1.43 s → 36 ms，自检 146.5 s → 2.8 s。**不是硬依赖**：第一次用到时拿一条公开的已知答案向量验加解密，验不过（或找不到库）自动退回纯 Python 并写一行日志；`GS_DES_PURE=1` 强制纯 Python | `server/gamesrv/crypto/des.py`；两条路线都逐字节对拍参考向量，`script/check_des.py` |
| A9 | **`targetSdkVersion` 23 → 33**（每次打包强制写回） | 原版按 Android 6（API 23）打的包。**Android 14 起拒绝安装 targetSdk < 23、Android 15 起 < 24** —— 以前装真机都得 `adb install --bypass-low-target-sdk-block`。当年抬不上去是有原因的：targetSdk 27+ 时那套 2016 年的 QuickSDK / 百度 SDK 会踩 `MODE_WORLD_READABLE no longer supported` 抛 SecurityException、28+ 撞隐藏 API 限制直接起不来；现在那套 Java 类已被 `script/sdk_strip/strip.py` 删光（剩余 151 个 smali 里 `MODE_WORLD_READABLE` / `getDeclaredMethod` / `Class.forName` 各 0 处，两个 `.so` 里 `quicksdk` / `baidu` 0 命中），前提不存在了 | 停在 **33**（Android 13）＝够新且坑最少：31+ 只需给带 intent-filter 的组件补显式 `android:exported`；34 会要求前台服务声明类型、35 强制 edge-to-edge（全屏横版容易画面出问题）。规则落在 `script/build_apk.py` 的 `normalize_android_manifest()`（幂等、每次打包都跑，因为 `game/` 不进 git）：targetSdk 33 + 补 exported + 删死掉的渠道 meta-data（`YESDK_*` / `BD*`）。顺带记一条：别照抄 `script/sdk_strip/manifest_clean.py` 那份「只给 SDK 用」的权限删除表 —— `WAKE_LOCK` / `ACCESS_WIFI_STATE` / `ACCESS_NETWORK_STATE` 现在都有人在用（`Utilsex.smali` 的 `PowerManager.newWakeLock`、客户端的 `WifiManager`） |
| A10 | **自己编 `arm64-v8a` 引擎**（原版只有 armeabi/x86） | 原版包的 `lib/` 只有 `armeabi` + `x86`；cocos 官方依赖包在 3.6 那个年代也只有这三套 ABI。真机（一加 PLZ110，`abilist=arm64-v8a`）本来靠厂商 32 位兼容层跑 armeabi-v7a，但那是**兼容层**：性能打折、64 位系统上还多一层转换。2026-09-20 把 arm64 依赖自己凑齐（依赖仓库里的 arm64 预编译 + 自建 chipmunk **6.2.1**、自建 libwebsockets **1.23**、SM 的 64 位 `js-config`、按 ABI 分的 curl / jpeg 头），编出 64 位 `libcocos2djs.so`（21.6 MB） | 实测真机 `primaryCpuAbi=arm64-v8a`、冷启动正常、登录 + 主界面轮询正常（**原生 64 位，不再走兼容层**）。一条命令可复现：`python script\build_arm64_deps.py` + `.\build.ps1 -Engine -Abi arm64-v8a -PackAbis arm64-v8a`；踩的三个坑（chipmunk 版本、libwebsockets 结构体布局导致 `setgid(0)` 被 seccomp `SIGSYS`、jpeg `boolean` 宽度不一致导致**所有 `.jpg` 静默变黑**）记在 [`../engine/ARM64.md`](../engine/ARM64.md) |

> A6 是**唯一一处"协议上和原版不一样"**的地方。它只影响握手强度，
> 不影响业务包（业务包是标准 DES + base64，和原版逐字节一致）。

---

## B. 私服取舍（数值 / 内容）

原版这些东西靠**运营活动**或**在线服务**，私服没有，所以换了给法。
**要还原原版手感就按最后一列改。**

| 项 | 私服 | 原版 | 怎么还原 |
|---|---|---|---|
| 指挥部等级 | 建号 **30 级** | 1 级起 | `store.MIN_PLAYER_LV` |
| 新手引导 | `guideMark` 全 1，**直接跳过** | 完整新手引导 | `store.GUIDE_MARK_DONE = 0` |
| 初始军士 | **18 个**（前锋/中卫/后卫各 6，品质 4） | 靠抽卡和剧情 | `store.SOLDIER_KEYS` |
| 军士栏位上限 | **300** | 50 起，再往上靠买 `100101 卡槽购买次数` | `store.MAX_SOLDIERS_COUNT`。私服卡池是全的（152 张自军卡），50 抽几次就顶到上限、新抽到的会落在栏位外，所以直接给足（老存档由 `_migrate` 补到 300） |
| 初始背包 | 钻石 10 万 / 萌钞 1000 万 / 行动力 999 | 很少 | `store.default_items()` |
| 天赋材料 | 200040~200048 **各 99** | 只能从**已停服**的运营活动拿 | `store.TALENT_MATERIAL_STOCK = 0` + `TALENT_STOCK_VERSION += 1` |
| 装备升级材料 | 100401 **×500** | 靠分解装备攒 | `store.EQUIPMENT_MATERIAL_STOCK` + 版本号 |
| 角色默认造型 | **自动发**默认衣服 + 默认背景 | 靠抽卡/活动 | `favor.ensure_default_looks()` |
| **衣柜 / 背景** | **一次性发满**（109 件衣服 + 54 张背景） | 靠扭蛋和活动；私服扭蛋是空卡池，不发的话「换装」「换背景」永远只有一件 | `favor.LOOK_STOCK_VERSION = 0`（或删掉 `ensure_look_stock` 的调用） |
| **「功能开启」弹窗** | **不弹**（把 32 个 mark 全标成已弹过） | 原版每个系统第一次开启弹一次；私服建号就 30 级 + 全解锁，一进游戏会**连弹 31 个**。纯服务端开关，客户端一行都不用动 | `store.MODULE_OPEN_POPUP_SKIP = False` 即还原（但要注意：**已经记进存档的 mark 仍然生效** —— 存档里那份 32 个已开是客户端回写下来的，要完全回到"每个系统第一次开启弹一次"还得把存档里的 `moduleOpenMark` 清掉）；这份 mark 有**两个形状坑**（放错层 / 值不等于键），都会让弹窗照弹，见 §F 最后两行 |
| **好感度礼物** | 建号/老存档**一次性发满 47 种 × 99 个** | 原版靠抽卡和活动拿；私服一件都不给的话宿舍「送礼」面板是空的、道具栏里也看不到礼物，整个玩法等于没做 | `store.GIFT_STOCK`（改成 0 并把 `GIFT_STOCK_VERSION` +1 即可还原；礼物 key 不硬抄，按 `table_item.type == 30` 取） |
| 宿舍互动判定框 | **放大到覆盖角色**（560×560） | 100×100，在角色右边且**不可见**，还只有 1 秒窗口 | 删掉 `patch.js` 末尾那段 |
| 排行榜 | **回空表** | 真实排行 | 不要改 —— 单机没榜，回空才对 |
| 好友 BOSS | **回空表** | 好友互动 | 同上 |
| 公会 / 竞技场 / 勋章 | **没做**（回空） | 完整社交 | 见 §C |
| 扭蛋 | **空卡池**（界面显示"没有卡池"） | 正常 | 缺的是**运营配置**（见 §C） |
| 公告 | 服务端 `var/notice.html` | 官方公告 | —— |
| **抽卡 / 扭蛋** | **自己造了 5 个池子**（免费 / 碎片单抽十连 / 钻石单抽十连），消耗 100 金条单抽、900 十连、9 好人卡十连；十连保底至少一张 S+；SR 档里 15% 是英雄/机甲大奖；初始送 99 好人卡 | 原版是**运营配置**（池子/概率/保底/排期全在服务端，随停服丢了；客户端 176 张表里一张 gacha 表都没有）。池子 **id 和枚举照客户端** `gachaconfig`（`GACHA_NAMES` 就是原版那 5 个 key），概率/保底/消耗是自己定的 | 单旋钮：`gacha.POOLS`（消耗/次数/每日限制）、`gacha.RARITY_WEIGHT`、`gacha.PRIZE_WEIGHT`、`gacha.TEN_GUARANTEE`、`store.FRAGMENT_STOCK` |
| **英雄 / 机甲** | 建号送 **1 英雄 + 1 机甲**（hadf + madflj），另外 1 个英雄（haysdn）和 5 台机甲只能**抽卡**获得 | 原版靠抽卡/活动 → 私服不送的话抽卡没大奖可出 | 抽到就进 `player["heros"]`/`player["mechas"]`，随登录块 `char.heros`/`char.mechas` 下发；`store.player_heros/player_mechas` |
| **充值 / 月卡 / 礼包（IAP）** | **直接成功**：客户端补丁把 `op.pay` 换成立刻回调，服务端按 `table_resource_exchange` 发货（含首单双倍），累计 30 元发首充奖励（金条 200 + 萌钞 50000 + 好人卡 10），月卡 = 30 天 + 每天 75 金条；**限购与活动档期都不卡** | 真实支付渠道 + 按 `exchange_num`/`interval_day` 限购、按 `begin_time`/`ended_time` 上下架 | 补丁在 `patch.js` 的 PAY-SUCCESS（整块删掉即还原成"点购买没反应"）。首充奖励内容**客户端全库没有**，只能自定（`exchange.FIRST_CHARGE_REWARD`）；月卡每日 75 只写在 `table_exchange_item[100001].desc` 里。⚠️ 不卡档期的另一个原因：90 个礼包的档期是 2016 年的，照表卡就全过期了 |
| **演习场对手** | 从 `table_friend_support_npc`（101 个 NPC）里按等级挑 8 个，名字/等级/5 个军士全抄 NPC 行 | 真人的 PvP 匹配（原版是别的玩家的阵容） | 单机没有别的玩家，只能拿 NPC 顶。想换口味改 `arena.make_rivals()` |
| **演习场重复打同一个对手** | 照给积分和萌币（`state` 只影响「已战胜」标记） | 未知；理论上赢了就不能再打（客户端 `state == 1` 时点挑战只弹 1603「已经战胜过他了呢~」），但结算面板上有「再来一次」，客户端会拿同一个 `index` 再进战斗 —— 这里回非 200 会让用户卡在战斗结束什么都不弹 | 想限制就在 `arena.exit_fight()` 里按 `state` 拒绝（注意上面那个副作用） |
| **日常 / 成就任务** | **按客户端表实现**：日常一天放当前等级那一档（7~11 条）、换日点 05:00、奖励按 `table_quest_reward` 真发 | 原版同样按表跑，但**部分条件的数据服务端拿不到**：12208 击杀鸭子数 / 12209 我方军士跪倒数 / 13102 技能熟练度 → 进度恒 0 | 那三个要战斗结算里的击杀/阵亡统计（客户端 `battleInfo` 里可能有） |
| **好友** | **好友全是 NPC**：`table_friend_support_npc` 的 101 行（numberId = 900001 + 表内下标），建号送 **5 个好友 + 2 条待处理申请**，申请出去 **60 秒**后 NPC 自动同意；**换日**清空四个物资位、让 **2 个**好友重新送物资 | 真人好友（搜索数字 ID 加人、真人之间的申请/送收） | 单机没有别的玩家。跨账号好友**还没做**（`searchplayer` 只认 NPC 号；要做的话得让两边各存一份镜像记录，见 [protocol.md §12](protocol.md)）。条数/延迟：`friends.SEED_FRIENDS` / `SEED_APPLIES` / `SEED_SENDS` / `ACCEPT_DELAY_SEC` |
| **好友送 / 收物资** | **送物资不花任何道具**（请求体里只有 `numberId`，没有任何消耗参数），收物资给 **行动力 `100003` ×2** | 未知（原版大概率是把自己的一点行动力送给对方） | 给什么、给多少完全由服务端定：`friends.TAKE_REWARD_KEY` / `TAKE_REWARD_COUNT` 两个环境变量。想改成"送的时候扣自己 1 点行动力"就在 `friends.send_materials` 里加 `items.sub_item` |
| **头像 / 勋章本体** | **一次性发满**：23 个头像 + 所有「已达成」勋章的勋章道具（初次登录共 47 件，全部带 **NEW** 标记） | 靠抽卡/活动/成就慢慢拿 | `medal.ensure()`。发满的理由和衣柜一样（私服扭蛋是空卡池、活动没了）；NEW 标记点过就消失（4 条 `set*old`/`clearallheadnew`） |
| **赛事/预约类勋章** | **直接算完成**（25 条「在[某某]活动中获得积分第 N 名」「预约参军达到指定人数」） | 靠早已停掉的运营活动 | `medal._activity_medal()`：`condition_kind` 为空的都算完成，否则永远拿不到。要还原就让它返回 False（那 25 条会变成永远未完成） |
| **好友 BOSS 从哪来** | 服务端**直接刷 3 只**：1 只挂自己名下（首战免费、打完能分享给萌友），2 只挂 NPC 萌友名下（要花 BP） | 列表里是**好友的** BOSS（好友打了才会出现在你这儿） | 单机没有别人。`boss.BOSS_COUNT` / `boss.owner_of()`；只刷 `fb4018 / fb4019 / fb4037` 三族（第四族 `fb4038` 的关卡 403819… 没有对应章节，章节面板里点不进去） |
| **BP（好友BOSS点 `100201`）** | 每天补到 **30** | 靠活动 / 好友互动攒 | `boss.POINT_DAILY`。不补的话 tier2/tier3（2/3 点一场）打两下就没了 |
| **BOSS 伤害 / 击杀奖励** | 伤害奖励 = 按伤害占 `init_hp` 的比例给萌钞（打满一只 5000）；击杀 = 金条 30 + 萌钞 20000 + 好人卡 5 + BP 3，再按品质 20/30/40 乘 1/2/4 | `table_world_boss[*].harm_relate_key`（`401801` 这种）指向的奖励表**客户端全库 0 命中** —— 是原版服务端数据，停服就没了 | `boss.HARM_MONEY` / `boss.KILL_REWARD` |
| **BOSS 死了/跑了之后** | 在列表里**再留 5 分钟**（客户端要画「已击杀 / 已逃跑」图标，也是留给分享和看挑战记录的时间），之后清掉腾位置刷新的 | 未知 | `boss.KEEP_DEAD_SEC`；每只 BOSS 的存活时间本身照表（`exist_time` 10 分钟 / 1.5 小时 / 5 小时） |

> ⚠️ 行动力道具（100003）客户端 `limit_count` 是 **300**，而初始包发的是 999 ——
> 这个是原版数据和我们初始值的冲突，`add_item` 已经不回缩了，但界面上仍可能对不齐。
> 想干净就把 `default_items()` 里的 `ITEM_ACTION_POINT` 改成 300。

---

## C. 还没做（缺口）

`python script\route_gap.py --static` 能列出全部。当前：客户端静态候选 **161** 条，
服务端已实现 **117** 条，静态扫报**缺 52** 条（真实缺口 **51** —— `cp.bb` 是假阳性：
那是 chipmunk 的 bounding box API，在 `assets/script/chipmunk/jsb_chipmunk.jsc` 里）。

| 命名空间 | 缺 | 原版是什么 | 为什么没做 |
|---|---|---|---|
| `society.*` / `societyclg.*` | 33 + 6 | 军团（公会）+ 军团 BOSS（客户端有 `table_society_boss`） | 单机没有别人，工作量最大 |
| `ticket.*` | 3 | 人气投票（`ui/ticketmain`：`getticketstate` / `getticketcountbycontrolid` / `vote`） | 要编投票排期 + 候选人（运营数据），单机也没人投 |
| `player.*` | 4 | `skipguide` 跳过引导 / `msgtoworld` 世界频道发言 / `updatemsgpushmark` 推送标记 / `usecdkey` CDK 兑换 | 引导私服本来就全跳过（`guideMark` 全 1）；聊天和 CDK 都是运营向 |
| `soldieractivity.*` | 2 | 新年活动（`ui/newyearactivity`：`getsoldieractivitystate` / `convert`） | 运营活动，存档结构要先定 |
| `actquest.updateactivites` / `consumeactivity.openactivity` | 1 + 1 | 活动任务 / 消耗活动 | 同上：排期是运营数据 |
| `agent.flogout` | 1 | 登出（`Player.logout`） | 私服是长连接单机，断线重连就行 |
| `gacha.*` | 内容缺口 | 扭蛋 | **不是接线缺口**：176 张客户端表里没有一张是卡池配置（那是服务端下发的），要做只能自己造 master 数据 |
| 其他 | 若干 | —— | —— |

> ✅ `diary.*`（2 条）**2026-09-21 已实现**（见 [protocol.md §16](protocol.md)）：
> `table_story_review` 那 38 行只有 `image`，行形状靠反汇编 `Diary._initData` 定的
> （`unlockLevels` / `lockLevels` 两个 map），已解锁 = 通关过的关卡 + 买过的。
> ✅ `boss.*`（原缺 5 条）**2026-09-21 已实现**（见 [protocol.md §17](protocol.md)）：
> BOSS 的名字/品质/消耗/战斗关卡全在客户端 `table_world_boss` 里，服务端只给状态。
> ✅ 充值（`exchange.payment` 那半边）**2026-09-21 打通**（见 [protocol.md §18](protocol.md)）：
> 客户端补丁把 `op.pay` 换成立刻回调，服务端按表发货 + 首充 + 月卡。

**已知的"能看见但不完整"：**

* **战果报告的「获得物资」** —— 服务端已经把奖励块挪到客户端真正读的那一层
  （`data.rewards.dropReward / firstComplete / appraise / levelReward`），
  但 `instancemanager` 是少数反汇编对不齐的文件，`showCb` 的入参拼不出来，
  所以「`args.result` 是不是 finishlevel 那个 `ret`」还没实机确认（见 `overview.md` §7 待办 2）
* ~~**助战（好友支援）弹窗渲染不出来**~~ —— **2026-09-21 修好**：回包形状错了
  （客户端读 `data.recommendList`，我们发的裸数组），见 `protocol.md` §15。
* **装备星级显示 0 颗** —— 第二属性组的选取规则没还原，`secondAttrKeys` 留空
* **指挥部（玩家）等级不会升** —— 服务端只累加 `curExp`，没有升级逻辑；
  经验条会涨、等级一直不变
* **客户端表里有一条走不通的好感度分支** —— `LevelWinBase.getFavorUpCharsInfo` 的
  `favors` 分支写的是 `favors.count`（`favors` 是数组，`.count` 恒为 `undefined`），
  所以服务端**不能**用 `rewards.favorReward.favors` 那种形状下发好感度，
  得用 `rewards.levelReward.favor`。这是原版客户端自己的 bug，没去改它
* **情报室（菜单 → 情报室）左上角的返回键实机点不动** —— **已修**（`patch.js` 第 11 条 ILLUST-RETURN）。
  根因：`illustrationscommonlayer.csb` 里**有两个**叫 `returnbutton` 的节点，
  `_init` 的 `ccui.helper.seekNodeByName(_ui, "returnbutton")` 按深度优先取到的是
  `playillustrationspanel(不可见) → levelpanel(不可见)` 里那个 `ccui.Layout`（世界坐标 830,476），
  而左上角看得见的那个是 `returnbuttonpanel` 下的 `ccui.Button`（8,543）—— 它**从来没接过回调**。
  所以 `register()` 里 `addMenuItemEvent(_returnBtn, onClickReturn)` 是"接到了隐藏页的按钮上"，
  同屏的类型页签/升序（都是可见节点）照常能用。实机量到的依据见
  `out/probe_return_btn2.js`；补丁把可见箭头再接一次同一个 `onClickReturn`
  （= `getRunningScene().pop(true)`），实测点一下就退回主界面
* **情报室的真正入口是 `assets/src/srcex/menubtnex.jsc` 的
  `MenuBtnEx._onClickIllustrationButton` → `cc.director.getRunningScene().push(new
  Illustratedcommonlayer(1), false, true)`**；`assets/src/ui/illustrated/illustratedlayer.jsc`
  是**死代码** —— 它要的 `res/illustrationslayer.csb` 根本没随包发（包里只有同名 `.png/.plist`），
  实例化它会让引擎 `CC_ASSERT(FileUtils::isFileExist)` 失败、接着解引用 null 直接 SIGSEGV
  （2026-09-20 我用探针 `new IllustratedLayer()` 真把客户端打崩过一次）。查这个界面的问题
  一律从 `Illustratedcommonlayer` 入手

---

## D. 数值是猜的（原版行为无从考证）

**这一类要特别注意**：原版服务端没了，客户端的这部分逻辑**只播动画、不算数**，
所以数值只能反推。以下是推断出来的，**可能和原版不一样**，
玩游戏时如果觉得"手感不对"，大概率在这几条里：

| 项 | 私服取值 | 依据 / 不确定性 |
|---|---|---|
| 派遣掉落内容 | 用 `table_detect_chapter.gainIcon2`（界面那排「可能掉落」图标）当奖池，按 `groupWeight*` 挑档位 | `gainItemGroup<i>` 指向的**真实道具组表客户端里没有**（把 `"101111"` 当 key 扫遍所有 `table_*` 都 0 命中）→ 只能拿客户端有的东西凑；掉几个（`probability<i>` 千分比）与档位权重是照表算的。单旋钮 `detect.roll_rewards` |
| 签到排期 / 奖励 | **7 天循环**，每天**恰好一件**：金条 200 / 行动力 30 / 萌军刊物 ×1 / 萌钞 5000 / 小颗糖果 ×1 / BP ×1 / 第 7 天萌能水晶 ×50 | 客户端表里**没有**签到奖励表（`jsc_find table_sign*` 0 命中）→ 排期和奖励全是服务端数据，原版怎么发的无从考证。单旋钮 `sign.SIGN_REWARDS` / `sign.SIGN_DAYS`。⚠️「每天恰好一件」不是随便定的：`SignRewardItem._init` 只在 `length === 1` 时画该道具的图标，多件一律用通用图标（见 §F）；第 7 天原本写的 `100101`「卡槽购买次数」更是画不出图标的计数器 |
| 好感度生日加成 | **×2**（额外再加一份等量经验） | `birthdayAdd` 这个字段得有含义，但**没有任何表能佐证倍数**（2026-09-20 又整表翻了一遍客户端 `table_constant` 的 223 项，没有生日/倍数相关的键）。单旋钮 `favor.FAVOR_BIRTHDAY_MULTIPLE` |
| 送礼物加好感 | 喜欢→`favor_love` / 讨厌→`favor_hate` / 普通→`favor` | 偏好档位是**实机问客户端**问出来的（`getPreferenceWithSendGift` 返回 2/4/3），但三个字段的用法是推的 |
| 生日偏好档 | 生日 → 档位 **1** | `getPreferenceWithSendGift` 只有 love/hate 两条分支，**永远回不了 1**；而回礼表里 1/2 两档才有东西、请求体里又带着 `isBirthday`，所以推成"生日=1" |
| 回礼概率 | `pr` 按**十分之几**（4 → 40%/件，`id1`/`id2` 各判一次） | 表里 `pr` 全是 4，**取值域无从校准**；`table_favor_receive` 客户端**一行都不读**（`jsc_find` 0 命中），所以只能自己定规则。单旋钮 `favor.RETURN_ITEM_PR_BASE` |
| 回礼内容 | 只在偏好档 1（生日）/ 2（喜欢）掉 `id1`/`id2`，数量取 `c1`/`c2` 的 `"min,max"` 区间 | `p=3/4` 那两行**表里压根没有 id/c 字段**（63 个角色 × 4 档全一样）→「普通/讨厌的礼物不回礼」是表里读出来的，不是猜的 |
| 换装 / 换背景 | `favorValue = 0`（不加好感度） | 没有换装表，`table_constant` 里也没对应项。客户端会把它拿去播"+N" |
| 抚摸的 `returnItems` | 恒给空 map | 那张回礼表是"收到礼物的反应"，和抚摸无关。给空 map 而不是 undefined，是因为客户端直接送进 `popupRewardWithItems` |
| 宿舍事件何时解锁 | 好感度达到 `table_favor_random_event.favor_lv` | 表结构反推；`newFavorEvent` 走哪条响应推下去也没实机确认 |
| 宿舍事件奖励 | 读事件时发 `reward_favor` | **客户端全库 0 命中**这个字段，说明是纯服务端数值，但"什么时候发"是推的 |
| 分区关卡每日次数 | **每关每天 3 次**（`instance.SUBAREA_DAILY_TIMES`），05:00 跨天清零 | 客户端表里**没有**这个数（剧情关的 `challenge_times` 也空着），原版给多少无从考证。但**不能给 0**：客户端 `isCanBattle` 是裸比较 `challengeTimes >= challengeTimeLimit`，0 会被判成「次数用完」（实测踩过）。单旋钮 |
| 分区关卡开放时间 | **不给**（`deadline`/`limitDay`/`limitTime` 全缺 = 永久开放） | 客户端 `isSubareaLevelOpen` 在这三个字段全缺时直接 `return true`，所以这是**客户端自己认的"不限时"**，不是我编的时间表。原版的排期（哪个区几点开）没处可查 |
| 分区章节次数 | **不限次**（`challengeTimes: -1`） | 这是**客户端自己的约定**：`Instance.updateActivityInstance/<` 见到 `-1` 就转成 `Number.MAX_VALUE`。所以不算我编的 |
| 分区关卡**列表**从哪来 | 服务端只认 `table_chapter.type == "5"` 的 4 个章节（5001~5004） | 章节清单**只存在客户端表**里（服务端没有别的来源），所以抽了 `table_chapter.json`；顺序照表里的 `priority` |
| 演习场**积分增减公式** | `swing = round((对手分 - 我分) * points_range / points_formula_a)`；赢 `+clamp(points_formula_c + swing, 1, points_volatility)`，输 `-clamp(points_formula_c - swing, 1, points_volatility)`（势均力敌 ±10） | 表里只给了系数（a=800 / c=15 / range=5 / volatility=10）、**没有任何公式**；原版服务端没了，无从考证。旋钮在 `arena.points_change()`。⚠️ 别和 `arenaInfo.change`（今日剩余挑战次数）混了 |
| 演习场**挑战次数** | **每天 `default_change` = 8 次**，每打一场扣 1，跨 05:00 重置 | `change <= 0` 时客户端两个按钮直接 `toast(1602)`「木有挑战次数了！」（`table_dictionary` 读出来的），所以这就是"每日次数"；但**默认几次、输了算不算**表里没写，只能这么定。旋钮 `arena.CHALLENGE_RESET_HOUR` / `default_change` |
| 演习场**赛后评价字母** | 战斗用时 ≤30s→sss…；阵亡 0→sss…；对手积分比自己高越多越好 | 客户端只用这三个字母挑 `res/icon/arenascore/*.png`（7 档），**评分规则全无出处**。旋钮 `arena.score_info()` |
| 演习场**对手的段位/积分** | 段位在「我的段位 ±1」内随机，积分取该段位区间内、往我的积分附近靠（±`points_volatility*points_range`） | 表里有 `arena_rival_condition_weight_a/b/c`（100/100/100）但**语义无从考证**，`robot_rival_lv_range_mode1..4` 也只知道是等级区间 → 这里只用它做了「挑等级接近的 NPC」这个意图 |
| 演习场**输了的奖励** | 给 `fail_coins`（14）个演习萌币，和赢的 `pvp_rewards`（`100019#14`）同量 | `pvp_rewards` 是客户端自己解析的（`ArenaSelectTeam` 显示「胜利奖励」），`fail_coins` 只是表里一个孤零零的 14，推断成"失败补偿" |
| 演习场**赛季重置时间** | `arena_reset_first_date`(2016-01-01) 起每 `arena_reset_cycle_day`(14) 天一轮，取下一个轮次 | 这是照表算的（不是编的），但**原版到底重不重置积分**无从考证 —— 现在只把这个时间发下去给客户端显示倒计时，服务端不做赛季清零 |
| 抽卡**概率 / 保底** | 单抽权重 `{n:560, r:300, s:110, sr:30}`（千分比）；`sr` 档里 15% 是英雄/机甲；十连**保底至少一张 S+** | 纯自造：运营配置随停服丢了，客户端也没有任何 gacha 表可以反推。旋钮 `gacha.RARITY_WEIGHT` / `PRIZE_WEIGHT` / `TEN_GUARANTEE` |
| 抽卡**池子消耗** | 钻石单抽 100 金条 / 十连 900；碎片单抽 1 好人卡 / 十连 9；免费池每天 1 次 | 同上，自造。`GACHA_NAMES` 只给了池子名字（免费/碎片/钻石·单抽十连），没给价格。想按原版改只动 `gacha.POOLS` |
| 抽卡**池子里有哪些卡** | 自军卡 152 张全进池（按 quality 分 4 档）+ 英雄 2 + 机甲 6 | 客户端**没有卡池配置表**，只能拿"能当军士发的卡"（`table_soldier_master[charKey].card_type == 1`）当池子；原版还有技能卡/经验卡（`GACHA_EFFECT_FILE` 里有 SKILL/EXP 的特效），暂时没放进去 |
| 好友**送物资**给不给东西 / 花不花东西 | **不花不赚**：只置「已送」位，不扣任何道具 | 请求体里**只有 `numberId`**，一个消耗参数都没有 → 原版要么是"免费友情动作"，要么是服务端按固定规则扣（比如扣 1 点行动力）。本服取前者。想改成扣自己行动力就在 `friends.send_materials` 里加一句 `items.sub_item` |
| 好友**收物资**的奖励 | **行动力 `100003` ×2** | 同上，给什么完全由服务端说了算（`data.reward` 是 `{itemKey: count}`）。原版大概率给行动力/物资，但**数值无处可查**。旋钮 `friends.TAKE_REWARD_KEY` / `TAKE_REWARD_COUNT` |
| 好友**申请多久被同意** | **60 秒**（NPC 自动同意，下次拉列表时结算） | 真人好友才有"对方点同意"，NPC 只能自己定。旋钮 `friends.ACCEPT_DELAY_SEC`（设 0 = 立刻同意） |
| 好友**建号送几个 / 每天几个 NPC 送物资** | 建号 **5 个好友 + 2 条申请**；换日后 **2 个**好友送物资 | 单机没有别的玩家，不给的话好友面板是空的、送/收物资一个都点不到。旋钮 `friends.SEED_FRIENDS` / `SEED_APPLIES` / `SEED_SENDS` |
| 好友**NPC 的头像** | 按表内下标轮流用 `table_item` 里 `type=60` 的头像（`"<itemKey>:2"`） | NPC 行里**没有头像字段**；客户端 `getHeadSpr` 对 `HEAD_TYPE.OTHER` 走 `new ItemIcon(key)`，只要图标 png 在 `res/charimage/` 里就能画（23 条里排除 `601005`/`gifttulip`，它的图标不在那个目录）。给空则全部走客户端的默认头像 `table_constant.default_head_id` |
| 勋章**条件语义**（`condition_kind`） | 10 类按 `table_medal.desc` + `times` 反推（抚摸 / 送礼 / 演习 / 派遣 / 军士达 X 级 / 好感达 X 级 / 抽卡 / 浴衣 / 日常条数 / 战斗失败） | `condition_id` 指向 `table_medal_condition`，那里只有 `condition_ids` = 原版**服务端**的 condition 对象 id（客户端没有对应的表）→ 语义只能从 desc 反推 | 阈值（70 级 / 好感 15）是用正则从 desc 里抠的；`1001` 通关指定关卡要点名关卡（需要 `table_level` 的「关卡名→key」表，还没抽）、`1003` 我方军士被推倒次数要战斗内部统计、`4002` 获得指定军士要「卡 key ↔ 名字」表 —— 这三类**进度恒 0，不假装完成**，见 `medal.progress_of` |
| 好友**的勋章**（`getfriendmedalinfo`） | 按 numberId 给一份**确定性**的：前 `1 + (numberId % 12)` 条勋章算达成、每组戴一个（最多 3 个） | 真人好友自己的勋章 | NPC 没有真实进度，确定性比随机好排查；换法在 `medal.friend_medal_info` |
| **礼包内容**（`convert.convert`） | 自己定三档：800001 → 金条 30 + 萌钞 5000；800002 → 金条 80 + 萌钞 15000 + 行动力 30；800003 → 金条 200 + 萌钞 40000 + 行动力 80 + 好人卡 5 | 原版在服务端，客户端表里**没有**：`table_convert_reward` 只给 `consume`/`reward_key`，而 `reward_key`（1030000x）指向的奖励内容全库 0 命中 | `handlers/convert.py` 的 `CONVERT_REWARDS`，改一个 dict 就行。⚠️ 礼包道具本身在私服没有稳定来源（原版靠活动），要试得先给自己发几个 800001~800003 |
| **私密剧情价格**（`diary.buyunlockstory`） | **`30 + 5 × 章节序号`** 金条（`table_story_review` 顺序），单章可覆盖 | `table_story_review`（38 行）客户端抽出来**只有 `image`**，`price` 是原版**服务端**塞进那张表的 → 数值无处可查。同一件事还导致**客户端 UI 里那个确认弹窗文案是「…花费 undefined 金条解锁该剧情？」**（`diarylevelitem` 读 `table_story_review[cid].price`） | 单旋钮 `diary.STORY_PRICE` / `diary.STORY_PRICE_DEFAULT`。想连弹窗文案一起修：登录块里已经顺带发了一份 `data.diary.prices`（客户端不读，专供补丁），在 `patch.js` 里把它填回 `table_story_review[*].price` 并重打包 APK |
| 私密剧情**可买章节 / 已解锁判定** | 38 章**全部可买**；「通关过的关卡」算已解锁（读 `instance` 存档 `levels[key].starMark/playCount`），其余进 `lockLevels` 可买 | 原版按活动时间逐步开（客户端表里 `activity_chapter_unlock_diary_days = 7` 就是那个天数），且"已解锁"多半也来自服务端下发 | 可买：`diary.buy_info()`（原版那套时间表无从考证）；判定：`diary._cleared_levels` / `bought_of` |

**反过来说，这些是"表里写死、和原版一致"的**（不用担心）：
好感度升级曲线（`table_favor_upgrade`，500/700/…/90000，满级 15）、
抚摸加值（`touch_favor_add = 22`）、互动次数上限 5 / 每小时回 1、
礼物加值（`table_favor_gift`）、回礼内容（`table_favor_receive` 的 `id1/id2` + `c1/c2` 区间；
台词在 `table_char_favor_receive_talk`）、
军士升级公式、装备属性 key 规则、天赋升级消耗、
**战斗结算的好感度**（`table_level.favor` / `favor_char_key`，895 关有值）——
全部用的客户端原表。

> 最后一条值得单说：`table_level.favor` / `favor_char_key` 这两列**客户端一行代码都不读**
> （`Level` 只把它们挂成只读属性，全库 `jsc_find getFavor` 只命中 getter 自己）。
> 也就是说它们是**原版留给服务端的数据**，正好落在我们手里 ——
> 这类"客户端表里没人读的列"是还原数值时最可靠的线索。

---

## E. 怎么快速判断"这是差异还是 bug"

1. **服务端日志 + 存档**：数值对不上先看存档，服务端说了算的地方不会有"原版行为"
2. **`git log` / 本文档**：A/B/C/D 四类都在这儿
3. **反汇编客户端**：原版行为只在客户端字节码里（`script/jsc_*.py`）。
   ⚠️ 但注意「客户端读了这个字段」≠「原版这么算」——
   好感度那套就是典型：客户端只拿 `favorValue` 播动画，数值全在服务端
4. **不确定就写进 §D**，别让它悄悄变成"事实"
5. **界面不动先看 §F**：服务端日志里一条请求都没有、但网络线程还活着（轮询照跑），
   基本就是客户端 JS 抛异常打断了初始化 —— 这时候该看的是**客户端日志**
   （`adb logcat | Select-String "JS:|JS ERROR"`），不是服务端

---

## F. 客户端的硬约束（服务端必须绕开）

原版客户端有几处"喂错数据就当场抛异常"的地方。这类既不是差异、也不是我们没做，
而是**服务端不能那样发**；踩过一次就记在这儿，都挂了自检兜着：

| 约束 | 踩过什么 | 后果 | 兜底 |
|---|---|---|---|
| **奖励 / 展示列表里不能出现 `table_item.ic == ""`（`q == 0`）的道具** | 2026-09-20：签到第 7 天的自造奖励用了 `100101`（表里叫「卡槽购买次数」；全表 481 条里只有它和 `100102` 没图标、品质 0） | 客户端 `ItemIcon.updateItemIcon` 只对 `bagconfig.ITEM_QUALITY`（白/绿/蓝/紫/黄）里的品质建 `_iconCase`，品质 0 一个档都匹配不上 → 循环走完 `this._iconCase` 还是 undefined，`if (iconPath) this._iconCase.addChild(sprite)` 抛 `TypeError`（itemicon.js:199；前面还有一发 `bag.getItemIcon()` 的 `cc.assert`，因为 `ic` 空串会拼出 `res/icon/item/undefined.png`）。这条链**一进游戏**就会跑（`SignRewardItem._init → rewardManager.getRewardIcon → new ItemIcon(key)`），异常打断主界面初始化 → **界面点不动、服务端一条请求都收不到**，但网络线程还活着（`boss.getbosslist` 照样每分钟轮询），特别误导 | `selftest_game.py` 的 `item_icon_check`（扫登录块 + 签到/派遣/分区成就/关卡掉落/商店/回礼）；`items.icon_of()` / `items.settle()` 里也加了 warning |
| `items` 块里别塞客户端不认识的 key | `Bag.updateItems` 是 `this._items[key].count = n`，client 那份 `_items` 是拿 `table_item` **全表预先建行**（481 条） | 真出现陌生 key 就是 `undefined.count = n` 的 TypeError | `items.changed_block()` 只回"改动前就有的 key" |
| 有些数组字段客户端是 **1 基**读的（下标 0 空着） | 签到奖励 `rewards`：客户端 `for (i = 0; i < rewardCount; i++) for (j = 1; sign.rewards[i + 1][j]; j++)`（i 从 0 数但取 `i+1`，j 从 1 数）。我一开始发的 0 基二维数组 | 走到最后一天 `sign.rewards[7]` 是 undefined → `TypeError: sign.rewards[(i + 1)] is undefined`（signnormallayer.js:84），和上一条一样**打断主界面初始化**（界面点不动、服务端没请求）。`count` 是 0 基而 `rewards` 是 1 基，这个错位是客户端自己的约定 | `sign._days_1based()`；`sign_check` 钉住形状；实机取数脚本 `out/probe_sign_shape.py` |
| 签到**每天必须恰好一件**奖励 | 我给每天塞了 2 件（金条 + 道具） | `SignRewardItem._init` 是 `length === 1` → 画该道具真图标；`> 1` → 一律 `res/signcommonicon` 通用图标（7 个格子长得一模一样，看不出给什么）；`=== 0` → `cc.warn` + `addChild(undefined)` 直接崩 | `sign.SIGN_REWARDS` 改成每天一件；`sign_check` 断言「恰好一件」 |
| **`char.charManual` 的 key 必须是军士**卡** key（`sasm010101`），不是角色 key（`sasm`）** | 2026-09-20：`charManual` 发的是 `table_soldier.card[].ck`（角色 key，196 个）。客户端情报室的清单是 `CharCenter.getSoldierManualKeys()`：`for (k in _charManual) if (charManager.getCharType(k) === CHAR_TYPE.SOLDIER && charManager.getSoldierCardType(k) === CARD_TYPE.TEAMMATE) push(k)`；而 `getCharType` 查的是 `table_soldier[k]`，`getSoldierCardType` 再走 `table_soldier[k].char_key → table_soldier_master[ck].card_type` —— **两张表都按卡 key 索引**（英雄/机甲那两个页签查 `table_hero`/`table_mecha`，key 本来就和角色 key 同名，混在一个 map 里没问题） | 每个角色 key 都打一行 `[error]charManager.getCharType() error, key is sasm`（实机 logcat 里刷了 196 行）；`getSoldierManualKeys()` 返回空 → 情报室「**数量 0/152**」、152 个格子**全是剪影**（格子是 `filtrateData()` 按 `table_soldier` 全表铺的，所以是"有格子没内容"） | `store.player_char_manual()` 只发玩家真有的卡（军士实例的 `key` + `player_heros/player_mechas`）；`selftest_game.py` 的 `char_manual_check` 按上面那条契约钉死。⚠️ 旧版这条检查只断言"非空"，而且里面那句 `[k for k, v in master.items() if str(v) == "1"]` 在 master 行是 `int` 的当前结构下**永远不成立** → 所以错误的实现照样"通过"了 |
| **键要挂在客户端真正读的那一层**：`moduleOpenMark` 在 `data.player` 里，不在 `data` 顶层 | 2026-09-20：我把「功能开启」标记放进了 `_module_stubs()`（= `data` 顶层）。客户端 `assets/src/data/player.jsc`：`Player.ctor(data) { this._moduleOpenMark = data.moduleOpenMark || {} }` —— 这里 `data` 是**玩家块**；`jsc_find moduleOpenMark` 全库只有 `Player.ctor` / `initModuleState` 两处，都读玩家块 | 顶层那份**谁都不看**，行为完全由"存档里那份老 mark"决定：存档只有 `{"1": 1}` 时 `_moduleOpenMark` 就只认 1 号，`moduleManager.popModuleOpen()` 一进游戏**连弹 31 个「xxx开启」**（logcat 里紧接着 `player.setmoduleopenmark [2,3,…,32]` 回写） | `handlers/agent.py` 的 `_player_block()` 统一往玩家块里灌；`module_open_check` 断言**玩家块**里那份，并且**故意把存档的 mark 掐成 `{"1": 1}`** 复现坏状态看服务端会不会补齐（旧代码下这条会当场报"缺 31 个 mark"，已验证） |
| **`moduleOpenMark` 每项的值必须等于它自己的键**（`{"7": 7}`，不是 `{"7": 1}`） | 2026-09-20 同日第二个坑：我按直觉发 `{markIndex: 1}`。客户端 `Player.initModuleState` 开头是 `for (var k in this._moduleOpenMark) moduleOpenMark[this._moduleOpenMark[k]] = this._moduleOpenMark[k];` —— **拿值当新键**（反汇编里 `setelem` 前压的两个表达式都是 `_moduleOpenMark[k]`） | 全发 1 会被塌缩成单个 `{"1": 1}` → 32 个模块里**只有 1 号算已开**，其余照弹。实机对照（活客户端内存里量）：`{i: 1}` → `isOpened` **1/32**、`{i: i+6}` → **26/32**（正好缺 1~6）、`{i: i}` → **32/32**。⚠️ 光看 `Object.keys(m).length` 是 32 会被骗过去，得看值 | `_module_open_mark()` 归一化成 `{str(mi): int(mi)}`；`player.setmoduleopenmark` 存回来时也是 `{mi: mi}`；`module_open_check` 按客户端那套拷贝语义（`{值: 值}`）算有效集合，并且断言每项值==键 |

---

## 相关文档

* [`overview.md`](overview.md) —— 全景 / 分层 / 待办；坑速查单独一篇：[`pitfalls.md`](pitfalls.md)
* [`protocol.md`](protocol.md) —— 协议逐项（含 A6 的握手差异）
* [`build.md`](build.md) —— 打包链路
* [`reverse-engineering.md`](reverse-engineering.md) —— 没有源码怎么反推
