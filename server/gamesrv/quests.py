"""quest.* —— 任务系统（主线 / 日常 / 成就 …）。

客户端怎么读这些数据
====================

全都从 `assets/src/data/questcenter.jsc` 里扒出来的（`tools/jsc_strings.py`）：

    QuestCenter.ctor(data):
        this._dailyQuestsTime = data.dailyQuestsTime;
        this._finishQuests    = data.finishQuests;
        this._updateTime      = data.updateTime;
        for (k in data.quests) this._quests[k] = this._createQuest(data.quests[k]);

    QuestCenter._createQuest(quest):        // 只吃一个对象，length === 1
        var row = table_quest[quest.questKey];
        ... schedule[i] = { condition, cur: quest.schedule[i].cur, tar }
        return { type, title, desc, icon, jumpView, reward, rank,
                 schedule, scheduleCur, scheduleTar, questKey, state };

所以服务端要给的形状是（登录的 data.quest 和 quest.getnewquest 的 data 一样）：

    {
      "quests": {
         "209001": {"id": "209001", "questKey": "209001",
                    "state": "3", "schedule": {"1": 10}},
         ...
      },
      "finishQuests": [],
      "finishQuestsId": [],
      "dailyQuestsTime": "YYYY-MM-DD HH:MM:SS",
      "updateTime": "YYYY-MM-DD HH:MM:SS"
    }

⚠️ `id` 必须给（客户端 `_createQuest` 结尾 `quest.id = data.id`，
   领奖/查表都靠它，不给就是"列表能看但点不了领"）。
⚠️ `schedule` 是个**对象**（不是数组），key 取自 `table_quest.schedule_i`
   的第一段（`"1#1"` -> `"1"`），value 是当前进度。客户端 `_createQuest` 里是
   `data.schedule[row["schedule_1"].split("#")[0]]` —— 回数组的话 cur 取到
   undefined，`scheduleCur` 算不出来，「领奖」按钮永远是灰的
   （表现：任务列表能看，但点不了领）。条件个数必须和 table_quest_condition
   里的条数一致。

状态机（QUEST_STATE）
=====================
    0 INACTIVE  未激活（列表里不显示）
    1 ACTIVATED 已激活
    2 ACCEPTED  已接受（进行中）
    3 ACHIEVED  已达成（可以领奖）
    4 FINISHED  已完成（已领奖）

领取走 `quest.submitquest`，请求体是 `{"questKey": "..."}`。
客户端 `QuestCenter.requestReceiveRewards` 只允许 state === ACHIEVED 的任务提交。

任务类型（QUEST_TYPE）
=====================
    1 日常 / 2 主线(普通) / 3 成就 / 4 公会 / 5 活动 / 6 新手

本模拟服只维护**主线**这一条链：按 `activate_quest_key` 顺序推进，
一次给客户端一个「窗口」（默认 12 条），窗口第一条是已达成（可以立刻领），
其余是进行中；领掉第一条之后窗口自动往后滑。

任务表来自客户端（`tools/extract_client_tables.py` 抽的 `data/table_quest.json`）。
"""

from __future__ import annotations

import json
import os
import threading

from . import logx, store

log = logx.get("quests")

CODE_OK = 200
CODE_FAIL = -1

TYPE_DAILY = "1"
TYPE_NORMAL = "2"
TYPE_ACHIEVEMENT = "3"

STATE_ACTIVATED = "1"
STATE_ACCEPTED = "2"
STATE_ACHIEVED = "3"
STATE_FINISHED = "4"

MAIN_TYPE = TYPE_NORMAL

# 主线一次给客户端多少条。客户端 QuestLayer 是一列滚动列表，
# 12 条刚好一屏多一点，和原版"一口气看到接下来几条主线"的感觉接近。
WINDOW = 12

_lock = threading.RLock()
_table: dict | None = None
_order_cache: list[str] | None = None

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "table_quest.json")


def table() -> dict:
    global _table
    with _lock:
        if _table is None:
            with open(_PATH, "r", encoding="utf-8") as fh:
                _table = json.load(fh)
            log.info("载入任务表 %s（%d 条）", os.path.basename(_PATH), len(_table))
        return _table


def order() -> list[str]:
    """主线任务的推进顺序。

    先按 (rank, key) 排出所有主线任务，再从「没有前置」的那些开始 BFS：
    `activate_quest_key` 指向的任务做完了才轮到它。这样 209001（<初试身手！>）
    会排在第一位，后面跟着它的子任务，符合原版新手主线的体感。
    """
    global _order_cache
    with _lock:
        if _order_cache is not None:
            return _order_cache
        tbl = table()
        keys = {k for k, row in tbl.items() if row.get("type") == MAIN_TYPE}
        children: dict[str, list[str]] = {}
        roots: list[str] = []
        for key in sorted(keys, key=lambda k: (tbl[k].get("rank", 0), k)):
            parent = tbl[key].get("ak") or ""
            if parent and parent in keys:
                children.setdefault(parent, []).append(key)
            else:
                roots.append(key)
        out: list[str] = []
        seen: set[str] = set()

        def walk(key: str) -> None:
            # 前序遍历：一条链一路走到底再回头，这样窗口里看到的是
            # 「初试身手 -> 小有所成 -> 已有所得 -> ...」的推进顺序，
            # 而不是把 6 个没有前置的任务平铺在最前面。
            if key in seen:
                return
            seen.add(key)
            out.append(key)
            for child in children.get(key, []):
                walk(child)

        for root in roots:
            walk(root)
        out.extend(sorted(keys - seen, key=lambda k: (tbl[k].get("rank", 0), k)))
        _order_cache = out
        return out


def _record(player: dict) -> dict:
    """玩家自己的任务进度（存在 players.json 里）。"""
    rec = player.get("quests")
    if not isinstance(rec, dict):
        rec = {}
        player["quests"] = rec
    if not isinstance(rec.get("done"), list):
        rec["done"] = []
    rec.setdefault("updateTime", store.time_str())
    return rec


def update_time(player: dict) -> str:
    return _record(player)["updateTime"]


def _touch(player: dict) -> str:
    """任务状态变了就刷新 updateTime —— 客户端拿它跟服务端比，
    不一样才会在 sync.syncupclient 里把新的 quest 块拉过去。"""
    rec = _record(player)
    rec["updateTime"] = store.time_str()
    return rec["updateTime"]


def active_keys(player: dict) -> list[str]:
    """当前该显示给客户端的主线任务（窗口）。"""
    done = set(_record(player)["done"])
    return [k for k in order() if k not in done][:WINDOW]


# ---------------------------------------------------------------------------
# 任务进度
#
# 客户端**不算**任务进度 —— `QuestCenter.checkQuestFinish(key)` 只是
# `return this._finishQuests[key]`（「这条领过没有」）。状态（ACCEPTED/ACHIEVED）
# 完全由服务端说了算，客户端只负责把 `schedule[cur]` 显示成进度条。
#
# 条件类型是从 `table_quest.desc` 反推出来的（见 tools/extract_client_tables.py）：
#
#   12212  队伍中只上阵 cp2 个军士获得胜利 cp1 次
#   12216  队伍中存在 cp2 兵种的军士获得胜利 cp1 次
#   13203  进行首次军士升级
#   13102  1 位 cp2 军阶的军士等级达到 cp1 级
#   13204  1 位军士进行首次突破（升星）
#
# 所以玩家身上只要记几个**计数器**就够了，具体某条任务的 cur 现算。
# ---------------------------------------------------------------------------

def _stats(player: dict) -> dict:
    st = player.get("questStats")
    if not isinstance(st, dict):        # 老存档 / 手改坏了都兜一下
        st = {}
        player["questStats"] = st
    st.setdefault("wins", 0)                 # 总胜利场次
    st.setdefault("winsByTeamSize", {})      # "上阵人数" -> 胜场
    st.setdefault("soldierUpgrades", 0)      # 军士升级次数
    st.setdefault("soldierStars", 0)         # 军士突破（升星）次数
    st.setdefault("soldierMaxLv", {})        # "军阶" -> 该军阶最高等级
    return st


def _bump(player: dict, field: str, amount: int = 1, sub: str | None = None) -> None:
    st = _stats(player)
    if sub is None:
        st[field] = int(st.get(field) or 0) + amount
    else:
        bucket = st.setdefault(field, {})
        bucket[sub] = int(bucket.get(sub) or 0) + amount


def progress_of(player: dict, key: str) -> int:
    """某条主线任务当前进度（拿来填 `schedule[cur]`）。"""
    row = table().get(key) or {}
    ct = str(row.get("ct") or "")
    cp2 = str(row.get("cp2") or "")
    st = _stats(player)
    if ct == "12212":        # 只上阵 cp2 个军士的胜场
        return int((st.get("winsByTeamSize") or {}).get(cp2) or 0)
    if ct == "12216":        # 指定兵种 —— 兵种表在客户端 char 表里，这里简化成"任意胜场"
        return int(st.get("wins") or 0)
    if ct == "13203":        # 首次军士升级
        return int(st.get("soldierUpgrades") or 0)
    if ct == "13204":        # 首次突破
        return int(st.get("soldierStars") or 0)
    if ct == "13102":        # 某军阶军士达到 N 级
        return int((st.get("soldierMaxLv") or {}).get(cp2) or 0)
    return 0


def _targets(key: str) -> list:
    return (table().get(key) or {}).get("tar") or []


def _achieved(player: dict, key: str) -> bool:
    tars = _targets(key)
    if not tars:
        return False
    cur = progress_of(player, key)
    for tar in tars:
        # 少数任务的条件值是字符串（关卡 key 之类），服务端伪造不了，一律算没达成
        if not isinstance(tar, (int, float)):
            return False
        if cur < tar:
            return False
    return True


def on_level_result(player: dict, victory: bool, team_size: int) -> None:
    """一次关卡结算。主线里绝大多数条件都是「胜利 M 次」，所以这里是主要入口。"""
    if not victory:
        return
    from . import instance  # 局部 import，避免 instance <-> quests 循环

    mult = max(1, int(instance.PROGRESS_MULT))
    _bump(player, "wins", mult)
    _bump(player, "winsByTeamSize", mult, sub=str(team_size))
    _touch(player)


def on_soldier_upgrade(player: dict, level: int, star_quality) -> None:
    """军士升级 / 突破（由 char.upgradesoldierlv / improvesoldierstar 调）。"""
    _bump(player, "soldierUpgrades", 1)
    if level:
        bucket = _stats(player).setdefault("soldierMaxLv", {})
        q = str(star_quality or "")
        bucket[q] = max(int(bucket.get(q) or 0), int(level))
    _touch(player)


def on_soldier_star(player: dict) -> None:
    _bump(player, "soldierStars", 1)
    _touch(player)


def _quest_entry(key: str, achieved: bool) -> dict:
    row = table()[key]
    tars = row.get("tar") or []
    skeys = row.get("sk") or []
    cur = list(tars) if achieved else [0] * len(tars)
    # ⚠️ schedule 是**对象**（key 是 table_quest.schedule_i 的第一段），不是数组。
    #    回数组的话客户端 data.schedule["1"] 取到 undefined，算不出 scheduleCur，
    #    任务界面的「领奖」按钮就一直是灰的 —— 列表能看但点不了领。
    schedule = {}
    for i, value in enumerate(cur):
        schedule[skeys[i] if i < len(skeys) else str(i + 1)] = value
    return {
        # ⚠️ id 是必须的：QuestCenter._createQuest 最后一句是 quest.id = data.id，
        #    而任务条目 QuestItem.updateState / QuestLayer._submitQuest 都拿
        #    quest.id 去查/提交（requestReceiveRewards({id: quest.id})）。
        #    不给 id 的话列表能显示，但点「领奖」时 id 是 undefined，
        #    客户端在本地就 return 了，根本不会发 quest.submitquest。
        "id": key,
        "questKey": key,
        "state": STATE_ACHIEVED if achieved else STATE_ACCEPTED,
        "schedule": schedule,
    }


def _entry_with_progress(player: dict, key: str, achieved: bool) -> dict:
    """和 `_quest_entry` 一样，但 `schedule[cur]` 用玩家真实进度。"""
    entry = _quest_entry(key, achieved)
    if achieved:
        return entry
    row = table()[key]
    tars = row.get("tar") or []
    skeys = row.get("sk") or []
    cur = progress_of(player, key)
    schedule = {}
    for i, tar in enumerate(tars):
        k = skeys[i] if i < len(skeys) else str(i + 1)
        # 进度条上的数字：别超过 tar，否则客户端会显示成 12/10
        if isinstance(tar, (int, float)) and tar:
            schedule[k] = min(cur, tar)
        else:
            schedule[k] = cur
    entry["schedule"] = schedule
    return entry


def block(player: dict) -> dict:
    """拼出 `data.quest`（登录、quest.getnewquest、关卡结算都用它）。"""
    keys = active_keys(player)
    quests: dict[str, dict] = {}
    for key in keys:
        quests[key] = _entry_with_progress(player, key, _achieved(player, key))

    # 已经领过的任务要再回一次 state=FINISHED，
    # 否则客户端 QuestCenter.updateByServer() 只覆盖不清理，
    # 那条还留在 _quests 里、状态还停在 ACHIEVED —— 表现就是
    # 「奖励领到了，但列表没刷新，那条还在，还能再点」。
    done = _record(player)["done"]
    for key in done[-WINDOW:]:
        entry = _quest_entry(key, True)
        entry["state"] = STATE_FINISHED
        quests[key] = entry

    now = store.time_str()
    rec = _record(player)
    return {
        "quests": quests,
        # finishQuests 是「已完成任务 key -> 1」的映射（客户端 _finishQuests[k] = 1），
        # 用来判断 showByQuest 依赖的那条前置任务是不是已经做完了。
        "finishQuests": {key: 1 for key in done},
        "finishQuestsId": [],
        "dailyQuestsTime": now,
        "updateTime": rec["updateTime"],
    }


def submit(player: dict, msg: dict) -> dict:
    """`quest.submitquest` —— 领奖。客户端只在 state === ACHIEVED 时才会发这个。"""
    key = str((msg or {}).get("questKey") or "")
    if not key:
        return {"code": CODE_FAIL, "msg": "no questKey", "data": {}}
    if key not in active_keys(player):
        log.warning("submit 的任务不在窗口里: %s", key)
        return {"code": CODE_FAIL, "msg": "quest not active", "data": {}}
    rec = _record(player)
    if key not in rec["done"]:
        rec["done"].append(key)
        _touch(player)
    log.info("主线任务完成: %s（累计 %d 条），窗口推进", key, len(rec["done"]))
    return {"code": CODE_OK, "msg": "", "data": {"rewards": []}}
