"""抽卡（`gacha.*`）—— 客户端没有卡池表，内容全在服务端这一侧。

## 为什么这块要"造内容"

`assets/src/table/` 里 176 张表**一张 gacha 的都没有**（`jsc_find table_gacha*` 0 命中）：
「有哪些池子、消耗什么、概率多少、能出哪些卡」全是**运营配置**，原版从服务端下发，
随停服一起没了。所以这里自己造一套池子，但 id / 枚举 / 形状严格照客户端：

    config/gachaconfig.jsc：
        GACHA_TYPE    = {10:FREE, 20:GEM, 30:FRAGMENT, 40:TIME_LIMIT, 50:WELFARE, 60:COMMON}
        GACHA_KEYS    = {FREE:"1001", GEM:"1002", FRAGMENT:"1003"}     ← ★ 池子 id 就是这三个
        GACHA_FORM    = {DEFAULT:"0", TIMES_CHANGEABLE:"1"}
        GACHA_SALE_TYPE = {t:TOTAL_TIMES, d:DAILY_TIMES, w:WEEKLY_TIMES, m:MONTHLY_TIMES}
        GACHA_TIMES_LIMIT = {d:DAILY, a:ACTIVITY, t:TOTAL}
        SOLDIER_S_QUALITY = 3 / SOLDIER_SR_QUALITY = 4
        GUIDE_GACHA_KEY = 1002        ← 新手引导指向钻石池
        GACHA_ERROR_DICT = {201 参数错误 / 202 找不到抽卡信息 / 203 军士库满员 /
                            204 次数用完 / 205 倒计时 / 206 资源不够 / 207 资源错误 /
                            210 非活动期 / 211~213 次数用完 / 405 更新数据错误}

    ⚠️ `GACHA_NAMES`（"1001:免费抽卡 / 2001:碎片单抽 / 2010:碎片十连 / 4001:钻石单抽 /
    4010:钻石十连"）在客户端**全库零引用**（`jsc_find --exact GACHA_NAMES` = 0 命中），
    是张死表，只是原始命名习惯 —— **别照它做池子**（我第一版照它做了 5 个池子，错的）。
    真正的模型是：**3 个 4 位池子 key，各自带「×1 / ×10」两个按钮**
    （`master.infoKeysObj = {"1": …, "10": …}`，`Gacha.getGachaTimes` 就是
    `_.keys(infoKeysObj).sort()`，按钮 1/2 分别取 `[0]`/`[1]`）。

## 客户端那条链（反汇编 + 活客户端实测）

    Gacha.update(data)          ← 登录块 `data.gacha`：{gachaData, gachaInfoList, gachaMasterList}
                                   **三个都是 map**，直接赋给 `_gachaDataObj/_gachaInfoObj/_gachaMasterObj`
    Gacha.gacha(key, times)     → `gacha.gacha {key, times}`；回包读 `result.gachaData`（**那一行**，
                                   不是 map！`updateGachaData(dataKey, result.gachaData)`）
                                   + `result.cards`（**角色 key 字符串数组**）
    Gacha.getLibraryShow(key)   → `gacha.getlibraryshow {key}`；回包 `{cards, upRate}`
                                   ⚠️ `upRate` 必须是**字符串**（`upRate.split("#")`），
                                   没有 UP 就发 `""`（发 `{}` 会 TypeError，图鉴层直接起不来）
    Gacha.updateGachaInfo()     → `gacha.getgacha`，回包**扁平**给 update() 那三件套

    卡池 master 字段（`_getGachaInfoByMaster` / `getGachaSale` / `getGachaFullInfo` /
    `_checkGachaVaild` / `getGachaResIdx` / `getGachaMasterList`）：
        {key, name, type, form, resIdx, showPriority, infoKeysObj | infoKeysArr,
         startTime?, endTime?}
        resIdx 1..54 → `res/ui/gacha/src/gachatypelayer<resIdx>.csb`、
                       `res/icon/gacha/gachabtnidx<resIdx>.png`（**GachaButton 硬 assert**）、
                       `res/bg/gachabg/gachabgidx<resIdx>.png`
        showPriority 决定轮盘顺序（**降序**）
        form=DEFAULT("0") → `_gachaInfoObj[infoKeysObj[times]]`
        form=TIMES_CHANGEABLE("1") → `_gachaInfoObj[infoKeysArr[min(len-1, total)].infoKey]`
        （只有 `type == 40` 才校验 startTime/endTime，普通池不填也永远有效）
    info 行：{itemKey, itemCount, voucherKey, voucherCount, receiveKey, receiveCount,
              limitTimesObj:{d,a,t}, saleInfoObj:{第几次:折扣%}, saleTypeObj:{type}}
        `isFreeGacha()` = itemCount 与 voucherCount 都为 0
        `getGachaConsume()` = itemCount * (saleInfoObj[次数] || saleInfoObj[0] || 100) / 100
    data 行（玩家次数，key = `masterKey + "0"+times`，见 `data_key()`）：
        {masterKey, todayTimes, totalTimes, lastGachaTimeSec, lastFreeTimeSec}

## 卡池内容（从客户端表里挑）

    `table_soldier` + `table_soldier_master[charKey].card_type == 1` → 自军卡 **152 张**，
        正好是每个角色的 4 档卡：quality 1/2/3/4（`template` 就是档位），
        q3 = S、q4 = SR（`SOLDIER_S/SR_QUALITY`）。
    `table_hero`（2 个，都带 `gacha_name`）/ `table_mecha`（6 个，同样）→ 大奖。
    `table_item` → 池子消耗（金条 100001 / 好人卡 = `ITEM_KEY.GACHA_FRAGMENT` 100016）。

## 概率 / 保底 / 消耗

**全是自己定的**（原版运营配置无从考证，见 `differences.md` §B/§D）：见下面 `POOLS`
和 `RARITY_WEIGHT`，都是单旋钮。
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

ONE = "1"
TEN = "10"


def _now() -> int:
    return int(time.time())


# ---------------------------------------------------------------------------
# 池子定义（3 个 4 位 key 照 `GACHA_KEYS`；消耗/概率是自己定的）
# ---------------------------------------------------------------------------
POOLS = {
    # 免费抽卡：**每天 1 次**（`limitTimesObj.d = 1`），不花钱
    # （客户端 `isFreeGacha()` 判定"免费"的依据就是 info 行里 itemCount/voucherCount 都是 0）
    "1001": {
        "key": "1001", "name": "免费抽卡", "type": "10",
        "resIdx": 1, "showPriority": 100, "dailyLimit": 1,
        "cost": {ONE: [], TEN: []},
    },
    # 钻石扭蛋（金条）：×1 100 / ×10 900，十连保底一张 S+
    "1002": {
        "key": "1002", "name": "钻石扭蛋", "type": "20",
        "resIdx": 2, "showPriority": 90,
        "cost": {ONE: [(TYPE_ITEM, GEM_KEY, 100)],
                 TEN: [(TYPE_ITEM, GEM_KEY, 900)]},
        "guarantee": TEN,
    },
    # 碎片扭蛋（好人卡）：×1 1 / ×10 9，十连保底一张 S+
    "1003": {
        "key": "1003", "name": "碎片扭蛋", "type": "30",
        "resIdx": 3, "showPriority": 80,
        "cost": {ONE: [(TYPE_ITEM, FRAGMENT_KEY, 1)],
                 TEN: [(TYPE_ITEM, FRAGMENT_KEY, 9)]},
        "guarantee": TEN,
    },
}


def _pool(key) -> dict | None:
    return POOLS.get(str(key or ""))


def times_list(conf: dict) -> list:
    """这个池子的按钮次数：**永远是 `["1", "10"]`**（客户端按 `_.keys(infoKeysObj).sort()`
    建两个按钮，键只能是字符串 "1"/"10" —— 键会被喂给 `getGachaTimesIconUrl(times)`，
    那里 `times<1 || times>10` 直接报错，而且 `"1","2","10"` 会排成 `["1","10","2"]`）。"""
    return [ONE, TEN]


def data_key(pool_key: str, times) -> str:
    """客户端 `Gacha.getGachaDataKey`：form=0（我们用的一直是）时
    `masterKey + (times < 10 ? "0" + times : times)` ⇒ `1001` + `"01"` = `100101`、
    `1001` + `"10"` = `100110`。"""
    t = str(times)
    return pool_key if t == "-1" else pool_key + ("0" + t if len(t) < 2 else t)


def info_key(pool_key: str, times) -> str:
    return "%s#%s" % (pool_key, times)


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


def roll(pool_key: str, times, rng: random.Random | None = None) -> list:
    """抽 `times` 次，返回 `[{type, key, rarity}, …]`（**不发货**）。"""
    conf = _pool(pool_key) or {}
    rng = rng or random.Random()
    out = []
    for _ in range(max(1, int(str(times) or 1))):
        out.append(_pick_card(_roll_rarity(rng), rng))
    guarantee = conf.get("guarantee")
    if guarantee and str(guarantee) == str(times) and out:
        if not any(c["rarity"] in SR_RARITY for c in out):
            pool = card_pool().get("s") or []
            if pool:
                out[-1] = {"type": TYPE_SOLDIER,
                           "key": pool[rng.randrange(len(pool))], "rarity": "s"}
    return out


# ---------------------------------------------------------------------------
# 玩家状态（次数）
# ---------------------------------------------------------------------------
def state(player: dict) -> dict:
    """`player["gacha"] = {<dataKey>: {masterKey, todayTimes, lastGachaTimeSec, totalTimes}}`。

    dataKey 照客户端 `Gacha.getGachaDataKey` 算（见 `data_key()`）；
    客户端自己也会 `resetData()` 按 `table_constant.common_reset_time`（05:00:00）
    清 `todayTimes`，服务端这份才是真存档，两边都得清（和 sign/arena 一个套路）。
    """
    st = player.get(PLAYER_KEY)
    if not isinstance(st, dict):
        st = {}
        player[PLAYER_KEY] = st
    return st


def reset_daily(player: dict, pool_key: str, times) -> dict:
    """跨天把 `todayTimes` 清掉（05:00 口径，跟签到/演习场一致）。返回那一行。"""
    key = data_key(pool_key, times)
    st = state(player)
    row = st.get(key)
    if not isinstance(row, dict):
        row = {}
        st[key] = row
    row["masterKey"] = str(pool_key)
    today = time.strftime("%Y-%m-%d", time.localtime(_now() - 5 * 3600))
    if str(row.get("todayDay") or "") != today:
        row["todayDay"] = today
        row["todayTimes"] = 0
    for field in ("todayTimes", "totalTimes", "lastGachaTimeSec", "lastFreeTimeSec"):
        try:
            row[field] = int(row.get(field) or 0)
        except (TypeError, ValueError):
            row[field] = 0
    return row


def row_view(row: dict) -> dict:
    """回包 / 登录块里那一行的形状（客户端的字段名）。"""
    return {
        "masterKey": str(row.get("masterKey") or ""),
        "todayTimes": int(row.get("todayTimes") or 0),
        "lastGachaTimeSec": int(row.get("lastGachaTimeSec") or 0),
        "lastFreeTimeSec": int(row.get("lastFreeTimeSec") or 0),
        "totalTimes": int(row.get("totalTimes") or 0),
    }


# ---------------------------------------------------------------------------
# 三份下发的数据
# ---------------------------------------------------------------------------
def info_rows() -> dict:
    """`gachaInfoList`：infoKey → 消耗行（客户端 `_gachaInfoObj`）。

    字段（反汇编 + 实机核对）：`itemKey/itemCount`（抽一次花什么、花多少；都为 0 时
    客户端 `isFreeGacha()` 判定为免费池）、`voucherKey/voucherCount`（代金券）、
    `receiveKey/receiveCount`（额外赠送）、`limitTimesObj`（`{d:每日, a:活动, t:总计}`）、
    `saleTypeObj`（`{type:"t"|"d"|"w"|"m"}`）、`saleInfoObj`（**按次数索引**的折扣表：
    `saleInfoObj[第几次]` 是那次的折扣百分比、`[0]` 是基准价，100 = 原价）、
    `freeInterval`（"HH:MM:SS"，免费 CD，不发就是无 CD）。

    ⚠️ `saleInfoObj` 写成 `{saleTimes, sale, defaultSale}` 那种对象是**错的**：
    客户端取 `saleInfoObj[次数]`，拿不到就是 undefined →
    `getGachaConsume = itemCount * undefined / 100 = NaN` → 界面上价格显示不出来
    （实机量到 `consume: null`）。
    """
    out = {}
    for key, conf in POOLS.items():
        for times in times_list(conf):
            cost = (conf.get("cost") or {}).get(times) or []
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
                "saleTypeObj": {"type": "t"},
                "freeInterval": "",
            }
    return out


def master_rows() -> dict:
    """`gachaMasterList`：池子 key → master（客户端 `_gachaMasterObj`）。

    `resIdx` **必须给**（1..54）：客户端拿它选
    `res/ui/gacha/src/gachatypelayer<resIdx>.csb` + `res/icon/gacha/gachabtnidx<resIdx>.png`
    （按钮图标是**硬 assert**，缺文件直接崩）；`showPriority` 决定轮盘顺序（降序）。
    `infoKeysObj` 的键只能是字符串 "1"/"10"（两个按钮）。
    """
    out = {}
    for key, conf in POOLS.items():
        out[str(key)] = {
            "key": str(key),
            "name": str(conf["name"]),
            "type": str(conf["type"]),
            "form": "0",                                  # GACHA_FORM.DEFAULT
            "resIdx": int(conf.get("resIdx") or 1),
            "showPriority": int(conf.get("showPriority") or 0),
            "infoKeysObj": {t: info_key(key, t) for t in times_list(conf)},
            "saleTypeObj": {"type": "t"},
        }
    return out


def data_rows(player: dict) -> dict:
    """`gachaData`：dataKey → 玩家次数行（客户端 `_gachaDataObj`）。"""
    out = {}
    for key, conf in POOLS.items():
        for times in times_list(conf):
            row = reset_daily(player, key, times)
            out[data_key(key, times)] = row_view(row)
    return out


def login_block(player: dict) -> dict:
    """登录块 `data.gacha`（`Gacha.update(data)` 只读前三个键，都是 **map**）。

    ⚠️ `gachaInfoList` / `gachaMasterList` 是关键：以前只发 `gachaData`，
    所以客户端一个池子都没有（`getGachaMasterList()` 空 → 界面显示「没有卡池」）。
    `gachaLibCards` / `lastUpdateInfoTime` / `activityTimes` 客户端**零引用**
    （libCards 只由 `gacha.getlibraryshow` 灌），所以不再发。
    """
    return {
        "gachaData": data_rows(player),
        "gachaInfoList": info_rows(),
        "gachaMasterList": master_rows(),
    }


# ---------------------------------------------------------------------------
# 抽卡：校验 → 抽 → 发货
# ---------------------------------------------------------------------------
def consume_of(pool_key: str, times) -> list:
    """这一次抽卡要扣什么（`[(type, key, count)]`，空 = 免费）。"""
    conf = _pool(pool_key) or {}
    return [(typ, key, int(count))
            for (typ, key, count) in ((conf.get("cost") or {}).get(str(times)) or [])]


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


def char_block(player: dict, granted: dict) -> dict:
    """回包里捎给 `CharCenter.updateByServer` 的块（抽到英雄/机甲/军士才带）。"""
    out = {}
    if granted.get("soldiers"):
        out["soldiers"] = store.ensure_soldiers(player)
    if granted.get("heros"):
        out["heros"] = [h for h in store.player_heros(player)
                        if h.get("key") in granted["heros"]]
    if granted.get("mechas"):
        out["mechas"] = [m for m in store.player_mechas(player)
                         if m.get("key") in granted["mechas"]]
    return out


def draw(player: dict, pool_key, times) -> dict:
    """`gacha.gacha {key, times}` 的业务体。

    回包（客户端 `Gacha.gacha/<` 读的）：
        gachaData    **那一行**（不是 map！`updateGachaData(dataKey, result.gachaData)`）
        cards        **角色 key 字符串数组**（客户端拿 key 查表画卡、播特效、埋点）
        extraReward  （可选）map，值形如 `{type,key,count}`；不发就不弹
        gemGachaTimes 累计抽数（写进 `player.gemGachaTimes`）
    失败用 `GACHA_ERROR_DICT` 里的码。
    """
    pool_key = str(pool_key or "")
    conf = _pool(pool_key)
    if conf is None:
        log.warning("gacha.gacha 没有这个池子 key=%s", pool_key)
        return {"code": ERR_NO_INFO, "msg": "找不到抽卡信息", "data": {}}
    times = str(times or ONE)
    if times not in times_list(conf):
        times = ONE

    row = reset_daily(player, pool_key, times)
    limit = conf.get("dailyLimit")
    if limit and row["todayTimes"] + 1 > int(limit):
        log.info("gacha.gacha %s x%s 今天次数用完（%s/%s）",
                 pool_key, times, row["todayTimes"], limit)
        return {"code": ERR_TIMES_OUT, "msg": "次数用完了呢~明天趁早哟~", "data": {}}

    cost = consume_of(pool_key, times)
    for (_typ, key, count) in cost:
        if int(count) > 0 and items.count_of(player, key) < int(count):
            log.info("gacha.gacha %s x%s 资源不够：要 %s x%s 有 %s",
                     pool_key, times, key, count, items.count_of(player, key))
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
    if not cost:
        row["lastFreeTimeSec"] = _now()
    log.info("gacha.gacha 池子 %s x%s → 军士 %s / 英雄 %s / 机甲 %s / 道具 %s",
             pool_key, times, granted["soldiers"], granted["heros"],
             granted["mechas"], granted["items"])
    return {
        "code": CODE_OK,
        "msg": "",
        "data": {
            "gachaData": row_view(row),
            "cards": [str(c["key"]) for c in cards],
            "extraReward": {},
            "gemGachaTimes": row["totalTimes"],
            "items": items.changed_block(player, known),
            "char": char_block(player, granted),
        },
    }


def library_show(player: dict, pool_key) -> dict:
    """`gacha.getlibraryshow {key}` → `{cards, upRate}`。

    `cards` 是**角色 key 数组**（客户端 `updateLibCards` 会拿 key 查
    `charManager.getCharType/getSoldierQuality` 排序，再 `createCharCardNode(key)` 画）。
    ⚠️ `upRate` **必须是字符串**（客户端 `upRate.split("#")`）—— 没有 UP 就发 `""`，
    发 `{}` 会 TypeError，整个图鉴层起不来。格式（若以后要显示概率）是
    `"1@30#2@25#3@25#4@20"`（1..4 = 白/绿/紫/金）。
    """
    cards = []
    for _rarity, pool in card_pool().items():
        cards.extend(pool)
    for (_typ, key) in prize_pool():
        cards.append(key)
    return {"cards": cards, "upRate": ""}
