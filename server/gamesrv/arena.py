"""演习场（`arena.*`）—— 主界面「演习场」按钮（模块 `100012`，解锁等级 27）。

客户端就 **4 条路由**（`src/data/arenacenter.jsc`）：

    arena.getrivallist  {}                                    → 刷新对手列表
    arena.resetrivals   {useGold}                             → 手动换一批对手
    arena.enterfight    {index}                               → 进战斗
    arena.exitfight     {index, success, battleInfo}          → 结算（积分/连胜/奖励）

其它全靠**登录块** `data.arena`（`ArenaCenter.ctor(data)` 原样读）：

```js
{arenaInfo:  {...},          // 我的演习场数据（points/change/wins/rating/refreshTime）
 rivals:     [ {...}, ... ],  // 对手列表，每项一个 {index,name,lv,rating,points,headId,asstKey,state,soldier1..5}
 resetTime:  秒级时间戳,       // 赛季重置（每 14 天一轮）
 refreshTime:秒级时间戳}       // 上一次刷新对手的时刻 —— 客户端会自己 +7200（见下）
```

`arenaInfo` 里客户端**只读 5 个键**：`points`（积分）、`change`（**今日剩余挑战次数**，
不是积分变化！`change <= 0` 时客户端两个按钮直接 toast `table_dictionary[1602]`
「木有挑战次数了！」）、`wins`（连胜）、`rating`（段位 1..5）、`refreshTime`。

⚠️ 三个容易踩的地方：

1. **`refreshTime` 要发两份**：构造函数读的是**顶层** `data.refreshTime`，
   而 `updateByServer` 读的是 **`data.arenaInfo.refreshTime`**
   （`_refreshTime = data.arenaInfo.refreshTime + table_arena_constant.update_interval_by_player`）。
   只发一处，另一条路径上 `_refreshTime` 会变成 `undefined + 7200 = NaN`（倒计时直接乱）。
2. **`rival.soldier<i>` 是「军士编码串」`"key#星级#等级#技能等级"`**（不是对象、也不是裸 key）——
   客户端 `ArenaCenter._init` 拿 `charManager.decodeSoldier(text)` 解出来挂到
   `rival.soldiers[i]`（**1 基**，`soldier1` → `soldiers[1]`）；`decodeSoldier` 的规则是
   `text.split("#")`，**少于 4 段直接 `cc.warn` + 返回 undefined**，同一角色重复出现会被丢掉。
   所以直接抄 `table_friend_support_npc` 里的军士字段（`general`/`brave`/`armor`/
   `biological`/`agent`），它们的值（如 `"scyy010104#1#30#1"`）正好就是这个格式。
3. **对手列表靠响应派发落回客户端**：`ArenaCenter.requestGetArenaInfo(cb)` 的 cb
   **不带参数**，真正把数据喂进去的是 `patch.js` 的 RESP-DISPATCH
   （`arena -> dataManager.arenaCenter.updateByServer`）——所以每条回包都要带
   `data.arena` 这个块，否则界面刷不动。
4. **`rival.asstKey` 是「军士卡 key」**（`"sgnw010104"`），不是角色 key（`"sgnw"`）：
   客户端 `headId` 为 0 时走 `new ItemIcon(asstKey)` → `Shop.getTypeById(key)`，
   而它只认 `table_item` / `table_soldier` / `table_mecha` / `table_hero` /
   `table_equipment` 的 key；给角色 key 会 `cc.log("key is error")` 且头像画不出来
   （实机量到 8 个对手各报一次）。
5. **结算回包 `data` 要平铺 `battleData` 的三行**：`combatTime`（秒）/ `death` /
   **`rank`（其实是「对手积分」**，`arenawinlayer.csb` 里的标题）——
   少任何一个，客户端 `numelabed.label.string = undefined` 会抛
   `js_cocos2dx_ui_Text_setString : Error processing arguments`，
   整个结算面板停在半路，玩家**退不出战斗**（2026-09-20 实机踩过）。
   `scoreInfo` 是这三行各自的评价字母（`a|b|c|d|s|ss|s`，赢了才画）。

数值来自 `table_arena_constant`（44 项，抽在 `data/table_arena_constant.json`）；
**积分公式原版无从考证**（原版服务端没了），这里按表里的系数自己定了一套，
见 `points_change()` 的注释与 `docs/differences.md` §D。
"""

from __future__ import annotations

import random
import time

from . import items, logx, store

log = logx.get("arena")

PLAYER_KEY = "arena"

# 对手数据的版本号：**改了 `make_rivals()` 发的字段就 +1**，老存档下次登录会重造一批。
# （和老存档兼容的套路：`store.GIFT_STOCK_VERSION` / `favor.LOOK_STOCK_VERSION` 同款。
#  加它是因为 `asstKey` 从角色 key 改成军士卡 key 时，存档里的旧对手会一直发旧字段。）
RIVAL_VERSION = 1

# 对手的 5 个军士槽位（`table_friend_support_npc` 的字段名 → soldier1..5）
RIVAL_SOLDIER_FIELDS = ("general", "brave", "armor", "biological", "agent")

RIVAL_STATE_READY = 0      # 还没赢过（客户端 `_setDekaroned(0)` 显示「挑战」按钮）
RIVAL_STATE_WON = 1        # **赢过**（`table_dictionary[1603]` =「已经战胜过他了呢~」）

CODE_OK = 200
CODE_PARAM_ERROR = 202
CODE_NO_MONEY = 205         # 萌钞不够（客户端 `requestResetRivalsCb` 只看 code != 200）
CODE_NO_RIVAL = 207

MONEY_KEY = "100002"        # 萌钞 —— 客户端刷新按钮查的就是 `ITEM_KEY.MONEY`
CHALLENGE_RESET_HOUR = 5    # 「今日剩余挑战次数」跨天点（和分区关卡同一个 05:00）

# 结算面板三行的评价字母（`ArenaWinLayer` 只用 `scoreInfo` 挑图标，赢了才画）
GRADE_LETTERS = ("d", "c", "b", "a", "s", "ss", "sss")


# ---------------------------------------------------------------------------
# 表
# ---------------------------------------------------------------------------
def const_table() -> dict:
    t = items.table("table_arena_constant")
    return t if isinstance(t, dict) else {}


def _int(name: str, default: int) -> int:
    try:
        return int(const_table().get(name, default))
    except (TypeError, ValueError):
        return default


def update_interval() -> int:
    """对手列表多久自动换一批（秒）：`update_interval_by_player` = 7200。"""
    return max(1, _int("update_interval_by_player", 7200))


def rival_count() -> int:
    return max(1, _int("rival_count", 8))


def refresh_cost() -> int:
    """手动刷新一次要多少金条（`refresh_cost` = "5"，冷却期内才收）。"""
    try:
        return max(0, int(str(const_table().get("refresh_cost") or "0").strip()))
    except (TypeError, ValueError):
        return 5


def refresh_cooldown() -> int:
    """两次免费刷新之间的冷却（秒）：`refresh_cooldown` = 120。"""
    return max(0, _int("refresh_cooldown", 120))


def max_rating() -> int:
    return max(1, _int("max_rating", 5))


def rating_threshold(rating: int) -> int:
    """`rating_1..5` = 100/1000/2000/3000/4000（积分段位下限）。越界给边界值。"""
    if rating <= 1:
        return _int("min_arena_points", 100)
    if rating > max_rating():
        return _int("max_arena_points", 10000)
    return _int("rating_%d" % rating, _int("min_arena_points", 100))


def rating_of(points: int) -> int:
    """积分 → 段位（1..max_rating）：取不超过积分的最大档。"""
    out = 1
    for r in range(1, max_rating() + 1):
        if points >= rating_threshold(r):
            out = r
    return out


def pvp_rewards() -> list:
    """`pvp_rewards` = "100019#14"（可多组用 `@` 分隔）→ [(type, key, count)]。

    客户端 `ArenaSelectTeam` 自己也会解析这个串来显示「胜利奖励」，
    所以这里按同一个格式解，别自己改数值。
    """
    text = str(const_table().get("pvp_rewards") or "")
    out = []
    for group in text.split("@"):
        parts = group.split("#")
        if len(parts) >= 2 and parts[0]:
            try:
                out.append(("2", parts[0], int(parts[1])))
            except (TypeError, ValueError):
                continue
    if not out:
        out = [("2", "100019", 14)]
    return out


def fail_rewards() -> list:
    """输了也给的东西：`fail_coins` = 14 个演习萌币（和 `pvp_rewards` 同一个道具）。"""
    key = pvp_rewards()[0][1]
    return [("2", key, max(0, _int("fail_coins", 14)))]


def npc_table() -> dict:
    return items.table("table_friend_support_npc") or {}


# ---------------------------------------------------------------------------
# 时间 / 数值
# ---------------------------------------------------------------------------
def reset_time(now: float | None = None) -> int:
    """赛季重置时刻：`arena_reset_first_date`（2016-01-01）起每
    `arena_reset_cycle_day`（14）天一轮，返回**下一个**轮次的时间戳。

    这是从表里算出来的，不是编的；客户端只拿它做一个倒计时。
    """
    first = str(const_table().get("arena_reset_first_date") or "2016-01-01")
    cycle_days = max(1, _int("arena_reset_cycle_day", 14))
    try:
        base = int(time.mktime(time.strptime(first, "%Y-%m-%d")))
    except ValueError:
        base = int(time.mktime(time.strptime("2016-01-01", "%Y-%m-%d")))
    cycle = cycle_days * 86400
    now = int(time.time() if now is None else now)
    if now <= base:
        return base + cycle
    n = (now - base) // cycle + 1
    return base + n * cycle


def points_change(my_points: int, rival_points: int, win: bool) -> int:
    """这一场的**积分增减**（**自己定的公式**，原版无从考证 —— 见 `differences.md` §D）。

    ⚠️ 别和登录块里的 `arenaInfo.change` 搞混：那个是**今日剩余挑战次数**
    （`table_dictionary[1602]` =「木有挑战次数了！」，客户端 `_onClickFightButton` /
    `_onClickRefreshButton` 都在 `change <= 0` 时直接 toast 退出），不是积分变化。
    积分变化只出现在结算面板的 `winPoints` 上。

    表里的旋钮：`points_formula_a` = 800（分母）、`points_formula_c` = 15（基础值）、
    `points_range` = 5（分差档位）、`points_volatility` = 10（单场上限）。

        swing = round((对手分 - 我分) * points_range / points_formula_a)   // 打强敌多加、打弱敌少加
        赢：delta = +clamp(points_formula_c + swing, 1, points_volatility)
        输：delta = -clamp(points_formula_c - swing, 1, points_volatility)

    势均力敌时就是 ±10（被 `points_volatility` 夹住）。
    """
    a = max(1, _int("points_formula_a", 800))
    c = max(1, _int("points_formula_c", 15))
    rng = max(1, _int("points_range", 5))
    vol = max(1, _int("points_volatility", 10))
    diff = (rival_points - my_points) if win else (my_points - rival_points)
    swing = int(round(diff * rng / float(a)))
    if win:
        return max(1, min(vol, c + swing))
    return -max(1, min(vol, c - swing))


def _day_key(now: float | None = None) -> str:
    """按 `CHALLENGE_RESET_HOUR`（05:00）切分的"今天"（`YYYY-MM-DD`）。

    跨天就把 `arenaInfo.change`（剩余挑战次数）重置回 `default_change`。
    """
    t = time.localtime((time.time() if now is None else now) - CHALLENGE_RESET_HOUR * 3600)
    return time.strftime("%Y-%m-%d", t)


def _grade(value: float, thresholds, letters=None) -> str:
    """按**从好到坏**的阈值表给一个评价字母。

    `thresholds` = `[(上限, 字母)]`，从好到坏；`value` 越小越好时直接用，
    越大越好时把 thresholds 反过来传（见 `score_info`）。
    """
    for limit, letter in thresholds:
        if value <= limit:
            return letter
    return thresholds[-1][1] if thresholds else "d"


def score_info(combat_time: int, death: int, rival_points: int, my_points: int) -> dict:
    """结算面板三行的评价字母（`ArenaWinLayer` 用它挑 `res/icon/arenascore/*.png`）。

    三行的标题（从 `arenawinlayer.csb` 里读出来的）：
        `combatTime` → 战斗用时   `death` → 人员伤亡   `rank` → **对手积分**
    （`rank` 这个名字有误导性，它显示的是对手积分，值来自 `rival.points`）。

    字母取值范围 = `a|b|c|d|s|ss|sss`（客户端类常量 `ARENA_SCORE_RES` 只有这 7 个）。
    ⚠️ **评分规则是自己定的**（原版无从考证，见 differences.md §D）：用时越短/伤亡越少
    越好，对手积分比自己高越多越好。客户端只在**赢了**的时候才画这几个图标。
    """
    time_grade = _grade(max(0, int(combat_time)),
                        [(30, "sss"), (45, "ss"), (60, "s"), (90, "a"),
                         (120, "b"), (180, "c")])
    death_grade = _grade(max(0, int(death)),
                         [(0, "sss"), (1, "ss"), (2, "s"), (3, "a"), (4, "b"), (5, "c")])
    diff = int(rival_points) - int(my_points)
    point_grade = _grade(-diff,
                         [(-200, "sss"), (-100, "ss"), (-50, "s"), (0, "a"),
                          (100, "b"), (250, "c")])
    return {"combatTime": time_grade, "death": death_grade, "rank": point_grade}


# ---------------------------------------------------------------------------
# 存档
# ---------------------------------------------------------------------------
def state(player: dict) -> dict:
    """`player["arena"] = {"info": {...}, "rivals": [...], "lastManualRefresh": 秒}`。

    `info` 里客户端**只读 5 个键**（`points` / `change` / `wins` / `rating` /
    `refreshTime`），其余（`changeDay`）是服务端自己记的。
    """
    st = player.get(PLAYER_KEY)
    if not isinstance(st, dict):
        st = {}
        player[PLAYER_KEY] = st
    info = st.get("info")
    if not isinstance(info, dict):
        info = {}
        st["info"] = info
    lo = _int("min_arena_points", 100)
    hi = _int("max_arena_points", 10000)
    try:
        points = int(info.get("points", _int("default_arena_points", 300)))
    except (TypeError, ValueError):
        points = _int("default_arena_points", 300)
    info["points"] = max(lo, min(hi, points))
    info["rating"] = rating_of(info["points"])
    try:
        info["wins"] = int(info.get("wins") or 0)          # 当前连胜（输了归零）
    except (TypeError, ValueError):
        info["wins"] = 0
    # `change` = **今日剩余挑战次数**（不是积分变化！见 points_change 的注释）
    daily = max(0, _int("default_change", 8))
    try:
        info["change"] = int(info.get("change", daily))
    except (TypeError, ValueError):
        info["change"] = daily
    today = _day_key()
    if str(info.get("changeDay") or "") != today:
        info["changeDay"] = today
        info["change"] = daily
    info["change"] = max(0, min(daily, info["change"]))
    try:
        info["refreshTime"] = int(info.get("refreshTime") or 0)
    except (TypeError, ValueError):
        info["refreshTime"] = 0
    try:
        st["lastManualRefresh"] = int(st.get("lastManualRefresh") or 0)
    except (TypeError, ValueError):
        st["lastManualRefresh"] = 0
    if not isinstance(st.get("rivals"), list):
        st["rivals"] = []
    return st


def info_view(st: dict) -> dict:
    """登录块 / 回包里的 `arenaInfo` —— **只发客户端真读的那 5 个键**。

    ⚠️ `change` 是**今日剩余挑战次数**（`default_change` = 8，跨 05:00 重置），
    客户端在 `change <= 0` 时会让「挑战」「刷新对手」两个按钮直接 toast
    「木有挑战次数了！」（`table_dictionary[1602]`）——所以它必须是正数才点得动。
    """
    info = st["info"]
    return {
        "points": info["points"],
        "change": info["change"],
        "wins": info["wins"],
        "rating": info["rating"],
        # ⚠️ 两份都要有：ctor 读顶层，updateByServer 读 arenaInfo 里的（见模块注释）
        "refreshTime": info["refreshTime"],
    }


# ---------------------------------------------------------------------------
# 对手
# ---------------------------------------------------------------------------
def _soldier_key(text) -> str:
    """NPC 表里的 `"scyy010104#1#30#1"` → `"scyy010104"`。"""
    return str(text or "").split("#")[0].strip()


def soldier_text_ok(text) -> bool:
    """客户端 `charManager.decodeSoldier(str)` 只认 `"key#星级#等级#技能等级"`：

        str.split("#") 之后 **length < 4 直接 cc.warn + 返回 undefined**
        （反汇编：`charManager.decodeSoldier error, param not enough`）

    也就是说 `rival.soldier<i>` 必须是**整个编码串**，不能只发 key ——
    只发 key 的话 `ArenaCenter._init` 里 `decodeSoldier` 全返回 undefined，
    对手的 `soldiers[]` 是空的，详情页一个头像都没有（而且战斗里敌方阵容也是空的）。
    """
    return len(str(text or "").split("#")) >= 4


def _points_for_rating(rating: int, my_points: int) -> int:
    """按段位造一个对手积分：段位区间内、尽量靠近我的积分（±波动）。"""
    lo = rating_threshold(rating)
    hi = max(lo, rating_threshold(rating + 1) - 1)
    if rating >= max_rating():
        hi = _int("max_arena_points", 10000)
    base = min(max(my_points, lo), hi)
    spread = max(0, _int("points_volatility", 10) * _int("points_range", 5))
    return max(lo, min(hi, base + random.randint(-spread, spread)))


def make_rivals(player: dict, count: int | None = None) -> list:
    """随机造一批对手：从 `table_friend_support_npc`（101 个 NPC）里挑。

    每个 NPC 自带 5 个军士（general/brave/armor/biological/agent，值是
    `"key#星级#等级#技能等级"` 编码串）和 `player_lv`、`player_name` ——
    正好就是对手要的东西，所以名字/军士全部照抄，只自己造段位和积分（表里没有这些）。

    等级：优先挑和玩家等级接近的 NPC（±6 级内，不够再放宽），
    这样「对手等级」看起来符合 `robot_rival_lv_range_mode*` 的意图。
    """
    info = state(player)["info"]
    count = rival_count() if count is None else max(1, int(count))
    try:
        my_lv = int(player.get("lv") or 1)
    except (TypeError, ValueError):
        my_lv = 1
    pool = []
    for key, row in npc_table().items():
        if not isinstance(row, dict):
            continue
        # ⚠️ 这里要的是**整个编码串**（"key#星级#等级#技能等级"），不是 key ——
        # 客户端 `ArenaCenter._init` 拿它喂 `charManager.decodeSoldier()`。
        soldiers = [str(row.get(f) or "").strip() for f in RIVAL_SOLDIER_FIELDS]
        soldiers = [s for s in soldiers if s and soldier_text_ok(s)]
        if len(soldiers) < 3:
            continue
        try:
            lv = int(row.get("player_lv") or 1)
        except (TypeError, ValueError):
            lv = 1
        pool.append((abs(lv - my_lv), key, row, soldiers, lv))
    if not pool:
        log.warning("table_friend_support_npc 没抽到（跑 script/extract_client_tables.py）")
        return []
    pool.sort(key=lambda x: x[0])
    near = [x for x in pool if x[0] <= 6] or pool
    random.shuffle(near)
    picked = near[:count]
    rivals = []
    for i, (_d, key, row, soldiers, lv) in enumerate(picked):
        rating = max(1, min(max_rating(), info["rating"] + random.choice((-1, 0, 0, 1))))
        rival = {
            "index": i,
            "name": str(row.get("player_name") or key),
            "lv": lv,
            "rating": rating,
            "points": _points_for_rating(rating, info["points"]),
            "headId": 0,                      # 0 → 客户端退回用 asstKey 画头像
            # ⚠️ `asstKey` 要发**军士卡 key**（"sgnw010104" 这种），不是角色 key：
            #    客户端拿它走 `new ItemIcon(asstKey)` → `Shop.getTypeById(key)`，
            #    而 getTypeById 只认 table_item / table_soldier / table_mecha /
            #    table_hero / table_equipment 的 key，角色 key（"sgnw"）会让它
            #    `cc.log("key is error")` 并且画不出头像（实机量到 8 个对手各报一次）。
            "asstKey": _soldier_key(soldiers[0]),
            "state": RIVAL_STATE_READY,
        }
        for n, text in enumerate(soldiers[:5]):
            # 整个 "key#星级#等级#技能等级" 串，见 soldier_text_ok 的注释
            rival["soldier%d" % (n + 1)] = text
        rivals.append(rival)
    return rivals


def ensure_fresh(player: dict, now: float | None = None) -> dict:
    """对手列表超过 `update_interval`（7200 秒）、或存档是旧版本格式，就重造一批。"""
    st = state(player)
    now = int(time.time() if now is None else now)
    info = st["info"]
    stale = str(info.get("rivalVersion") or "") != str(RIVAL_VERSION)
    if not st["rivals"] or not info["refreshTime"] or stale:
        st["rivals"] = make_rivals(player)
        info["refreshTime"] = now
        info["rivalVersion"] = RIVAL_VERSION
        for r in st["rivals"]:
            r["state"] = RIVAL_STATE_READY
        log.info("演习场生成 %d 个对手（玩家 %s%s）", len(st["rivals"]), player.get("account"),
                 "，旧格式存档重造" if stale else "")
        return st
    if now - info["refreshTime"] >= update_interval():
        st["rivals"] = make_rivals(player)
        info["refreshTime"] = now
        info["rivalVersion"] = RIVAL_VERSION
        for r in st["rivals"]:
            r["state"] = RIVAL_STATE_READY
        log.info("演习场自动换了一批对手（间隔 %d 秒）", update_interval())
    return st


def rival_by_index(st: dict, index) -> dict | None:
    try:
        want = int(index)
    except (TypeError, ValueError):
        return None
    for r in st["rivals"]:
        try:
            if int(r.get("index")) == want:
                return r
        except (TypeError, ValueError):
            continue
    return None


def block(player: dict) -> dict:
    """登录块 / 回包里的 `arena` 块（`ArenaCenter.ctor` 与 `updateByServer` 都吃这个）。

    ⚠️ **不发 `mechaSuperSkillCorrectOwn`**：客户端全库 797 个 `.jsc` 里搜不到这个键
    （`jsc_find --exact mechaSuperSkillCorrectOwn` 0 命中），`ArenaCenter.ctor` 也只读
    `arenaInfo` / `rivals` / `resetTime` / `refreshTime` 四个。演习场的机甲超必杀走的是
    客户端**本地静态表** `table_arena_mecha_super_skill_correct_own`
    （唯一消费者 `Mecha.loadArenaSuperSkill`，键 `"<mechaKey>#<superSkillLv>"`）。
    """
    st = ensure_fresh(player)
    return {
        "arenaInfo": info_view(st),
        "rivals": st["rivals"],
        "resetTime": reset_time(),
        "refreshTime": st["info"]["refreshTime"],
    }


# ---------------------------------------------------------------------------
# 路由业务体（都返回完整响应 dict；回包统一带 `data.arena` 给 RESP-DISPATCH）
# ---------------------------------------------------------------------------
def get_rival_list(player: dict) -> dict:
    """`arena.getrivallist {}`。

    客户端 cb 不带参数 —— 数据是靠响应里的 `data.arena` 走 RESP-DISPATCH
    （`arena -> arenaCenter.updateByServer`）落回去的，所以**必须**带这个块。
    """
    st = ensure_fresh(player)
    log.info("arena.getrivallist 玩家 %s：%d 个对手，积分 %s 段位 %s",
             player.get("account"), len(st["rivals"]),
             st["info"]["points"], st["info"]["rating"])
    return {"code": CODE_OK, "msg": "", "data": {"arena": block(player)}}


def reset_rivals(player: dict, use_gold) -> dict:
    """`arena.resetrivals {useGold}` —— 手动换一批对手。

    规则（照客户端按钮的判定反推）：
      * 冷却中（`refresh_cooldown` = 120 秒内）客户端会先弹
        `table_dictionary[1611]`「是否消耗 N 金条立刻刷新所有对手？」，点确定才发
        `useGold=true`；冷却过了发 `useGold=false`（免费）。
      * 花钱那种要扣 `refresh_cost` = 5 —— **扣萌钞（`ITEM_KEY.MONEY` = 100002）**：
        客户端查的就是 `bag.getItemCount(ITEM_KEY.MONEY) < refresh_cost` → `goldTip()`，
        扣金条的话玩家能白刷（1611 的文案写的是"金条"，和它自己的判定不一致 ——
        原版客户端的小矛盾，以判定为准）。
      * 失败用非 200：客户端不看 `msg`，它弹的是 **`table_dictionary[1609]`**
        （「冷却未结束」），所以只在冷却/货币不够这类情形回非 200。
    """
    st = state(player)
    now = int(time.time())
    cooling = (now - st["lastManualRefresh"]) < refresh_cooldown()
    cost = refresh_cost()
    if cooling and not use_gold:
        return {"code": CODE_PARAM_ERROR, "msg": "冷却中，需要花费萌钞刷新",
                "data": {"code": CODE_PARAM_ERROR}}
    if cooling and use_gold:
        if items.count_of(player, MONEY_KEY) < cost:
            return {"code": CODE_NO_MONEY, "msg": "萌钞不足",
                    "data": {"code": CODE_NO_MONEY}}
        items.sub_item(player, MONEY_KEY, cost)
    st["rivals"] = make_rivals(player)
    st["info"]["refreshTime"] = now
    st["lastManualRefresh"] = now
    for r in st["rivals"]:
        r["state"] = RIVAL_STATE_READY
    log.info("arena.resetrivals 玩家 %s：换了 %d 个对手（useGold=%s 花 %s）",
             player.get("account"), len(st["rivals"]), bool(use_gold),
             cost if (cooling and use_gold) else 0)
    return {"code": CODE_OK, "msg": "", "data": {"arena": block(player)}}


def enter_fight(player: dict, index) -> dict:
    """`arena.enterfight {index}` —— 客户端随后自己跑 BattleScene（战斗在客户端算）。

    这里只做一件事：确认对手存在（顺手把过期的列表刷掉），回包带 `data.arena`
    让界面保持最新。真正结算在 `exit_fight`。
    """
    st = ensure_fresh(player)
    rival = rival_by_index(st, index)
    if rival is None:
        log.warning("arena.enterfight 没有 index=%s 这个对手", index)
        return {"code": CODE_NO_RIVAL, "msg": "对手不存在", "data": {}}
    if int(st["info"]["change"]) <= 0:
        # 客户端按钮自己会挡住（toast 1602「木有挑战次数了！」），走到这儿只能是改包/并发
        log.warning("arena.enterfight 玩家 %s 今日挑战次数已用完（change=0）",
                    player.get("account"))
    log.info("arena.enterfight 玩家 %s → index=%s（%s lv%s 段位%s）",
             player.get("account"), index, rival.get("name"),
             rival.get("lv"), rival.get("rating"))
    return {"code": CODE_OK, "msg": "", "data": {"arena": block(player)}}


def exit_fight(player: dict, index, success, battle_info) -> dict:
    """`arena.exitfight {index, success, battleInfo}` —— 结算。

    请求体（客户端 `_requestExitFight` 拼的）：
        {index, success, battleInfo: {combatTime, ownSoldierDiedCount}}

    ⚠️ `combatTime` **是秒**（不是毫秒）：实机对过一场 26 秒的战斗，客户端发上来的
    就是 `combatTime: 23`。而结算面板里 `ArenaWinLayer` 会 `battleData.combatTime * 1000`
    再喂 `op.getCountdownStr()`（那个函数收毫秒）——所以服务端**原样把秒发回去**就对了。

    回包 `data` 里除了 `arena` 块（给 RESP-DISPATCH），还要**平铺**下面这些，
    因为 `ArenaLayer._fightResult(err, data)` 直接读它们：

        success     服务端确认的胜负（客户端 `result.win`）
        rewards     普通奖励 `[{type,key,count}]`（客户端按 REWARD_TYPE 拆成 cards/items；
                    type 2 = ITEM 和客户端的 `REWARD_TYPE.ITEM` 是同一个值）
        winsRewards 连胜奖励（走 `popupReward(util.objectToArray(...))`，发 `[]` 就不弹）
        winPoints   这一场的**积分增减**（结算面板第一行的数字）
        battleData  结算面板三行：`combatTime` 战斗用时(秒) / `death` 人员伤亡 /
                    **`rank` 对手积分**（名字有误导性，`arenawinlayer.csb` 里的标题是
                    「对手积分」，值给 `rival.points`）——缺一个 `setString(undefined)`
                    会抛 `js_cocos2dx_ui_Text_setString : Error processing arguments`
                    把整个面板打断（玩家就卡在战斗结束、退不出来，2026-09-20 实机踩过）
        scoreInfo   上面三行各自的评价字母（`a|b|c|d|s|ss|s`，只有赢了才画）

    胜负的影响：赢 → 连胜 +1、该对手 `state = 1`（客户端 1603「已经战胜过他了呢~」）；
    输 → 连胜归零、对手 `state` 保持 0（可以再挑战）。两种情况都扣 1 次挑战次数
    （`change`），因为客户端按钮的门槛就是它。

    ⚠️ **重复打不过分拒绝**：结算面板上有「再来一次」，客户端会拿同一个 index 再进一次
    战斗；这里要是回非 200，`_fightResult(err)` 直接 return，用户会卡在战斗结束、
    什么都不弹（和上面那个 setString 崩溃是同一个表现）。所以重复上报照样算积分/给萌币
    —— 反正打一次要真打完一场战斗，私服不差这点（见 differences.md §B）。
    """
    st = ensure_fresh(player)
    rival = rival_by_index(st, index)
    if rival is None:
        return {"code": CODE_NO_RIVAL, "msg": "对手不存在", "data": {}}

    info = st["info"]
    win = bool(success)
    before_points = info["points"]
    rival_points = int(rival.get("points") or 0)
    delta = points_change(before_points, rival_points, win)
    lo = _int("min_arena_points", 100)
    hi = _int("max_arena_points", 10000)
    info["points"] = max(lo, min(hi, before_points + delta))
    win_points = info["points"] - before_points
    info["wins"] = (info["wins"] + 1) if win else 0
    info["rating"] = rating_of(info["points"])
    if win:
        rival["state"] = RIVAL_STATE_WON
    info["change"] = max(0, int(info["change"]) - 1)      # 扣一次挑战次数

    rewards = pvp_rewards() if win else fail_rewards()
    known = {str(k) for k in items.items_of(player)}
    granted = items.settle(player, list(rewards))
    log.info("arena.exitfight 玩家 %s vs %s：%s，积分 %s→%s（%+d），连胜 %s，"
             "挑战次数还剩 %s，发 %s",
             player.get("account"), rival.get("name"), "胜" if win else "负",
             before_points, info["points"], win_points, info["wins"],
             info["change"], granted.get("items"))

    battle_info = battle_info if isinstance(battle_info, dict) else {}
    combat_time = int(battle_info.get("combatTime") or 0)          # 秒（见 docstring）
    death = int(battle_info.get("ownSoldierDiedCount") or 0)
    data = {
        "arena": block(player),
        "items": items.changed_block(player, known),
        "success": 1 if win else 0,
        "rewards": [{"type": t, "key": k, "count": c} for (t, k, c) in rewards],
        "winsRewards": [],
        "winPoints": win_points,
        "scoreInfo": score_info(combat_time, death, rival_points, before_points),
        "battleData": {
            "combatTime": combat_time,     # 秒 —— 客户端面板会 ×1000
            "death": death,                # 「人员伤亡」
            "rank": rival_points,          # 「对手积分」（字段名叫 rank）
        },
    }
    return {"code": CODE_OK, "msg": "", "data": data}
