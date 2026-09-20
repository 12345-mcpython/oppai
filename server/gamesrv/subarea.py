"""分区成就（`subareaachievement.*`）。

谁在算成就 —— **客户端自己算**
================================

`game/assets/src/manager/subareaachievementmanager.jsc` 里那张表就是干这个的：

    formatBattleInfo(battleResult, battleId, team)
        newParam = battleResult.battleInfo          -> 补 victory / battleId / missleName
        checkAchievements(newParam)                 -> 逐条判定（条件类型就是方法名：
                                                       1003 击杀 / 1004 耗时 / 1005 机甲炮弹 /
                                                       1008 全程血量 / 1020 全区勘察完成 /
                                                       1021 掉落累计）
        返回 {time, battleId, victory,
              modifyAchievements: {<id>: {progress, progressInfo, countKey, complete}},
              newAchievements:    ["100101", ...]}   -> 玩家本地**还没有**的成就行

这个返回值就是 `instance.finishlevel` 请求体里的 `subareaInfo`（日志里抓到过完整样例）：

    "subareaInfo": {"time": 22966.67, "battleId": "100102", "victory": true,
                    "modifyAchievements": {}, "newAchievements": ["100101", ...]}

所以服务端**不需要复刻那套条件判定**（`table_subarea_achievement_condition` 的
`param_1..param_7` + `battleInfo` 里的 ownUnitsInfo/enemyUnitsInfo/missleName/… 比对
全在客户端），只要落盘 + 发奖。

服务端做三件事
==============

1. `instance.finishlevel` → `sync_from_battle()`：`newAchievements` 建行、
   `modifyAchievements` 合并进度（`complete` → 写 `completeTime`），
   回包带 `updateSubareaAchievements`（客户端 RESP-DISPATCH 里那条 customTargets
   `updateSubareaAchievements -> SubareaAchievement.updateSubareaAchievements(list)` 接好了，
   它按 `list[i].id` 覆盖本地行）。
2. 登录块 `data.subareaachievement = block(player)`。
3. `subareaachievement.receivereward {achievementId}` → 按
   `table_subarea_achievement_reward` 发东西 + 置 `isReceiveReward`。

行形状（客户端 `SubareaAchievement.createAchievement`）
======================================================

    {"id": "100101", "progress": 0, "progressInfo": {}, "isReceiveReward": 0}

`completeTime` 由服务端在收到 `complete` 时写。界面完全由这几个字段驱动
（`SubareaChapterRewardLayer._getSortIdx`）：

    没有 completeTime -> 2 未完成（显示 progress/times 进度）
    isReceiveReward   -> 3 已领（显示「已领取」章）
    其余              -> 1 可领（显示领取按钮）

⚠️ 登录块里的行**必须**只包含 `table_subarea_achievement` 里有的 id ——
客户端 `_initSubareaAchievementInfo()` 是「遍历玩家行 → 查表拿 sub_area」，
表里查不到的 id 会 `info.sub_area` 抛 TypeError（整个成就页打不开）。

表（`script/extract_client_tables.py --only subarea` 抽的）
==========================================================

    table_subarea_achievement[<id>]                   {condition_id, desc, jump_level_key, sub_area, times}
    table_subarea_achievement_condition[<conditionId>] {param_1..param_7, type}
    table_subarea_achievement_reward["<id>#<i>"]       {count, key, type}   // type "2" = 道具
"""

from __future__ import annotations

from . import items, logx, store

log = logx.get("subarea")

# 客户端 `SubareaAchievement.ERROR_CODE`（模块体里 201..206，逐个对过）：
# `receiveReward/<` 按 `res.code` 取 `table_dictionary[10000/10001/4200/4201/4202]` 弹提示。
# 我们的失败分支就是回这些码，客户端自己会弹对应的文案。
PARAM_ERROR = 201
UPDATA_DB_ERROR = 202
ACHIEVEMENT_NOT_EXIST = 203
REWARD_RECEIVED = 204
REWARD_NOT_EXIST = 205
RECEIVE_REWARD_ERROR = 206

_ACH_TABLE = "table_subarea_achievement"
_REWARD_TABLE = "table_subarea_achievement_reward"

# 存档里的字段名。`_migrate` 会按 `new_player` 补，所以老存档也能直接用。
PLAYER_KEY = "subareaAchievements"


def ach_table() -> dict:
    """`table_subarea_achievement`（84 条）。表没抽出来时回空 dict。"""
    table = items.table(_ACH_TABLE)
    return table if isinstance(table, dict) else {}


def reward_table() -> dict:
    """`table_subarea_achievement_reward`（232 条，key 形如 "100101#1"）。"""
    table = items.table(_REWARD_TABLE)
    return table if isinstance(table, dict) else {}


def new_row(achievement_id) -> dict:
    """空成就行 —— 字段照客户端 `createAchievement` 抄（`completeTime` 先不给）。"""
    return {
        "id": str(achievement_id),
        "progress": 0,
        "progressInfo": {},
        "isReceiveReward": 0,
    }


def rows(player: dict) -> dict:
    """玩家已有的成就行（map，key = achievementId）。顺手把坏形状修成 map。"""
    got = player.get(PLAYER_KEY)
    if not isinstance(got, dict):
        got = {}
        player[PLAYER_KEY] = got
    return got


def block(player: dict) -> dict:
    """登录块 `data.subareaachievement`。

    客户端 `new SubareaAchievement(data.subareaachievement)` →
    `_initData`: `this._achivevements = data.achievements`（**map**，不是 list）。
    """
    return {"achievements": rows(player)}


def sync_from_battle(player: dict, subarea_info: dict) -> list:
    """把这一场战斗的成就变化落盘，返回**被改动的行**（给回包用）。

    `subarea_info` 是客户端 `subareaAchievementManager.formatBattleInfo()` 的返回值：
        {time, battleId, victory, modifyAchievements: {...}, newAchievements: [...]}

    * `newAchievements`：玩家本地还没有的成就 → 建空行
      （注意它只表示「新见到」，**不代表已完成** —— 完成看 modifyAchievements.complete）
    * `modifyAchievements[id]`：`{progress: 增量, progressInfo: {对象id: true}, countKey, complete}`
    """
    info = subarea_info if isinstance(subarea_info, dict) else {}
    table = ach_table()
    if not table:
        # 表没抽出来时**什么都别写** —— 写进去的行在客户端查表会抛异常
        log.warning("没有 table_subarea_achievement，跳过分区成就同步")
        return []

    got = rows(player)
    touched: list = []

    new_ids = info.get("newAchievements") or []
    if not isinstance(new_ids, list):
        new_ids = []
    for aid in new_ids:
        aid = str(aid)
        if aid not in table:
            log.warning("客户端上报了表里没有的成就 %s（忽略）", aid)
            continue
        if aid not in got:
            got[aid] = new_row(aid)
            touched.append(aid)
            log.info("分区成就新建 %s（%s）", aid, table[aid].get("desc"))

    mods = info.get("modifyAchievements") or {}
    if not isinstance(mods, dict):
        mods = {}
    for aid, mod in mods.items():
        aid = str(aid)
        if aid not in table:
            log.warning("客户端上报了表里没有的成就 %s（忽略）", aid)
            continue
        row = got.get(aid)
        if row is None:
            row = got[aid] = new_row(aid)
        if not isinstance(mod, dict):
            mod = {}
        add = mod.get("progress") or 0
        try:
            add = int(add)
        except (TypeError, ValueError):
            add = 0
        if add:
            row["progress"] = int(row.get("progress") or 0) + add
        p_info = mod.get("progressInfo")
        if isinstance(p_info, dict) and p_info:
            cur = row.get("progressInfo")
            if not isinstance(cur, dict):
                cur = {}
                row["progressInfo"] = cur
            for k, v in p_info.items():
                cur[str(k)] = v
        if mod.get("countKey"):
            row["countKey"] = str(mod["countKey"])
        if mod.get("complete") and not row.get("completeTime"):
            row["completeTime"] = store.now_ms()
            log.info("分区成就完成 %s（%s）", aid, table[aid].get("desc"))
        if aid not in touched:
            touched.append(aid)

    return [got[aid] for aid in touched if aid in got]


def reward_rows(achievement_id) -> list:
    """这个成就的奖励行 → `[(type, key, count)]`（喂 `items.settle`）。

    表的 key 是 `"<achievementId>#<序号>"`，序号从 1 开始但不保证连续
    （实测有 2 条 / 3 条 / 5 条三种），所以按 key 前缀扫。
    """
    aid = str(achievement_id)
    prefix = aid + "#"
    out = []
    for key, row in sorted(reward_table().items()):
        if not key.startswith(prefix):
            continue
        if not isinstance(row, dict):
            continue
        rtype = str(row.get("type") or "")
        count = row.get("count") or 0
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            continue
        out.append((rtype, str(row.get("key") or ""), count))
    return out


def claim(player: dict, achievement_id) -> dict:
    """`subareaachievement.receivereward` 的业务体（返回完整响应 dict）。

    ⚠️ 回包 `data` **必须就是「道具key -> 数量」这张 map**：
    客户端 `receiveReward/<` 成功分支是
        this._achivevements[id].isReceiveReward = 1;
        succCb && succCb(achievementId, res.data)
    而 `SubareaChapterRewardLayer._receiveRewardSucc` 直接
        ccuiManager.popupReward(util.objectToArray(res.data))
    —— 往 data 里掺别的键（比如再塞一份 updateSubareaAchievements）
    会被当成道具塞进奖励弹窗。
    """
    aid = str(achievement_id or "")
    if not aid:
        return {"code": PARAM_ERROR, "msg": "achievementId 为空", "data": {}}

    table = ach_table()
    if aid not in table:
        log.warning("领分区成就 %s：表里没有这条", aid)
        return {"code": ACHIEVEMENT_NOT_EXIST, "msg": "成就不存在", "data": {}}

    row = rows(player).get(aid)
    if row is None:
        log.warning("领分区成就 %s：玩家没有这条（客户端没上报过）", aid)
        return {"code": ACHIEVEMENT_NOT_EXIST, "msg": "成就不存在", "data": {}}

    if int(row.get("isReceiveReward") or 0):
        return {"code": REWARD_RECEIVED, "msg": "已经领过了", "data": {}}

    if not row.get("completeTime"):
        # 没完成不能领。客户端 `_getSortIdx` 把没完成的排最后、也不显示领取按钮，
        # 正常走不到这里 —— 兜住手改包的情况。
        log.warning("领分区成就 %s：还没完成", aid)
        return {"code": RECEIVE_REWARD_ERROR, "msg": "成就还没完成", "data": {}}

    rewards = reward_rows(aid)
    if not rewards:
        log.warning("领分区成就 %s：奖励表里一条都没有", aid)
        return {"code": REWARD_NOT_EXIST, "msg": "奖励不存在", "data": {}}

    granted = items.settle(player, rewards)
    if granted.get("pending"):
        # 成就奖励现在全是道具（type "2"）。真出现发不动的类型就**不当成功**，
        # 免得玩家点一下奖励没了。
        log.warning("领分区成就 %s：有奖励发不动 %s", aid, granted["pending"])
        return {"code": UPDATA_DB_ERROR, "msg": "奖励发放失败", "data": {}}

    row["isReceiveReward"] = 1
    log.info("分区成就领奖 %s（%s）-> %s", aid, table[aid].get("desc"), granted["items"])
    return {"code": 200, "msg": "", "data": granted["items"]}
