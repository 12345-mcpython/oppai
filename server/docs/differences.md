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
| A1 | **整个服务端自研** | 原版服务端已随停服消失。纯标准库 Python，CDN/gate/login/game 四个端口 | `server/` |
| A2 | **重建 `libcocos2djs.so`** | 原版 `.so` 是用**改过的** cocos2d-js 3.6 编的，仓库里那版对不上。按 v3.6 重建 + 13 个补丁 | `engine/`、[`engine-debug.md`](engine-debug.md) |
| A3 | **客户端适配 7 条**（`patch.js`） | 引擎换了，几个绑定名对不上，不改直接黑屏；另外引擎里 `responseConfig` 不派发、少了几个绑定 | `server/client/patch.js` 头部 |
| A3b | **响应派发自己补一层** | 原版靠 `src/util/server.js` 的 `responseConfig` 把响应里的模块块推给各中心，这套引擎上**一次都没跑**（实测 `Favor.prototype.update` 调用 0 次）。`patch.js` 的 RESP-DISPATCH 照它的三类写法补齐才生效 | [protocol.md §5.2](protocol.md) |
| A3c | **地址改成运行时改写**（`URL-REWRITE`，默认行为） | jsc 里的地址只能等长替换（`<host>:18080` 必须 19 字节 → **host 必须 13 字符**），而且那几个文件是就地改写的，换地址时替换逻辑找不到旧串会**静默跳过**、整包作废。现在 jsc 保留原始地址，改由 `patch.js` 在运行时改写 XHR / WebSocket；`project.manifest` 按 JSON 重写（它走原生 curl，拦不到）。代价：包内仍带官方地址 | [`REPRODUCE.md`](../../REPRODUCE.md) Step 4b、`script/build_apk.py`（老路子 `--patch-jsc-urls`） |
| A4 | **Java 层绕开渠道登录** | 原版走 QuickSDK→百度登录，那两个服务器早下线了，弹窗永远登不进去。改成原生直接回调「登录成功」 | `server/client/patch_smali.py`、`ServerLoginRunnable.smali` |
| A5 | **删掉第三方 SDK（46.6MB）** | 统计/推送/广告/渠道全下线了，留着只是体积 | `script/sdk_strip/` |
| A6 | **登录不走真实 DH** | 原版握手用自研 DH + `hashKey`/`hmac64`，算法没还原。私服用 **DH 单位元**当共享密钥 | [`protocol.md`](protocol.md) |
| A7 | **`.ps1` 全部加 UTF-8 BOM** | PowerShell 5.1 对无 BOM 的 `.ps1` 按 GBK 读，中文注释会吃掉引号 → **解析失败 = 一行都不执行** | [`build.md`](build.md) |

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
| 初始背包 | 钻石 10 万 / 萌钞 1000 万 / 行动力 999 | 很少 | `store.default_items()` |
| 天赋材料 | 200040~200048 **各 99** | 只能从**已停服**的运营活动拿 | `store.TALENT_MATERIAL_STOCK = 0` + `TALENT_STOCK_VERSION += 1` |
| 装备升级材料 | 100401 **×500** | 靠分解装备攒 | `store.EQUIPMENT_MATERIAL_STOCK` + 版本号 |
| 角色默认造型 | **自动发**默认衣服 + 默认背景 | 靠抽卡/活动 | `favor.ensure_default_looks()` |
| **衣柜 / 背景** | **一次性发满**（109 件衣服 + 54 张背景） | 靠扭蛋和活动；私服扭蛋是空卡池，不发的话「换装」「换背景」永远只有一件 | `favor.LOOK_STOCK_VERSION = 0`（或删掉 `ensure_look_stock` 的调用） |
| 宿舍互动判定框 | **放大到覆盖角色**（560×560） | 100×100，在角色右边且**不可见**，还只有 1 秒窗口 | 删掉 `patch.js` 末尾那段 |
| 排行榜 | **回空表** | 真实排行 | 不要改 —— 单机没榜，回空才对 |
| 好友 BOSS | **回空表** | 好友互动 | 同上 |
| 公会 / 好友 / 竞技场 / 勋章 | **没做**（回空） | 完整社交 | 见 §C |
| 扭蛋 | **空卡池**（界面显示"没有卡池"） | 正常 | 缺的是**运营配置**（见 §C） |
| 公告 | 服务端 `var/notice.html` | 官方公告 | —— |

> ⚠️ 行动力道具（100003）客户端 `limit_count` 是 **300**，而初始包发的是 999 ——
> 这个是原版数据和我们初始值的冲突，`add_item` 已经不回缩了，但界面上仍可能对不齐。
> 想干净就把 `default_items()` 里的 `ITEM_ACTION_POINT` 改成 300。

---

## C. 还没做（缺口）

`python script\route_gap.py --static` 能列出全部。当前：客户端静态候选 **161** 条，
服务端已实现 **72** 条，**缺 97** 条。

| 命名空间 | 缺 | 原版是什么 | 为什么没做 |
|---|---|---|---|
| `society.*` / `societyclg.*` | 33 + 6 | 军团（公会） | 单机没有别人，工作量最大 |
| `friend.*` / `medal.*` / `arena.*` | 9 + 9 + 4 | 好友 / 勋章 / 竞技场 | 社交类，单机价值低 |
| `exchange.*` | 6 | 黑市交易所 | 要抽兑换表 |
| `detect.*` | 6 | 侦查玩法 | 未开工 |
| `boss.*` | 5 | 好友 BOSS | 私服**故意**回空 |
| `gacha.*` | 内容缺口 | 扭蛋 | **不是接线缺口**：176 张客户端表里没有一张是卡池配置（那是服务端下发的），要做只能自己造 master 数据 |
| `diary.*` / `sign.*` / `subareaachievement.*` / `convert.*` / `share.*` | 各 1 | 零散领奖 | 好做，只是还没做 |
| 其他 | 若干 | —— | —— |

**已知的"能看见但不完整"：**

* **战果报告的「获得物资」** —— 服务端已经把奖励块挪到客户端真正读的那一层
  （`data.rewards.dropReward / firstComplete / appraise / levelReward`），
  但 `instancemanager` 是少数反汇编对不齐的文件，`showCb` 的入参拼不出来，
  所以「`args.result` 是不是 finishlevel 那个 `ret`」还没实机确认（见 `overview.md` §7 待办 3）
* **助战（好友支援）弹窗渲染不出来** —— 服务端能正确回 20 个 `npcId`，
  客户端 `FriendSupport._recommendList` 也收到了，但 `SupportChoiceLayer` 不显示。
  **不影响战斗**（那弹窗是可选的）
* **装备星级显示 0 颗** —— 第二属性组的选取规则没还原，`secondAttrKeys` 留空
* **指挥部（玩家）等级不会升** —— 服务端只累加 `curExp`，没有升级逻辑；
  经验条会涨、等级一直不变
* **客户端表里有一条走不通的好感度分支** —— `LevelWinBase.getFavorUpCharsInfo` 的
  `favors` 分支写的是 `favors.count`（`favors` 是数组，`.count` 恒为 `undefined`），
  所以服务端**不能**用 `rewards.favorReward.favors` 那种形状下发好感度，
  得用 `rewards.levelReward.favor`。这是原版客户端自己的 bug，没去改它

---

## D. 数值是猜的（原版行为无从考证）

**这一类要特别注意**：原版服务端没了，客户端的这部分逻辑**只播动画、不算数**，
所以数值只能反推。以下是推断出来的，**可能和原版不一样**，
玩游戏时如果觉得"手感不对"，大概率在这几条里：

| 项 | 私服取值 | 依据 / 不确定性 |
|---|---|---|
| 好感度生日加成 | **×2**（额外再加一份等量经验） | `birthdayAdd` 这个字段得有含义，但**没有任何表能佐证倍数**。单旋钮 `favor.FAVOR_BIRTHDAY_MULTIPLE` |
| 送礼物加好感 | 喜欢→`favor_love` / 讨厌→`favor_hate` / 普通→`favor` | 偏好档位是**实机问客户端**问出来的（`getPreferenceWithSendGift` 返回 2/4/3），但三个字段的用法是推的 |
| 生日偏好档 | 生日 → 档位 **1** | `getPreferenceWithSendGift` 只有 love/hate 两条分支，**永远回不了 1**；而回礼表里 1/2 两档才有东西、请求体里又带着 `isBirthday`，所以推成"生日=1" |
| 回礼概率 | `return_item_pr` 按**十分之几**（40%） | 表里 `pr` 全是 4，**取值域无从校准**。单旋钮 `favor.RETURN_ITEM_PR_BASE` |
| 换装 / 换背景 | `favorValue = 0`（不加好感度） | 没有换装表，`table_constant` 里也没对应项。客户端会把它拿去播"+N" |
| 抚摸的 `returnItems` | 恒给空 map | 那张回礼表是"收到礼物的反应"，和抚摸无关。给空 map 而不是 undefined，是因为客户端直接送进 `popupRewardWithItems` |
| 宿舍事件何时解锁 | 好感度达到 `table_favor_random_event.favor_lv` | 表结构反推；`newFavorEvent` 走哪条响应推下去也没实机确认 |
| 宿舍事件奖励 | 读事件时发 `reward_favor` | **客户端全库 0 命中**这个字段，说明是纯服务端数值，但"什么时候发"是推的 |
| 分区关卡每日次数 | **每关每天 3 次**（`instance.SUBAREA_DAILY_TIMES`），05:00 跨天清零 | 客户端表里**没有**这个数（剧情关的 `challenge_times` 也空着），原版给多少无从考证。但**不能给 0**：客户端 `isCanBattle` 是裸比较 `challengeTimes >= challengeTimeLimit`，0 会被判成「次数用完」（实测踩过）。单旋钮 |
| 分区关卡开放时间 | **不给**（`deadline`/`limitDay`/`limitTime` 全缺 = 永久开放） | 客户端 `isSubareaLevelOpen` 在这三个字段全缺时直接 `return true`，所以这是**客户端自己认的"不限时"**，不是我编的时间表。原版的排期（哪个区几点开）没处可查 |

**反过来说，这些是"表里写死、和原版一致"的**（不用担心）：
好感度升级曲线（`table_favor_upgrade`，500/700/…/90000，满级 15）、
抚摸加值（`touch_favor_add = 22`）、互动次数上限 5 / 每小时回 1、
礼物加值（`table_favor_gift`）、回礼内容（`table_char_favor_receive_talk`）、
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

---

## 相关文档

* [`overview.md`](overview.md) —— 全景 + §6 坑速查
* [`protocol.md`](protocol.md) —— 协议逐项（含 A6 的握手差异）
* [`build.md`](build.md) —— 打包链路
* [`reverse-engineering.md`](reverse-engineering.md) —— 没有源码怎么反推
