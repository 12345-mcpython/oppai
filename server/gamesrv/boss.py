"""好友 BOSS（`boss.*`）—— 单机版：BOSS 由服务端刷在**自己**和**萌友（NPC）**名下。

客户端那半边（`assets/src/data/bosscenter.jsc` + `bossunit.jsc` + `manager/bossmanager.jsc`，
全是从反汇编里读出来的）:

    BossCenter.ctor(data)        // 登录块 data.boss —— ⚠️ 这份**就是「击杀奖励列表」**
        _friendBossObj = {}      // 好友 BOSS：{bossId: BossUtil}
        _bossRecordObj = {}      // 我的战绩：{bossTableKey: {firstFightFalg, shareFlag}}
        _bossFightLog  = {}      // 挑战记录：{bossId: {logList, updateTime}}
        _updateBossKillReward(data)   // data = [{bossKey, reward:{scores,cards,items}}]

    boss.getbosslist {type}      -> data -> BossCenter.update(data)
        data.friendBossList = {bossId: 行}      // map！不是数组
        data.bossRecordList = {bossTableKey: 行}
    boss.startfight {id, curTeamIdx}
        -> data.boss = 更新后的行（只读 curHp；⚠️ 必须带 type+id，否则 _updateBoss 不认）
    boss.finishfight {id, harm}
        -> data = {boss, record, result:{harmReward, killReward, playerInfo, exp}}
           result 会整个交给 BossFightResultLayer（harm 数字动画 + killReward 弹窗 + rewards 列表）
    boss.getbossfightlog {id}    -> data **就是**记录数组（直接塞进 _bossFightLog[id].logList）
    boss.randomsharefriend {id}  -> data = {shareFriends: [{name, lv, headId, asstKey, …}]}
    boss.shareboss {id}          -> data = 更新后的 record

行的形状（`BossUtil.ctor(args)` 逐个读，字段名一个都不能差）::

    {id, key, type:"friend", ownerId, ownerName, lv,
     initHp, curHp, levelKey, appearTimeSec, disappearTimeSec}

⚠️ **`key` 是 `table_world_boss` 的 key**（`fb401811` 这种）—— BOSS 的名字/品质/消耗/
战斗关卡全在客户端那张表里，服务端一个数值都不用编：
`table_world_boss[key] = {name, quality(20/30/40), consume_key(100201), consume_count(1/2/3),
boss_level_key(战斗用的关卡), level_robot_id(NPC), init_hp(150000/450000/1500000),
exist_time(0:10:00 / 1:30:00 / 5:00:00), exp, desc}`。

那张表 36 行 = **4 族 × 3 型（弹/毒/盾）× 3 档**：档位 1/2/3 分别是
`150000 血 / 1 点 BP / 10 分钟`、`450000 / 2 / 1.5 小时`、`1500000 / 3 / 5 小时`。

`levelKey` 和 `boss_level_key` **不是一回事**（这是最容易搞错的一处）：

* `levelKey` = BOSS **出现在哪一关**（客户端 `getChapterBossInfo(chapterId)` 拿它和
  该章节的 `getLevels(chapterId)` 里的 `levelId` 逐个比，相等才显示「有 BOSS」红点）；
* `boss_level_key` = **打它时进的那一关**（`bossmanager.onFight` 用它当
  `doorParams.id` + `Team.getInstanceBattleTeam(id)`，那一关的 `enemy_ids` 第一项
  正好是 `"2" + level_robot_id`，BOSS 单位就是按 `table_npc[level_robot_id]` 建的）。

我们取 `levelKey = boss_level_key - 1`（401819 -> 401818），那一关正好是同一个
「极密」章节（4018）的最后一关，客户端章节面板里点进去就能看到 BOSS 入口。

私服的取舍（见 differences §B/§D）：
* BOSS「自己刷」：原版是好友打了 BOSS 才会出现在你的列表里（`friendBossList` 是别人的），
  单机没有别人 —— 所以服务端**直接给自己刷 3 只**：1 只挂自己名下（首战免费 + 能分享），
  2 只挂 NPC 萌友名下（要花 BP）；
* BP（`100201` 好友BOSS点）原版靠活动/好友互动攒，私服每天补到 `POINT_DAILY`；
* 伤害奖励 / 击杀奖励（`harm_relate_key` 指向的表**客户端全库 0 命中**，是原版服务端的）
  由 `HARM_MONEY` / `KILL_REWARD` 定（旋钮就在这儿）。
"""

from __future__ import annotations

import time

from . import friends, items, logx, quests, store

log = logx.get("boss")

# ---------------------------------------------------------------------------
# 旋钮（私服自己定的都在这儿）
# ---------------------------------------------------------------------------

# 消耗：`table_world_boss[*].consume_key` 也是这个（ITEM_KEY.FRIEND_BOSS_POINT）
POINT_KEY = "100201"

BOSS_TYPE_FRIEND = "friend"

# 同时在场的 BOSS 数 / 每天把 BP 补到多少 / 打光之后隔多久再刷
BOSS_COUNT = 3
POINT_DAILY = 30
SPAWN_DELAY_SEC = 60

# 只有这三族的 `levelKey`（= boss_level_key - 1）落在真实章节里：
#   4018 极密B221-阿尔索斯的葱油饼 / 4019 / 4037 —— 都是 `table_chapter` 里 type=="4" 的章节。
# 第四族 fb4038 的关卡是 403819…，但没有 4038 这个章节（章节面板里点不到），所以不刷。
FAMILIES = ("fb4018", "fb4019", "fb4037")
KINDS = ("1", "2", "3")          # 1 弹 / 2 毒 / 3 盾
TIERS = ("1", "2", "3")          # 1 普通 / 2 改 / 3 改二

# 伤害奖励：按打掉的血量比例给萌钞（打满一只给 HARM_MONEY）
HARM_MONEY = 5000
# 击杀奖励（金条 / 萌钞 / 好人卡 / BP）
KILL_REWARD = {"100001": 30, "100002": 20000, "100016": 5, "100201": 3}

# 每个 BOSS 最多留多少条挑战记录（客户端只显示最近的那几条）
LOG_MAX = 30

# 死了/跑了的 BOSS 在列表里**再留一会儿**（客户端要显示「已击杀」/「已逃跑」图标，
# 而且刚打完那一场还要能分享 / 看记录）。过了这个时间就清掉、腾位置刷新的。
KEEP_DEAD_SEC = 300

# 失败码（客户端 `BOSS_CODE_DICT`，码 -> `table_dictionary` 文案）：
#   201 参数错误(10000)   202 木有找到对应BOSS(2202)   203 木有挑战权限(2203)
#   204 BOSS已经被击杀(2204) 205 BOSS都逃走了(2205)     206 已经分享过了(2206)
#   207 没有挑战记录(2207)  208 另一场BOSS战斗中(2208)  209 BP不足(2209)
#   210 军士库满员(808)     211 木有找到可挑战的BOSS(2211) 212 无效的BOSS ID(2212)
#   213 找不到分享的萌友(2213) 214 已经取消了分享(2214)  215 木有可分享的萌友(2215)
#   216 要完成一次挑战才能分享(2217)
CODE_PARAM = 201
CODE_NOT_FOUND = 202
CODE_KILLED = 204
CODE_ESCAPED = 205
CODE_SHARED = 206
CODE_NO_POINT = 209
CODE_SHARE_CANCELED = 214
CODE_NO_SHARE_FRIEND = 215
CODE_NEED_FIGHT = 216


# ---------------------------------------------------------------------------
# 客户端表
# ---------------------------------------------------------------------------

def _table() -> dict:
    return items.table("table_world_boss")


def exist_sec(text) -> int:
    """`"1:30:00"` -> 5400（表里只有 0:10:00 / 1:30:00 / 5:00:00 三种）。"""
    parts = str(text or "").split(":")
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return 600
    while len(nums) < 3:
        nums.insert(0, 0)
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def _appear_level(family: str, boss_level_key: str) -> str:
    """BOSS **出现在哪一关**（客户端 `getChapterBossInfo` 拿它和章节关卡逐个比）。

    ⚠️ 不是 `boss_level_key - 1`！一族有 9 只（3 型 × 3 档），战斗关卡是
    `403719/403720/403721`（型 1 的三档）… `403727`（型 3 的档 3），
    而「出现的那一关」应该是它们所属章节的**最后一关** —— `table_chapter["4037"].lv`
    的末尾 = `403718`（三档共用同一关，客户端只是把 BOSS 挂在那个关卡图标上）。

    族名 `fb4018` 的第 2 位起就是章节 id（`4018`），直接查表拿最后一关。
    """
    chapter_id = str(family)[2:]
    chapter = items.table("table_chapter").get(chapter_id) or {}
    levels = [str(x) for x in (chapter.get("lv") or [])]
    if levels:
        return levels[-1]
    try:                                    # 兜底：章节表里没有这一族时退一格
        return str(int(boss_level_key) - 1)
    except ValueError:
        return str(boss_level_key)


def rows() -> list:
    """`table_world_boss` 里属于 `FAMILIES` 的行，按 (族, 型, 档) 稳定排序。

    行的 key 是 `fb401811` = family + kind + tier（4+1+1+1 位）。
    """
    table = _table()
    out = []
    for family in FAMILIES:
        for kind in KINDS:
            for tier in TIERS:
                key = family + kind + tier
                row = table.get(key)
                if not isinstance(row, dict):
                    continue
                level_key = str(row.get("boss_level_key") or "")
                out.append({
                    "key": key,
                    "family": family,
                    "kind": kind,
                    "tier": tier,
                    "name": str(row.get("name") or key),
                    "quality": int(row.get("quality") or 20),
                    "consume_key": str(row.get("consume_key") or POINT_KEY),
                    "consume_count": int(row.get("consume_count") or 1),
                    "level_key": _appear_level(family, level_key),
                    "boss_level_key": level_key,
                    "robot_id": str(row.get("level_robot_id") or ""),
                    "init_hp": int(row.get("init_hp") or 0),
                    "exist_sec": exist_sec(row.get("exist_time")),
                    "exp": int(row.get("exp") or 0),
                    "desc": str(row.get("desc") or ""),
                })
    return out


def row_of(table_key) -> dict | None:
    key = str(table_key or "")
    for row in rows():
        if row["key"] == key:
            return row
    return None


# ---------------------------------------------------------------------------
# 存档
# ---------------------------------------------------------------------------

def state(player: dict) -> dict:
    """`player["boss"]`：

        seq          递增序号（刷 BOSS 轮换 + 生成 id 都靠它，确定性）
        day          游戏内的今天（换日点 05:00，见 quests.day_str）
        spawn_at_sec 上一次刷新时间（打光之后隔 SPAWN_DELAY_SEC 再刷）
        list         {bossId: 行}（给客户端的那个 map）
        records      {bossTableKey: {firstFightFalg, shareFlag}}
        log          {bossId: [记录, …]}
    """
    st = player.get("boss")
    if not isinstance(st, dict):
        st = {}
        player["boss"] = st
    for key, empty in (("list", {}), ("records", {}), ("log", {})):
        if not isinstance(st.get(key), dict):
            st[key] = empty
    if not isinstance(st.get("seq"), int):
        st["seq"] = 0
    if not isinstance(st.get("day"), str):
        st["day"] = ""
    if not isinstance(st.get("spawn_at_sec"), int):
        st["spawn_at_sec"] = 0
    return st


def by_id(player: dict, boss_id):
    return state(player)["list"].get(str(boss_id or ""))


def alive_of(player: dict, now: int | None = None) -> list:
    """列表里**还在占位**的 BOSS（没被清掉的，含刚死/刚跑还在展示期的）。"""
    ts = int(now if now is not None else time.time())
    return [row for row in state(player)["list"].values() if not gone(row, ts)]


def is_die(row: dict) -> bool:
    return int((row or {}).get("curHp") or 0) <= 0


def is_escape(row: dict, now: int | None = None) -> bool:
    """客户端 `BossUtil.isEscape()`：还活着但过了消失时间。

    ⚠️ 客户端的写法是 `if (curHp <= 0) return;  return now > disappearTime`，
    所以**死了的不算逃跑**（`_getStatus()` 先判 isDie）。
    """
    if is_die(row):
        return False
    ts = int(now if now is not None else time.time())
    return ts > int((row or {}).get("disappearTimeSec") or 0)


def gone(row: dict, now: int | None = None) -> bool:
    """该从列表里清掉了：死了/跑了**并且**过了 `KEEP_DEAD_SEC` 的展示期。"""
    ts = int(now if now is not None else time.time())
    if is_die(row):
        return ts > int(row.get("dieTimeSec") or 0) + KEEP_DEAD_SEC
    if is_escape(row, ts):
        return ts > int(row.get("disappearTimeSec") or 0) + KEEP_DEAD_SEC
    return False


def client_row(row: dict) -> dict:
    """给客户端的行（`BossUtil.ctor` 读的字段 + 我们自己要看的那几个）。"""
    return {
        "id": int(row["id"]),
        "key": row["key"],
        "type": BOSS_TYPE_FRIEND,
        "ownerId": row["ownerId"],
        "ownerName": row.get("ownerName") or "",
        "lv": int(row.get("lv") or 1),
        "initHp": int(row["initHp"]),
        "curHp": int(row["curHp"]),
        "levelKey": row["levelKey"],
        "appearTimeSec": int(row["appearTimeSec"]),
        "disappearTimeSec": int(row["disappearTimeSec"]),
    }


def record_block(player: dict) -> dict:
    """`bossRecordList`：**按 BOSS 表 key 索引**（客户端 `_bossRecordObj[boss.key]`）。"""
    return {k: dict(v) for k, v in state(player)["records"].items()}


def record_of(player: dict, row: dict) -> dict:
    rec = state(player)["records"].get(row["key"])
    if not isinstance(rec, dict):
        rec = {}
        state(player)["records"][row["key"]] = rec
    rec.setdefault("firstFightFalg", 0)
    rec.setdefault("shareFlag", 0)
    return rec


# ---------------------------------------------------------------------------
# 刷新 / 换日
# ---------------------------------------------------------------------------

def owner_of(player: dict, index: int, ts: int) -> tuple:
    """第 index 只 BOSS 挂谁名下 -> (ownerId, ownerName)。

    * `index == 0` -> **自己**（首战免费、打完能分享给萌友）
    * 其余 -> 自己的 NPC 萌友（没有好友就用 NPC 表的推荐名单）
    """
    if index == 0:
        return int(player.get("id") or 1), str(player.get("name") or "指挥官")
    picks = _friend_npcs(player)
    if not picks:
        return int(player.get("id") or 1), str(player.get("name") or "指挥官")
    return picks[(index - 1) % len(picks)]


def _friend_npcs(player: dict) -> list:
    """[(numberId, name)] —— 优先用**已经是好友**的 NPC，没有就用推荐名单。"""
    out = []
    try:
        rows = friends.npc_rows()
    except Exception:                                   # noqa: BLE001
        return out
    mine = set()
    try:
        for entry in (friends.friend_map_list(player) or {}).values():
            if isinstance(entry, dict) and isinstance(entry.get("player"), dict):
                mine.add(int(entry["player"].get("numberId") or 0))
    except Exception:                                   # noqa: BLE001
        mine = set()
    for number_id, key, row, _head in rows:
        name = str(row.get("player_name") or key)
        if int(number_id) in mine:
            out.insert(0, (int(number_id), name))
        else:
            out.append((int(number_id), name))
    return out


def _spawn_one(player: dict, st: dict, index: int, ts: int) -> dict | None:
    """刷第 index 只（0..BOSS_COUNT-1）。轮换靠 `seq`，可复现。"""
    table = rows()
    if not table:
        log.warning("table_world_boss 还没抽（跑 script/extract_decompile 那套）—— BOSS 刷不出来")
        return None
    seq = int(st["seq"])
    family_idx = (seq + index) % len(FAMILIES)
    kind_idx = (seq // len(FAMILIES) + index) % len(KINDS)
    tier_idx = index % len(TIERS)
    table_key = FAMILIES[family_idx] + KINDS[kind_idx] + TIERS[tier_idx]
    row = row_of(table_key) or table[(seq + index) % len(table)]
    st["seq"] = seq + 1

    owner_id, owner_name = owner_of(player, index, ts)
    boss_id = ts * 10 + index          # 递增且不会撞（同一秒最多 BOSS_COUNT 只）
    boss = {
        "id": boss_id,
        "key": row["key"],
        "name": row["name"],
        "ownerId": owner_id,
        "ownerName": owner_name,
        "lv": 1,
        "initHp": row["init_hp"],
        "curHp": row["init_hp"],
        "levelKey": row["level_key"],
        "appearTimeSec": ts,
        "disappearTimeSec": ts + row["exist_sec"],
        "_exp": row["exp"],
        "_consumeCount": row["consume_count"],
    }
    st["list"][str(boss_id)] = boss
    # 新刷的 BOSS 战绩清零（记录是按 BOSS 表 key 索引的，同 key 复用一条）
    st["records"][row["key"]] = {"firstFightFalg": 0, "shareFlag": 0}
    st["log"][str(boss_id)] = []
    log.info("刷好友BOSS %s(%s) id=%s 挂 %s(%s) 名下，%s 分钟后消失",
             row["name"], row["key"], boss_id, owner_name, owner_id,
             row["exist_sec"] // 60)
    return boss


def ensure(player: dict, now: int | None = None) -> bool:
    """换日重置 + 补 BP + 刷 BOSS（幂等，登录和 getbosslist 都会调）。"""
    ts = int(now if now is not None else time.time())
    st = state(player)
    changed = False

    today = quests.day_str(ts)
    if st["day"] != today:
        st["day"] = today
        st["list"] = {}
        st["records"] = {}
        st["log"] = {}
        st["spawn_at_sec"] = 0
        changed = True
        # BP 每天补到 POINT_DAILY（只加不减 —— 花掉的不回收，多了也不动）
        bag = items.items_of(player)
        if int(bag.get(POINT_KEY) or 0) < POINT_DAILY:
            bag[POINT_KEY] = POINT_DAILY
            log.info("换日补 %s 点到 %s", POINT_KEY, POINT_DAILY)

    # 死了/跑了的过了展示期就清掉（腾位置刷新的；客户端看 curHp/时间自己画状态）
    for boss_id in [k for k, row in st["list"].items() if gone(row, ts)]:
        del st["list"][boss_id]
        changed = True

    live = alive_of(player, ts)
    if len(live) < BOSS_COUNT and ts - int(st["spawn_at_sec"]) >= SPAWN_DELAY_SEC:
        for index in range(len(live), BOSS_COUNT):
            _spawn_one(player, st, index, ts)
        st["spawn_at_sec"] = ts
        changed = True
    return changed


# ---------------------------------------------------------------------------
# 登录块 / 各 route
# ---------------------------------------------------------------------------

def login_block(player: dict) -> list:
    """登录块 `data.boss` —— **它是一份「击杀奖励列表」，不是 BOSS 列表**。

    `BossCenter.ctor(data)` 只把它交给 `_updateBossKillReward()`：
    `[{bossKey, reward: {scores, cards, items}}]`，列表非空就弹「击杀奖励」提示层
    （`getBossKillRewardList()` 取一次就清空）。

    BOSS 列表本身是 `boss.getbosslist` 拉的（客户端进 BOSS 界面时自己请求），
    所以这里给空数组就行 —— 但**必须给**，`new BossCenter(undefined)` 也能跑，
    只是「好友打了你的 BOSS」那套提示就永远不出现（单机本来也没有）。
    """
    ensure(player)
    return []


def list_route(player: dict) -> dict:
    """`boss.getbosslist {type}` -> `{friendBossList, bossRecordList}`。"""
    ensure(player)
    st = state(player)
    return {
        "friendBossList": {k: client_row(row) for k, row in st["list"].items()},
        "bossRecordList": record_block(player),
    }


def consume_count(player: dict, row: dict) -> int:
    """客户端 `getBossConsume()` 的服务端版：**自己的 BOSS 第一次打免费**。

        boss.ownerId == player.id && (没有战绩 || !firstFightFalg) && status == FIGHT
          -> 0
        否则 -> 表里的 consume_count
    """
    rec = state(player)["records"].get(row["key"]) or {}
    if int(row.get("ownerId") or 0) == int(player.get("id") or 1) \
            and not int(rec.get("firstFightFalg") or 0) and not is_die(row):
        return 0
    return int(row.get("_consumeCount") or row.get("consume_count") or 1)


def start_fight(player: dict, msg: dict) -> dict:
    """`boss.startfight {id, curTeamIdx}` —— 只做校验 + 扣 BP，战斗在客户端打。"""
    from .gameproto import CODE_OK

    ts = int(time.time())
    ensure(player, ts)
    boss_id = str((msg or {}).get("id") or "")
    row = by_id(player, boss_id)
    if not row:
        return {"code": CODE_NOT_FOUND, "msg": "木有找到对应BOSS", "data": {}}
    if is_die(row):
        return {"code": CODE_KILLED, "msg": "BOSS已经被击杀", "data": {}}
    if is_escape(row, ts):
        return {"code": CODE_ESCAPED, "msg": "BOSS已经逃跑了", "data": {}}

    cost = consume_count(player, row)
    if cost > 0 and items.count_of(player, POINT_KEY) < cost:
        log.info("玩家 %s 打 BOSS %s 的 BP 不够（要 %s 有 %s）",
                 player.get("account"), row["key"], cost, items.count_of(player, POINT_KEY))
        return {"code": CODE_NO_POINT, "msg": "BP不足", "data": {}}
    if cost > 0:
        items.sub_item(player, POINT_KEY, cost)
    log.info("玩家 %s 开始打 BOSS %s(%s)，花 %s 点 BP（剩 %s）",
             player.get("account"), row.get("name"), row["key"], cost,
             items.count_of(player, POINT_KEY))
    # ⚠️ 回包必须带 type + id：客户端 `_updateBoss(data.boss)` 是按
    #    `boss.type == BOSS_TYPE.FRIEND` 分流、再 `_friendBossObj[boss.id].update(boss)`，
    #    只回 curHp 的话两边都取不到。
    return {"code": CODE_OK, "msg": "", "data": {
        "boss": {"id": client_row(row)["id"], "type": BOSS_TYPE_FRIEND,
                 "curHp": int(row["curHp"])},
    }}


def _reward_money(percent: float) -> int:
    return max(1, int(round(HARM_MONEY * percent)))


def finish_fight(player: dict, msg: dict) -> dict:
    """`boss.finishfight {id, harm}` —— 结算伤害、给奖励、可能击杀。

    `harm` 是**客户端算的**（`bossmanager.onFight`：`curHp - enemyUnitsInfo[robotId].hp`）。
    服务端这边只做**夹取**：不能超过剩余血量，也不能是负数 —— 不然一次改包就能秒掉。
    """
    from .gameproto import CODE_OK

    ts = int(time.time())
    ensure(player, ts)
    boss_id = str((msg or {}).get("id") or "")
    row = by_id(player, boss_id)
    if not row:
        return {"code": CODE_NOT_FOUND, "msg": "木有找到对应BOSS", "data": {}}
    try:
        harm = int((msg or {}).get("harm") or 0)
    except (TypeError, ValueError):
        return {"code": CODE_PARAM, "msg": "参数错误", "data": {}}
    if harm < 0:
        return {"code": CODE_PARAM, "msg": "参数错误", "data": {}}
    if is_die(row):
        return {"code": CODE_KILLED, "msg": "BOSS已经被击杀", "data": {}}
    if is_escape(row, ts):
        return {"code": CODE_ESCAPED, "msg": "BOSS已经逃跑了", "data": {}}

    harm = min(harm, int(row["curHp"]))
    row["curHp"] = int(row["curHp"]) - harm
    killed = int(row["curHp"]) <= 0
    if killed:
        row["dieTimeSec"] = ts          # 死了也留 KEEP_DEAD_SEC（见 gone()）
    percent = harm / float(row["initHp"] or 1)

    rec = record_of(player, row)
    rec["firstFightFalg"] = 1                      # 打过了 -> 可以分享（isNeedShare）

    harm_reward = {store.ITEM_MONEY: _reward_money(percent)} if harm > 0 else {}
    kill_reward = {}
    if killed:
        kill_reward = dict(KILL_REWARD)
        # 击杀奖励按 BOSS 档位放大（品质 20/30/40 -> ×1 / ×2 / ×4）
        mult = max(1, int(round(int(_row_quality(row)) / 20.0)))
        kill_reward = {k: v * mult for k, v in kill_reward.items()}

    for key, count in list(harm_reward.items()) + list(kill_reward.items()):
        items.add_item(player, key, count)

    exp = int(row.get("_exp") or 0) if killed else 0
    old_exp = int(player.get("curExp") or 0)
    player["curExp"] = old_exp + exp

    _push_log(player, row, harm, ts)
    if killed:
        log.info("玩家 %s 击杀好友BOSS %s(%s)，伤害 %s，击杀奖励 %s",
                 player.get("account"), row.get("name"), row["key"], harm, kill_reward)
    else:
        log.info("玩家 %s 打好友BOSS %s(%s) 伤害 %s（剩 %s/%s），伤害奖励 %s",
                 player.get("account"), row.get("name"), row["key"], harm,
                 row["curHp"], row["initHp"], harm_reward)

    result = {
        "harmReward": _reward_block(harm_reward),
        # 客户端 `_dealResult`：`result.killReward` 有值才 setProp("killReward")
        # （`BossFightResultLayer` 拿它弹 `LevelRewardLayer.pop(…, KILL_BOSS)`）
        "playerInfo": {"playerAttr": {"curExp": player["curExp"],
                                      "lv": int(player.get("lv") or 1)}},
    }
    if kill_reward:
        result["killReward"] = _reward_block(kill_reward)
    data = {
        "boss": {"id": client_row(row)["id"], "type": BOSS_TYPE_FRIEND,
                 "curHp": int(row["curHp"])},
        "record": dict(rec),
        "result": result,
    }
    if exp:
        result["exp"] = exp
        data["player"] = {"playerAttr": {"curExp": player["curExp"],
                                         "lv": int(player.get("lv") or 1)}}
    return {"code": CODE_OK, "msg": "", "data": data}


def _row_quality(row: dict) -> int:
    table_row = _table().get(row.get("key")) or {}
    return int(table_row.get("quality") or 20)


def _reward_block(rewards: dict) -> dict:
    """`{道具key: 数量}` -> 客户端 `_handlerRewards` 要的 `{items: {...}}`。

    ⚠️ `items` 必须是 **map**（`_getRewardTotal` 是按 key 累加），
    它再排成 `[{key, count}]` 给奖励面板。
    """
    return {"items": {str(k): int(v) for k, v in (rewards or {}).items() if int(v) > 0}}


def _push_log(player: dict, row: dict, harm: int, ts: int) -> None:
    """挑战记录（客户端 `BossRecordInfoItem.update` 读的字段）。"""
    entries = state(player)["log"].setdefault(str(row["id"]), [])
    entries.append({
        "name": str(player.get("name") or "指挥官"),
        "lv": int(player.get("lv") or 1),
        "harm": int(harm),
        "createTimeSec": ts,
        "headId": "",
        "asstKey": "",
    })
    del entries[:-LOG_MAX]


def log_route(player: dict, msg: dict) -> dict:
    """`boss.getbossfightlog {id}` —— ⚠️ `data` **就是那个数组**（不是 `{logList: …}`）。"""
    from .gameproto import CODE_OK

    ensure(player)
    boss_id = str((msg or {}).get("id") or "")
    if not by_id(player, boss_id):
        return {"code": CODE_NOT_FOUND, "msg": "木有找到对应BOSS", "data": {}}
    entries = list(state(player)["log"].get(boss_id) or [])
    entries.reverse()                    # 客户端按 createTimeSec 排，新的在前
    return {"code": CODE_OK, "msg": "", "data": entries}


def _share_friends(player: dict) -> list:
    """能分享给谁 -> 客户端 `_updatePlayerInfo` 读的那四个字段。"""
    out = []
    for number_id, name in _friend_npcs(player):
        info = friends.npc_info(number_id) or {}
        out.append({
            "playerId": int(number_id),
            "numberId": int(number_id),
            "name": info.get("name") or name,
            "lv": int(info.get("lv") or 1),
            "headId": info.get("headId") or "",
            "asstKey": "",
        })
        if len(out) >= 5:
            break
    return out


def random_share_friend(player: dict, msg: dict) -> dict:
    """`boss.randomsharefriend {id}` -> `{shareFriends: [...]}`（分享面板的候选萌友）。"""
    from .gameproto import CODE_OK

    ensure(player)
    boss_id = str((msg or {}).get("id") or "")
    row = by_id(player, boss_id)
    if not row:
        return {"code": CODE_NOT_FOUND, "msg": "木有找到对应BOSS", "data": {}}
    rec = record_of(player, row)
    if not int(rec.get("firstFightFalg") or 0):
        # 客户端 `isNeedShare` 也是这个条件（要打过一次才能分享）
        return {"code": CODE_NEED_FIGHT, "msg": "要完成一次挑战才能分享", "data": {}}
    friends_list = _share_friends(player)
    if not friends_list:
        return {"code": CODE_NO_SHARE_FRIEND, "msg": "木有可以分享BOSS信息的萌友", "data": {}}
    return {"code": CODE_OK, "msg": "", "data": {"shareFriends": friends_list}}


def share_boss(player: dict, msg: dict) -> dict:
    """`boss.shareboss {id}` —— **分享和取消分享是同一条 route**。

    客户端 `shareBoss()` 和 `cancelShareBoss()` 发的请求**一模一样**（都只有 `id`）：
    真正"是分享还是取消"由 `record.shareFlag` 决定（分享面板的「确定」和「取消」两个按钮
    走的是同一个请求）。所以服务端这里做成**开关**：

        shareFlag == 0 -> 分享（置 1，记下分享给了谁）
        shareFlag == 1 -> 取消（置回 0）

    ⚠️ 客户端自己那道闸（`judgeShare` -> `isNeedShare`）在 `shareFlag == 1` 时
    **两条路都拦住**（toast 206「已经分享过了」），所以实机上取消基本点不到 ——
    这是客户端自己的毛病，服务端两种都支持。
    """
    from .gameproto import CODE_OK

    ensure(player)
    boss_id = str((msg or {}).get("id") or "")
    row = by_id(player, boss_id)
    if not row:
        return {"code": CODE_NOT_FOUND, "msg": "木有找到对应BOSS", "data": {}}
    rec = record_of(player, row)
    if not int(rec.get("firstFightFalg") or 0):
        return {"code": CODE_NEED_FIGHT, "msg": "要完成一次挑战才能分享", "data": {}}

    if int(rec.get("shareFlag") or 0):
        rec["shareFlag"] = 0
        log.info("玩家 %s 取消分享 BOSS %s(%s)", player.get("account"), row.get("name"), row["key"])
    else:
        shared = _share_friends(player)
        if not shared:
            return {"code": CODE_NO_SHARE_FRIEND, "msg": "木有可以分享BOSS信息的萌友",
                    "data": {}}
        rec["shareFlag"] = 1
        log.info("玩家 %s 把 BOSS %s(%s) 分享给 %s 个萌友",
                 player.get("account"), row.get("name"), row["key"], len(shared))
    return {"code": CODE_OK, "msg": "", "data": dict(rec)}
