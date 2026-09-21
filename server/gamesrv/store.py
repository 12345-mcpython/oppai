"""玩家数据存储。

单机模拟服，先用 JSON 文件存，够用且方便手改。
字段命名参考客户端 src/data/player.js 里 updateByServer / _getXxx 的用法。
"""

from __future__ import annotations

import json
import os
import threading
import time

from . import config, logx

log = logx.get("store")

_path = os.path.join(config.DATA_DIR, "players.json")
_lock = threading.RLock()


def now_ms() -> int:
    return int(time.time() * 1000)


def time_str(ts: int | None = None) -> str:
    """客户端 Player.ctor 会用 util.getTimeByDateStr() 解析时间字段，
    所以时间字段必须是 "YYYY-MM-DD HH:MM:SS" 字符串而不是时间戳。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts if ts is not None else time.time()))


def time_obj() -> dict:
    return {
        "timeSec": int(time.time()),
        "timezoneOffset": 0,
        "now": now_ms(),
    }


def _load() -> dict:
    if not os.path.exists(_path):
        return {}
    try:
        with open(_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        log.exception("players.json 损坏，重建")
        return {}


def _save(db: dict) -> None:
    tmp = _path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(db, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _path)


# 默认角色 / 机甲（key 必须存在于客户端 table_hero / table_mecha 里）
# table_hero: hadf(阿呆芙·洛玛) / haysdn
# table_mecha: madflj(六酱) / madfxdlj / madftiger / madfewt
HERO_KEY = "hadf"
MECHA_KEY = "madflj"
CHAR_TYPE_HERO = "h"
CHAR_TYPE_MECHA = "m"
CHAR_TYPE_SOLDIER = "s"

# 背包里的道具 key，取自客户端 ITEM_KEY：
#   GEM 100001 / MONEY 100002 / ACTION_POINT 100003（行动力，玩家口中的"甜甜圈"）
ITEM_GEM = "100001"
ITEM_MONEY = "100002"
ITEM_ACTION_POINT = "100003"


# 抽卡碎片（好人卡，`ITEM_KEY.GACHA_FRAGMENT` = 100016）。原版从活动/关卡里攒，
# 私服一件都不给的话「碎片单抽/十连」两个池子永远是「资源不够啦……OAQ」(206)。
# 改成 0 并把 `FRAGMENT_STOCK_VERSION` +1 即可还原。
FRAGMENT_KEY = "100016"
FRAGMENT_STOCK = 99
FRAGMENT_STOCK_VERSION = 1


def top_up_fragments(player: dict) -> bool:
    """老存档补一份抽卡碎片（按版本号只补一次）。"""
    if int(player.get("fragmentStockVersion") or 0) >= FRAGMENT_STOCK_VERSION:
        return False
    bag = player_items(player)
    bag[FRAGMENT_KEY] = max(int(bag.get(FRAGMENT_KEY) or 0), FRAGMENT_STOCK)
    player["fragmentStockVersion"] = FRAGMENT_STOCK_VERSION
    log.info("玩家 %s 补抽卡碎片 %s x%d（私服取舍，见 docs/differences.md §B）",
             player.get("account"), FRAGMENT_KEY, FRAGMENT_STOCK)
    return True


def default_items() -> dict:
    """新号的初始背包。登录包里 `item` 那个块就是这个形状（`{key: count}`）。

    放在 store 而不是 items.py，是为了避免循环 import：
    items.py 要用 store（建军士、读常量），store 不该反过来依赖 items。
    """
    return {
        ITEM_GEM: 100000,
        ITEM_MONEY: 10000000,
        ITEM_ACTION_POINT: 999,
        FRAGMENT_KEY: FRAGMENT_STOCK,
    }


def player_items(player: dict) -> dict:
    """玩家背包。缺失/坏掉就补一份默认的（客户端直接读属性，缺了会抛异常）。"""
    items = player.get("items")
    if not isinstance(items, dict):
        items = default_items()
        player["items"] = items
    return items


# ---------------------------------------------------------------------------
# 天赋 / 培养
# ---------------------------------------------------------------------------
# 三条天赋线。key 取自客户端 `table_talent_type`，服务端抄了一份在
# `gamesrv/data/table_talent_type.json`（101 军士课题 / 102 机甲课题 / 103 克制课题）。
TALENT_TYPES = ("101", "102", "103")

# 天赋升级材料：200040~200048 = 废弃子弹 / 机械齿轮 / 战略指南，每条线三档。
# 原版只能从已经停服的运营活动里拿，私服直接发一份 —— 否则「培养」里点升级
# 永远提示材料不足，这个系统等于没做。想还原原版手感就把存量改成 0 并
# 把 TALENT_STOCK_VERSION +1。客户端 `table_item.limit_count` 上限是 999。
TALENT_MATERIAL_KEYS = (
    "200040", "200041", "200042",   # 军士增幅（type 101）
    "200043", "200044", "200045",   # 统帅增幅（type 102）
    "200046", "200047", "200048",   # 战略增幅（type 103）
)
TALENT_MATERIAL_STOCK = 99

# 好感度礼物（`table_item.type == 30`，共 47 件）。原版从抽卡 / 活动里拿，
# 私服一份都不给的话，宿舍「送礼」面板是空的、道具栏里也看不到礼物 —— 整个玩法等于没做。
# 想还原原版手感：`GIFT_STOCK` 改成 0 并把 `GIFT_STOCK_VERSION` +1。
#
# ⚠️ 礼物 key **不在 store 里硬抄**（不像天赋材料那 9 个）：直接从客户端表
# `table_item` 里按 type==30 取，表变了自动跟上。查表要走 `items.table()`，
# 而 items.py 反过来 import store → 只能函数内局部 import（同 `_add_soldier` 的写法）。
GIFT_STOCK = 99
GIFT_STOCK_VERSION = 1

# 材料补货的版本号。改了存量/加了材料就把这个 +1，所有存档（含玩过的）会再补一次。
#
# ⚠️ 不能写成「少于 STOCK 就补」：`_migrate` 每次取存档都会跑
# （get_or_create_player / update_player 都调），那样花掉的材料会被立刻补回来，
# 相当于无限材料。必须靠版本号做成一次性的。
#
# ⚠️⚠️ **`new_player()` 里绝对不能写这个字段**。踩过一次：
#     `_migrate` 开头有「按 key 把 new_player 的字段补进老存档」那一圈，
#     它会把 `talentStockVersion` 先补上，于是轮到下面那段补货代码时
#     「版本已经等于当前版本」→ 直接跳过 → 老存档一颗材料都拿不到。
#     这个字段只能由 `_migrate` 自己写。
TALENT_STOCK_VERSION = 2


def new_talents() -> dict:
    """天赋的初始状态。

    形状是**反汇编 `TalentCenter._initTalentTypes(args)` 的字节码定死的**，不是猜的：

        this._talentTypes = {};
        for (var k in args) {                       // ← 遍历 args 的 key（= type）
            cc.assert(args[k].curTalentKey !== undefined && args[k].lv !== undefined, ...);
            this._talentTypes[k] = {type: k, curTalentKey: args[k].curTalentKey,
                                    lv: args[k].lv, unlockLv: table_talent_type[k].unlock_lv};
            if (this._talentTypes[k].curTalentKey === "")
                this._talentTypes[k].curTalentKey = table_talent_type[k].default_talent_key;
        }

    ⚠️ 每个 value 必须是 **`{curTalentKey, lv}` 对象**。早先给的是
    `{"101": "1001"}`（字符串），于是 `args[k].curTalentKey` / `.lv` 全是 undefined：
    `cc.assert` 当场失败，紧接着 `_initTalents()` 用 `k + "#" + lv` 拼出
    `"1001#undefined"` 去查 `table_talent`，整条链断掉 —— 表现就是天赋面板空的、
    logcat 里只有构造容错吞掉的一条异常。

    `curTalentKey` 给空串 = 「还没选」，客户端会用
    `table_talent_type[k].default_talent_key`（1001 / 2001 / 3001）补上，
    这正是原版新号的状态。
    """
    return {t: {"curTalentKey": "", "lv": 0} for t in TALENT_TYPES}


def player_talents(player: dict) -> dict:
    """玩家天赋状态。

    缺失/坏掉就补一份默认的（客户端构造时直接读属性，缺了会在 initUserData 里抛）。
    已经存在的按 key 补齐，以后多加一条天赋线时老存档也能直接用。
    """
    talents = player.get("talents")
    if not isinstance(talents, dict):
        talents = new_talents()
        player["talents"] = talents
        return talents
    for t in TALENT_TYPES:
        row = talents.get(t)
        if not isinstance(row, dict):
            talents[t] = {"curTalentKey": "", "lv": 0}
        else:
            row.setdefault("curTalentKey", "")
            row.setdefault("lv", 0)
    return talents


def top_up_talent_materials(items: dict) -> None:
    """把天赋材料补到 TALENT_MATERIAL_STOCK（只加不减，不碰玩家已有的更多存量）。"""
    for key in TALENT_MATERIAL_KEYS:
        items[key] = max(int(items.get(key) or 0), TALENT_MATERIAL_STOCK)


# ---------------------------------------------------------------------------
# 装备
# ---------------------------------------------------------------------------
# 常量取自客户端 `table_equipment_constant`（服务端抄了一份在 data/ 下）。
EQUIPMENT_SLOT_TOTAL = 1000            # equipment_slot_total
EQUIPMENT_MAX_GROUP = 1                # equipment_max_group
EQUIPMENT_UPGRADE_ITEM = "100401"      # equipment_level_up_key —— 升级用的材料
EQUIPMENT_MAX_LV = 15                  # max_equipment_lv_1..5 都是 15

# 升级材料的初始存量。原版靠**分解装备**攒
# （`table_equipment_level["<quality>#<lv>"].decomposes_material`，quality1 每件给 10），
# 私服直接发一份 —— 不然第一次点「升级」必然提示材料不足。
EQUIPMENT_MATERIAL_STOCK = 500
# 见 _migrate：一次性补货的版本号（同样**不能**写进 new_player()）
EQUIPMENT_STOCK_VERSION = 1

# 初始装备：quality=1 的四个 type 各一件。挑 quality=1 是因为升级消耗最小
# （`1#0` 只要 130 萌钞 + 2 材料），好试。key 必须存在于客户端 table_equipment。
EQUIPMENT_START_KEYS = (
    "eq10101010101",   # type 1  R-LSMK手枪
    "eq10101010201",   # type 2  R-夜行军面罩
    "eq10101010301",   # type 3  R-战术水壶
    "eq10101010401",   # type 4  R-萌军狗牌
)


def equipment_attr_key(group, lv: int, index: int = 1) -> str:
    """`table_equipment_attr` 的 key 规则（从表里读出来的，不是猜的）：

        <5 位 attr group> + %02d(lv) + %02d(index)

        10101 + 00 + 01 = 101010001   # 组 10101 的 lv0/index1 = 生命值 hp 210
        10101 + 15 + 01 = 101011501   # lv15
        20131 + 00 + 02 = 201310002   # 第二属性组同一个规则

    ⚠️ **这个 key 必须有值**：客户端 `equipmentManager.addEquipmentAttrByKeys(keys)` 是

        while (true) { var attr = table_equipment_attr[keys[i]];
                       ... attr.attr_key ...                 // ← 空数组时 keys[0]=undefined → 崩
                       if (!(i < keys.length)) break; }      // ← 长度判断在取值**之后**

    **先取值再判长度**，所以空数组也会 `attr.attr_key` 抛 TypeError。
    表现：「强化」点了没反应（`EquipmentStrengeLayer` 构造抛异常被吞掉），
    logcat 里只有一行 `JS ERROR: TypeError: config is undefined`。
    """
    if not group:
        return ""
    return "%s%02d%02d" % (group, int(lv), int(index))


def _equipment_first_group(key) -> str:
    """`table_equipment[key].first_attr_group`。

    局部 import 是为了避开循环依赖：items 依赖 store，store 不能在模块级 import items。
    """
    from . import items

    row = items.table("table_equipment").get(str(key)) or {}
    return str(row.get("f") or "")


def _pick_attr_key(group, lv: int) -> str:
    """挑一个**真实存在**的第一属性 key。

    优先 `<group>%02d(lv)01`；表里没有就退到「同前缀、同等级」的第一个 key。

    ⚠️ 为什么必须回退：**不是所有 `first_attr_group` 都在表里**。实测初始四件装备里
    `10102` / `10104` 这两组在 `table_equipment_attr` 里**不存在**
    （该表的第一属性只有 10101 / 10103 / 10131~10139 这些族）。
    照拼的话客户端 `table_equipment_attr[keys[0]]` 又是 undefined → 强化界面照样崩。
    """
    from . import items

    table = items.table("table_equipment_attr")
    if not table:
        return ""
    lv = int(lv)
    if group:
        preferred = equipment_attr_key(group, lv, 1)
        if preferred in table:
            return preferred
        prefix = str(group)[:3]
        same = sorted(k for k, r in table.items()
                      if int((r or {}).get("lv") or 0) == lv and k.startswith(prefix))
        if same:
            return same[0]
    same_lv = sorted(k for k, r in table.items() if int((r or {}).get("lv") or 0) == lv)
    return same_lv[0] if same_lv else ""


def _fill_equipment_attrs(rows: list) -> bool:
    """按当前等级重算每件装备的属性 key。返回是否改动过。"""
    changed = False
    for row in rows:
        lv = int(row.get("lv") or 0)
        want = [k for k in (_pick_attr_key(_equipment_first_group(row.get("key")), lv),) if k]
        if list(row.get("firstAttrKeys") or []) != want:
            row["firstAttrKeys"] = want
            changed = True
        if not isinstance(row.get("secondAttrKeys"), list):
            row["secondAttrKeys"] = []
            changed = True
    return changed


def refresh_equipment_attrs(player: dict) -> bool:
    """给玩家所有装备重算属性 key。

    `firstAttrKeys` 是**随等级变**的：同一组里 lv0~lv15 各一行（16 行），
    所以升级之后必须重算，否则属性面板不更新、还会查到不存在的 key。

    `secondAttrKeys` 暂时留空：quality1 装备的 `second_attr_group_1..5` 是 "20101"，
    而 `table_equipment_attr` 里第二属性的组是 `20131`~`20541` —— **"20101" 不存在**。
    原版也是升到 lv10 之后（`table_equipment_level.second_attr_add_limit` 由 0 变 3/4）
    才加第二属性。要补的话得先弄清那个组是怎么选的。
    """
    return _fill_equipment_attrs(player_equipments(player))


def new_equipments() -> list:
    """初始装备。

    每条实例的字段是**反汇编 `EquipmentCenter` + `initEquipment(eq)` 定的**：

        id / key / soldierId / isLock / isNew / lv / firstAttrKeys / secondAttrKeys

    ⚠️ `firstAttrKeys` / `secondAttrKeys` **必须是数组，而且 `firstAttrKeys` 不能为空** ——
    见 `equipment_attr_key()` 的说明，空数组会让客户端在 `addEquipmentAttrByKeys`
    里 `attr.attr_key` 抛 TypeError。

    `name / type / quality / suitKey` **不用发** —— `initEquipment()` 自己从
    `table_equipment[eq.key]` 里补（`eq.key` 是唯一必须对的字段）。
    """
    out = []
    for i, key in enumerate(EQUIPMENT_START_KEYS, start=1):
        out.append({
            "id": i,
            "key": key,
            "soldierId": "",      # 空串 = 没装在任何军士身上
            "isLock": 0,
            "isNew": 1,
            "lv": 0,
            "firstAttrKeys": [],
            "secondAttrKeys": [],
        })
    _fill_equipment_attrs(out)
    return out


def new_equipment_groups() -> list:
    """装备编组。客户端 `_initGroupEquipments` 按 `index` 索引，每组 `{index, equipments}`。"""
    return [{"index": i, "equipments": {}} for i in range(1, EQUIPMENT_MAX_GROUP + 1)]


def player_equipments(player: dict) -> list:
    """玩家装备列表。缺失/坏掉就补一份默认的（客户端直接迭代它）。"""
    rows = player.get("equipments")
    if not isinstance(rows, list):
        rows = new_equipments()
        player["equipments"] = rows
    return rows


def player_equipment_groups(player: dict) -> list:
    groups = player.get("equipmentGroups")
    if not isinstance(groups, list):
        groups = new_equipment_groups()
        player["equipmentGroups"] = groups
    return groups


def equipment_block(player: dict) -> dict:
    """登录包里 `equipment` 那块。形状见 `EquipmentCenter.ctor(data)`。

    ⚠️ 是 `maxEquipmentCount / equipments / equipmentGroups` 三个键，
    **不是**以前那种 `{equipments, suits}` —— `suits` 客户端压根不读。
    """
    return {
        "maxEquipmentCount": EQUIPMENT_SLOT_TOTAL,
        "equipments": player_equipments(player),
        "equipmentGroups": player_equipment_groups(player),
    }


def find_equipment(player: dict, equipment_id):
    for row in player_equipments(player):
        if str(row.get("id")) == str(equipment_id):
            return row
    return None


def next_equipment_id(player: dict) -> int:
    used = {int(r.get("id") or 0) for r in player_equipments(player)}
    n = 1
    while n in used:
        n += 1
    return n


def top_up_equipment_material(items: dict) -> None:
    """把装备升级材料补到 EQUIPMENT_MATERIAL_STOCK（只加不减）。"""
    items[EQUIPMENT_UPGRADE_ITEM] = max(int(items.get(EQUIPMENT_UPGRADE_ITEM) or 0),
                                        EQUIPMENT_MATERIAL_STOCK)


def top_up_gifts(items: dict) -> int:
    """把 47 件好感度礼物补到 GIFT_STOCK（只加不减、不碰玩家已有的更多存量）。

    返回补了几种。礼物 key 从客户端表 `table_item` 里按 `type == 30` 取
    （抽表时压成了短键 `t`；拿不到表就回 0，不让建号挂掉）。
    """
    from . import items as items_mod   # 局部 import：store ← items 是循环依赖

    table = items_mod.table("table_item")
    n = 0
    for key, row in (table or {}).items():
        if str((row or {}).get("t")) != "30":     # t = type；30 = ITEM_TYPE.GIFT
            continue
        key = str(key)
        items[key] = max(int(items.get(key) or 0), GIFT_STOCK)
        n += 1
    return n


# ---------------------------------------------------------------------------
# 好感度（宿舍 / favor.*）
# ---------------------------------------------------------------------------
# ⚠️ 这里**只是存储层**（形状 + 读写）。凡是需要查客户端表的逻辑
# （谁能有好感度、礼物加多少、回礼掉什么）一律在 `gamesrv/favor.py` 里 ——
# 因为查表要走 `items.table()`，而 items.py 反过来 import store，
# 在 store 里 import 它就是循环依赖（store.py 顶部那段注释说的是同一件事）。

# 默认值抄自客户端 `table_favor_constant`。表抽出来之后以表为准（见 favor.py）。
FAVOR_INTERACT_MAX = 5              # max_favor_interact_times
FAVOR_INTERACT_COOLDOWN = 3600      # favor_interact_cooldown_time（秒）
FAVOR_DEFAULT_BG_KEY = "500001"     # default_favor_bg_item_key

# `Favor.ctor` 里未获得角色的默认值：
#     this._descUnlockMark = favorData.id ? (favorData.descUnlockMark || 0)
#                                        : FAVOR_DESC_ALL_UNLOCKED_MARK;
# 全 1（= -1）表示「全部台词都已读」—— 没获得的角色不该有未读红点。
FAVOR_DESC_ALL_UNLOCKED_MARK = -1


def new_favor_row(char_key: str, favor_id: int) -> dict:
    """一个角色的好感度行。

    ⚠️ 字段名要**去掉下划线**：`Favor.update(info)` 是
        for (var i in data) { this["_" + i] = data[i]; }
    也就是说服务端给的 key 必须是 `lv / curExp / curClothes / newBgList / descUnlockMark`
    这些，客户端自己加 `_` 前缀。给错名字不会报错，只会静默不生效。

    ⚠️ **`id` 的存在与否就是「是否已获得」**：
        this._isAcquired = typeof favorData.id != "undefined";
    所以没获得的角色**不能**有 `id` —— 那种行压根不用发，客户端会自己
    用 `{charKey: charKey}` 兜底（见 favor.py 的 ensure_favors）。
    """
    return {
        "charKey": char_key,
        "id": favor_id,
        "lv": 1,
        "curExp": 0,
        # ⚠️ 这里留空，由 `favor.ensure_default_looks()` 在登录时补成角色的默认衣服
        #    （要查 table_item，store 这层不能 import items，见 favor.default_clothes）。
        #    **不能一直留空**：客户端 `createExpSpriteEx` 会 `bag.getItem("")` 拿到
        #    undefined 然后直接 return，表现是抚摸完全没反应。
        "curClothes": "",
        # 新衣服 / 新背景：客户端 `getHaveNewClothes()` 是 `for (var k in _newClothes) return true`，
        # 所以形状是**集合**（`{itemKey: 1}`），不是数组。
        "newClothes": {},
        "curBg": FAVOR_DEFAULT_BG_KEY,
        "newBgList": {},
        "descUnlockMark": 0,
    }


def player_favors(player: dict) -> dict:
    """玩家好感度表：`{charKey: 行}`。

    ⚠️ **是 map 不是 list**。反汇编 `FavorCenter._initData`：
        var favorsData = data.favors;
        for (var i in favorsData) existedKeys.push(favorsData[i].charKey);
        ...
        var favorData = existedKeys.indexOf(charKey) === -1 ? {charKey: charKey}
                                                           : favorsData[charKey];
    它拿 **charKey 当 key** 去索引 —— 回数组的话 `favorsData["sasm"]` 恒为
    undefined，所有角色都会退化成「未获得」。
    以前登录包里写的就是 `"favors": []`，表现是宿舍里 63 个角色全是灰的。
    """
    favors = player.get("favors")
    if not isinstance(favors, dict):
        favors = {}
        player["favors"] = favors
    return favors


def find_favor(player: dict, char_key) -> dict | None:
    return player_favors(player).get(str(char_key))


def next_favor_id(player: dict) -> int:
    used = {int(r.get("id") or 0) for r in player_favors(player).values()}
    n = 1
    while n in used:
        n += 1
    return n


def favor_interact(player: dict) -> dict:
    """抚摸（互动）次数状态 → `{chance, updateTimeSec}`。

    客户端 `FavorCenter.updateFavorInteract()`：
        if (this._favorInteractChance >= table_constant.max_favor_interact_times) { 夹到上限; return; }
        var add = Math.floor((util.time() / 1000 - this._favorInteractUpdateTimeSec)
                             / table_constant.favor_interact_cooldown_time);
        this._favorInteractChance += add;

    所以 `updateTimeSec` 是 **epoch 秒**，而且客户端**自己不会推进它** ——
    每次回满的时刻由服务端在响应里给（`setFavorInteract(res.data)`）。
    """
    st = player.get("favorInteract")
    if not isinstance(st, dict):
        st = {}
        player["favorInteract"] = st
    try:
        chance = int(st.get("chance"))
    except (TypeError, ValueError):
        chance = FAVOR_INTERACT_MAX
    try:
        base = int(st.get("updateTimeSec"))
    except (TypeError, ValueError):
        base = int(time.time())
    st["chance"] = max(0, min(chance, FAVOR_INTERACT_MAX))
    st["updateTimeSec"] = base
    return st


def new_favor_gift_state() -> dict:
    return {"usedCount": 0, "lastTimeSec": 0}


def favor_gift_state(player: dict) -> dict:
    """送礼物计数。

    ⚠️ 用的是 **player 顶层的 `usedGiftCount` / `lastGiftTimeSec`**，
    不是另起一个嵌套块 —— 因为客户端 `Player.ctor` 就是从登录包的 player 对象里
    直接读这两个名字，响应那边的键 `useGiftStatus` 走 `Player.cb4UseGiftStatus`，
    读的也是 `data.usedGiftCount` / `data.lastGiftTimeSec`。放两份迟早对不上。
    """
    try:
        used = int(player.get("usedGiftCount") or 0)
    except (TypeError, ValueError):
        used = 0
    try:
        last = int(player.get("lastGiftTimeSec") or 0)
    except (TypeError, ValueError):
        last = 0
    player["usedGiftCount"] = used
    player["lastGiftTimeSec"] = last
    return {"usedCount": used, "lastTimeSec": last}


# --- 宿舍事件（favorevent.*）------------------------------------------------
# 客户端的枚举（实机取过 FAVOR_EVENT_STATUS）：
#     {ACCEPTABLE: 1, ACCEPTED: 2, FINISHED: 3, OVER: 4}
# 显示上只有两处用到：
#   `FavorEvent._getIsOver()`  = `_id && _status == OVER`  -> `getUnlockStatus()` 回 LOCKED
#   `FavorEventCenter.isFavorEventUnlock()` = `status == ACCEPTED`
# 服务端约定：新建 = ACCEPTABLE + `isToBeUnlocked = 1`（红点），
# 读过 = ACCEPTED + `isToBeUnlocked = 0`。见 favor.sync_events / favor.read_events。
FAVOR_EVENT_ACCEPTABLE = 1
FAVOR_EVENT_ACCEPTED = 2


def new_favor_event_row(event_key: str, event_id: int, lv: int) -> dict:
    """一条宿舍事件。

    字段名对应 `FavorEvent._initData(favorEvent, eventInTable)` 的前半段
    —— 它读 `id / eventKey / lv / isToBeUnlocked / status / createTimeSec`，
    后半段（`char_key / level_key / title / desc / type / favor_lv`）
    是客户端自己从 `table_favor_random_event[eventKey]` 补的，**不用发**。
    """
    return {
        "id": event_id,
        "eventKey": str(event_key),
        "lv": int(lv or 1),
        "status": FAVOR_EVENT_ACCEPTABLE,
        "isToBeUnlocked": 1,
        "createTimeSec": int(time.time()),
    }


def player_favor_events(player: dict) -> dict:
    """玩家的宿舍事件：`{eventKey: 行}`（和 `_favorEvents` 一样按 eventKey 索引）。"""
    events = player.get("favorEvents")
    if not isinstance(events, dict):
        events = {}
        player["favorEvents"] = events
    return events


def find_favor_event(player: dict, event_key) -> dict | None:
    return player_favor_events(player).get(str(event_key))


def next_favor_event_id(player: dict) -> int:
    used = {int(r.get("id") or 0) for r in player_favor_events(player).values()}
    n = 1
    while n in used:
        n += 1
    return n


# --- 守护灵（宿舍的 guard 面板 / char.upgradedaemon）------------------------
# **按 charKey 索引的 map**，不是数组。判据是反汇编 `CharCenter`：
#
#   ctor:          this._daemons = data.daemons;          // 原样赋值，不重排
#   getDaemon(k):  this._daemons[k]                       // ← 拿 charKey 直接索引
#   updateDaemon(d): this._daemons[d.charKey] = d;        // ← 同上
#
# 所以登录块里给数组的话 `getDaemon('sasm')` 恒为 undefined，守护灵面板全空。
# 行只有三个字段：`{charKey, lv, curExp}` —— `maxExp` / `nextTipsDesc` / `attrTotal`
# 都是客户端自己拿 table_daemon_upgrade / table_daemon 算的，不用发。
def new_daemon_row(char_key: str) -> dict:
    return {"charKey": str(char_key), "lv": 0, "curExp": 0}


def player_daemons(player: dict) -> dict:
    daemons = player.get("daemons")
    if not isinstance(daemons, dict):
        daemons = {}
        player["daemons"] = daemons
    return daemons


def find_daemon(player: dict, char_key) -> dict | None:
    return player_daemons(player).get(str(char_key))


def ensure_daemons(player: dict) -> bool:
    """给每个「在 table_soldier_master 里有 daemon_mode 的角色」补一行。

    主角（hero）没有 daemon_mode，`charManager.getDaemonAttr('hadf')` 会直接抛
    `table_soldier_master[charKey] is undefined` —— 所以守护灵只对军士角色开放，
    这里也只给这些角色建行。返回是否有改动。
    """
    from . import items  # 延迟 import：items 反过来依赖 store，模块级会循环

    daemon_cfg = (items.table("table_soldier") or {}).get("daemon") or {}
    rows = player_daemons(player)
    changed = False
    for char_key in favor_char_keys(player):
        if char_key in rows:
            continue
        if not (daemon_cfg.get(char_key) or {}).get("mode"):
            continue
        rows[char_key] = new_daemon_row(char_key)
        changed = True
    return changed


def favor_char_keys(player: dict) -> list:
    """玩家拥有好感度的角色 key（好感度行的那批，含主角）。"""
    return list(player_favors(player))


# 新手引导位掩码全 1 = 所有引导都已完成。见 new_player() 里的说明。
GUIDE_MARK_DONE = 0x7FFFFFFF

# 「功能开启」弹窗：私服默认**不弹**。
#
# 客户端 `MainLayer._updateAnimation()` 里有一条 `moduleManager.popModuleOpen()`：
# 它遍历 `player.updateModuleState()`，把所有 **已解锁但 `isOpened` 还是假**的模块
# 挨个弹一遍「功能开启」动画（`table_function_open` 有 32 条，我们建号就把
# `moduleState[*].isUnlock` 全给了 1、等级也直接 30，所以一进游戏会连弹 32 个）。
#
# 而 `isOpened` 是 `Player.initModuleState()` 从**登录块的 `moduleOpenMark[mark_index]`**
# 读的（`Player.updateModuleState` 里弹完会把这些 mark 写回 `player.setmoduleopenmark`）。
# ⇒ 服务端只要在登录块里把 mark 全标成"已弹过"就不会弹，**不用动客户端**。
#
# 想还原原版手感：`MODULE_OPEN_POPUP_SKIP = False`（老存档里被写回的 mark 仍然生效）。
MODULE_OPEN_POPUP_SKIP = True
# 玩家「指挥部」初始等级。客户端按等级解锁功能，最靠前的门槛是编成里的
# 「培养 / 升级军士」= 6 级，所以默认给 30 一步到位（顺带过了 25 级那批）。
MIN_PLAYER_LV = 30

# 初始士兵名单的版本号。**改动 SOLDIER_KEYS 时要 +1**，老存档会被补齐
# （见 replenish_soldiers：缺的补回来、已有的原样留着，不会重置等级）。
#
# v4：把「整个重发」改成「补齐」。原因是脚本 `selftest_game.py` 的军士链路
#     每跑一次吃掉 2 个军士当材料，把默认账号的名单啃到只剩 4 个，
#     而当时唯一的恢复手段（重发）会顺手把玩家练过的那几个也清成 1 级。
#     改成补齐之后版本号一涨就能安全地补回来，所以顺手 +1 把丢掉的补上。
ROSTER_VERSION = 4

# 初始士兵。(key, 站位, 品质)，key 取自客户端 table_soldier。
#
# 站位必须三个都有：SOLDIER_POSITIONING = {FRONT:1, MIDDLE:2, BACK:3}，
# 「编成 -> 上阵队伍 -> 加号」的队员选择界面是按站位分页的，
# 只有前锋的话切到「炮弹」页就是「没有符合要求的军士哦~」。
#
# 而且每个士兵的 char_key 必须互不相同 —— 客户端规则是
# 「相同的角色只能选择一个作为上阵军士」，同一个 char_key 只会出现一个。
# 之前给的是 sads010101~04，四个全是同一个角色 sads，所以列表里基本是空的。
#
# ⚠️⚠️ **必须是「玩家可获得」的军士**，判据是
#     table_soldier_master[char_key].card_type === CARD_TYPE.TEAMMATE(1)
# `Soldier._loadMasterAttr` 就一句 `this._cardType = table_soldier_master[char_key].card_type`，
# 只有 1 是自军卡（2=ENEMY，3=EXP，4=SKILL）。
#
# 早先这份名单是按「能 new 出来」挑的，结果挑中一堆 card_type==2 的**敌方单位**
# （sfog 是先代巫女 BOSS，sads/safj/sbdsl 等也全是敌人），后果有两个：
#   1. 「编成 -> 培养」选不出材料 —— 材料列表只收 cardType==TEAMMATE，
#      18 个里只剩 7 个；
#   2. `CharCenter.calcSoldierUpgrade` 的 gainExp 算成 NaN —— 那些行没有
#      `base_exp` 字段，`table_soldier[key].base_exp` 是 undefined，
#      加法得 NaN，`while (gainExp < maxExp)` 永远为假，直接顶到 maxLv。
# 现在按 card_type==1 + quality==4 + 三站位各 6 个挑，实测 18 个全部构造成功且 ct=1。
#
# ⚠️ 一个士兵构造失败会**整份士兵列表全丢**（CharCenter 里是整体 try），
# 所以只能用实测能 new 出来的 key。lfcz01/lfcz02/lfcz03（废柴子系）
# 在客户端会抛 "TypeError: row is undefined"，千万别放进来。
SOLDIER_KEYS = [
    # 前锋 FRONT = 1
    ("sasm010104", 1, 4),      # 阿斯麦
    ("sbns010104", 1, 4),      # 柏妮丝
    ("sglrs010104", 1, 4),
    ("shx010104", 1, 4),
    ("sjm010104", 1, 4),
    ("skdln010104", 1, 4),
    # 中卫 MIDDLE = 2
    ("sbd010104", 2, 4),       # 巴度
    ("sbq010104", 2, 4),       # 贝琪
    ("sflr010104", 2, 4),
    ("sfn010104", 2, 4),
    ("shs010104", 2, 4),
    ("sjlt010104", 2, 4),
    # 后卫 BACK = 3
    ("saf010104", 3, 4),       # 爱芙
    ("same010104", 3, 4),      # 爱莫儿
    ("scyy010104", 3, 4),      # 长月遥
    ("sda010104", 3, 4),
    ("sdde010104", 3, 4),
    ("sdfn010104", 3, 4),
]


# 功能模块（主界面按钮）开启状态。key 是客户端 table_function_open /
# table_main_layer.module_key 里的数字编号，客户端有两处读它：
#
#   moduleManager.isModuleUnlock(key):
#       var module = dataManager.player.moduleState[key];
#       return module.isUnlock;
#   mainlayer._initModuleButtons:
#       var modules = dataManager.player.moduleState;   // ← 缺失时是 undefined
#       ...
#       var module = modules[row.module_key];
#       var isUnlock = module.isUnlock;                 // ← 抛 TypeError
#
# 缺这个字段主界面构造就会死在
#   TypeError: modules is undefined @ src/ui/main/mainlayer.js:188
# 表现就是登录后黑屏。全 1 = 所有功能都开放。
MODULE_KEYS = [str(100001 + i) for i in range(32)]


def new_module_state() -> dict:
    return {k: {"isUnlock": 1, "unlockLv": 1} for k in MODULE_KEYS}


def new_hero(key: str = HERO_KEY) -> dict:
    """主角/英雄。字段名来自客户端 src/data/hero.js 的 Hero。

    `key` 默认是建号送的主角的那个（`HERO_KEY` = hadf）；抽卡抽到另一个英雄
    （`table_hero` 里还有 `haysdn`）时要按那张表填 `curMechaKey`/`mechaKeys`。
    """
    from . import items as items_mod   # 局部 import：循环依赖

    row = (items_mod.table("table_hero") or {}).get(str(key)) or {}
    mechas = [row.get("mecha_%d" % i) for i in range(1, 5)]
    mechas = [str(m) for m in mechas if m]
    return {
        "id": 1,
        "key": str(key),
        "charType": CHAR_TYPE_HERO,
        "curMechaKey": (mechas[0] if mechas else MECHA_KEY),
        "mechaKeys": (mechas or ["madflj", "madfxdlj", "madftiger", "madfewt"]),
        "favorLv": 1,
        "favorCurExp": 0,
        "talentsLv": {},
        "fashionKey": "",
        "recCount": 0,
        "lastRecTime": time_str(),
        "isCastEnabled": 0,
        "isAsstEnabled": 0,
    }


def new_soldier(index: int, key: str, positioning: int = 1, quality: int = 1,
                lv: int = 1, star: int = 1) -> dict:
    """士兵。字段名来自客户端 src/data/soldier.js 的 Soldier（_init 里读的）。

    客户端 Soldier._init 会拿 key 去查 table_soldier 取 char_key / quality /
    各项属性，所以这里只需要给出会变的那几个字段；positioning / quality 也照
    table_soldier 填一份，免得客户端某处直接读服务端这份。

    ⚠️ `skillLv` 不能省。客户端 `Soldier._init` 第一句是
        this._originData = util.encodeOriginData(args);
    把**服务端原始数据**存成快照（数字 <<5 之后 JSON），
    之后 `isNormalData()` 拿它跟本地值逐项比对（key/quality/star/lv/skillLv），
    其中 lv 是 `this._mainSkill.lv = args.skillLv || 1` —— 默认 1。
    不给 skillLv 的话快照里根本没有这个字段，比对永远 `undefined != 1`，
    表现就是编好队一点「战斗」弹「队伍数据异常，请重新登陆」，然后闪退。
    （空队伍不会触发：`Team.isNormalData()` 是遍历队员逐个查的。）
    """
    return {
        "id": index,
        "key": key,
        "charType": CHAR_TYPE_SOLDIER,
        "lv": lv,
        "star": star,
        "curExp": 0,
        "quality": quality,
        "positioning": positioning,
        "skillLv": 1,
        # 技能等级表：客户端 _loadSkill 按 table_soldier.skill_index 逐位取，
        # 数量对得上才会把技能建出来（table 里 skill_index 形如 "1#2"，两位）
        "skillLvList": [1, 1],
        "equipments": [],
        "isLock": 0,
        "isDel": 0,
        "isNew": 1,
        "isDetect": 0,
        "createTimeSec": int(time.time()),
    }


def new_soldiers() -> list:
    """初始士兵列表（三个站位各 6 个，角色互不重复）。"""
    return [new_soldier(i + 1, key, pos, quality)
            for i, (key, pos, quality) in enumerate(SOLDIER_KEYS)]


def ensure_soldiers(player: dict) -> list:
    """玩家的军士列表。

    ⚠️ 军士必须**存在玩家身上**，不能每次登录现生成 ——
    不然「军士升级」升完一重登就没了（任务 210001「首次升级」也会跟着回退）。
    第一次登录时按初始名单发一份，之后就一直是玩家的。
    """
    soldiers = player.get("soldiers")
    if not isinstance(soldiers, list) or not soldiers:
        soldiers = new_soldiers()
        player["soldiers"] = soldiers
    return soldiers


def _soldier_team_cleanup(player: dict, removed_ids: set) -> None:
    """把被删掉的军士从各个队伍里摘掉（不然队伍会指向不存在的 id）。"""
    if not removed_ids:
        return
    for team in player.get("teams") or []:
        if not isinstance(team, dict):
            continue
        keys = team.get("soldierKeys")
        if isinstance(keys, list):
            team["soldierKeys"] = [k for k in keys if k not in removed_ids]
            team["soldierCount"] = len(team["soldierKeys"])


def replenish_soldiers(player: dict) -> tuple:
    """按 `SOLDIER_KEYS` **补齐**军士名单：保留已有的，只补缺的。返回 (补了几个, 删了几个)。

    ⚠️⚠️ **旧的 `ROSTER_VERSION` 迁移是「整个重发」**（`player["soldiers"] = new_soldiers()`），
    那是为了「名单本身改了，老 key 留着没用」。代价很重：**所有军士等级归零**。
    踩过一次：`script/selftest_game.py` 的军士链路每跑一次吃掉 2 个军士当材料，
    跑了 7 轮之后名单只剩 4 个；而恢复手段只有「重发」，等于顺手把玩家练过的那 4 个
    也一起清了。

    改成合并之后：**版本号一涨，缺的补回来、已有的原样留着（等级/星级/技能都在）**，
    只有「已经不在 `SOLDIER_KEYS` 里的旧 key」才会被删掉。

    注意这**不是**每次登录都跑 —— 那样被当材料吃掉的军士会立刻长回来（等于无限材料）。
    只在 `_migrate` 里 rosterVersion 变了的时候跑一次，和天赋/装备材料补货同一个道理。
    """
    keys = [k for k, _p, _q in SOLDIER_KEYS]
    key_set = set(keys)
    old = player.get("soldiers")
    old = old if isinstance(old, list) else []

    kept, dropped_ids, seen = [], set(), set()
    for row in old:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "")
        if key not in key_set or key in seen:
            # 已经不在名单里的旧 key（比如早期误收的敌方单位）→ 删
            # 同一个 key 出现两次 → 只留第一个（免得越补越多）
            if row.get("id") is not None:
                dropped_ids.add(row.get("id"))
            continue
        seen.add(key)
        kept.append(row)

    used_ids = {int(r.get("id") or 0) for r in kept}
    next_id = 1
    added = 0
    for key, positioning, quality in SOLDIER_KEYS:
        if key in seen:
            continue
        while next_id in used_ids:
            next_id += 1
        kept.append(new_soldier(next_id, key, positioning, quality))
        used_ids.add(next_id)
        seen.add(key)
        added += 1

    kept.sort(key=lambda r: int(r.get("id") or 0))
    player["soldiers"] = kept
    _soldier_team_cleanup(player, dropped_ids)
    return added, len(dropped_ids)


def find_soldier(player: dict, soldier_id) -> dict | None:
    try:
        soldier_id = int(soldier_id)
    except (TypeError, ValueError):
        return None
    for s in ensure_soldiers(player):
        if int(s.get("id") or 0) == soldier_id:
            return s
    return None


def new_mecha(key: str = MECHA_KEY) -> dict:
    """默认机甲。字段名来自客户端 src/data/mecha.js 的 Mecha。

    `key` 默认是建号送的那台（`MECHA_KEY` = madflj）；抽卡抽到别的机甲
    （`table_mecha` 一共 6 台）时按那张表填定位/旋转范围。
    """
    from . import items as items_mod   # 局部 import：循环依赖

    row = (items_mod.table("table_mecha") or {}).get(str(key)) or {}
    return {
        "id": 1,
        "key": str(key),
        "uuid": str(key),
        "lv": 1,
        "charType": CHAR_TYPE_MECHA,
        "hidden": int(row.get("hidden") or 0),
        "positioning": int(row.get("positioning") or 1),
        "scale": int(row.get("scale") or 1000),
        "type": int(row.get("type") or 1),
        "maxLv": 1,
        "maxSkillLv": 1,
        "minRotation": int(row.get("min_rotation") or -20),
        "maxRotation": int(row.get("max_rotation") or 80),
    }


# ---------------------------------------------------------------------------
# 英雄 / 机甲：抽卡会往这两个列表里加（建号默认各一个）
# ---------------------------------------------------------------------------
def player_heros(player: dict) -> list:
    """玩家拥有的英雄列表（`data.char.heros`，元素形状见 `new_hero()`）。"""
    got = player.get("heros")
    if not isinstance(got, list) or not got:
        got = [new_hero()]
        player["heros"] = got
    return got


# ---------------------------------------------------------------------------
# 角色图鉴（主界面 → 菜单 → 情报室）
# ---------------------------------------------------------------------------
CHAR_MANUAL_VERSION = 2


def player_char_manual(player: dict) -> dict:
    """登录块 `char.charManual`：`{卡key: {"isNewHead": 0}}`。

    ⚠️ **军士的 key 必须是 `table_soldier` 的 key（军士卡 key，如 `sasm010101`），
    不是角色 key（`sasm`）。** 客户端是这么用的（`CharCenter.getSoldierManualKeys`）：

        for (k in _charManual)
            if (charManager.getCharType(k) === CHAR_TYPE.SOLDIER &&
                charManager.getSoldierCardType(k) === CARD_TYPE.TEAMMATE) res.push(k)

    `getCharType(k)` 查的是 `table_soldier[k]`，`getSoldierCardType(k)` 再走
    `table_soldier[k].char_key → table_soldier_master[ck].card_type` —— 两张表都按
    **卡 key** 索引。发角色 key 的后果（2026-09-20 实机踩到）：

      * 每个角色 key 都打一行 `[error]charManager.getCharType() error, key is sasm`
        （logcat 里刷了 196 行）；
      * `getSoldierManualKeys()` 返回空 → 情报室「数量 0/152」、152 个格子全是剪影
        （格子是 `filtrateData()` 按 `table_soldier` 全表铺的，所以是"有格子没内容"）。

    英雄 / 机甲那两个页签查的是 `table_hero` / `table_mecha`，它们的 key 本来就和角色 key
    同名（`hadf` / `madflj`），混在同一个 map 里没问题。

    只发玩家**真的拥有**的卡：军士取 `player["soldiers"]` 的 `key`，英雄/机甲同理 ——
    这样「数量 x/152」是真实的收集进度（原版也是靠"第一次获得"逐个解锁的），
    抽卡抽到新卡之后图鉴自己就亮了。`isNewHead` 一律 0，免得每个头像挂"新"点。
    """
    if int(player.get("charManualVersion") or 0) < CHAR_MANUAL_VERSION:
        player["charManualVersion"] = CHAR_MANUAL_VERSION

    keys = set()
    for one in player.get("soldiers") or []:
        if isinstance(one, dict) and one.get("key"):
            keys.add(str(one["key"]))
    for one in player_heros(player) + player_mechas(player):
        if isinstance(one, dict) and one.get("key"):
            keys.add(str(one["key"]))
    return {key: {"isNewHead": 0} for key in sorted(keys)}


def player_mechas(player: dict) -> list:
    """玩家拥有的机甲列表（`data.char.mechas`）。"""
    got = player.get("mechas")
    if not isinstance(got, list) or not got:
        got = [new_mecha()]
        player["mechas"] = got
    return got


def add_hero(player: dict, key: str) -> bool:
    """加一个英雄（已经有了就返回 False）。"""
    key = str(key)
    if not key:
        return False
    for row in player_heros(player):
        if str(row.get("key")) == key:
            return False
    player["heros"].append(new_hero(key))
    # 客户端 `Hero` 靠 `mechaKeys` 带出它自己的机甲，这里顺手把没拥有的也加上
    for mecha_key in (new_hero(key).get("mechaKeys") or []):
        add_mecha(player, mecha_key)
    return True


def add_mecha(player: dict, key: str) -> bool:
    """加一台机甲（已经有了就返回 False）。"""
    key = str(key)
    if not key:
        return False
    for row in player_mechas(player):
        if str(row.get("key")) == key:
            return False
    player["mechas"].append(new_mecha(key))
    return True


def new_team(index: int) -> dict:
    """空队伍。字段名来自客户端 src/data/team.js 的 Team。

    ⚠️ `id` **等于 `index`（0 起）**，不是 index+1。客户端 `Player.initTeams` 是

        for (var i in this._teams) { ... new Team(this._teams[i], this._character, i); }

    —— `Team.ctor(team, chars, id)` 的第三个参数就是 `for..in` 的 key（"0".."4"），
    所以客户端认为的队伍 id 就是**服务端 teams 数组的下标**，
    `player.updateteams` 发上来的也是 "0".."4"。
    以前这里写 `index + 1`，跟客户端对不上 → 编成保存被整单跳过
    （症状：编好的队伍一直消失）。见 `normalize_team_ids()`。
    """
    return {
        "id": index,
        "index": index,
        "hero": new_hero(),
        "heroKey": HERO_KEY,
        "mechaKey": MECHA_KEY,
        "soldierKeys": [],
        "soldiers": [],
        "soldierCount": 0,
        "spInit": 250,
        "spRate": 10,
        "spMax": 1800,
    }


def normalize_team_ids(player: dict) -> bool:
    """把队伍的 `id`/`index` 对齐成 0 起下标（老存档是 `id = index + 1`）。返回是否有改动。

    为什么必须对齐：客户端 `player.updateteams` 发的是 `{id: "0".."4"}`（见 `new_team` 的说明），
    服务端按 id 找队伍时，老存档的 1 起 id 会让**整单被跳过** ——
    症状就是「编好的队伍一直消失」（重登还是空的）。
    更糟的是 id 偶尔"撞上"：客户端发 "1" 时恰好匹配到我们 id=1 那行，
    但那是 index 0 的队伍 → **写错队伍**。
    """
    changed = False
    for i, team in enumerate(player.get("teams") or []):
        if not isinstance(team, dict):
            continue
        if team.get("index") != i:
            team["index"] = i
            changed = True
        if team.get("id") != i:
            team["id"] = i
            changed = True
    return changed


def new_player(account: str) -> dict:
    """新建玩家。

    ⚠️ 等级直接给 MIN_PLAYER_LV 而不是 1：客户端一大堆功能是按「指挥部等级」
    解锁的（编成里培养/升级军士要 6 级，有的入口要 25 级，提示语在
    table_dictionary[2401]「指挥部等级#@1@#开启」/ [4207]），
    1 级进去点什么都提示"指挥部等级不足哦~OAQ"。
    私服没必要让人从 1 级刷起，想体验原版就从 MIN_PLAYER_LV 改回去。
    """
    now = int(time.time())
    items = default_items()
    top_up_talent_materials(items)
    top_up_equipment_material(items)
    top_up_gifts(items)          # 47 件好感度礼物（宿舍送礼要用）
    return {
        "id": 1,
        "account": account,
        "name": account,
        # 数字 ID（客户端 `Player.ctor` 读 `data.numberId`）。**0 = 待分配**，
        # 由 `_migrate` 分配一个稳定且不撞号的号（见 `_next_number_id`）：
        # 好友系统的记录 key 就是 `"<我的numberId>#<对方numberId>"`，
        # 客户端 `Friend.getFriendNumberId` / `isBeApplyFor` 全拿它比。
        "numberId": 0,
        "lv": MIN_PLAYER_LV,
        "curExp": 0,
        "maxSoldiersCount": 50,
        "actionPoint": 100,
        "maxActionPoint": 100,
        "actionPointTime": time_str(now),
        "selfDesc": "",
        "headId": 1,
        "medalClothesId": 0,
        "medalBgId": 0,
        # 佩戴的勋章：`{勋章id: 佩戴位下标}`（客户端 `Player.medalWear`，
        # `Medal.wearMedal` 先读它判断"这一位是不是已经戴着这个"）。
        # 三个 id 字段的**合法值**由 `medal.ensure()` 在登录时补成
        # `"<头像itemKey>:<类型>"` / 默认衣服 / 默认背景（原始值 1/0/0 是瞎填的）。
        "medalWear": {},
        "curTeamIdx": 0,
        "moduleState": new_module_state(),
        # 背包。登录包的 `item` 块直接用它，买东西/领奖励也改它。
        # 平铺的 `{itemKey: count}` —— 客户端 Bag.ctor 拿它 + table_item 建对象。
        "items": items,
        # 天赋（培养）。形状见 new_talents() 的注释 —— 必须是 {curTalentKey, lv} 对象。
        "talents": new_talents(),
        # 装备。字段形状见 new_equipments() 的注释（attr 两个数组不能缺）。
        "equipments": new_equipments(),
        "equipmentGroups": new_equipment_groups(),
        # 好感度（宿舍）。**这里给空 map**：行由 `favor.ensure_favors()` 按
        # 「玩家实际拥有的角色」补，建号这一刻还没有任何角色获得好感度。
        # 形状见 player_favors() 的注释（map，不是 list）。
        "favors": {},
        # 宿舍事件（好感度到级解锁的剧情）。空 map：`favor.sync_events()` 按
        # `table_favor_random_event` + 好感度等级逐个建。形状见 player_favor_events()。
        "favorEvents": {},
        # 抚摸次数（每小时回 1，上限 5）。见 favor_interact()。
        "favorInteract": {"chance": FAVOR_INTERACT_MAX, "updateTimeSec": now},
        # 送礼计数。客户端 Player.ctor 直接读这两个顶层字段。
        "usedGiftCount": 0,
        "lastGiftTimeSec": 0,
        # ⚠️ 这里**故意不写** `talentStockVersion`：它由 _migrate 独家维护，
        #    否则「按 key 补字段」那一圈会先把版本号补上，把一次性补货门闩顶开。见上面的注释。
        # 军士（18 个初始军士）。**建号时就发**，不是等第一次登录现生成 ——
        # `ensure_soldiers` 只在内存里补，登录接口不写盘，所以「现生成」的版本
        # 每次都可能是新的，军士升级/突破的结果会莫名其妙回退。
        "soldiers": new_soldiers(),
        "rosterVersion": ROSTER_VERSION,
        # 新手引导位掩码（客户端 guideManager.checkGuide 用 id & (1 << n) 判断）。
        # 全 1 表示所有引导都已完成 —— 否则 GuideLayer 会一直拦着菜单点击：
        #   op.uiLoader.addTouchEventListener 里，只要
        #   GuideLayer.getInstance().isGuide() 为真，就先把点击交给
        #   GuideLayer.nextStep()，正常回调永远走不到。
        "guideMark": GUIDE_MARK_DONE,
        "createTime": time_str(now),
        # 客户端 Player.initTeams() 会按 TEAM_COUNT_LIMIT 建队，队伍数量给足
        "teams": [new_team(i) for i in range(5)],
        "character": {},
        "asst": {},
        "asstKey": "",
        "guild": {},
        "gachaTimes": 0,
        "worldChatTime": time_str(now),
        "worldChatCount": 0,
        "monthCardDueTimeSec": time_str(now),
        "msgPushMark": 0,
        # 分区成就。**空 map**：行由客户端在战斗结算时上报
        # （`instance.finishlevel` 的 `subareaInfo.newAchievements` / `modifyAchievements`），
        # 服务端只落盘 + 发奖。形状见 gamesrv/subarea.py（`{id, progress, progressInfo,
        # isReceiveReward, completeTime}`）。
        "subareaAchievements": {},
        # 任务进度。放在 new_player 里（而不是等第一次用到再 setdefault），
        # 是为了让 updateTime 稳定：客户端会拿它跟服务端比来决定要不要拉新数据，
        # 每次请求现生成的话 sync.syncupclient 会永远认为「有变化」。
        # 新手引导 / 任务见 gamesrv/quests.py。
        "quests": {"done": [], "updateTime": time_str(now)},
    }


def _migrate(player: dict) -> bool:
    """给老存档补齐后来才加上的字段。

    客户端对这些字段是「直接读属性」的，缺一个就在主界面抛 JS 异常，
    所以宁可在这里无条件补齐。返回是否改动过。
    """
    fresh = new_player(player.get("account", "player"))
    changed = False
    # 军士名单要在下面「按 key 补齐」之前处理 —— rosterVersion 也是 new_player
    # 里的字段，先补齐的话这里就永远看不出「版本变了」。
    #
    # 军士列表必须是真列表。踩过一次：存档里存成了 null，
    # `find_soldier` 于是每次现发一份新的，升级结果一重登就回退。
    #
    # ROSTER_VERSION 变了就**补齐**（不是重发）：缺的按 SOLDIER_KEYS 补回来，
    # 已有的原样留着（等级/星级/技能都不动），只有已经不在名单里的旧 key 才删。
    # 详见 replenish_soldiers 的 docstring —— 旧版是 `= new_soldiers()` 整个重发，
    # 代价是所有军士等级归零，踩过一次。
    if not isinstance(player.get("soldiers"), list) or not player["soldiers"]:
        player["soldiers"] = new_soldiers()
        player["rosterVersion"] = ROSTER_VERSION
        changed = True
        log.info("玩家 %s 没有军士名单，按 v%s 发一份 %d 个",
                 player.get("account"), ROSTER_VERSION, len(player["soldiers"]))
    elif int(player.get("rosterVersion") or 0) != ROSTER_VERSION:
        added, dropped = replenish_soldiers(player)
        player["rosterVersion"] = ROSTER_VERSION
        changed = True
        log.info("玩家 %s 军士名单补齐到 v%s：补 %d 个、删 %d 个，现在 %d 个",
                 player.get("account"), ROSTER_VERSION, added, dropped,
                 len(player["soldiers"]))
    for key, value in fresh.items():
        if key not in player:
            player[key] = value
            changed = True
    # 数字 ID（好友系统要用）。老存档和 new_player 给的都是 0，这里分配一个
    # **稳定**的号并留在存档里 —— 每次现算的话好友记录 key 会跟着变，
    # 玩家一重登好友列表就全对不上了。
    if not int(player.get("numberId") or 0):
        player["numberId"] = _next_number_id(player.get("account"))
        changed = True
        log.info("玩家 %s 分配数字 ID %s", player.get("account"), player["numberId"])
    # moduleState 要按 key 合并，不能整个覆盖掉玩家已有的开启状态
    state = player.get("moduleState")
    if not isinstance(state, dict):
        state = {}
        player["moduleState"] = state
        changed = True
    for key, value in new_module_state().items():
        if key not in state:
            state[key] = value
            changed = True
    # 老存档是 1 级建的，光靠 new_player 改默认值救不回来，这里补一次升级。
    # 见 new_player 的注释：指挥部等级不够，编成里「培养」「升级军士」直接不给点。
    if int(player.get("lv") or 1) < MIN_PLAYER_LV:
        player["lv"] = MIN_PLAYER_LV
        changed = True
        log.info("玩家 %s 指挥部等级补到 %d", player.get("account"), MIN_PLAYER_LV)
    # 天赋材料补货。老存档的 `items` 已经存在，上面「按 key 补字段」那圈救不到，
    # 所以单独做一步。
    #
    # ⚠️ **必须靠版本号做成一次性的**：`_migrate` 每次取存档都会跑
    # （get_or_create_player / update_player 都调它），
    # 要是写成「少于 STOCK 就补」，玩家花掉的材料会立刻长回来 —— 等于无限材料。
    if int(player.get("talentStockVersion") or 0) != TALENT_STOCK_VERSION:
        items = player.get("items")
        if isinstance(items, dict):
            top_up_talent_materials(items)
            log.info("玩家 %s 补天赋材料（v%s，%d 种各 %d 个）",
                     player.get("account"), TALENT_STOCK_VERSION,
                     len(TALENT_MATERIAL_KEYS), TALENT_MATERIAL_STOCK)
        player["talentStockVersion"] = TALENT_STOCK_VERSION
        changed = True
    # 装备升级材料补货。同样的道理：老存档的 `items` 已经存在，「按 key 补字段」救不到，
    # 而且必须靠版本号做成一次性的（否则花掉的材料会被立刻补回来）。
    if int(player.get("equipmentStockVersion") or 0) != EQUIPMENT_STOCK_VERSION:
        items = player.get("items")
        if isinstance(items, dict):
            top_up_equipment_material(items)
            log.info("玩家 %s 补装备升级材料 %s（v%s，%d 个）",
                     player.get("account"), EQUIPMENT_UPGRADE_ITEM,
                     EQUIPMENT_STOCK_VERSION, EQUIPMENT_MATERIAL_STOCK)
        player["equipmentStockVersion"] = EQUIPMENT_STOCK_VERSION
        changed = True
    # 好感度礼物补货（47 件）。同天赋/装备材料：老存档的 `items` 已存在，
    # 「按 key 补字段」救不到，必须靠版本号做成一次性的。
    if int(player.get("giftStockVersion") or 0) != GIFT_STOCK_VERSION:
        bag = player.get("items")
        if isinstance(bag, dict):
            n = top_up_gifts(bag)
            if n:
                log.info("玩家 %s 补好感度礼物（v%s，%d 种各 %d 个）",
                         player.get("account"), GIFT_STOCK_VERSION, n, GIFT_STOCK)
        player["giftStockVersion"] = GIFT_STOCK_VERSION
        changed = True
    # 抽卡碎片（好人卡）补货。同上：老存档的 items 已存在，只能靠版本号补一次。
    if int(player.get("fragmentStockVersion") or 0) != FRAGMENT_STOCK_VERSION:
        if top_up_fragments(player):
            changed = True
    # 英雄 / 机甲列表（抽卡会往里加）。老存档里没有这两个字段 → 补一个默认的，
    # 否则登录块 `char.heros`/`char.mechas` 是空的，客户端一个英雄/机甲都没有。
    if not isinstance(player.get("heros"), list) or not player.get("heros"):
        player["heros"] = [new_hero()]
        changed = True
    if not isinstance(player.get("mechas"), list) or not player.get("mechas"):
        player["mechas"] = [new_mecha()]
        changed = True
    # 装备的属性 key。早期发出去的装备行 `firstAttrKeys` 是空数组，而客户端
    # `addEquipmentAttrByKeys` 是**先取值后判长度** → 空数组也崩（强化界面打不开）。
    # 无条件重算一遍，顺带兼容「升级后 key 要跟着等级走」（见 equipment_attr_key）。
    if refresh_equipment_attrs(player):
        changed = True
        log.info("玩家 %s 重算了装备属性 key", player.get("account"))
    # 队伍 id 对齐成 0 起下标。
    #
    # ⚠️ 客户端认的队伍 id 就是**服务端 teams 数组的下标**（`Player.initTeams` 里
    # `new Team(this._teams[i], this._character, i)`），`player.updateteams` 发的也是 "0".."4"。
    # 早期 `new_team()` 给的是 `id = index + 1`，两边对不上 → 编成保存**整单被静默跳过**，
    # 症状是「编好的队伍一直消失」（重登又是空的）。这里在加载时无条件对齐（幂等）。
    if normalize_team_ids(player):
        changed = True
        log.info("玩家 %s 队伍 id 对齐成 0 起下标（原来可能是 index+1）",
                 player.get("account"))
    return changed


def player_exists(account: str) -> bool:
    with _lock:
        return account in _load()


# 玩家数字 ID 的起始号。和 NPC 好友的号段（`friends.NPC_ID_BASE` = 900001）错开，
# 免得搜好友时搜到自己人。
NUMBER_ID_START = 100001


def _next_number_id(account: str | None) -> int:
    """给一个还没号的老存档分配数字 ID：从 `NUMBER_ID_START` 起找没被占的最小号。

    `_migrate` 是在 `_lock` 里被调的（`_load` / `all_players` 都拿同一把
    RLock，可重入），所以这里直接读全库是安全的。只在真的缺号时调一次。
    """
    used = set()
    for name, row in all_players().items():
        if name == account or not isinstance(row, dict):
            continue
        try:
            used.add(int(row.get("numberId") or 0))
        except (TypeError, ValueError):
            continue
    number_id = NUMBER_ID_START
    while number_id in used:
        number_id += 1
    return number_id


def get_or_create_player(account: str) -> dict:
    with _lock:
        db = _load()
        if account not in db:
            db[account] = new_player(account)
            _save(db)
            log.info("创建玩家 %s", account)
        elif _migrate(db[account]):
            _save(db)
            log.info("补齐玩家 %s 缺失的字段", account)
        return db[account]


def update_player(account: str, **fields) -> dict:
    with _lock:
        db = _load()
        player = db.setdefault(account, new_player(account))
        _migrate(player)
        player.update(fields)
        _save(db)
        return player


def save_player(player: dict) -> None:
    """把改过的 player 写回 players.json。

    ⚠️ 这个很容易踩：`get_or_create_player()` 每次都从文件重新 load，
    返回的是**临时副本**，直接改它、不写回去就全丢了。
    主线任务领奖曾经就栽在这里 —— 每次 `done` 都从空开始，
    日志里永远是「累计 1 条」，客户端下次刷新又看到同一条能领，
    表现就是「反复刷新，顶上一直是这两条任务」。
    """
    account = player.get("account") or config.DEFAULT_ACCOUNT
    save_player_dict(account, player)


def save_player_dict(account: str, player: dict) -> None:
    """按账号整份覆盖存档。

    `save_player` 是「拿传入的这份去合并覆盖」，调用方必须先
    `get_or_create_player()` 拿到当前存档、改完再传进来。
    devtools 的存档编辑面板拿到的是一整个 JSON，直接用这个更直白：
    传进来什么就是什么（仍然带上 `account`，免得两份数据对不上号）。
    """
    player = dict(player)
    player["account"] = account
    with _lock:
        db = _load()
        db[account] = player
        _save(db)


def all_players() -> dict:
    with _lock:
        return _load()
