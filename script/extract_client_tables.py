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
# 服务端只需要「关卡 -> 掉什么」这一份，所以这里压成一棵小表。
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
            ap: r.appraise_reward_ids || ""
        };
    }
    out.reward = {};
    for (var j in table_level_reward) {
        out.reward[j] = table_level_reward[j];
    }
    return JSON.stringify(out);
})()
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
    for (var m in table_soldier_master) {
        master[m] = table_soldier_master[m].card_type;
    }
    return JSON.stringify({
        upgrade_exp: table_soldier_upgrade_exp,
        to_exp: table_soldier_to_exp,
        to_cost: table_soldier_to_cost_for_upgrade,
        lv_limit: table_soldier_lv_limit,
        constant: table_soldier_constant,
        card: card,
        master: master
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
    from gamesrv import config

    os.makedirs(DATA_DIR, exist_ok=True)
    base = f"http://127.0.0.1:{config.CDN_PORT}"

    table = _dump(base, QUEST_JS, "table_quest.json")
    if table is None:
        return 1
    kinds: dict[str, int] = {}
    for row in table.values():
        kinds[row["type"]] = kinds.get(row["type"], 0) + 1
    print(f"[extract] 按 type 统计: {kinds}")

    if _dump(base, NPC_JS, "table_friend_support_npc.json") is None:
        return 1
    if _dump(base, LEVEL_JS, "table_level_reward.json") is None:
        return 1
    if _dump(base, SOLDIER_JS, "table_soldier.json") is None:
        return 1
    if _dump(base, SHELF_JS, "table_shelf.json") is None:
        return 1
    if _dump(base, SHOP_JS, "table_shop.json") is None:
        return 1
    if _dump(base, TALENT_TYPE_JS, "table_talent_type.json") is None:
        return 1
    if _dump(base, TALENT_MASTER_JS, "table_talent_master.json") is None:
        return 1
    if _dump(base, TALENT_UPGRADE_JS, "table_talent_upgrade.json") is None:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
