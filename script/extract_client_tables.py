"""把客户端（游戏进程里的）table_* 抽成服务端能用的 JSON。

为什么要从客户端抽：
    table_quest / table_quest_condition 这些表被编译成了
    assets/src/table/tablequest.jsc，服务端没有对应的原始数据文件。
    但这些表是**客户端静态配置**（不是服务端下发的），
    所以最省事的做法就是让游戏自己把它吐出来，存成服务端的 JSON。

前提：游戏正在运行、探针已加载、run.py 在跑（和 tools/repl.py 一样）。

    python tools/extract_client_tables.py
        -> gamesrv/data/table_quest.json

以后客户端换版本，重跑一次就行。
"""

from __future__ import annotations

import json
import os
import sys

# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repl import eval_remote  # noqa: E402



DATA_DIR = os.path.join(_paths.SERVER, "gamesrv", "data")

# 只抽服务端真正要用的字段，省得把几十万字的属性表搬过来。
#   type            QUEST_TYPE：1 日常 / 2 主线(普通) / 3 成就 / 4 公会 / 5 活动 / 6 新手
#   rank            同屏排序权重
#   lv              activate_lv，能接这个任务需要的主角等级
#   ak              activate_quest_key，前一个任务（做完了才轮到它）
#   n               这一条有几个 schedule 条件（服务端要按这个长度回 schedule）
#   tar             每个条件的达成值 table_quest_condition[key#i].param_1
#   sk              每个条件在 data.schedule 里的 key —— 见下面的说明
#   ct/cp1/cp2      第一个条件的 type / param_1 / param_2
#                   服务端要靠这几个字段判断「玩家做到没有」：(含义从 desc 反推)
#                     12212  队伍中只上阵 cp2 个军士获得胜利 cp1 次
#                     12216  队伍中存在 cp2 兵种的军士获得胜利 cp1 次
#                     13203  进行首次军士升级
#                     13102  1 位 cp2 军阶的军士等级达到 cp1 级
#
# ⚠️ data.schedule 是**对象**不是数组！
#    QuestCenter._createQuest 里是 data.schedule[ row["schedule_1"].split("#")[0] ]
#    也就是拿 table_quest.schedule_1 的第一段（"1#1" -> "1"）当 key 去取。
#    回数组的话 cur 取到 undefined，客户端算不出 scheduleCur，
#    任务界面上「领奖」按钮就永远是灰的（表现：列表能看，点不了领）。
#    没有 schedule_i 字段的任务走另一条分支、用下标 0。
QUEST_JS = r"""
(function () {
    var out = {};
    for (var k in table_quest) {
        var row = table_quest[k];
        if (!row) { continue; }
        var tars = [], keys = [], ct = "", cp1 = 0, cp2 = "";
        for (var i = 1; ; i++) {
            var cond = table_quest_condition[k + "#" + i];
            if (!cond) { break; }
            tars.push(cond.param_1 || 0);
            var sch = row["schedule_" + i];
            keys.push(sch ? String(sch).split("#")[0] : "0");
            if (i === 1) {
                ct = String(cond.type === undefined ? "" : cond.type);
                cp1 = cond.param_1 || 0;
                cp2 = cond.param_2 === undefined ? "" : String(cond.param_2);
            }
        }
        out[k] = {
            type: row.type,
            rank: row.rank || 0,
            lv: row.activate_lv || 0,
            ak: row.activate_quest_key || "",
            n: tars.length,
            tar: tars,
            sk: keys,
            ct: ct,
            cp1: cp1,
            cp2: cp2
        };
    }
    return JSON.stringify(out);
})()
"""

# 助战（好友支援）推荐用的 NPC 名单。
#
# 为什么要抽：客户端的 `SupportChoiceItem.createSolider(item, topType)` 里是
#     if (item.npcId) { var npc = table_friend_support_npc[item.npcId]; ... }
# 也就是说**服务端只需要回 npcId**，NPC 的兵种/等级/名字客户端自己表里有。
# 但服务端得知道有哪些合法 npcId，所以把这张表抽出来。
# 行里形如 `"general": "sasm010103#1#30#1"`，即 `key#星#等级#技能等级`。
NPC_JS = r"""
(function () {
    var out = {};
    for (var k in table_friend_support_npc) {
        out[k] = table_friend_support_npc[k];
    }
    return JSON.stringify(out);
})()
"""

# 商店货架（商品）。
#
# 客户端自带这张表（2049 条），`Shop.judgeBuyGood` 用 shelfKey 查它做**客户端侧**
# 校验（等级 / 公会等级 / 限购次数 / 消耗够不够 / 背包满没满），但真正成交
# 必须服务端说了算 —— 服务端要按 shelfKey 扣钱发货，就得知道每个货架卖什么、
# 要多少、限购几次。
#
# 行形如：
#   100101 = {shop_key:"1001", good_type:"i", good_key:"100038", good_count:1,
#             consume_key_1:"100019", consume_count_1:200, cycle_times_limit:1}
SHELF_JS = r"""
(function () {
    var out = {};
    for (var k in table_shelf) {
        out[k] = table_shelf[k];
    }
    return JSON.stringify(out);
})()
"""

# 商店本身（名字 / 类型 / 刷新时间表 / 拿什么货架）。
SHOP_JS = r"""
(function () {
    var out = {};
    for (var k in table_shop) {
        out[k] = table_shop[k];
    }
    return JSON.stringify(out);
})()
"""

# 关卡通关奖励。
#
# 战斗结算面板上「获得物资」那一栏是空的，就是因为服务端没回奖励。
# 客户端 `Instance._handlerRewards(rewards)` 要的是
#     {items: {道具key: 数量}, cards: {...}, equips: {...}, favors: {...}, scores: {...}}
# （`_getRewardTotal` 是按 key 累加的 map，`_getItemsBySort` 再转成数组排序）
#
# 数值来自客户端表：
#   table_level[levelId].level_reward_id        -> table_level_reward[id] 形如
#       {"key_1":"100002","min_count_1":540,"max_count_1":900,"span_1":1}
#   table_level[levelId].first_complete_reward_ids -> "id#id#id"
#   table_level[levelId].exp
#   table_level[levelId].favor               通关给多少好感度
#   table_level[levelId].favor_char_key      这份好感度给谁
# 服务端只需要「关卡 -> 掉什么」这一份，所以这里压成一棵小表。
#
# ⚠️ `favor` / `favor_char_key` 这两列**客户端一行代码都不读**（`Level` 只把
# 它们挂成只读属性 `_favor` / `_favorCharKey`，全库 `jsc_find getFavor` 只有
# getter 自己命中）。它们是原版**服务端**的数值 —— 结算面板上的好感上涨弹窗
# （`LevelWinBase._showOtherRewards` → `CharFavorUpLayer.pop`）要求服务端回
# `rewards.favorReward = {favors: {charKey: 数量}}`，数量只能从这两列来。
LEVEL_JS = r"""
(function () {
    var out = {level: {}};
    for (var k in table_level) {
        var r = table_level[k];
        if (!r) { continue; }
        out.level[k] = {
            exp: r.exp || 0,
            lvr: r.level_reward_id || "",
            fc: r.first_complete_reward_ids || "",
            ap: r.appraise_reward_ids || "",
            favor: r.favor || 0,
            fck: r.favor_char_key || "",
            it: r.instance_type || ""
        };
    }
    out.reward = {};
    for (var j in table_level_reward) {
        out.reward[j] = table_level_reward[j];
    }
    return JSON.stringify(out);
})()
"""


# 章节表（`table_chapter`，72 条）。
#
# 为什么要它：**分区玩法的章节清单在客户端表里**（`type == INSTANCE_TYPE.SUBAREA("5")`，
# 共 4 个：5001~5004），而服务端要回一份 `data.activityChapters` 告诉客户端"有哪些章节"：
#
#   Instance.getActivityChapterListOfType(type)
#       for (k in _activityChapters) {
#           var c = table_chapter[_activityChapters[k].key];   // ← key 要能在客户端表里查到
#           if (c.type === type) push(_activityChapters[k]);
#       }
#       sort(按 isActivityChapterOpen + priority)
#
# 所以服务端至少要知道每个章节的 `type`（过滤）和 `priority`（排序）；
# `lv`（章节的关卡列表）留着给分区成就用（成就条件就是"通关 A-x …"）。
CHAPTER_JS = r"""
(function () {
    var out = {};
    for (var k in table_chapter) {
        var r = table_chapter[k];
        if (!r) { continue; }
        var levels = [];
        for (var i = 1; i <= 40; i++) {
            var lv = r["level_" + i];
            if (lv) { levels.push(lv); }
        }
        out[k] = {
            t: r.type || "",
            p: r.priority || 0,
            ll: r.limit_lv || 0,
            n: r.name || "",
            lv: levels,
            sr: r.stars_reward || ""
        };
    }
    return JSON.stringify(out);
})()
"""


# 分区成就三张表（成就 84 条 / 条件 84 条 / 奖励 232 条）。
#
# ⚠️ **条件判定不用服务端做 —— 客户端自己算**。
# `subareaAchievementManager.formatBattleInfo(battleResult, battleId, team)` 在战斗结算时
# 按 `table_subarea_achievement_condition` 逐条判定（条件类型就是方法名：1003~1021，
# 拿 `param_1/param_2/param_3` 和 `battleInfo` 里的 ownUnitsInfo / enemyUnitsInfo /
# missleName / heroSuperSkillCount / unitDiedCount … 比），然后拼出
#
#     subareaInfo = {time, battleId, victory,
#                    modifyAchievements: {<id>: {progress, progressInfo, countKey, complete}},
#                    newAchievements: ["100101", ...]}      // 玩家本地还没有的成就行
#
# 塞进 `instance.finishlevel` 的 msg 发上来（日志里抓到过完整样例）。
#
# 所以服务端只需要：
#   ① `newAchievements` → 建行；`modifyAchievements` → 合并进度（`complete` → 写 `completeTime`）
#   ② 回包带 `updateSubareaAchievements: [行…]`（patch.js 的 RESP-DISPATCH 已经接好了）
#   ③ `subareaachievement.receivereward {achievementId}` → 按 reward 表发东西 + 置 `isReceiveReward`
#
# 行形状来自客户端 `SubareaAchievement.createAchievement`：
#     {id, progress: 0, progressInfo: {}, isReceiveReward: 0}      // completeTime 由服务端给
# 界面判可领：`_getSortIdx` = 无 completeTime → 2（未完成）／isReceiveReward → 3（已领）／其余 → 1（可领）。
#
#   table_subarea_achievement[<achievementId>]        = {condition_id, desc, jump_level_key, sub_area, times}
#   table_subarea_achievement_condition[<conditionId>] = {param_1, param_2, param_3, type}
#   table_subarea_achievement_reward["<achievementId>#<i>"] = {count, key, type}
SUBAREA_ACHIEVEMENT_JS = r"""
(function () { return JSON.stringify(table_subarea_achievement); })()
"""

SUBAREA_ACHIEVEMENT_CONDITION_JS = r"""
(function () { return JSON.stringify(table_subarea_achievement_condition); })()
"""

SUBAREA_ACHIEVEMENT_REWARD_JS = r"""
(function () { return JSON.stringify(table_subarea_achievement_reward); })()
"""


# 黑市交易所（ExchangeCenter）。
#
# 客户端的数据模型（反汇编 `exchangecenter.jsc`）：
#
#   `_exchangeData[<key>]` = 这一段交易所的玩家行
#       {exchangeKey, todayExchangeTimes, totalExchangeTimes, lastExchangeTimeSec}
#       —— `todayExchangeTimes` 的每日清零是**客户端自己**按
#       `table_constant.common_reset_time` + `lastExchangeTimeSec` 算的（`_resetData`）。
#
#   `table_exchange_item[<key>]` 是**价格阶梯**：里面再按次数分档
#       getExchangeInfoKey(key)：times = (行 ? 行.todayExchangeTimes + 1 : 1)
#                                 exchangeKey = key + times        // 字符串拼接！
#                                 取不到就退回 key + "default"
#   也就是「第 N 次兑换」有各自的消耗/产出，服务端按同一个规则算档位。
#
#   `table_resource_exchange` 是资源兑换（`getExchangeResourceByKey`：
#       receive_key_<i> / receive_count_<i> 逐条读）。
#
#   `exchange.exchange {key}` 只带 item key（次数服务端自己推），
#   回包是**新的那一段行**（带 exchangeKey），客户端 `update(res.data)` 合并。
EXCHANGE_ITEM_JS = r"""
(function () { return JSON.stringify(table_exchange_item); })()
"""

EXCHANGE_RESOURCE_JS = r"""
(function () { return JSON.stringify(table_resource_exchange); })()
"""


# 军士养成表。
#
# 「培养（升级）」这条链路的数值全在客户端本地算，服务端要复刻一遍才不会
# 「界面预览升到 X 级、点完变成 Y 级」。`CharCenter.calcSoldierUpgrade` 用到：
#
#   table_soldier_to_exp[lv]["quality_<q>_<star>"]          一个 lv 级材料的「经验值」
#   table_soldier[key].base_exp / base_cost                 卡片自身的经验/金币基准值
#   table_soldier_to_cost_for_upgrade[lv]["quality_<q>_<star>"]  额外金币
#   table_soldier_upgrade_exp[lv]["quality_<q>"]            升到下一级需要多少经验
#   table_soldier_lv_limit[star]["quality_<q>"]             等级上限
#
# `card` 只留服务端会用到的几个字段，整张表压下来也就百来 KB。
#
# ⚠️ `base_exp` 只有 card_type==TEAMMATE 的行才有；敌方行是 undefined，
# 客户端那边一加就变 NaN（表现：培养直接顶到等级上限）。所以服务端取值一律
# 用 `card[key].e`（抽取时已经 `|| 0`）。
SOLDIER_JS = r"""
(function () {
    var card = {};
    for (var k in table_soldier) {
        var r = table_soldier[k];
        if (!r) { continue; }
        card[k] = {
            e: r.base_exp || 0,
            c: r.base_cost || 0,
            q: r.quality || 0,
            p: r.positioning || 0,
            t: r.template || 0,
            ck: r.char_key || ""
        };
    }
    // char_key -> card_type。CARD_TYPE = {TEAMMATE:1, ENEMY:2, EXP:3, SKILL:4}。
    // 只有 1 是自军卡 —— 见 gamesrv/store.py 里 SOLDIER_KEYS 的说明，
    // 服务端要靠它判断"这个 key 能不能当军士发给玩家"。
    var master = {};
    // 守护灵（宿舍）：`daemon_mode` 就是 table_daemon 的 key（at0101/df0101/hp0101），
    // `type` 用来判"同类型材料"的额外经验。缺这俩字段驾驭不了 daemon 升级。
    var daemon = {};
    for (var m in table_soldier_master) {
        var mr = table_soldier_master[m];
        master[m] = mr.card_type;
        daemon[m] = {mode: mr.daemon_mode || "", type: mr.type};
    }
    return JSON.stringify({
        upgrade_exp: table_soldier_upgrade_exp,
        to_exp: table_soldier_to_exp,
        to_cost: table_soldier_to_cost_for_upgrade,
        lv_limit: table_soldier_lv_limit,
        constant: table_soldier_constant,
        card: card,
        master: master,
        daemon: daemon
    });
})()
"""


# 天赋（培养）三张表。客户端 `TalentCenter` 的构造和升级消耗全靠它们：
#
#   table_talent_type[type]              unlock_lv / default_talent_key / max_lv / upgrade_material_key
#   table_talent_master[masterKey]       type / index / unlock_lv / icon / desc（12 个天赋）
#   table_talent_upgrade["<type>#<lv>"]  {money, item, count} —— 升到 lv+1 要扣的东西
#
# ⚠️ key 的形状是反汇编 `TalentCenter._initTalentTypes` / `_initTalents` 定的，
#    别把两套 key 混了：
#      * `_initTalentTypes(args)` 是 `for (k in args)`，k 就是 **type**（"101"/"102"/"103"）
#      * `_initTalents()` 拿 `table_talent_master[k].type` 去索引 `_talentTypes`，
#        再用 `k + "#" + lv` 去索引 `table_talent`（那张表的 key 是 "1001#0" 这种）
#    也就是说 type 的 key（101）和 master 的 key（1001）**不是一回事**。
TALENT_TYPE_JS = r"""
(function () { return JSON.stringify(table_talent_type); })()
"""

TALENT_MASTER_JS = r"""
(function () { return JSON.stringify(table_talent_master); })()
"""

TALENT_UPGRADE_JS = r"""
(function () { return JSON.stringify(table_talent_upgrade); })()
"""


# 装备三张表。`EquipmentCenter` 的登录块和 7 条 equipment.* 路由全靠它们：
#
#   table_equipment_constant   槽位上限 / 每组上限 / 升级材料 key / 各品质等级上限
#   table_equipment_level      "<quality>#<lv>" -> {upgrade_money, upgrade_material,
#                                                   decompose_money, decomposes_material, ...}
#   table_equipment            3121 件装备。**只留服务端要用的字段**：
#                              n=name t=type q=quality m=max_lv f=first_attr_group
#                              o=outfit_attr_key
#
#   `name / type / quality / suitKey` 客户端自己从它那份全表里补
#   （`initEquipment(eq)` 就干这个），服务端要 `f` 是因为**装备的属性 key 得服务端算**：
#   `firstAttrKeys = [f + %02d(lv) + "01"]`（见 store.equipment_attr_key）。
EQUIPMENT_CONSTANT_JS = r"""
(function () { return JSON.stringify(table_equipment_constant); })()
"""

EQUIPMENT_LEVEL_JS = r"""
(function () { return JSON.stringify(table_equipment_level); })()
"""

EQUIPMENT_JS = r"""
(function () {
    var out = {};
    for (var k in table_equipment) {
        var r = table_equipment[k];
        if (!r) { continue; }
        out[k] = {n: r.name, t: r.type, q: r.quality, m: r.max_lv,
                  f: r.first_attr_group, o: r.outfit_attr_key};
    }
    return JSON.stringify(out);
})()
"""


# 好感度（宿舍 / favor.*）六张表 + 一份常量。
#
# 服务端要复刻的东西全在这儿：
#
#   table_favor_upgrade["<lv>"]           15 行，key 是 "1".."15"
#       favor             升到 lv+1 需要的经验；最后一档是 -1（= MAX_FAVOR_LV，
#                         见 favorconfig.js 里 `if (row.favor == -1) MAX_FAVOR_LV = k`）
#       touch_favor       \
#       touch_favor_add   / 抚摸给的加值。**客户端从来不读这两个字段**
#                         （jsc_find 全库 0 命中），是纯服务端数值。
#                         表里两列恒等（每级都是 22），所以取哪个都一样。
#       max_daemon_lv     这一级开放的「守护灵」等级，只影响客户端提示
#   table_favor_common["<lv>"]            {enable_set_asst, enable_cast, quality_up_probability}
#   table_favor_gift_type["<charKey>"]    {love_type:"5,6", hate_type:"4"} —— 逗号分隔的 gift_type
#   table_char_desc["<charKey>"]          is_favor_char / birthday（"4.1" = 4 月 1 日）
#   table_item 里 type==30(ITEM_TYPE.GIFT) 的 47 件礼物
#
# ⚠️ 礼物加好感度的公式是**服务端才算**的（客户端只负责播动画）。
#    客户端唯一暴露线索的是 `favorManager.getPreferenceWithSendGift(favor, item)`，
#    实机问过它，返回的是「偏好档位」而不是数值：
#        giftType ∈ love_type -> 2      giftType ∈ hate_type -> 4      其余 -> 3
#    所以服务端按同一套偏好选 gift 的三个数值字段：
#        favor        基础值（普通礼物）
#        favor_love   喜欢时的值
#        favor_hate   讨厌时的值（47 件里只有 308401/308402 非 0）
FAVOR_UPGRADE_JS = r"""
(function () { return JSON.stringify(table_favor_upgrade); })()
"""

FAVOR_COMMON_JS = r"""
(function () { return JSON.stringify(table_favor_common); })()
"""

FAVOR_GIFT_TYPE_JS = r"""
(function () { return JSON.stringify(table_favor_gift_type); })()
"""

# ⚠️ 只留服务端要用的字段。整行带着 desc_1 / desc_2 的话单是这两段角色小传
# 就有几万字，`/control/eval` 的返回值要走一遍 base64 + DES 再回传，
# 是上一轮把模拟器压卡的元凶之一。
FAVOR_CHAR_DESC_JS = r"""
(function () {
    var out = {};
    for (var k in table_char_desc) {
        var r = table_char_desc[k];
        if (!r) { continue; }
        var row = {};
        for (var f in r) {
            if (f === "desc_1" || f === "desc_2") { continue; }
            row[f] = r[f];
        }
        out[k] = row;
    }
    return JSON.stringify(out);
})()
"""

# 礼物。**在客户端就把表压扁**，只回服务端要的字段 —— 见上面 FAVOR_CHAR_DESC_JS 的说明。
FAVOR_GIFT_JS = r"""
(function () {
    var out = {};
    for (var k in table_item) {
        var r = table_item[k];
        if (!r || r.type !== 30) { continue; }
        out[k] = {n: r.name, q: r.quality, gt: r.gift_type, f: r.favor || 0,
                  fl: r.favor_love || 0, fh: r.favor_hate || 0,
                  lv: r.favor_lv || 0, il: r.intimac_lv || 0};
    }
    return JSON.stringify(out);
})()
"""

# 回礼（用礼物/抚摸之后角色回赠的东西）。
# 每个角色一行，按 preference（2=喜欢 / 3=普通 / 4=讨厌）分成四条，各自两档掉落：
#     return_item_id_N / return_item_count_N（"最小值,最大值"）/ return_item_pr_N
FAVOR_RECEIVE_JS = r"""
(function () {
    var out = {};
    for (var k in table_char_favor_receive_talk) {
        var rows = table_char_favor_receive_talk[k] || [];
        var arr = [];
        for (var i = 0; i < rows.length; i++) {
            var r = rows[i];
            arr.push({p: r.preference,
                      id1: r.return_item_id_1, c1: r.return_item_count_1, pr1: r.return_item_pr_1,
                      id2: r.return_item_id_2, c2: r.return_item_count_2, pr2: r.return_item_pr_2});
        }
        out[k] = arr;
    }
    return JSON.stringify(out);
})()
"""

# 只有服务端读的那几个 table_constant。
FAVOR_CONSTANT_JS = r"""
(function () {
    return JSON.stringify({
        max_favor_interact_times: table_constant.max_favor_interact_times,
        favor_interact_cooldown_time: table_constant.favor_interact_cooldown_time,
        default_favor_bg_item_key: table_constant.default_favor_bg_item_key,
        daemon_need_favor_lv: table_constant.daemon_need_favor_lv,
        favor_asst_favor_value: table_constant.favor_asst_favor_value,
        favor_asst_interval_sec: table_constant.favor_asst_interval_sec,
        favor_asst_max_multiple: table_constant.favor_asst_max_multiple,
        use_gift_lv_10: table_constant.use_gift_lv_10,
        use_gift_lv_20: table_constant.use_gift_lv_20,
        use_gift_lv_30: table_constant.use_gift_lv_30,
        use_gift_lv_40: table_constant.use_gift_lv_40
    });
})()
"""


# `table_item`（全部 481 件道具）。
#
# 为什么服务端一直没抽它、现在又要抽：**服务端有三处真的需要整张道具表** ——
#   1. `items._item_limit()`：`Bag._addCount` 会按 `table_item[key].limit_count` 截断，
#      服务端要按同一个上限发，不然客户端拿到超上限的堆叠会自己夹掉（数量对不上）
#   2. 换装 / 换背景要判断 `table_item[itemKey].type`
#      （CLOTHES=40 / BG_IMG=50），否则随便编个 key 就能换上去
#   3. **找角色的「默认衣服」**（见 favor.default_clothes）。判据是
#      `icon == "appareldefault"` 或 `replace_key == char_key`（后者等于"不替换立绘"）。
#      ⚠️ 这个字段不是可选的：`curClothes` 给空串的话，客户端
#      `favorManager.createExpSpriteEx` 第一句 `bag.getItem("")` 就是 undefined，
#      直接 `return undefined` —— 抚摸特效拿不到表情立绘就不往下走，
#      表现是**摸角色完全没反应（不扣次数、不加好感度）**，而 logcat 里只有
#      一行 `favorManager.createExpSprite error, clothes item not found`。
#
# ⚠️ 只留这几个字段。整表 481 行全字段的话，`/control/eval` 的返回值要
# base64 + DES 走一遍 HTTP，抽的时候会把模拟器压到卡（已验证过一次）。
ITEM_JS = r"""
(function () {
    var out = {};
    for (var k in table_item) {
        var r = table_item[k];
        if (!r) { continue; }
        out[k] = {n: r.name, t: r.type, q: r.quality, lc: r.limit_count || 0,
                  ck: r.char_key || "", ic: r.icon || "", rk: r.replace_key || ""};
    }
    return JSON.stringify(out);
})()
"""


# 宿舍事件（`favorevent.*`）用的那张表。184 条，key 形如 "410101"。
#
#   char_key      这个事件属于哪个角色（charKey，如 hadf）
#   favor_lv      **好感度到几级才解锁**（字符串 "1"/"3"/"5"...）
#   reward_favor  读完给多少好感度 —— ⚠️ **客户端全库 0 命中**（jsc_find），
#                 也就是说纯服务端数值，得服务端自己发
#   level_key     形如 "310101"；`FavorEvent.levelKey` 有 getter 但**没人读**
#   title / desc  剧情标题和正文（客户端 `favoreventitemwrapper` 直接显示）
#
# ⚠️ 客户端 `FavorEventCenter._initData` 会自己往行里塞 `table_id = eventKey`
#    （反汇编里是 `this._eventsInTable[i].table_id = i`），所以服务端不用管这个字段。
FAVOR_EVENT_JS = r"""
(function () {
    var out = {};
    for (var k in table_favor_random_event) {
        var r = table_favor_random_event[k];
        if (!r) { continue; }
        out[k] = {ck: r.char_key, lv: r.favor_lv, rf: r.reward_favor,
                  lk: r.level_key, n: r.title, d: r.desc, t: r.type};
    }
    return JSON.stringify(out);
})()
"""


# 守护灵（宿舍左侧那个 guard 按钮）四张表。全部很小。
#
#   table_daemon_upgrade["<lv>"]        lv = "0".."10"（MAX_DAEMON_LV = 10）
#       exp   升到 lv+1 需要的经验；**最后一档是 -1**（和 table_favor_upgrade 一个套路）
#   table_daemon["<mode>"]              mode = at0101 / df0101 / hp0101
#       11 项数组（daemon_lv 0..10），每项给 damage/defense/hp 加成（都是 +40）
#       角色用哪一套由 `table_soldier_master[charKey].daemon_mode` 决定
#   table_daemon_exp["<quality>"]       quality = "1".."4"
#       {default, same_type, same_char} —— 当材料喂进去时给多少经验：
#         同角色  same_char  >  同类型  same_type  >  其它  default
#       品质 1 全是 0（喂 1 星材料一点经验都没有）
#   table_soldier_master[charKey].{daemon_mode, type}  —— 见 SOLDIER_JS 的 `daemon`
#
# ⚠️ `CharCenter.calcDaemonUpgrade(materials, daemon, favorLv)` 是**客户端预览**，
#    服务端要按同一套规则算，否则「预览涨 300、点完不变」。
#    另外升级上限不是固定 10，而是 `table_favor_upgrade[favorLv].max_daemon_lv`
#    （实测 getMaxDaemonLv(1)=0 / (5)=0 / (10)=4），见 favor.daemon_max_lv()。
DAEMON_UPGRADE_JS = r"""
(function () { return JSON.stringify(table_daemon_upgrade); })()
"""

DAEMON_JS = r"""
(function () { return JSON.stringify(table_daemon); })()
"""

DAEMON_EXP_JS = r"""
(function () { return JSON.stringify(table_daemon_exp); })()
"""


def _dump(base: str, js: str, name: str):
    result = eval_remote(base, js, timeout=30.0)
    if not result.get("ok"):
        print("!! 抽取失败:", result, file=sys.stderr)
        return None
    value = result["value"]
    # /control/eval 会把 JS 的返回值 JSON 编码一次；如果 JS 本身返回的是字符串
    # （我们这里 return JSON.stringify(...)），拿到手就是「字符串里的 JSON」，
    # 所以这里一路解到 dict 为止。
    while isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        print("!! 抽取结果不是对象:", type(value), file=sys.stderr)
        return None
    path = os.path.join(DATA_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    print(f"[extract] {path}  ({len(value)} 条)")
    return value


def main() -> int:
    import argparse

    from gamesrv import config

    ap = argparse.ArgumentParser(description="从运行中的客户端抽 table_* 到 gamesrv/data/")
    ap.add_argument("--only", default=None,
                    help="只抽文件名含这个子串的表（例：--only favor）。"
                         "**只补新表时务必用它** —— 每张表都是一次 /control/eval，"
                         "而 eval 跑在游戏主线程上、返回值还要 base64+DES 回传，"
                         "整轮全抽（含 table_soldier / table_equipment 这种几十万字的）"
                         "会把模拟器压到卡死。")
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    base = f"http://127.0.0.1:{config.CDN_PORT}"

    jobs = [
        ("table_quest.json", QUEST_JS),
        ("table_friend_support_npc.json", NPC_JS),
        ("table_level_reward.json", LEVEL_JS),
        ("table_chapter.json", CHAPTER_JS),
        ("table_subarea_achievement.json", SUBAREA_ACHIEVEMENT_JS),
        ("table_subarea_achievement_condition.json", SUBAREA_ACHIEVEMENT_CONDITION_JS),
        ("table_subarea_achievement_reward.json", SUBAREA_ACHIEVEMENT_REWARD_JS),
        ("table_exchange_item.json", EXCHANGE_ITEM_JS),
        ("table_resource_exchange.json", EXCHANGE_RESOURCE_JS),
        ("table_soldier.json", SOLDIER_JS),
        ("table_shelf.json", SHELF_JS),
        ("table_shop.json", SHOP_JS),
        ("table_talent_type.json", TALENT_TYPE_JS),
        ("table_talent_master.json", TALENT_MASTER_JS),
        ("table_talent_upgrade.json", TALENT_UPGRADE_JS),
        ("table_equipment_constant.json", EQUIPMENT_CONSTANT_JS),
        ("table_equipment_level.json", EQUIPMENT_LEVEL_JS),
        ("table_equipment.json", EQUIPMENT_JS),
        ("table_favor_upgrade.json", FAVOR_UPGRADE_JS),
        ("table_favor_common.json", FAVOR_COMMON_JS),
        ("table_favor_gift_type.json", FAVOR_GIFT_TYPE_JS),
        ("table_char_desc.json", FAVOR_CHAR_DESC_JS),
        ("table_favor_gift.json", FAVOR_GIFT_JS),
        ("table_favor_receive.json", FAVOR_RECEIVE_JS),
        ("table_favor_constant.json", FAVOR_CONSTANT_JS),
        ("table_favor_event.json", FAVOR_EVENT_JS),
        ("table_daemon_upgrade.json", DAEMON_UPGRADE_JS),
        ("table_daemon.json", DAEMON_JS),
        ("table_daemon_exp.json", DAEMON_EXP_JS),
        ("table_item.json", ITEM_JS),
    ]
    if args.only:
        jobs = [j for j in jobs if args.only in j[0]]
        if not jobs:
            print(f"!! --only {args.only} 没匹配到任何表", file=sys.stderr)
            return 2
        print(f"[extract] 只抽 {len(jobs)} 张：{[j[0] for j in jobs]}")

    for name, js in jobs:
        table = _dump(base, js, name)
        if table is None:
            return 1
        if name == "table_quest.json":
            kinds: dict[str, int] = {}
            for row in table.values():
                kinds[row["type"]] = kinds.get(row["type"], 0) + 1
            print(f"[extract] 按 type 统计: {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
