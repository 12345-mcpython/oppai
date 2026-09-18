"""instance.* —— 主线副本 / 关卡进度。

客户端怎么用（`tools/jsc_strings.py` + `tools/disasm_func.py` 扒出来的）
=====================================================================

    Instance.ctor(data):
        this._initLevel();                  // 用**客户端自己的** table_level 建 1142 个关卡
        this._updateLevels(data.levels);    // 服务端只回「进度」
        this._chapters = data.chapters;

    Level.updateLevel(level):               // 服务端每关只要这三个字段
        this._starMark          = level.starMark ?? -1;
        this._challengeTimes    = level.challengeTimes ?? 0;
        this._lastUpdateTimeSec = level.lastUpdateTimeSec ?? 0;

关卡表、章节表都在客户端（`table_level` 1142 条 / `table_chapter` 72 条），
服务端**不需要**给关卡列表 —— 只给「哪些关通了、几星、打了几次」。

战斗链路（`src/manager/instancemanager.jsc` 的 onBattle）
=======================================================

    onBattle(levelId, cb, immCb)
      -> BattleScene.combat({id, team, rewards, resultCb, showCb, endCb, ...})
      打完 -> resultCb(args)
      -> Instance.finishLevel(levelId, starMark, rewards, battleInfo, cb, ...)
      -> server.request('instance.finishlevel',
                        {levelId, starMark, rewards, battleInfo,
                         curTeamIdx, curExp, lv, actionPoint})
      回包 res.data.level  ->  Instance._updateResult()  ->  level.updateLevel()
                                                        （所以星级要放在 data.level 里）

`starMark` 是客户端算的（`calLevelStarMark`）：1=通关 / +2=英雄击杀达标 / +4=己方阵亡达标，
所以 7 就是三星。服务端只管记下来。

服务端在这里顺手做的事
======================
一次胜利就是一次主线任务的进度 —— 见 `gamesrv/quests.py` 的 `on_level_result()`。
（主线 209001~209003 是「只上阵 N 个军士胜利 M 次」，209004+ 是「队里存在某兵种军士胜利 M 次」。）
"""

from __future__ import annotations

import json
import os
import random
import threading
import time

from . import logx, quests, store

log = logx.get("instance")

SUCCESS_CODE = 200

# 私服想快点把主线推完就把这个调大：一次胜利按 N 次算。
# 1 = 和原版一致（209001 真的要赢 10 场）。
PROGRESS_MULT = float(os.environ.get("GS_QUEST_MULT", "1") or 1)

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_level_cache: dict | None = None
_lock = threading.RLock()

# 首通奖励：客户端表里 `first_complete_reward_ids` 指向的 id 在
# table_level_reward 里查不到（应该是另一张没被我们抽到的表），
# 所以暂时用一份固定的小奖励顶上，别让"首通"看起来什么都没有。
FIRST_CLEAR_FALLBACK = {"100001": 20}      # 100001 = 钻石


def _level_table() -> dict:
    """`tools/extract_client_tables.py` 抽出来的关卡奖励表。

    {"level": {levelId: {exp, lvr, fc, ap}}, "reward": {rewardId: {key_1, min_count_1, ...}}}
    """
    global _level_cache
    with _lock:
        if _level_cache is None:
            path = os.path.join(_DATA_DIR, "table_level_reward.json")
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    _level_cache = json.load(fh)
            except Exception:  # noqa: BLE001
                log.warning("载入 %s 失败，关卡奖励会是空的", path)
                _level_cache = {"level": {}, "reward": {}}
        return _level_cache


def _roll(row: dict) -> dict:
    """把一条 reward 表行掷成 {道具key: 数量}。

    表行形如 `{"key_1":"100002","min_count_1":540,"max_count_1":900,"span_1":1}`，
    可以有 key_1..key_N 多组。
    """
    items: dict[str, int] = {}
    i = 1
    while True:
        key = row.get("key_%d" % i)
        if key is None:
            break
        try:
            lo = int(row.get("min_count_%d" % i) or 1)
            hi = int(row.get("max_count_%d" % i) or lo)
        except (TypeError, ValueError):
            i += 1
            continue
        if hi < lo:
            lo, hi = hi, lo
        key = str(key)
        items[key] = items.get(key, 0) + random.randint(lo, hi)
        i += 1
    return items


def _rewards_for(ids) -> dict:
    """`"10010201#10010202"` -> `{道具key: 数量}`（同 key 累加）。"""
    tbl = _level_table().get("reward") or {}
    items: dict[str, int] = {}
    for one in str(ids or "").split("#"):
        row = tbl.get(one)
        if not isinstance(row, dict):
            continue
        for key, count in _roll(row).items():
            items[key] = items.get(key, 0) + count
    return items


def _merge(*groups) -> dict:
    out: dict[str, int] = {}
    for g in groups:
        for k, v in (g or {}).items():
            out[k] = out.get(k, 0) + v
    return out


def _record(player: dict) -> dict:
    """关卡进度：levelId -> {starMark, challengeTimes, lastUpdateTimeSec}。"""
    return player.setdefault("levels", {})


def login_block(player: dict) -> dict:
    """拼出 `data.instance`。

    客户端 `Instance.ctor` 只从服务端取**进度**，关卡结构它自己表里有，
    所以这里给空容器也不会缺关卡（`_initLevel()` 已经把 1142 关建好了）。
    `chapters` 目前给空字典 —— 客户端的章节列表走 `table_chapter`，
    章节星级是 `getChapterStars()` 从 `_levels` 现算的。
    """
    return {
        "levels": _record(player),
        "chapters": {},
        "activityChapters": [],
        "subareaLevels": [],
        "appearBossKey": {},
        "newActChapterFlag": {},
        "updateTime": store.time_str(),
    }


def _team(player: dict, idx) -> dict:
    teams = player.get("teams") or []
    try:
        idx = int(idx or 0)
    except (TypeError, ValueError):
        idx = 0
    if 0 <= idx < len(teams):
        return teams[idx] or {}
    return {}


def finish_level(player: dict, msg: dict) -> dict | None:
    """`instance.finishlevel` —— 记一次关卡结果。

    msg（客户端 `Instance.finishLevel` 拼的）::

        {levelId, starMark, rewards, battleInfo, subareaInfo,
         curTeamIdx, curExp, lv, actionPoint}
    """
    level_id = str((msg or {}).get("levelId") or "")
    if not level_id:
        return None

    try:
        star_mark = int((msg or {}).get("starMark") or 0)
    except (TypeError, ValueError):
        star_mark = 0

    rec = _record(player)
    lv = rec.setdefault(level_id, {"starMark": -1, "challengeTimes": 0, "lastUpdateTimeSec": 0})
    lv["starMark"] = max(int(lv.get("starMark", -1)), star_mark)
    lv["challengeTimes"] = int(lv.get("challengeTimes") or 0) + 1
    lv["lastUpdateTimeSec"] = int(time.time())
    first_clear = lv["challengeTimes"] == 1 and star_mark > 0

    # 一次胜利 = 一次主线任务进度
    team = _team(player, (msg or {}).get("curTeamIdx"))
    team_size = len(team.get("soldierKeys") or team.get("soldiers") or [])
    quests.on_level_result(player, victory=star_mark > 0, team_size=team_size)

    # ---- 通关奖励 ----
    #
    # 「获得物资」那一栏原本永远是空的，因为服务端没回奖励。
    # 客户端 `Instance._dealLevelResult(level)` 会读这几个字段，
    # 每个都是 `{items: {道具key: 数量}}` 这种形状
    # （`_getRewardTotal` 是按 key 累加的 map）：
    #     dropReward    普通掉落（table_level.level_reward_id）
    #     firstComplete 首通奖励（first_complete_reward_ids）
    #     appraise      星级评价奖励（appraise_reward_ids）
    info = (_level_table().get("level") or {}).get(level_id) or {}
    drop = _rewards_for(info.get("lvr"))
    appraise = _rewards_for(info.get("ap"))
    first = _rewards_for(info.get("fc"))
    if first_clear and not first:
        first = dict(FIRST_CLEAR_FALLBACK)
    exp = int(info.get("exp") or 0)

    # 经验真的加上去（客户端 Player.updatePlayerAttr 只认 curExp / lv）
    old_exp = int(player.get("curExp") or 0)
    new_exp = old_exp + exp
    player["curExp"] = new_exp
    player_attr = {"curExp": new_exp, "lv": player.get("lv") or 1}

    log.info("关卡结算 %s starMark=%s 第 %s 次（上阵 %s 人）掉落=%s 首通=%s exp=%s",
             level_id, lv["starMark"], lv["challengeTimes"], team_size, drop, first, exp)

    level_payload = dict(lv, levelId=level_id)
    level_payload["dropReward"] = {"items": drop}
    level_payload["appraise"] = {"items": appraise}
    if first:
        level_payload["firstComplete"] = {"items": first}
    # levelReward 只影响结算面板上那个 "Exp+N"（以及升级动画的前后快照）
    level_payload["levelReward"] = {"exp": exp, "playerInfo": {"playerAttr": player_attr}}

    # data.level 会走客户端的 _updateResult -> level.updateLevel()，
    # 所以星级/次数必须放在这里；
    # data.quest / data.player 由客户端的 RESP-DISPATCH 派发。
    return {
        "levels": {level_id: lv},
        "level": level_payload,
        "rewards": [],
        "quest": quests.block(player),
        "player": {"playerAttr": player_attr},
    }
