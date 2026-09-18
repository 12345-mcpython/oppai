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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.repl import eval_remote  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "gamesrv", "data")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
