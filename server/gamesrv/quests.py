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

本服维护三类：**主线**（一条链，按 `activate_quest_key` 推进）、**日常**、**成就**。

日常：**按等级分档，一天只放当前这一档**
--------------------------------------
`table_quest` 里 type=1 有 246 条，按 `lv` 分成 24 档（1 / 5 / 10 / … / 116）。
每一档 7~11 条，而且档内那条「完成所有的日常任务哦！」（`ct=19201`）的 `cp1`
**正好等于该档条数 − 1**（实测每一档都对得上，例如 lv=1 档 8 条 → cp1=7、
lv=30 档 10 条 → cp1=9）。所以「日常列表」= **当前等级所在的那一档**，
不是把所有 ≤ 等级的档都铺出来（那样 30 级会看到 40 多条，也不符合 cp1 的语义）。

进度按**天**清零：`RESET_HOUR = 5`（和分区的每日次数同一个换日点，见
`instance.SUBAREA_RESET_HOUR`）。`quests.daily.day` 存「游戏内的今天」，
每次进 block / submit 都会先 `ensure_daily_reset()`，跨天就清空当日 done。

成就：**不分档，按 `lv` 门槛解锁**，进度是长期累计。
--------------------------------------------------
91 条 type=3，`lv` 大多是 0（无门槛），少数 8/10/20。进度要么来自计数器
（累计获得道具 / 战斗次数 / 胜场 / 败场 / 分解军士…），要么现算（军士数量、
战役星数、私密开启数）。少数几个条件需要战斗内部数据（击杀鸭子数、我方军士跪倒数）
本服拿不到，**一律返回 0**（既不假装达成，也不崩）—— 见 `progress_of` 的注释。

奖励
====
`table_quest_reward`（客户端全局名同名，1534 行，离线反编译抽的，见 build.md 的
「离线抽表」）每条形如 `{type, param_1, param_2}`：type=1 是玩家经验（无 key）、
2 道具、5 军士、6 公会经验、8 装备。发给客户端时统一成
**`[{type, key, count}]`** —— 这是 `ccuiManager.popupReward(rewards)` /
`popupRewardWithItems` 认的形状（`RewardTipsLayer(rewards, ...)`）。

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
# 给 20 条是为了让 209xxx（战斗类）后面的 210001「首次升级」这类
# 也早早出现在列表里，不然要打完前 12 条才看得到。
WINDOW = 20

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


# 日常换日点（小时）。和 `instance.SUBAREA_RESET_HOUR` 保持一致 ——
# 同一个游戏里两处「每日」用不同的换日点会让玩家觉得计数坏了。
RESET_HOUR = 5

_REWARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "table_quest_reward.json")
_reward_table: dict | None = None


def _record(player: dict) -> dict:
    """玩家自己的任务进度（存在 players.json 里）。

    * `done`            —— 主线（历史字段名，老存档直接沿用，不迁移）
    * `daily.done`      —— 今天已领的日常；`daily.day` 是「游戏内的今天」
    * `achievement.done`—— 成就（长期，不重置）
    """
    rec = player.get("quests")
    if not isinstance(rec, dict):
        rec = {}
        player["quests"] = rec
    if not isinstance(rec.get("done"), list):
        rec["done"] = []
    rec.setdefault("updateTime", store.time_str())
    for field in ("daily", "achievement"):
        sub = rec.get(field)
        if not isinstance(sub, dict):
            sub = {}
            rec[field] = sub
        if not isinstance(sub.get("done"), list):
            sub["done"] = []
    return rec


def done_of(player: dict, qtype: str = MAIN_TYPE) -> list:
    """某一类任务「已领奖」的 key 列表。"""
    rec = _record(player)
    if qtype == TYPE_DAILY:
        return rec["daily"]["done"]
    if qtype == TYPE_ACHIEVEMENT:
        return rec["achievement"]["done"]
    return rec["done"]


def day_str(now: float | None = None) -> str:
    """「游戏内的今天」—— 按 `RESET_HOUR` 换日，格式 YYYY-MM-DD。"""
    import time as _time

    ts = _time.time() if now is None else float(now)
    return _time.strftime("%Y-%m-%d", _time.localtime(ts - RESET_HOUR * 3600))


def ensure_daily_reset(player: dict) -> bool:
    """跨过换日点就把当天的日常 done 清空（幂等，任何入口都可以先调一次）。

    ⚠️ 这里**自己落盘**（别的函数都是让 handler 去 save）。原因：重置是个"过了就回不去"
    的状态变更，而调用它的路径里有 `quest.getnewquest` 这种**只读不 save** 的 handler ——
    不落盘的话，磁盘上永远停在昨天，每次请求都要在内存里重算一遍，`updateTime` 也跟着
    反复变（客户端的 sync 会因此一直推 quest 块）。一天最多触发一次，代价可以忽略。
    """
    sub = _record(player)["daily"]
    today = day_str()
    if sub.get("day") == today:
        return False
    sub["day"] = today
    sub["done"] = []
    _touch(player)
    store.save_player(player)
    log.info("日常重置（%s，换日点 %02d:00）", today, RESET_HOUR)
    return True


def update_time(player: dict) -> str:
    return _record(player)["updateTime"]


def _touch(player: dict) -> str:
    """任务状态变了就刷新 updateTime —— 客户端拿它跟服务端比，
    不一样才会在 sync.syncupclient 里把新的 quest 块拉过去。"""
    rec = _record(player)
    rec["updateTime"] = store.time_str()
    return rec["updateTime"]


# ---------------------------------------------------------------------------
# 窗口：主线是滑动窗口，日常是「当前等级那一档」，成就是所有已解锁的
# ---------------------------------------------------------------------------
def _rows_of(qtype: str) -> list[tuple[str, dict]]:
    return [(k, row) for k, row in table().items() if str(row.get("type")) == qtype]


def _daily_band(player: dict) -> int | None:
    """当前日常档的 `lv`（= 不超过玩家等级的最大档）。没有可用档返回 None。"""
    lv = int(player.get("lv") or 0)
    levels = sorted({int(row.get("lv") or 0) for _, row in _rows_of(TYPE_DAILY)
                     if int(row.get("lv") or 0) <= lv})
    return levels[-1] if levels else None


def active_keys(player: dict, qtype: str = MAIN_TYPE) -> list[str]:
    """还没领的任务 key（主线 = 滑动窗口；日常/成就 = 全部该显示的）。"""
    keys = display_keys(player, qtype)
    done = set(done_of(player, qtype))
    return [k for k in keys if k not in done]


def display_keys(player: dict, qtype: str = MAIN_TYPE) -> list[str]:
    """这一类任务**该显示给客户端**的全部 key（含已领奖的，它们要回 state=4）。"""
    if qtype == MAIN_TYPE:
        order_ = order()
        done = set(_record(player)["done"])
        return [k for k in order_ if k not in done][:WINDOW] + _record(player)["done"][-WINDOW:]

    ensure_daily_reset(player)
    lv = int(player.get("lv") or 0)
    if qtype == TYPE_DAILY:
        band = _daily_band(player)
        if band is None:
            return []
        keys = [k for k, row in _rows_of(TYPE_DAILY) if int(row.get("lv") or 0) == band]
        # 同档内按 rank 排，和客户端列表的观感一致
        return sorted(keys, key=lambda k: (table()[k].get("rank", 0), k))
    keys = [k for k, row in _rows_of(qtype) if int(row.get("lv") or 0) <= lv]
    return sorted(keys, key=lambda k: (table()[k].get("rank", 0), k))


def finish_all(player: dict) -> tuple[list[str], str]:
    """把整条主线一口气标成「已领奖」（devtools 的作弊项）。

    返回 `(done 列表, 说明文案)`。只是把 key 塞进 `done` ——
    客户端看到 `state:"4"` 就当已领，不用真的去打关。
    顺手把 `questStats` 也灌满，免得之后的 `13102 军阶等级达到 N 级`
    这类任务因为计数器是 0 又退回去。
    """
    keys = list(order())
    rec = _record(player)
    rec["done"] = sorted(set(rec["done"]) | set(keys))
    player["questStats"] = {
        "wins": 9999,
        "winsByTeamSize": {str(n): 9999 for n in range(1, 7)},
        "soldierUpgrades": 9999,
        "soldierStars": 9999,
        "soldierMaxLv": {str(q): 99 for q in range(1, 5)},
    }
    _touch(player)
    return rec["done"], f"{len(keys)} 条主线任务全部标记为已领奖"


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
    # --- 日常 / 成就用到的计数器（每一条的含义都用客户端 desc 核对过）---
    st.setdefault("battles", 0)              # 战斗次数（含失败）      ct 12201
    st.setdefault("losses", 0)               # 战斗失败次数            ct 12205
    st.setdefault("clears", 0)               # 通关任意关卡次数        ct 12204
    st.setdefault("clearsByLevel", {})       # levelKey -> 通关次数    ct 12207
    st.setdefault("clearsWithChar", {})      # charKey -> [levelKey]   ct 12210（去重）
    st.setdefault("touches", 0)              # 抚摸总次数              ct 13232
    st.setdefault("touchesByChar", {})       # charKey -> 抚摸次数     ct 13232（成就指定角色）
    st.setdefault("gifts", 0)                # 送礼次数                ct 13231
    st.setdefault("arenaFights", 0)          # 演习次数                ct 15202
    st.setdefault("detects", 0)              # 任务派遣完成次数        ct 16202
    st.setdefault("soldierSells", 0)         # 军士退伍（分解）个数    ct 13201
    st.setdefault("itemGained", {})          # itemKey -> 累计获得     ct 11201
    st.setdefault("friendSends", 0)          # 给好友送物资次数        ct 18201（好友系统未实现，恒 0）
    return st


def _bump(player: dict, field: str, amount: int = 1, sub: str | None = None) -> None:
    st = _stats(player)
    if sub is None:
        st[field] = int(st.get(field) or 0) + amount
    else:
        bucket = st.setdefault(field, {})
        bucket[sub] = int(bucket.get(sub) or 0) + amount


# ---------------------------------------------------------------------------
# 进度：按条件码（`ct`）分派。
#
# `ct` 的含义不是猜的 —— `table_quest_condition` 只有参数，文案在客户端
# `table_quest.desc` 里（例如 100001 `ct=12204 cp1=5` -> 「通关任意关卡5次！」、
# 100007 `ct=13232 cp1=5` -> 「抚摸妹纸达到5次！」、301001 `ct=11201 cp2=100002`
# -> 「累计获得萌抄达到5000000大元！」）。抽表脚本不抽 desc，所以这里逐码注明来源。
#
# ⚠️ 拿不到数据的条件**返回 0**，不假装达成：12208（击杀鸭子数）、12209（我方军士
#    跪倒数）要战斗内部的统计，本服结算时没有；13102（技能熟练度）客户端存的是
#    熟练度等级，服务端名单里没有这个字段。
# ---------------------------------------------------------------------------
def _stars_total(player: dict) -> int:
    """关卡星级合计（ct 12101「战役获得的星星数量」）。

    ⚠️ 表里 `cp2` 区分普通(1)/困难(2)，但本服的关卡记录没存难度，所以先按
    **全部星数合计**算 —— 两个难度的成就都会各自涨，不会卡住，只是偏宽松。
    """
    try:
        from . import instance
        levels = (instance._record(player) or {}).get("levels") or {}
    except Exception:                                   # noqa: BLE001
        return 0
    total = 0
    for row in levels.values():
        if isinstance(row, dict):
            try:
                total += int(row.get("starMark") or 0)
            except (TypeError, ValueError):
                continue
    return total


def _soldiers_of_quality(player: dict, quality) -> int:
    """某军阶的**不同**军士数量（ct 13103「获得的不同的N军阶军士数量」）。"""
    try:
        rows = store.ensure_soldiers(player)
    except Exception:                                   # noqa: BLE001
        return 0
    keys = set()
    for row in rows:
        if isinstance(row, dict) and str(row.get("quality")) == str(quality):
            keys.add(str(row.get("key") or row.get("card_key") or row.get("soldier_key") or ""))
    keys.discard("")
    return len(keys)


def _favor_desc_count(player: dict) -> int:
    """已「全部解锁」私密剧情的角色数（ct 13105）。标记见 store.FAVOR_DESC_ALL_UNLOCKED_MARK。"""
    favors = store.player_favors(player)
    n = 0
    for row in favors.values():
        if not isinstance(row, dict):
            continue
        desc = row.get("desc")
        if desc == store.FAVOR_DESC_ALL_UNLOCKED_MARK:
            n += 1
    return n


def progress_of(player: dict, key: str) -> int:
    """某条任务当前进度（拿来填 `schedule[cur]`）。"""
    row = table().get(key) or {}
    ct = str(row.get("ct") or "")
    cp1 = row.get("cp1")
    cp2 = str(row.get("cp2") or "")
    st = _stats(player)

    if ct == "12204":        # 通关任意关卡 / 战胜 N 次
        return int(st.get("wins") or 0)
    if ct == "12212":        # 只上阵 cp2 个军士的胜场
        return int((st.get("winsByTeamSize") or {}).get(cp2) or 0)
    if ct == "12216":        # 指定兵种 —— 兵种表在客户端，这里简化成"任意胜场"
        return int(st.get("wins") or 0)
    if ct == "13203":        # 提升军士等级 N 次
        return int(st.get("soldierUpgrades") or 0)
    if ct == "13204":        # 首次突破
        return int(st.get("soldierStars") or 0)
    if ct == "13102":        # 某军阶军士达到 N 级
        return int((st.get("soldierMaxLv") or {}).get(cp2) or 0)
    if ct == "12207":        # 通关 cp2 里那些关卡（同一副本的各难度）共 N 次
        keys = [k for k in cp2.split("#") if k]
        by_level = st.get("clearsByLevel") or {}
        return sum(int(by_level.get(k) or 0) for k in keys)
    if ct == "15202":        # 完成演习 N 次
        return int(st.get("arenaFights") or 0)
    if ct == "16202":        # 完成任务派遣 N 次
        return int(st.get("detects") or 0)
    if ct == "18201":        # 给基友发送物资 N 次（好友系统还没做）
        return int(st.get("friendSends") or 0)
    if ct == "19201":        # 完成所有日常：今天在本档里已领的条数（不含自己）
        daily_done = set(_record(player)["daily"]["done"])
        band = [k for k, r in _rows_of(TYPE_DAILY)
                if int(r.get("lv") or 0) == int(row.get("lv") or 0)]
        return len([k for k in band if k != key and k in daily_done])
    if ct == "13232":        # 抚摸 N 次；成就里 cp2 指定角色
        if cp2:
            return int((st.get("touchesByChar") or {}).get(cp2) or 0)
        return int(st.get("touches") or 0)
    if ct == "13231":        # 赠送礼物 N 次
        return int(st.get("gifts") or 0)
    if ct == "12201":        # 与鸭子的战斗次数
        return int(st.get("battles") or 0)
    if ct == "12205":        # 战斗失败次数
        return int(st.get("losses") or 0)
    if ct == "11201":        # 累计获得 cp2 道具 N 个
        return int((st.get("itemGained") or {}).get(cp2) or 0)
    if ct == "12101":        # 战役获得的星星数量
        return _stars_total(player)
    if ct == "13103":        # 不同 cp2 军阶的军士数量
        return _soldiers_of_quality(player, cp2)
    if ct == "13105":        # 角色私密开启数量
        return _favor_desc_count(player)
    if ct == "13201":        # 累计军士退伍个数
        return int(st.get("soldierSells") or 0)
    if ct == "12210":        # 队伍中存在 cp1 角色通关过 cp2 那个关卡
        levels = (st.get("clearsWithChar") or {}).get(cp2) or []
        return 1 if cp1 in levels else 0
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


# ---------------------------------------------------------------------------
# 事件钩子：由各 handler 调，把玩家的行为记成计数器
# ---------------------------------------------------------------------------
def on_level_result(player: dict, victory: bool, team_size: int,
                    level_key: str | None = None, char_keys=()) -> None:
    """一次关卡结算。主线里绝大多数条件都是「胜利 M 次」，所以这里是主要入口。

    `level_key` / `char_keys` 是给日常「通关某副本 N 次」和成就「队伍中存在
    某角色通关某关」用的，调用方拿得到就传（拿不到不影响胜场统计）。
    """
    from . import instance  # 局部 import，避免 instance <-> quests 循环

    mult = max(1, int(instance.PROGRESS_MULT))
    st = _stats(player)
    _bump(player, "battles", mult)
    if victory:
        _bump(player, "wins", mult)
        _bump(player, "winsByTeamSize", mult, sub=str(team_size))
        _bump(player, "clears", mult)
        if level_key:
            _bump(player, "clearsByLevel", mult, sub=str(level_key))
            with_char = st.setdefault("clearsWithChar", {})
            for ck in char_keys or ():
                lst = with_char.setdefault(str(ck), [])
                if str(level_key) not in lst:
                    lst.append(str(level_key))
                    lst[:] = lst[-200:]        # 只留最近 200 个，别把存档撑大
    else:
        _bump(player, "losses", mult)
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


def on_touch_char(player: dict, char_key: str = "") -> None:
    """抚摸角色（favor.touchcharasst）。日常 13232「抚摸妹纸 N 次」。"""
    _bump(player, "touches", 1)
    if char_key:
        _bump(player, "touchesByChar", 1, sub=str(char_key))
    _touch(player)


def on_gift(player: dict, count: int = 1) -> None:
    """赠送礼物（favor.usegift）。日常 13231「赠送 N 个礼物」。"""
    _bump(player, "gifts", max(1, int(count)))
    _touch(player)


def on_arena_fight(player: dict) -> None:
    """完成一次演习（日常 15202）。"""
    _bump(player, "arenaFights", 1)
    _touch(player)


def on_detect_complete(player: dict) -> None:
    """完成一次任务派遣（日常 16202）。"""
    _bump(player, "detects", 1)
    _touch(player)


def on_soldier_sell(player: dict, count: int = 1) -> None:
    """军士退伍 / 分解（char.sellsoldiers，成就 13201）。"""
    _bump(player, "soldierSells", max(0, int(count)))
    _touch(player)


def on_item_gained(player: dict, key, count: int) -> None:
    """道具入账（成就 11201「累计获得某道具 N 个」）。

    由 `items.add_item()` 调 —— 那是**所有**道具入账的唯一出口（奖励、购买、
    邮件附件都走它），所以这里的"累计获得"是名副其实的。只累计正数（扣道具不算）。
    """
    if int(count or 0) <= 0:
        return
    _bump(player, "itemGained", int(count), sub=str(key))


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


def _reward_rows(key: str) -> list[dict]:
    """某条任务的奖励（`table_quest_reward` 里 `"<questKey>#N"` 那些行，按 N 排）。

    表是离线反编译抽的（客户端全局名 `table_quest_reward`，字段 `{type, param_1, param_2}`）：
    type=1 玩家经验（无 key，param_1 就是数量）、2 道具、5 军士、6 公会经验、8 装备。
    空槽（`{}`）跳过。
    """
    global _reward_table
    with _lock:
        if _reward_table is None:
            try:
                with open(_REWARD_PATH, "r", encoding="utf-8") as fh:
                    _reward_table = json.load(fh)
                log.info("载入任务奖励表（%d 条）", len(_reward_table))
            except OSError as exc:
                log.warning("没有任务奖励表 %s（%s），领奖只回空", os.path.basename(_REWARD_PATH), exc)
                _reward_table = {}
    rows = []
    for rk, row in _reward_table.items():
        if not isinstance(row, dict) or rk.split("#")[0] != str(key):
            continue
        if not row.get("type"):
            continue
        try:
            idx = int(rk.split("#")[1]) if "#" in rk else 0
        except (IndexError, ValueError):
            idx = 0
        rows.append((idx, row))
    return [row for _, row in sorted(rows, key=lambda t: t[0])]


def rewards_of(key: str) -> list[dict]:
    """某条任务的奖励 -> `[{type, key, count}]`。

    ⚠️ 这个形状是客户端定的：`ccuiManager.popupReward(rewards)` 把它塞进
    `RewardTipsLayer(rewards, cb, type, receiveState)`，而 `popupRewardWithItems`
    拼出来的正是 `{type, key, count}`（见 `manager/ccuimanager.jsc` 反汇编）。
    经验没有 key（客户端按 type 取图标），给空串。
    """
    out = []
    for row in _reward_rows(key):
        rtype = str(row.get("type"))
        if rtype == "1":                      # PLAYER_EXP：只有数量
            count = int(row.get("param_1") or 0)
            out.append({"type": rtype, "key": "", "count": count})
        elif rtype == "8":                    # EQUIPMENT：只有 key，数量恒 1
            out.append({"type": rtype, "key": str(row.get("param_1") or ""), "count": 1})
        else:                                 # ITEM / SOLDIER / GUILD_EXP
            out.append({"type": rtype,
                        "key": str(row.get("param_1") or ""),
                        "count": int(row.get("param_2") or 0)})
    return [r for r in out if r["key"] or r["type"] == "1"]


def block(player: dict) -> dict:
    """拼出 `data.quest`（登录、quest.getnewquest、关卡结算都用它）。

    **三类任务一起回**：客户端任务界面有「日常 / 主线 / 成就」三个页签，
    `QuestLayer` 分别调 `QuestCenter.getQuests(QUEST_TYPE.X)`，而 getQuests 是从
    同一个 `_quests` map 里按 type 筛 —— 所以服务端只要把三类的条目都放进
    `quests` 就行，页签自己会分好。
    """
    ensure_daily_reset(player)
    quests: dict[str, dict] = {}
    all_done: list[str] = []
    for qtype in (MAIN_TYPE, TYPE_DAILY, TYPE_ACHIEVEMENT):
        done = set(done_of(player, qtype))
        all_done.extend(done)
        for key in display_keys(player, qtype):
            achieved = key in done or _achieved(player, key)
            entry = _entry_with_progress(player, key, achieved)
            if key in done:
                entry["state"] = STATE_FINISHED
            quests[key] = entry

    now = store.time_str()
    rec = _record(player)
    return {
        "quests": quests,
        # finishQuests 是「已完成任务 key -> 1」的映射（客户端 _finishQuests[k] = 1），
        # 用来判断 showByQuest 依赖的那条前置任务是不是已经做完了 —— 三类都要算上。
        "finishQuests": {key: 1 for key in all_done},
        "finishQuestsId": [],
        # 客户端只把它存进 `_dailyQuestsTime`、没有任何地方读（jsc_find 全量扫过），
        # 所以这里给「下次日常重置的时刻」，语义上最贴切。
        "dailyQuestsTime": _next_reset_str(),
        "updateTime": rec["updateTime"],
    }


def _next_reset_str() -> str:
    """下一个日常换日点（本地时间字符串，给 `dailyQuestsTime`）。"""
    import time as _time

    now = _time.time()
    lt = _time.localtime(now)
    today_reset = _time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, RESET_HOUR, 0, 0, 0, 0, -1))
    nxt = today_reset if today_reset > now else today_reset + 86400
    return _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(nxt))


def submit(player: dict, msg: dict) -> dict:
    """`quest.submitquest` —— 领奖。客户端只在 state === ACHIEVED 时才会发这个。

    返回的 `data.rewards` 就是要弹给玩家看的东西（形状见 `rewards_of`），
    handler 会把它和新的 quest 块一起回给客户端。
    """
    key = str((msg or {}).get("questKey") or "")
    row = table().get(key)
    if not key or not isinstance(row, dict):
        return {"code": CODE_FAIL, "msg": "no questKey", "data": {}}
    qtype = str(row.get("type"))
    if qtype not in (TYPE_DAILY, TYPE_NORMAL, TYPE_ACHIEVEMENT):
        log.warning("submit 的任务类型还不支持: %s（type=%s）", key, qtype)
        return {"code": CODE_FAIL, "msg": "quest type not supported", "data": {}}

    ensure_daily_reset(player)
    if key not in display_keys(player, qtype):
        log.warning("submit 的任务不在窗口里: %s（type=%s）", key, qtype)
        return {"code": CODE_FAIL, "msg": "quest not active", "data": {}}
    done = done_of(player, qtype)
    if key in done:
        log.warning("submit 的任务已经领过: %s", key)
        return {"code": CODE_FAIL, "msg": "already finished", "data": {}}

    done.append(key)
    _touch(player)
    rewards = rewards_of(key)
    if rewards:
        from . import items  # 局部 import：items 也 import quests（累计获得那条）

        items.settle(player, [(r["type"], r["key"], r["count"]) for r in rewards])
    log.info("%s任务完成: %s（累计 %d 条）奖励 %s",
             {"1": "日常", "2": "主线", "3": "成就"}.get(qtype, qtype),
             key, len(done), [(r["type"], r["key"], r["count"]) for r in rewards])
    return {"code": CODE_OK, "msg": "", "data": {"rewards": rewards}}
