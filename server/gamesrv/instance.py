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

from . import favor, logx, quests, store, subarea

log = logx.get("instance")

SUCCESS_CODE = 200

# 私服想快点把主线推完就把这个调大：一次胜利按 N 次算。
# 原版是 1（209001 真的要赢 10 场、209006 要赢 500 场）。
# 默认给 10：主线整条链的达成值动辄 50/100/500 场，1 倍的话自己玩根本推不完。
# 想要原汁原味就设环境变量 GS_QUEST_MULT=1。
PROGRESS_MULT = float(os.environ.get("GS_QUEST_MULT", "10") or 10)

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


_chapter_cache: dict | None = None


def _chapter_table() -> dict:
    """`table_chapter.json`：章节表（`{chapterId: {t,p,ll,n,lv,sr}}`）。

    分区玩法的**章节清单只存在客户端**，服务端要回一份 `data.activityChapters`
    告诉客户端有哪些章节（客户端按 `table_chapter[key].type == 5` 过滤），
    所以这张表必须抽出来（见 extract_client_tables.py 的 CHAPTER_JS）。
    """
    global _chapter_cache
    with _lock:
        if _chapter_cache is None:
            path = os.path.join(_DATA_DIR, "table_chapter.json")
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    _chapter_cache = json.load(fh)
            except Exception:  # noqa: BLE001
                log.warning("载入 %s 失败，分区章节列表会是空的", path)
                _chapter_cache = {}
        return _chapter_cache


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


# 「按关卡解锁」的功能按钮：客户端 `MainLayer` 的按钮可见性看
# `table_function_open[<功能key>].unlock_level_key` 指定的关卡通没通关
# （`levels[关卡].starMark >= 1`）。**建号 30 级也不会解锁这 6 个** ——
# 表里它们只有 `lk` 没有 `lv`：
#
#     100006 宿舍系统   → 100110      100007 黑市系统（抽卡入口）→ 100105
#     100011 活动副本   → 100204      100014 天赋系统  → 100316
#     100019 困难副本   → 100110      100023 公会系统  → 100213
#
# 不打通就意味着主界面**少 6 个按钮**（实机表现：玩家问「卡池入口在哪」，
# 因为黑市按钮压根没画出来）。私服取舍：登录时把这几个解锁关卡直接标成通关，
# 和 `store.MIN_PLAYER_LV = 30`（建号满级）是同一类。要还原原版就把
# `MODULE_UNLOCK_VERSION` 清掉并别再调它。
MODULE_UNLOCK_VERSION = 1


def unlock_module_levels(player: dict) -> list:
    """把「解锁功能按钮」需要的关卡标成通关，返回这次改动的关卡列表。"""
    if int(player.get("moduleUnlockVersion") or 0) >= MODULE_UNLOCK_VERSION:
        return []
    from . import items as items_mod   # 局部 import：items ← store ← instance 是循环依赖

    need = set()
    for row in (items_mod.table("table_function_open") or {}).values():
        lk = str((row or {}).get("lk") or "")
        if lk:
            need.add(lk)
    rec = _record(player)
    changed = []
    for level_id in sorted(need):
        row = rec.get(level_id)
        if not isinstance(row, dict):
            row = {"starMark": -1, "challengeTimes": 0, "lastUpdateTimeSec": 0}
            rec[level_id] = row
        try:
            star = int(row.get("starMark") or -1)
        except (TypeError, ValueError):
            star = -1
        if star < 1:
            row["starMark"] = 1          # 1 = 通关（星级细节客户端自己算）
            changed.append(level_id)
    player["moduleUnlockVersion"] = MODULE_UNLOCK_VERSION
    if changed:
        log.info("玩家 %s 解锁功能按钮所需的 %d 关标成通关：%s",
                 player.get("account"), len(changed), changed)
    return changed


def _visible_levels(player: dict) -> dict:
    """登录块里**只发非默认**的关卡行。

    客户端 `Instance._updateLevels(data.levels)` 对每一行是
    `this._starMark = level.starMark ?? -1`（次数/时间同理给 0）——
    也就是说**没打过的关卡根本不用发**。

    为什么必须裁：1142 行全发的话 `instance` 一块就 92 KB（整包 110 KB），
    而响应要过一遍纯 Python DES（~85 KB/s）→ **一次登录光加密就 1.3 秒**，
    自检一轮 20 多次登录块调用 = 149 秒。裁完只剩真正打过的那几行（~1 KB）。
    """
    out = {}
    for key, row in _record(player).items():
        if not isinstance(row, dict):
            continue
        try:
            star = int(row.get("starMark") or -1)
        except (TypeError, ValueError):
            star = -1
        try:
            times = int(row.get("challengeTimes") or 0)
        except (TypeError, ValueError):
            times = 0
        try:
            last = int(row.get("lastUpdateTimeSec") or 0)
        except (TypeError, ValueError):
            last = 0
        if star >= 0 or times > 0 or last > 0:
            out[str(key)] = {"starMark": star, "challengeTimes": times,
                             "lastUpdateTimeSec": last}
    return out


def login_block(player: dict) -> dict:
    """拼出 `data.instance`。

    客户端 `Instance.ctor` 只从服务端取**进度**，关卡结构它自己表里有，
    所以这里给空容器也不会缺关卡（`_initLevel()` 已经把 1142 关建好了）。
    `chapters` 目前给空字典 —— 客户端的章节列表走 `table_chapter`，
    章节星级是 `getChapterStars()` 从 `_levels` 现算的。

    ⚠️ 发之前先 `sync_subarea_plays`：分区关卡的 `challengeTimes` 是**今日已打次数**，
    跨天（05:00）要清零，而客户端自己不会清（`isCanBattle` 是裸比较）。
    ⚠️ 还要 `unlock_module_levels`：不然「黑市（抽卡）/宿舍/天赋/活动副本/困难副本/公会」
    这 6 个按钮根本不显示（见那个函数的注释）。
    ⚠️ `levels` 走 `_visible_levels`（只发非默认行），别直接发 `_record(player)`。
    """
    sync_subarea_plays(player)
    unlock_module_levels(player)
    return {
        "levels": _visible_levels(player),
        "chapters": {},
        "activityChapters": activity_chapters(player),
        "subareaLevels": subarea_levels(player),
        "appearBossKey": {},
        "newActChapterFlag": {},
        "updateTime": store.time_str(),
    }


# 分区关卡（`INSTANCE_TYPE.SUBAREA`）的 instance_type。
# 抽表时从 `table_level[k].instance_type` 带出来（`table_level_reward.json` 的 `it`），
# 1142 关里 27 关是分区关（500001~500003、500101~…）。
SUBAREA_INSTANCE_TYPE = "5"

# 分区关卡的**每日挑战上限**。
#
# ⚠️ 这个数是**服务端配置**，客户端表里没有（剧情关的 `challenge_times` 也空着），
#    原版给多少无从考证 —— 取值写进 differences.md §D，单旋钮。
#
# ⚠️ 不能给 0（我第一版就是 0，实测被挡）：客户端 `isCanBattle` 末尾是**裸比较**
#
#        if (level.challengeTimes >= level.challengeTimeLimit) return dic[208];  // 次数用完
#
#    —— 没有 `!limit` 那层保护（那是 `checkLevelChallengeTimes` 才有的，而
#    `isCanBattle` **不调用它**）。limit=0 时 `0 >= 0` 成立 → 一进去就"次数用完啦~TuT"。
#    所以必须给正数，并且由**服务端**把已打次数按天清零（见 sync_subarea_plays）。
SUBAREA_DAILY_TIMES = 3

# 跨天时刻：`table_constant.common_reset_time` = "05:00:00"（客户端表里的值）。
# 服务端没抽 `table_constant`，所以这里写死，改的时候两边一起改。
SUBAREA_RESET_HOUR = 5


def subarea_level_ids() -> list:
    """哪些关卡是分区关（`instance_type == 5`）。无缓存，27 条，够快。"""
    return [str(k) for k, v in (_level_table().get("level") or {}).items()
            if str((v or {}).get("it") or "") == SUBAREA_INSTANCE_TYPE]


def subarea_day(now: int | None = None) -> str:
    """按 `SUBAREA_RESET_HOUR` 划天的日期串（'YYYY-MM-DD'）。"""
    ts = int(now if now is not None else time.time()) - SUBAREA_RESET_HOUR * 3600
    return store.time_str(ts)[:10]


def sync_subarea_plays(player: dict, now: int | None = None) -> bool:
    """分区关卡的「今日已打次数」按天对齐（换天就清零），返回是否有改动。

    ⚠️ 为什么**必须服务端**清：客户端 `isCanBattle` 只做
    `challengeTimes >= challengeTimeLimit` 的裸比较，**不会**自己按天重置
    （带重置逻辑的 `checkLevelChallengeTimes` 它压根没调用）。所以"每天能打几次"
    这件事完全落在服务端：`levels[levelId].challengeTimes` 对我们来说就是**今日次数**。

    ⚠️ **只动分区关卡**：剧情关的 `challengeTimes` 客户端当"历史挑战次数"展示，
    乱清零会把它抹掉。
    """
    day = subarea_day(now)
    if player.get("subareaDay") == day:
        return False
    player["subareaDay"] = day
    rec = _record(player)
    for level_id in subarea_level_ids():
        row = rec.get(level_id)
        if isinstance(row, dict):
            row["challengeTimes"] = 0
    log.info("分区关卡次数跨天重置（%s 起算）：%d 关", day, len(subarea_level_ids()))
    return True


def subarea_levels(player: dict) -> dict:
    """`data.subareaLevels` / `instance.getsubarealevel` 的那份 map。
    形状（反汇编 `Instance.updateSubareaLevel/<` 钉的）::

        {levelId: {levelId, challengeTimes, [deadline, limitDay, limitTime], [ac_*]}}

    客户端只读两处：

    * `level.challengeTimeLimit = 条目.challengeTimes`
      —— 注意：服务端这个字段客户端当**上限**用（名字叫 times，语义是 limit）
    * `isSubareaLevelOpen(levelId)` / `isSubareaUpLevelOpen` / `getSubareaEndTime`
      —— **`deadline` / `limitDay` / `limitTime` 三个都缺就直接 return true**
      （永久开放），所以这里干脆不给，省得编造开放时间表；`ac_*` 同理
      （那是"进阶"变体，`isSubareaUpLevelOpen` 一套一样的判定）。

    ⚠️ key 用什么无所谓（客户端只用条目里的 `levelId` 回查 `_levels`），
    这里用 levelId 自己，方便调试时一眼看懂。

    ⚠️ 关卡进度（星级/**今日已打次数**/最后时间）不走这里 —— 走登录块的 `levels`，
    那本来就覆盖全部 1142 关（调用方要保证先跑过 `sync_subarea_plays`）。
    """
    return {level_id: {"levelId": level_id, "challengeTimes": SUBAREA_DAILY_TIMES}
            for level_id in subarea_level_ids()}


# `INSTANCE_TYPE.SUBAREA`（客户端那是个**字符串**枚举："1"主线 / "3"好感 / "4"活动 / "5"分区）
ACTIVITY_CHAPTER_TYPE = "5"


def activity_chapters(player: dict) -> dict:
    """`data.activityChapters` / `instance.getactivityinstance` 的那份 map。

    ⚠️ **分区界面左侧的章节列表就靠它**：`SubareaChapterMenuLayer.showLayer` 会先调
    `instance.getactivityinstance`，回调里 `setSubareaChapterList(
    getActivityChapterListOfType(INSTANCE_TYPE.SUBAREA))`；而
    `getActivityChapterListOfType` 是

        for (k in _activityChapters) {
            var c = table_chapter[_activityChapters[k].key];   // ← key 要能在客户端表里查到
            if (c.type === type) push(_activityChapters[k]);   // ← 严格相等，两边都是字符串
        }
        sort(按 isActivityChapterOpen + priority)

    以前这条路由回的是 `{"activityChapters": []}`（桩），所以界面上「分区战场」**一片空白**
    —— 地图、按钮都在，就是没有章节。

    形状（反汇编 `Instance.updateActivityInstance/<`）::

        {chapterId: {key, challengeTimes, priority, [limitDay, limitTime]}}

    * `key` = 客户端 `table_chapter` 的 key（服务端只认 `type == "5"` 的那 4 个：
      5001 伯尼尔生物研究 / 5002 冰河集团 / 5003 第三工业园区 / 5004 太古重工）
    * `challengeTimes = -1` —— 客户端见到 -1 会转成 `Number.MAX_VALUE`，即**章节级不限次**。
      这是客户端自己的约定，不是我编的数字
    * `priority` 从 `table_chapter` 抄（排序用）
    * `limitDay` / `limitTime` **不给** —— `isActivityChapterOpen` 在这两个字段缺时直接放行
      （跟 `isSubareaLevelOpen` 一个套路），这样就不用编造开放时间表
    """
    out = {}
    for chapter_id, info in (_chapter_table() or {}).items():
        if str((info or {}).get("t") or "") != ACTIVITY_CHAPTER_TYPE:
            continue
        out[str(chapter_id)] = {
            "key": str(chapter_id),
            "challengeTimes": -1,
            "priority": int((info or {}).get("p") or 0),
        }
    return out


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

    ## 回包形状（照客户端 `Instance.finishLevel/<` 逐条对出来的）

        data.level      只喂 `_updateResult(levelId, level)` -> `Level.updateLevel()`，
                        它只挑 starMark / challengeTimes / lastUpdateTimeSec
        data.rewards    ★ **奖励块全在这一层**，`_dealLevelResult(rewards)` 才看得到：
                        dropReward / firstComplete / appraise / levelReward
                        （还有 favorReward / friendSupportReward 两个没用上）
        data.quest      主线任务进度（客户端 RESP-DISPATCH 派发）
        data.player     新值 —— `Player.updateByServer` -> `updatePlayerAttr`
        data.favor      这一关涨了好感的角色行 -> `FavorCenter.cb4ResFavor`

    ⚠️ 奖励块以前挂在 `data.level` 上，那是错的：`finishLevel/<` 读的是
    `res.rewards.levelReward`，而 `data.level` 只走 `updateLevel()`（只认那三个字段）。
    表现就是结算面板「获得物资」永远空着、经验也不动。

    ⚠️ `levelReward.playerInfo.playerAttr` 要放**战前**快照：客户端

        rank = this._updatePlayer(playerInfo.playerAttr)      // from = 快照
        rank.to = {curExp: player.curExp, lv: player.lv, ...} // 本地值（已被 data.player 更新）
        rank.exp = rewards.levelReward.exp

    拿来算「Exp+N」和升级动画。以前给的是加完之后的 lv/curExp，from == to，
    那个 +N 恒等于 0。
    """
    level_id = str((msg or {}).get("levelId") or "")
    if not level_id:
        return None

    try:
        star_mark = int((msg or {}).get("starMark") or 0)
    except (TypeError, ValueError):
        star_mark = 0

    # 分区关卡的「今日已打」跨天要先清零，否则今天第一次打完会变成 N+1（见 sync_subarea_plays）
    sync_subarea_plays(player)

    rec = _record(player)
    lv = rec.setdefault(level_id, {"starMark": -1, "challengeTimes": 0, "lastUpdateTimeSec": 0})
    prev_star = int(lv.get("starMark", -1))
    lv["starMark"] = max(prev_star, star_mark)
    lv["challengeTimes"] = int(lv.get("challengeTimes") or 0) + 1
    lv["lastUpdateTimeSec"] = int(time.time())

    # 首通 = 这一关**以前从没通关过**（星级第一次从 -1 变成正数）。
    #
    # ⚠️ **不能**写成 `challengeTimes == 1`：分区关卡的 `challengeTimes` 现在是
    #    「今日已打次数」（`sync_subarea_plays` 每天清零），那样会变成
    #    「每天都能领一次首通奖励」。用星级判断对两种关都对，而且顺带修掉一个老问题：
    #    先失败一次（star_mark=0）再通关时，旧写法 `challengeTimes == 1` 是 false，
    #    首通奖励会漏发。
    first_clear = star_mark > 0 and prev_star < 0

    # 一次胜利 = 一次主线任务进度；日常/成就还要知道「通的哪一关、队伍里有谁」
    # （12207 = 通关某副本 N 次、12210 = 队伍中存在某角色通关某关）
    team = _team(player, (msg or {}).get("curTeamIdx"))
    team_size = len(team.get("soldierKeys") or team.get("soldiers") or [])
    team_chars = [team.get("heroKey"), team.get("mechaKey")] + list(team.get("soldierKeys") or [])
    quests.on_level_result(player, victory=star_mark > 0, team_size=team_size,
                           level_key=level_id, char_keys=[c for c in team_chars if c])

    # ---- 奖励 ----
    #
    # 数值来自客户端表 `table_level`：
    #     level_reward_id          普通掉落
    #     first_complete_reward_ids 首通奖励
    #     appraise_reward_ids      星级评价奖励
    #     exp                      指挥部经验
    #     favor / favor_char_key   ★ 这一关给多少好感度、给谁（见 favor.py）
    # 每个奖励块的形状都是 `{items: {道具key: 数量}}`
    # （客户端 `_getRewardTotal` 是按 key 累加的 map，`_getItemsBySort` 再排成数组）。
    info = (_level_table().get("level") or {}).get(level_id) or {}
    drop = _rewards_for(info.get("lvr"))
    appraise = _rewards_for(info.get("ap"))
    first = _rewards_for(info.get("fc"))
    if first_clear and not first:
        first = dict(FIRST_CLEAR_FALLBACK)
    exp = int(info.get("exp") or 0)

    # 经验真的加上去（客户端 Player.updatePlayerAttr 只认 curExp / lv）
    old_attr = {"curExp": int(player.get("curExp") or 0), "lv": int(player.get("lv") or 1)}
    new_exp = old_attr["curExp"] + exp
    player["curExp"] = new_exp
    player_attr = {"curExp": new_exp, "lv": player.get("lv") or 1}

    # ---- 好感度 ----
    #
    # ⚠️ 服务端只回**数量**（`rewards.levelReward.favor`），"给谁"是客户端拿
    # 自己 `table_level` 的 `favor_char_key` 算的。这里必须按同一条规则记存档，
    # 否则弹窗和存档会对不上（规则见 favor.level_favor_targets）。
    favor_add = int(info.get("favor") or 0)
    favor_gain = favor.grant_level_favor(player, info, team)
    if favor_gain:
        # 好感涨了 -> 可能跨过某条宿舍事件的解锁等级，顺带把新解锁的推下去
        new_events = favor.sync_events(player)
    else:
        new_events = []

    log.info("关卡结算 %s starMark=%s 第 %s 次（上阵 %s 人）掉落=%s 首通=%s exp=%s 好感=%s",
             level_id, lv["starMark"], lv["challengeTimes"], team_size, drop, first, exp, favor_gain)

    rewards: dict = {}
    if drop:
        rewards["dropReward"] = {"items": drop}
    if first:
        rewards["firstComplete"] = {"items": first}
    if appraise:
        rewards["appraise"] = {"items": appraise}
    level_reward = {"exp": exp, "playerInfo": {"playerAttr": old_attr}}
    if favor_gain:
        # 客户端 `Instance.finishLevel/<` 读 `rewards.levelReward.favor` 填 `ret.favorObj`
        # （只有真的加上去了才回，见 favor.grant_level_favor）
        level_reward["favor"] = favor_add
    rewards["levelReward"] = level_reward

    # ---- 分区成就 ----
    #
    # ⚠️ 成就**是客户端算的**（`subareaAchievementManager.formatBattleInfo()` 在结算时按
    # `table_subarea_achievement_condition` 逐条判定），结果塞在 msg.subareaInfo 里：
    #
    #     {time, battleId, victory,
    #      modifyAchievements: {<id>: {progress, progressInfo, countKey, complete}},
    #      newAchievements: ["100101", ...]}
    #
    # 服务端不复刻条件判定，只落盘 + 把改动的行回给客户端
    # （客户端 RESP-DISPATCH 的 `updateSubareaAchievements` 会按 list[i].id 覆盖本地行）。
    achievement_rows = subarea.sync_from_battle(player, (msg or {}).get("subareaInfo") or {})

    # data.level 会走客户端的 _updateResult -> level.updateLevel()，
    # 所以星级/次数必须放在这里；
    # data.quest / data.player / data.favor 由客户端的响应派发处理。
    data = {
        "levels": {level_id: lv},
        "level": dict(lv, levelId=level_id),
        "rewards": rewards,
        "quest": quests.block(player),
        "player": {"playerAttr": player_attr},
    }
    if achievement_rows:
        data["updateSubareaAchievements"] = achievement_rows
    if favor_gain:
        data["favor"] = favor.rows_block(player, favor_gain)
        block = favor.new_event_block(new_events)
        if block:
            data["newFavorEvent"] = block
    return data

