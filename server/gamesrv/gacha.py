"""抽卡（`gacha.*`）—— 客户端没有卡池表，内容全在服务端这一侧。

## 为什么这块要"造内容"

`assets/src/table/` 里 176 张表**一张 gacha 的都没有**（`jsc_find table_gacha*` 0 命中）：
「有哪些池子、消耗什么、概率多少、能出哪些卡」全是**运营配置**，原版从服务端下发，
随停服一起没了。所以这里自己造一套池子，但**池子 id 和枚举严格照客户端**：

    config/gachaconfig.jsc：
        GACHA_TYPE    = {10:FREE, 20:GEM, 30:FRAGMENT, 40:TIME_LIMIT, 50:WELFARE, 60:COMMON}
        GACHA_KEYS    = {FREE:"1001", GEM:"1002", FRAGMENT:"1003"}
        GACHA_NAMES   = {1001:"免费抽卡", 2001:"碎片单抽", 2010:"碎片十连",
                         4001:"钻石单抽", 4010:"钻石十连"}
        GACHA_FORM    = {DEFAULT:"0", TIMES_CHANGEABLE:"1"}
        GACHA_SALE_TYPE = {TOTAL_TIMES, DAILY_TIMES, WEEKLY_TIMES, MONTHLY_TIMES}
        SOLDIER_S_QUALITY = 3 / SOLDIER_SR_QUALITY = 4
        GACHA_ERROR_DICT = {201 参数错误 / 202 找不到抽卡信息 / 203 军士库满员 /
                            204 次数用完 / 205 倒计时中 / 206 资源不够 / 207 资源错误 /
                            210 非活动期 / 211/212/213 次数用完 / 405 更新数据错误}

  也就是说**池子 id 我不用编**：`GACHA_NAMES` 里那 5 个就是原版的池子 key。

## 卡池内容（从客户端表里挑）

    `table_soldier` + `table_soldier_master[charKey].card_type == 1` → 自军卡 **152 张**，
        正好是每个角色的 4 档卡：quality 1/2/3/4（`template` 就是档位），
        q3 = S、q4 = SR（`SOLDIER_S/SR_QUALITY`）。
    `table_hero`（2 个，都带 `gacha_name`）/ `table_mecha`（6 个，同样）→ 大奖。
    `table_item` → 池子消耗（金条 100001 / 好人卡 = `ITEM_KEY.GACHA_FRAGMENT` 100016）。

## 概率 / 保底 / 消耗

**全是自己定的**（原版运营配置无从考证，见 `differences.md` §B/§D）：见下面 `POOLS`
和 `RARITY_WEIGHT`，都是单旋钮。

## 客户端那条链（读响应的地方）

    Gacha.update(data)          ← 登录块 `data.gacha`：{gachaData, gachaInfoList, gachaMasterList}
    Gacha.gacha(key, times)     → `gacha.gacha {key, times}`，回包读 `data.gachaData` + `cards`
    Gacha.getLibraryShow(key)   → `gacha.getlibraryshow {key}`，回包读 `data.{cards, upRate, gachaInfo}`
    Gacha.updateGachaInfo()     → `gacha.getgacha`，回包**扁平**给 update() 那五件套

    卡池 master 形状（反汇编 `Gacha._getGachaInfoByMaster` / `getGachaSale` / `getGachaFullInfo`）：
        master = {key, name, form, infoKeysObj | infoKeysArr, saleTypeObj, …}
            form == GACHA_FORM.DEFAULT("0")  → 用 `infoKeysObj[times]` 找 infoKey
            form == TIMES_CHANGEABLE("1")    → 用 `infoKeysArr[min(times-1, len-1)].infoKey`
        info   = {itemKey, itemCount, voucherKey, voucherCount, useVoucher,
                  receiveKey, receiveCount, saleInfoObj:{saleTimes, sale, defaultSale}}
            —— `itemKey/itemCount` 就是"抽一次花什么、花多少"，
               `Gacha.getGachaConsume` = `itemCount * sale / 100`（sale 是百分比折扣）。
"""

from __future__ import annotations

import random
import time

from . import items, logx, store

log = logx.get("gacha")

PLAYER_KEY = "gacha"

CODE_OK = 200
# 客户端 `GACHA_ERROR_DICT` 的码（回非 200 会弹对应文案）
ERR_PARAM = 201          # 参数错误
ERR_NO_INFO = 202        # 找不到抽卡信息
ERR_SOLDIER_FULL = 203   # 军士库满员了哟~快去整理一下！
ERR_TIMES_OUT = 204      # 次数用完了呢~明天趁早哟~
ERR_CD = 205             # 时间还在倒数哟亲~
ERR_NO_RESOURCE = 206    # 资源不够啦……OAQ
ERR_RESOURCE = 207       # 资源错误
ERR_NOT_ACTIVITY = 210   # 现在不是活动期
ERR_TOTAL_TIMES = 213    # 抽卡次数已用完
ERR_DB = 405             # 更新数据错误

GEM_KEY = "100001"       # 金条
FRAGMENT_KEY = "100016"  # 好人卡（`ITEM_KEY.GACHA_FRAGMENT`）

# 卡池里能出的东西（客户端 `REWARD_TYPE`）
TYPE_ITEM = "2"
TYPE_HERO = "3"
TYPE_MECHA = "4"
TYPE_SOLDIER = "5"

# 稀有度档：军士卡的 quality（1..4，q3 = S / q4 = SR）
RARITY_BY_QUALITY = {1: "n", 2: "r", 3: "s", 4: "sr"}
RARITY_ORDER = ("n", "r", "s", "sr")
SR_RARITY = ("s", "sr")          # 十连保底至少要抽到这一档

# 单抽各档权重（千分比）。**自己定的**，见模块注释。
RARITY_WEIGHT = {"n": 560, "r": 300, "s": 110, "sr": 30}
# sr 那一档里再抽一次"是不是大奖（英雄/机甲）"，千分比。
PRIZE_WEIGHT = 150               # 15% 的 sr → 英雄/机甲，其余是 sr 军士卡

# 保底：十连至少一张 S+
TEN_GUARANTEE = "s"


def _pool(key: str) -> dict | None:
    return POOLS.get(str(key))


def _now() -> int:
    return int(time.time())


# ---------------------------------------------------------------------------
# 池子定义（id 照 `GACHA_NAMES`；消耗/概率是自己定的）
# ---------------------------------------------------------------------------
POOLS = {
    # 免费抽卡：**每天 1 次**（`limitTimesObj.d = 1`），不花钱
    # （客户端 `isFreeGacha()` 判定"免费"的依据就是 info 行里 `itemCount`/`voucherCount` 都是 0）
    "1001": {
        "key": "1001", "name": "免费抽卡", "type": "10", "times": 1,
        "dailyLimit": 1, "cost": [], "sort": 1,
    },
    # 碎片（好人卡）单抽 / 十连
    "2001": {
        "key": "2001", "name": "碎片单抽", "type": "30", "times": 1,
        "cost": [(TYPE_ITEM, FRAGMENT_KEY, 1)], "sort": 20,
    },
    "2010": {
        "key": "2010", "name": "碎片十连", "type": "30", "times": 10,
        "cost": [(TYPE_ITEM, FRAGMENT_KEY, 9)],       # 十连打 9 折
        "guarantee": TEN_GUARANTEE, "sort": 21,
    },
    # 钻石（金条）单抽 / 十连
    "4001": {
        "key": "4001", "name": "钻石单抽", "type": "20", "times": 1,
        "cost": [(TYPE_ITEM, GEM_KEY, 100)], "sort": 10,
    },
    "4010": {
        "key": "4010", "name": "钻石十连", "type": "20", "times": 10,
        "cost": [(TYPE_ITEM, GEM_KEY, 900)],          # 十连打 9 折
        "guarantee": TEN_GUARANTEE, "sort": 11,
    },
}


# ---------------------------------------------------------------------------
# 卡池内容（从客户端表里挑）
# ---------------------------------------------------------------------------
_pool_cache: dict | None = None


def card_pool() -> dict:
    """`{稀有度: [军士卡 key, …]}` —— 自军卡按 quality 分档（q3=S / q4=SR）。"""
    global _pool_cache
    if _pool_cache is not None:
        return _pool_cache
    out: dict = {r: [] for r in RARITY_ORDER}
    cards = (items.table("table_soldier") or {}).get("card") or {}
    master = (items.table("table_soldier") or {}).get("master") or {}
    for key, row in cards.items():
        if not isinstance(row, dict):
            continue
        # `master[charKey]` 是 `table_soldier_master[charKey].card_type`（1 = 自军卡）
        if str(master.get(str(row.get("ck") or "")) or "") != "1":
            continue
        try:
            quality = int(row.get("q") or 0)
        except (TypeError, ValueError):
            continue
        rarity = RARITY_BY_QUALITY.get(quality)
        if rarity:
            out[rarity].append(str(key))
    for rarity in out:
        out[rarity].sort()
    _pool_cache = out
    return out


def prize_pool() -> list:
    """大奖：[("3" 英雄 key / "4" 机甲 key), …]。"""
    out = []
    for key in sorted(items.table("table_hero") or {}):
        out.append((TYPE_HERO, str(key)))
    for key in sorted(items.table("table_mecha") or {}):
        out.append((TYPE_MECHA, str(key)))
    return out


# ---------------------------------------------------------------------------
# 抽
# ---------------------------------------------------------------------------
def _roll_rarity(rng: random.Random) -> str:
    total = sum(RARITY_WEIGHT.values())
    roll = rng.randint(1, max(1, total))
    upto = 0
    for rarity in RARITY_ORDER:
        upto += RARITY_WEIGHT.get(rarity, 0)
        if roll <= upto:
            return rarity
    return RARITY_ORDER[0]


def _pick_card(rarity: str, rng: random.Random) -> dict:
    """按稀有度挑一张卡；`sr` 那一档有 `PRIZE_WEIGHT` 概率变成英雄/机甲。"""
    prizes = prize_pool()
    if rarity == "sr" and prizes and rng.randint(1, 1000) <= PRIZE_WEIGHT:
        kind, key = prizes[rng.randrange(len(prizes))]
        return {"type": kind, "key": key, "rarity": "sr"}
    pool = card_pool().get(rarity) or []
    if not pool:
        # 该档没卡（表没抽全）就往下退一档
        for fallback in reversed(RARITY_ORDER):
            pool = card_pool().get(fallback) or []
            if pool:
                rarity = fallback
                break
    if not pool:
        return {"type": TYPE_ITEM, "key": FRAGMENT_KEY, "count": 1, "rarity": "n"}
    return {"type": TYPE_SOLDIER, "key": pool[rng.randrange(len(pool))], "rarity": rarity}


def roll(pool_key: str, times: int, rng: random.Random | None = None) -> list:
    """抽 `times` 次，返回 `[{type, key, rarity}, …]`（**不发货**）。"""
    conf = _pool(pool_key) or {}
    rng = rng or random.Random()
    out = []
    for _ in range(max(1, int(times))):
        out.append(_pick_card(_roll_rarity(rng), rng))
    guarantee = conf.get("guarantee")
    if guarantee and out and not any(c["rarity"] in SR_RARITY for c in out):
        # 保底：把最后一张换成至少 S 档（优先 S，其次 SR）
        pool = card_pool().get(guarantee) or card_pool().get("s") or []
        if pool:
            out[-1] = {"type": TYPE_SOLDIER,
                       "key": pool[rng.randrange(len(pool))], "rarity": guarantee}
    return out


# ---------------------------------------------------------------------------
# 玩家状态（次数）
# ---------------------------------------------------------------------------
def state(player: dict) -> dict:
    """`player["gacha"] = {<dataKey>: {masterKey, todayTimes, lastGachaTimeSec, totalTimes}}`。

    dataKey 照客户端 `Gacha.getGachaDataKey` 算（见 `data_key()`）；
    客户端自己也会 `resetData()` 按 `table_constant.common_reset_time` 清 `todayTimes`，
    服务端这份才是真存档，两边都得清（和 sign/arena 一个套路）。
    """
    st = player.get(PLAYER_KEY)
    if not isinstance(st, dict):
        st = {}
        player[PLAYER_KEY] = st
    return st


def data_key(pool_key: str, times: int) -> str:
    """客户端 `Gacha.getGachaDataKey`：`form == TIMES_CHANGEABLE("1")` 直接用 masterKey，
    否则 `masterKey + ("0" + times)`（times < 10 补一个 0，我们所有池子都是这一种）。"""
    times = int(times)
    return pool_key if times < 0 else pool_key + ("0%d" % times if times < 10 else str(times))


def reset_daily(player: dict, pool_key: str, times: int) -> dict:
    """跨天把 `todayTimes` 清掉（05:00 口径，跟签到/演习场一致）。"""
    row = state(player).setdefault(data_key(pool_key, times), {})
    if not isinstance(row, dict):
        row = {}
        state(player)[data_key(pool_key, times)] = row
    row["masterKey"] = str(pool_key)
    today = time.strftime("%Y-%m-%d", time.localtime(_now() - 5 * 3600))
    if str(row.get("todayDay") or "") != today:
        row["todayDay"] = today
        row["todayTimes"] = 0
    for field in ("todayTimes", "totalTimes"):
        try:
            row[field] = int(row.get(field) or 0)
        except (TypeError, ValueError):
            row[field] = 0
    try:
        row["lastGachaTimeSec"] = int(row.get("lastGachaTimeSec") or 0)
    except (TypeError, ValueError):
        row["lastGachaTimeSec"] = 0
    return row


def info_key(pool_key: str, times: int) -> str:
    return "info_%s_%d" % (pool_key, int(times))


def info_rows() -> dict:
    """`gachaInfoList`：infoKey → 消耗行（客户端 `_gachaInfoObj`）。

    字段是反汇编出来的：`itemKey/itemCount`（抽一次花什么、花多少；都为 0 时
    客户端 `isFreeGacha()` 判定为免费池）、`voucherKey/voucherCount/useVoucher`（代金券）、
    `receiveKey/receiveCount`、`limitTimesObj`（`{d:每日, a:活动, t:总计}`，
    对应 `GACHA_TIMES_LIMIT = {DAILY:"d", ACTIVITY:"a", TOTAL:"t"}`）、
    `saleInfoObj`（**按次数索引**的折扣表：`saleInfoObj[第几次]` 是那一次的折扣百分比，
    `saleInfoObj[0]` 是基准价，100 = 原价；客户端 `getGachaSale` 就是
    `saleInfoObj[次数] || saleInfoObj[0] || 100`）、`saleTypeObj`（可选，决定按
    总计/每日/周期哪个次数去索引）。

    ⚠️ 一开始把 `saleInfoObj` 写成了 `{saleTimes, sale, defaultSale}` 对象 ——
    客户端取的是 `saleInfoObj[次数]`，拿不到就是 undefined → `getGachaConsume()` 算出
    `itemCount * undefined / 100 = NaN` → 「价格」显示不出来（实机量到 `consume: null`）。
    """
    out = {}
    for key, conf in POOLS.items():
        times = int(conf["times"])
        cost = conf.get("cost") or []
        item_key = cost[0][1] if cost else ""
        item_count = int(cost[0][2]) if cost else 0
        limit = {"d": int(conf["dailyLimit"])} if conf.get("dailyLimit") else {}
        out[info_key(key, times)] = {
            "itemKey": str(item_key),
            "itemCount": item_count,
            "voucherKey": "",
            "voucherCount": 0,
            "useVoucher": 0,
            "receiveKey": "",
            "receiveCount": 0,
            "limitTimesObj": limit,
            "saleInfoObj": {"0": 100},
            "saleTypeObj": {"type": "TOTAL_TIMES"},
            "freeInterval": "",
        }
    return out


def master_rows() -> dict:
    """`gachaMasterList`：池子 key → master（客户端 `_gachaMasterObj`）。

    字段（反汇编 `_getGachaInfoByMaster` / `getGachaSale` / `getGachaFullInfo` /
    `_checkGachaVaild`）：`key` / `name` / `type`(GACHA_TYPE) / `form`(GACHA_FORM，
    我们全用 "0" DEFAULT) / `infoKeysObj`（`{times: infoKey}`）/ `saleTypeObj`。
    没有 `startTime`/`endTime` 就不是活动池；`type != 40`（TIME_LIMIT）不校验时间
    （`_checkGachaVaild` 直接 true）→ 池子一定显示得出来。
    """
    out = {}
    for key, conf in POOLS.items():
        times = int(conf["times"])
        out[str(key)] = {
            "key": str(key),
            "name": str(conf["name"]),
            "type": str(conf["type"]),
            "form": "0",                                  # GACHA_FORM.DEFAULT
            "infoKeysObj": {str(times): info_key(key, times)},
            "saleTypeObj": {"type": "TOTAL_TIMES"},
            "sort": int(conf.get("sort") or 0),
        }
    return out


def data_rows(player: dict) -> dict:
    """`gachaData`：dataKey → 玩家次数行（客户端 `_gachaDataObj`）。"""
    out = {}
    for key, conf in POOLS.items():
        row = reset_daily(player, key, int(conf["times"]))
        out[data_key(key, int(conf["times"]))] = {
            "masterKey": str(key),
            "todayTimes": row["todayTimes"],
            "lastGachaTimeSec": row["lastGachaTimeSec"],
            "totalTimes": row["totalTimes"],
        }
    return out


def login_block(player: dict) -> dict:
    """登录块 `data.gacha`（`Gacha.update(data)` 读前三个键，都是 **map**）。

    ⚠️ `gachaInfoList` / `gachaMasterList` 是关键：以前只发 `gachaData`/`gachaLibCards`，
    所以客户端一个池子都没有（`getGachaMasterList()` 空 → 界面显示「没有卡池」）。
    """
    return {
        "gachaData": data_rows(player),
        "gachaInfoList": info_rows(),
        "gachaMasterList": master_rows(),
        "gachaLibCards": {},
        "lastUpdateInfoTime": store.now_ms(),
        "freeGachaTip": {},
        "activityTimes": {},
    }


# ---------------------------------------------------------------------------
# 抽卡：校验 → 抽 → 发货
# ---------------------------------------------------------------------------
def _times_list(conf: dict) -> list:
    return [int(conf["times"])]


def consume_of(pool_key: str, times: int) -> list:
    """这一次抽卡要扣什么（`[(type, key, count)]`，空 = 免费）。

    单抽池和十连池是**两个独立的 key**（`GACHA_NAMES` 里 4001/4010 分开列），
    所以消耗直接取池子自己的配置，不用按次数乘。
    """
    conf = _pool(pool_key) or {}
    return [(typ, key, int(count)) for (typ, key, count) in (conf.get("cost") or [])]


def grant(player: dict, cards: list) -> dict:
    """发货：军士卡进名单（`items.settle` 的 SOLDIER 分支），道具进背包，
    英雄/机甲进 `player["heros"]`/`player["mechas"]`（客户端 `CharCenter.addHeros/addMechas`）。"""
    out = {"items": {}, "soldiers": [], "heros": [], "mechas": []}
    for card in cards:
        typ = card.get("type")
        key = str(card.get("key") or "")
        if not key:
            continue
        if typ == TYPE_SOLDIER:
            res = items.settle(player, [(TYPE_SOLDIER, key, 1)])
            out["soldiers"].extend(res.get("soldiers") or [])
            if not res.get("soldiers"):
                out["items"][key] = out["items"].get(key, 0) + 1
        elif typ == TYPE_HERO:
            if store.add_hero(player, key):
                out["heros"].append(key)
        elif typ == TYPE_MECHA:
            if store.add_mecha(player, key):
                out["mechas"].append(key)
        else:
            count = int(card.get("count") or 1)
            items.add_item(player, key, count)
            out["items"][key] = out["items"].get(key, 0) + count
    return out


def card_view(card: dict) -> dict:
    """`cards` 数组里的一个元素（客户端拿去播翻牌动画）。

    `type` 用客户端的 `REWARD_TYPE`（2 道具 / 3 英雄 / 4 机甲 / 5 军士），
    `key` 是要发的东西的 key；军士卡再带一个 `quality`（`table_soldier.card[key].q`），
    因为动画是按类型 + 品质挑特效的（见 `gachaconfig.GACHA_EFFECT_FILE`：
    HERO/MECHA/SKILL/EXP 各有 S/SR 档特效）。
    """
    out = {"type": card.get("type"), "key": card.get("key")}
    if card.get("count"):
        out["count"] = int(card["count"])
    if card.get("type") == TYPE_SOLDIER:
        row = ((items.table("table_soldier") or {}).get("card") or {}).get(str(card["key"])) or {}
        out["quality"] = int(row.get("q") or 0)
    return out


def draw(player: dict, pool_key, times) -> dict:
    """`gacha.gacha {key, times}` 的业务体。

    失败回 `GACHA_ERROR_DICT` 里的码（客户端按码弹文案），成功回 200 +
    `{gachaData, cards, itemKey, extraReward, gemGachaTimes}`（客户端 `Gacha.gacha/<` 读的）。
    """
    pool_key = str(pool_key or "")
    conf = _pool(pool_key)
    if conf is None:
        log.warning("gacha.gacha 没有这个池子 key=%s", pool_key)
        return {"code": ERR_NO_INFO, "msg": "找不到抽卡信息", "data": {}}
    try:
        times = int(times or conf["times"])
    except (TypeError, ValueError):
        return {"code": ERR_PARAM, "msg": "参数错误", "data": {}}
    if times not in _times_list(conf):
        # 池子只有固定次数（单抽池 / 十连池各是一个 key）
        times = int(conf["times"])

    row = reset_daily(player, pool_key, times)
    limit = conf.get("dailyLimit")
    if limit and row["todayTimes"] + 1 > int(limit):
        log.info("gacha.gacha %s 今天次数用完（%s/%s）", pool_key, row["todayTimes"], limit)
        return {"code": ERR_TIMES_OUT, "msg": "次数用完了呢~明天趁早哟~", "data": {}}

    cost = consume_of(pool_key, times)
    for (_typ, key, count) in cost:
        if int(count) > 0 and items.count_of(player, key) < int(count):
            log.info("gacha.gacha %s 资源不够：要 %s x%s 有 %s",
                     pool_key, key, count, items.count_of(player, key))
            return {"code": ERR_NO_RESOURCE, "msg": "资源不够啦……OAQ", "data": {}}
    for (_typ, key, count) in cost:
        if int(count) > 0:
            items.sub_item(player, key, int(count))

    cards = roll(pool_key, times)
    known = {str(k) for k in items.items_of(player)}
    granted = grant(player, cards)
    row["todayTimes"] = row["todayTimes"] + 1
    row["totalTimes"] = row["totalTimes"] + 1
    row["lastGachaTimeSec"] = _now()
    log.info("gacha.gacha 池子 %s x%s → 军士 %s / 英雄 %s / 机甲 %s / 道具 %s",
             pool_key, times, granted["soldiers"], granted["heros"],
             granted["mechas"], granted["items"])
    return {
        "code": CODE_OK,
        "msg": "",
        "data": {
            "gachaData": {data_key(pool_key, times): {
                "masterKey": pool_key,
                "todayTimes": row["todayTimes"],
                "lastGachaTimeSec": row["lastGachaTimeSec"],
                "totalTimes": row["totalTimes"],
            }},
            "cards": [card_view(c) for c in cards],
            "itemKey": (cost[0][1] if cost else ""),
            "extraReward": [],
            "gemGachaTimes": row["totalTimes"],
            "items": items.changed_block(player, known),
            "char": char_block(player, granted),
        },
    }


def char_block(player: dict, granted: dict) -> dict:
    """回包里捎给 `CharCenter.updateByServer` 的块（抽到英雄/机甲/军士才带）。"""
    out = {}
    if granted.get("soldiers"):
        out["soldiers"] = store.ensure_soldiers(player)
    if granted.get("heros"):
        out["heros"] = [h for h in store.player_heros(player) if h.get("key") in granted["heros"]]
    if granted.get("mechas"):
        out["mechas"] = [m for m in store.player_mechas(player)
                         if m.get("key") in granted["mechas"]]
    return out


def library_show(player: dict, pool_key) -> dict:
    """`gacha.getlibraryshow {key}` → `{cards, upRate, gachaInfo}`。

    客户端 `updateLibCards(cards, upRate)` 会拿 `cards[].type/quality` 排序
    （`charManager.getCharType` → `CHAR_TYPE.HERO/MECHA`、`getSoldierQuality`），
    所以每张卡要带 `key` + `type`，军士卡带 `quality`。
    """
    conf = _pool(pool_key)
    cards = []
    for _rarity, pool in card_pool().items():
        for card_key in pool:
            row = ((items.table("table_soldier") or {}).get("card") or {}).get(card_key) or {}
            cards.append({"key": card_key, "type": TYPE_SOLDIER,
                          "quality": int(row.get("q") or 0)})
    for (typ, key) in prize_pool():
        cards.append({"key": key, "type": typ, "quality": 5})
    return {"cards": cards, "upRate": {}, "gachaInfo": master_rows().get(str(pool_key)) or {}}

