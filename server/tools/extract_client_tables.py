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
        var tars = [], keys = [];
        for (var i = 1; ; i++) {
            var cond = table_quest_condition[k + "#" + i];
            if (!cond) { break; }
            tars.push(cond.param_1 || 0);
            var sch = row["schedule_" + i];
            keys.push(sch ? String(sch).split("#")[0] : "0");
        }
        out[k] = {
            type: row.type,
            rank: row.rank || 0,
            lv: row.activate_lv || 0,
            ak: row.activate_quest_key || "",
            n: tars.length,
            tar: tars,
            sk: keys
        };
    }
    return JSON.stringify(out);
})()
"""


def main() -> int:
    from gamesrv import config

    os.makedirs(DATA_DIR, exist_ok=True)
    base = f"http://127.0.0.1:{config.CDN_PORT}"

    result = eval_remote(base, QUEST_JS, timeout=30.0)
    if not result.get("ok"):
        print("!! 抽取失败:", result, file=sys.stderr)
        return 1

    table = result["value"]
    # /control/eval 会把 JS 的返回值 JSON 编码一次；如果 JS 本身返回的是字符串
    # （我们这里 return JSON.stringify(...)），拿到手就是「字符串里的 JSON」，
    # 所以这里一路解到 dict 为止。
    while isinstance(table, str):
        table = json.loads(table)
    if not isinstance(table, dict):
        print("!! 抽取结果不是对象:", type(table), file=sys.stderr)
        return 1
    path = os.path.join(DATA_DIR, "table_quest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(table, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    kinds: dict[str, int] = {}
    for row in table.values():
        kinds[row["type"]] = kinds.get(row["type"], 0) + 1
    print(f"[extract] {path}")
    print(f"[extract] {len(table)} 条任务，按 type 统计: {kinds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
