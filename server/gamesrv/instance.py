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

import os
import time

from . import logx, quests, store

log = logx.get("instance")

SUCCESS_CODE = 200

# 私服想快点把主线推完就把这个调大：一次胜利按 N 次算。
# 1 = 和原版一致（209001 真的要赢 10 场）。
PROGRESS_MULT = float(os.environ.get("GS_QUEST_MULT", "1") or 1)


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

    # 一次胜利 = 一次主线任务进度
    team = _team(player, (msg or {}).get("curTeamIdx"))
    team_size = len(team.get("soldierKeys") or team.get("soldiers") or [])
    quests.on_level_result(player, victory=star_mark > 0, team_size=team_size)

    log.info("关卡结算 %s starMark=%s 第 %s 次（上阵 %s 人）",
             level_id, lv["starMark"], lv["challengeTimes"], team_size)

    # data.level 会走客户端的 _updateResult -> level.updateLevel()，
    # 所以星级/次数必须放在这里；data.quest 由客户端的 RESP-DISPATCH 派发。
    return {
        "levels": {level_id: lv},
        "level": dict(lv, levelId=level_id),
        "rewards": [],
        "quest": quests.block(player),
    }
