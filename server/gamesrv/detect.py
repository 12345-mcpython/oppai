"""任务派遣（`detect.*`，主界面「派遣」按钮）。

客户端侧（反汇编 `src/data/detect.jsc` + `src/ui/detect/*`）
==========================================================

* **登录块** `data.detect`：

      {"speedInfo": {"10001": 已用免费加速次数, ...},
       "detect":    {"<章节key>": {"beginTimeSec": 秒, "waitTime": 秒, "subCD": 秒, "speedCount": n}}}

  `Detect.ctor`: `_speedInfo = detect.speedInfo`，`_initDetectInfo(detect.detect)`；
  `getSpeedCountInfo(type) = (table_detect[type].speed_count - _speedInfo[type]) + "/" + …`
  —— 所以 `speedInfo` 的值是**数字**（已用次数），不是对象。

* **计时口径**（`detectItem._getGoDetectTime`）：

      剩余毫秒 = (beginTimeSec + waitTime - subCD) * 1000 - util.time()

  三个字段**都是秒**。

* **响应里的错误码**是 `data.code`（`DETECT_ERROR_CODE`），不是 HTTP 码：

      200 OK / 202 PARAM_ERROR / 203 TIME_CLOSE / 204 GODETECT_RUNNING /
      205 GODETECT_COMPLETE / 206 GODETECT_NOTSELECT / 207 GODETECT_COUNTMAX /
      208 GODETECT_SOLV / 209 GODETECT_NOTGO / 210 GODETECT_NOTCOMPLETE /
      211 GODETECT_SOISGO / 212 GODETECT_SOTYPE / 213 SPEED_MAX / 214 SPEED_ITEM_MAX

* 六条路由：`getdetectlist {id}` / `godetect {detectkey}` / `godetectcomplete {detectkey}` /
  `subtime {detectkey, timesed}` / `selectsolders {detectkey, solders}` /
  `checkdetectmain {idlist}`。

数据（客户端表，已抽进 gamesrv/data/）
=====================================

    table_detect[<分类>]              {name, detectMax, speed_count}     // 10001 礼物 / 10002 卡牌 / 10003 材料
    table_detect_chapter[<章节>]      {key(分类), name, soldierNum, soldierLv, soldierType,
                                       waitTime, subCD, probability1..6, gainItemGroup1..6,
                                       gainIcon, gainIcon2, needUnlockType/Value, priority}
    table_detect_chapter_reward_group {group1..4, groupWeight1..4, groupWeightP1..4}

⚠️ **`gainItemGroup<i>` 指向的那张「组里到底有哪些道具」的表客户端里没有**
（把 "101111" 当 key 扫遍所有 `table_*` 都没有命中）—— 也就是说**掉落内容只在服务端**，
原版发什么无从考证。这里用客户端**表里就有**的 `gainIcon2`（＝界面上「可能掉落」那排图标）
当奖池、按 `groupWeight*` 挑档位，见 differences.md §D。
"""

from __future__ import annotations

import random
import time

from . import items, logx, store

log = logx.get("detect")

# 客户端 DETECT_ERROR_CODE（放在响应 `data.code` 里）
OK = 200
PARAM_ERROR = 202
TIME_CLOSE = 203
GODETECT_RUNNING = 204
GODETECT_COMPLETE = 205
GODETECT_NOTSELECT = 206
GODETECT_COUNTMAX = 207
GODETECT_SOLV = 208
GODETECT_NOTGO = 209
GODETECT_NOTCOMPLETE = 210
GODETECT_SOISGO = 211
GODETECT_SOTYPE = 212
SPEED_MAX = 213
SPEED_ITEM_MAX = 214

CATEGORIES = ("10001", "10002", "10003")
PLAYER_KEY = "detect"
MAX_GROUPS = 6
PROB_BASE = 1000           # probability<i> 是千分比（1000 = 100%）


def category_table() -> dict:
    t = items.table("table_detect")
    return t if isinstance(t, dict) else {}


def chapter_table() -> dict:
    t = items.table("table_detect_chapter")
    return t if isinstance(t, dict) else {}


def group_table() -> dict:
    t = items.table("table_detect_chapter_reward_group")
    return t if isinstance(t, dict) else {}


# ---------------------------------------------------------------------------
# 存档
# ---------------------------------------------------------------------------
def state(player: dict) -> dict:
    st = player.get(PLAYER_KEY)
    if not isinstance(st, dict):
        st = {}
        player[PLAYER_KEY] = st
    if not isinstance(st.get("runs"), dict):
        st["runs"] = {}
    if not isinstance(st.get("speedInfo"), dict):
        st["speedInfo"] = {}
    st["speedInfo"] = {str(k): int(v or 0) for k, v in st["speedInfo"].items()}
    if not isinstance(st.get("speedDay"), str):
        st["speedDay"] = ""
    try:
        st["completeCount"] = int(st.get("completeCount") or 0)
    except (TypeError, ValueError):
        st["completeCount"] = 0
    # 免费加速次数按天重置（客户端只拿它算 remaining，规则由服务端定）
    today = time.strftime("%Y-%m-%d", time.localtime())
    if st["speedDay"] != today:
        st["speedDay"] = today
        st["speedInfo"] = {c: 0 for c in CATEGORIES}
    for c in CATEGORIES:
        st["speedInfo"].setdefault(c, 0)
    return st


def run_of(player: dict, chapter_key) -> dict | None:
    return state(player)["runs"].get(str(chapter_key))


def _category_of(chapter_key) -> str:
    row = chapter_table().get(str(chapter_key)) or {}
    return str(row.get("key") or "")


def _wait_time(chapter_key) -> int:
    row = chapter_table().get(str(chapter_key)) or {}
    try:
        return int(row.get("waitTime") or 0)
    except (TypeError, ValueError):
        return 0


def _remain_sec(run: dict) -> int:
    end = int(run.get("beginTimeSec") or 0) + int(run.get("waitTime") or 0) \
        - int(run.get("subCD") or 0)
    return max(0, end - int(time.time()))


def is_complete(run: dict) -> bool:
    return _remain_sec(run) <= 0


def run_view(run: dict | None, chapter_key) -> dict:
    """给客户端那一条 `{beginTimeSec, waitTime, subCD, speedCount}`。没在跑就给全 0。"""
    if not run:
        return {"beginTimeSec": 0, "waitTime": 0, "subCD": 0, "speedCount": 0}
    return {
        "beginTimeSec": int(run.get("beginTimeSec") or 0),
        "waitTime": int(run.get("waitTime") or 0),
        "subCD": int(run.get("subCD") or 0),
        "speedCount": int(run.get("speedCount") or 0),
    }


def block(player: dict) -> dict:
    """登录块 `data.detect`。"""
    st = state(player)
    return {
        "speedInfo": st["speedInfo"],
        "detect": {k: run_view(v, k) for k, v in st["runs"].items()},
    }


# ---------------------------------------------------------------------------
# 掉落
# ---------------------------------------------------------------------------
def _pool(row: dict) -> list:
    """奖池 = 表里的 `gainIcon2`（界面上「可能掉落」那排图标就是它）。"""
    text = str(row.get("gainIcon2") or row.get("gainIcon") or "")
    return [x for x in text.split("#") if x]


def _pick_tier(group_key) -> int:
    """按 `groupWeight*` 挑一个档位（1..4）。表里没有就回 2（权重最大的那档）。"""
    grp = group_table().get(str(group_key)) or {}
    weights = []
    for i in range(1, 5):
        try:
            w = int(grp.get("groupWeight%d" % i) or 0)
        except (TypeError, ValueError):
            w = 0
        if w > 0:
            weights.append((w, i))
    if not weights:
        return 2
    total = sum(w for w, _ in weights)
    roll = random.randint(1, total)
    upto = 0
    for w, tier in weights:
        upto += w
        if roll <= upto:
            return tier
    return weights[-1][1]


def roll_rewards(chapter_key) -> dict:
    """一次派遣完成的掉落 `{itemKey: count}`。

    `gainItemGroup<i>` 的第 i 个槽位：先按 `probability<i>`（千分比）掷一次，
    中了就按该组的 `groupWeight*` 挑档位、从 `gainIcon2` 奖池里按档位取一件。
    （`gainItemGroup<i>` 指向的真实道具组客户端没有，见模块注释）
    """
    row = chapter_table().get(str(chapter_key)) or {}
    pool = _pool(row)
    out: dict = {}
    if not pool:
        return out
    for i in range(1, MAX_GROUPS + 1):
        group_key = row.get("gainItemGroup%d" % i)
        if not group_key:
            continue
        try:
            prob = int(row.get("probability%d" % i) or 0)
        except (TypeError, ValueError):
            prob = 0
        if prob <= 0 or random.randint(1, PROB_BASE) > prob:
            continue
        tier = _pick_tier(group_key)
        key = pool[(tier - 1) % len(pool)]
        out[key] = out.get(key, 0) + 1
    return out


# ---------------------------------------------------------------------------
# 路由业务体（都返回完整响应 dict；`data.code` 用 DETECT_ERROR_CODE）
# ---------------------------------------------------------------------------
def list_for(player: dict, category) -> dict:
    """`detect.getdetectlist {id}` → `{code, detectList: [{index, data}]}`。"""
    category = str(category or "")
    if category not in category_table():
        return {"code": PARAM_ERROR, "msg": "没有这个分类", "data": {"code": PARAM_ERROR}}
    st = state(player)
    out = []
    for key, row in sorted(chapter_table().items(),
                           key=lambda kv: int((kv[1] or {}).get("priority") or 0)):
        if str((row or {}).get("key")) != category:
            continue
        out.append({"index": str(key), "data": run_view(st["runs"].get(str(key)), key)})
    return {"code": OK, "msg": "", "data": {"code": OK, "detectList": out}}


def check_main(player: dict, idlist) -> dict:
    """`detect.checkdetectmain {idlist}` → `{code, detectMainList: [{id, isOpen}]}`。

    客户端拿它决定三个页签的可见/可点（`isOpen` 用模块解锁状态）。
    """
    if not isinstance(idlist, (list, tuple)) or not idlist:
        idlist = list(CATEGORIES)
    out = []
    for cid in idlist:
        cid = str(cid)
        if cid not in category_table():
            continue
        out.append({"id": cid, "isOpen": 1})
    return {"code": OK, "msg": "", "data": {"code": OK, "detectMainList": out}}


def select_solders(player: dict, chapter_key, solders) -> dict:
    """`detect.selectsolders {detectkey, solders}` —— 只记下这队人，真正的校验在 go 里。"""
    chapter_key = str(chapter_key or "")
    if chapter_key not in chapter_table():
        return {"code": PARAM_ERROR, "msg": "没有这个派遣", "data": {"code": PARAM_ERROR}}
    ids = []
    if isinstance(solders, (list, tuple)):
        for s in solders:
            try:
                ids.append(int(s))
            except (TypeError, ValueError):
                continue
    elif solders is not None:
        try:
            ids = [int(solders)]
        except (TypeError, ValueError):
            ids = []
    st = state(player)
    run = st["runs"].get(chapter_key)
    if run is None:
        run = st["runs"][chapter_key] = {
            "beginTimeSec": 0, "waitTime": _wait_time(chapter_key),
            "subCD": 0, "speedCount": 0,
        }
    run["soldiers"] = ids
    log.info("detect.selectsolders %s 选了 %s", chapter_key, ids)
    return {"code": OK, "msg": "", "data": {"code": OK}}


def _check_soldiers(player: dict, row: dict, ids: list) -> int:
    """校验上阵的军士，返回 DETECT_ERROR_CODE（OK 表示通过）。"""
    need = int(row.get("soldierNum") or 0)
    need_lv = int(row.get("soldierLv") or 0)
    need_type = int(row.get("soldierType") or 0)
    if not ids:
        return GODETECT_NOTSELECT
    if len(ids) != need:
        return GODETECT_NOTSELECT
    if len(set(ids)) != len(ids):
        return GODETECT_SOISGO                      # 同一名军士选了两次
    soldiers = {int(s.get("id") or 0): s for s in (player.get("soldiers") or [])}
    try:
        from . import soldier as soldier_mod
    except Exception:  # noqa: BLE001
        soldier_mod = None
    for sid in ids:
        s = soldiers.get(int(sid))
        if s is None:
            return GODETECT_SOTYPE
        if need_lv and int(s.get("lv") or 1) < need_lv:
            return GODETECT_SOLV
        if need_type and soldier_mod is not None:
            try:
                got = int(soldier_mod.class_type(s.get("key")) or 0)
            except Exception:  # noqa: BLE001
                got = 0
            # 兵种表没抽出来（got == 0）时不拦人 —— 客户端自己的勾选列表已经按
            # `needTypeSoldier` 灰掉了不合格的军士
            if got and got != need_type:
                return GODETECT_SOTYPE
    return OK


def go(player: dict, chapter_key) -> dict:
    """`detect.godetect {detectkey}` —— 开始派遣。"""
    chapter_key = str(chapter_key or "")
    row = chapter_table().get(chapter_key)
    if not row:
        return {"code": PARAM_ERROR, "msg": "没有这个派遣", "data": {"code": PARAM_ERROR}}
    st = state(player)
    run = st["runs"].get(chapter_key)
    if run and int(run.get("beginTimeSec") or 0) > 0:
        # 已经在跑：没跑完就是 RUNNING，跑完了是 COMPLETE（客户端据此提示"去领取"）
        return {"code": GODETECT_COMPLETE if is_complete(run) else GODETECT_RUNNING,
                "msg": "已经在派遣中", "data": {"code": GODETECT_RUNNING}}
    category = _category_of(chapter_key)
    running = [k for k, v in st["runs"].items()
               if k != chapter_key and int(v.get("beginTimeSec") or 0) > 0
               and _category_of(k) == category]
    limit = int((category_table().get(category) or {}).get("max") or 1)
    if len(running) >= limit:
        return {"code": GODETECT_COUNTMAX, "msg": "这个分类同时在派的数量到上限了",
                "data": {"code": GODETECT_COUNTMAX}}
    ids = [int(x) for x in (run or {}).get("soldiers") or []]
    err = _check_soldiers(player, row, ids)
    if err != OK:
        return {"code": err, "msg": "上阵军士不满足条件", "data": {"code": err}}
    if run is None:
        run = st["runs"][chapter_key] = {}
    run.update({
        "beginTimeSec": int(time.time()),
        "waitTime": _wait_time(chapter_key),
        "subCD": 0,
        "speedCount": 0,
        "soldiers": ids,
    })
    log.info("detect.godetect %s（%s）开始，等待 %s 秒，军士 %s",
             chapter_key, row.get("name"), run["waitTime"], ids)
    return {"code": OK, "msg": "", "data": {"code": OK, "detect": {chapter_key: run_view(run, chapter_key)}}}


def complete(player: dict, chapter_key) -> dict:
    """`detect.godetectcomplete {detectkey}` —— 领取（发货）。"""
    chapter_key = str(chapter_key or "")
    if chapter_key not in chapter_table():
        return {"code": PARAM_ERROR, "msg": "没有这个派遣", "data": {"code": PARAM_ERROR}}
    st = state(player)
    run = st["runs"].get(chapter_key)
    if not run or int(run.get("beginTimeSec") or 0) <= 0:
        return {"code": GODETECT_NOTGO, "msg": "还没开始派遣", "data": {"code": GODETECT_NOTGO}}
    if not is_complete(run):
        return {"code": GODETECT_NOTCOMPLETE, "msg": "还没到时间", "data": {"code": GODETECT_NOTCOMPLETE}}

    known = {str(k) for k in items.items_of(player)}
    rewards = roll_rewards(chapter_key)
    granted = items.settle(player, [("2", k, c) for k, c in rewards.items()])
    st["runs"].pop(chapter_key, None)
    st["completeCount"] += 1
    log.info("detect.godetectcomplete %s（%s）-> %s", chapter_key,
             (chapter_table().get(chapter_key) or {}).get("name"), granted.get("items"))
    return {
        "code": OK,
        "msg": "",
        "data": {
            "code": OK,
            "complateDetectCount": st["completeCount"],       # 客户端拼写就是 complate
            "detect": {chapter_key: run_view(None, chapter_key)},
            "rewards": granted.get("items") or {},
            "items": items.changed_block(player, known),
        },
    }


def sub_time(player: dict, chapter_key, timesed) -> dict:
    """`detect.subtime {detectkey, timesed}` —— 跳过 `timesed` 秒。"""
    chapter_key = str(chapter_key or "")
    row = chapter_table().get(chapter_key)
    if not row:
        return {"code": PARAM_ERROR, "msg": "没有这个派遣", "data": {"code": PARAM_ERROR}}
    st = state(player)
    run = st["runs"].get(chapter_key)
    if not run or int(run.get("beginTimeSec") or 0) <= 0:
        return {"code": GODETECT_NOTGO, "msg": "还没开始派遣", "data": {"code": GODETECT_NOTGO}}
    category = _category_of(chapter_key)
    limit = int((category_table().get(category) or {}).get("sc") or 0)
    used = int(st["speedInfo"].get(category) or 0)
    if limit and used >= limit:
        return {"code": SPEED_MAX, "msg": "今天免费加速次数用完了", "data": {"code": SPEED_MAX}}
    try:
        sec = int(timesed or 0)
    except (TypeError, ValueError):
        sec = 0
    remain = _remain_sec(run)
    if sec <= 0:
        # 客户端没给具体秒数（`canSpeed` 那种）就按表里的 `subCD` 走
        try:
            sec = int(row.get("subCD") or 0)
        except (TypeError, ValueError):
            sec = 0
    sec = min(sec, remain)
    run["subCD"] = int(run.get("subCD") or 0) + sec
    run["speedCount"] = int(run.get("speedCount") or 0) + 1
    st["speedInfo"][category] = used + 1
    log.info("detect.subtime %s 跳过 %s 秒（剩 %s），今日已用 %s/%s",
             chapter_key, sec, _remain_sec(run), st["speedInfo"][category], limit)
    return {
        "code": OK,
        "msg": "",
        "data": {
            "code": OK,
            "speedInfo": st["speedInfo"],
            "detect": {chapter_key: run_view(run, chapter_key)},
        },
    }
