"""军士养成：升级（培养）、突破、技能升级的数值。

数值全部来自客户端表（`tools/extract_client_tables.py` 抽的
`data/table_soldier.json`），因为**这套计算在原版里就是客户端本地做的**
（`CharCenter.calcSoldierUpgrade`），服务端只负责「认不认」。这里是把它
逐行复刻回服务端，不这么做的话界面预览和实际结果会对不上：
客户端先本地算出 `tarLv` 显示在面板上，点确认时才把材料发给服务端，
服务端回什么等级它就显示什么等级 —— 算得不一样就是「点一下跳一级」。

复刻自 `src/data/charcenter.jsc` 的 `calcSoldierUpgrade(materials, target)`::

    gainExp = 0; needMoney = 0;
    for (k in materials) {
        soldier = (typeof materials[k] === "string" || "number")
                  ? this._soldiers[materials[k]]      // 传的是军士 id
                  : materials[k];                     // 传的是对象
        gainExp   += table_soldier_to_exp[soldier.lv]["quality_"+soldier.quality+"_"+soldier.star];
        needMoney += table_soldier[soldier.key].base_cost
                   + table_soldier_to_cost_for_upgrade[soldier.lv]["quality_"+soldier.quality+"_"+soldier.star];
    }
    tarLv = target.lv; tarExp = target.curExp + gainExp;
    key = "quality_" + target.quality;
    while (true) {
        if (tarLv >= target.maxLv) { tarLv = target.maxLv; tarExp = 0; break; }
        maxExp = table_soldier_upgrade_exp[tarLv][key];
        if (tarExp < maxExp) break;
        tarExp -= maxExp;
        tarLv++;
    }
    tarMaxExp = table_soldier_upgrade_exp[tarLv][key];
    return {gainExp, needMoney, skillLv, tarLv, tarExp, tarSkillLv, tarMaxExp};

⚠️ `materials` 里装的是**军士 id 的数字数组**，不是对象。见
`SoldierCheckBoxListLayer._onClickConfirmButton`：`_targets` 是以 id 为下标的
稀疏数组，回调时 `for (k in _targets) if (_targets[k]) out.push(k)`，
拿到的 `targets` 就是一串 id；`SoldierDetailLayer._upgradeMaterials` 同理。
（不过 `calcSoldierUpgrade` 两种都吃，所以下面 `_material_of` 也两种都认，
免得以后哪条入口换成了传对象就直接算错。）
"""

from __future__ import annotations

import json
import os
import threading

from . import logx

log = logx.get("soldier")

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_lock = threading.RLock()
_cache: dict | None = None

# table_soldier_upgrade_exp 里到顶之后没有更高的一条，用它兜底（服务端算
# tarMaxExp 时如果越界就给 0，客户端那边的 `|| 0` 也是这个意思）。
_EMPTY_ROW: dict = {}


def tables() -> dict:
    """{"upgrade_exp":..., "to_exp":..., "to_cost":..., "lv_limit":..., "constant":..., "card":...}"""
    global _cache
    with _lock:
        if _cache is None:
            path = os.path.join(_DATA_DIR, "table_soldier.json")
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    _cache = json.load(fh)
            except Exception:  # noqa: BLE001
                log.warning("载入 %s 失败，军士升级会退化成「每次 +1 级」", path)
                _cache = {}
        return _cache


def _row(table: str, key) -> dict:
    got = (tables().get(table) or {}).get(str(key))
    return got if isinstance(got, dict) else _EMPTY_ROW


def max_lv(quality: int, star: int) -> int:
    """军士等级上限。table_soldier_lv_limit[star]["quality_<q>"]。"""
    return int(_row("lv_limit", star).get("quality_%d" % quality) or 0)


def max_star(quality: int) -> int:
    """军士星级上限。table_soldier_constant["max_star_<q>"]（品质 4 -> 5 星）。"""
    const = tables().get("constant") or {}
    return int(const.get("max_star_%d" % quality) or const.get("max_star") or 0)


def max_skill_lv(quality: int) -> int:
    """军士技能等级上限。table_soldier_constant["max_skill_lv_<q>"]。"""
    const = tables().get("constant") or {}
    return int(const.get("max_skill_lv_%d" % quality) or 0)


def exp_to_next(lv: int, quality: int) -> int:
    """从 lv 升到 lv+1 需要多少经验。"""
    return int(_row("upgrade_exp", lv).get("quality_%d" % quality) or 0)


def card_cost(key: str) -> int:
    """这张军士卡自身的 `base_cost`（喂掉时额外算的萌钞）。

    ⚠️ 是 `base_cost` 不是 `base_exp`。用实测值反推过：
    喂一个 `sasm010104`（template 4）时客户端回 `needMoney = 50000`，
    正好等于 `table_soldier['sasm010104'].base_cost`。
    """
    return int((tables().get("card") or {}).get(str(key), {}).get("c") or 0)


def char_key_of(key: str) -> str:
    """军士表的 key（`sads010101`）-> 角色 key（`sads`）。

    走 `table_soldier[key].char_key`，抽取时压成了 `card.<key>.ck`。
    好感度那条链路要用它把 `Team.soldierKeys`（**军士 id**）翻成角色 key
    —— 客户端 `Team.getSoldierKeys()` 给的是 `_soldiers[i].charKey`。
    """
    return str(((tables().get("card") or {}).get(str(key)) or {}).get("ck") or "")


# CARD_TYPE（客户端 src/data/soldier.js）
CARD_TYPE_TEAMMATE = 1
CARD_TYPE_ENEMY = 2
CARD_TYPE_EXP = 3
CARD_TYPE_SKILL = 4


def card_type(key: str) -> int:
    """这个 key 的 `card_type`（按 **char_key** 查 table_soldier_master）。

    没有这张表（老版本抽的数据）就返回 0，调用方自己决定怎么兜。
    """
    row = (tables().get("card") or {}).get(str(key))
    if not isinstance(row, dict):
        return 0
    char_key = row.get("ck") or ""
    if not char_key:
        return 0
    value = (tables().get("master") or {}).get(char_key)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def is_teammate(key: str) -> bool:
    """能不能当**玩家自己的军士**发出去。

    只有 `card_type == 1` 才行 —— 敌方单位（2）进了名单，客户端
    `calcSoldierUpgrade` 会因为缺 `base_cost` 算出 NaN，直接顶到等级上限。
    取不到 `card_type` 时保守放行（老数据），别把开发卡死。
    """
    value = card_type(key)
    return value in (0, CARD_TYPE_TEAMMATE)


def _qkey(quality, star) -> str:
    return "quality_%s_%s" % (quality, star)


def _material_of(player: dict, raw) -> dict | None:
    """把 materials 数组里的一项解析成玩家名下的军士 dict。

    正常是数字 id；也容忍 {"id":..} / {"_id":..} / 直接是军士 key。
    """
    from . import store  # 循环导入：store 不依赖 soldier，这里只是懒加载

    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        return store.find_soldier(player, int(raw))
    if isinstance(raw, str):
        # 纯数字当 id，否则当 key
        if raw.isdigit():
            got = store.find_soldier(player, int(raw))
            if got is not None:
                return got
        for s in store.ensure_soldiers(player):
            if s.get("key") == raw:
                return s
        return None
    if isinstance(raw, dict):
        for field in ("id", "_id"):
            if raw.get(field) is not None:
                got = store.find_soldier(player, raw[field])
                if got is not None:
                    return got
        key = raw.get("key") or raw.get("_key")
        if key:
            for s in store.ensure_soldiers(player):
                if s.get("key") == key:
                    return s
    return None


def _field(soldier: dict, *names, default=0):
    for name in names:
        if name in soldier and soldier[name] is not None:
            return soldier[name]
    return default


def _calc(target: dict, materials: list) -> dict:
    """纯计算部分，不碰玩家存档（`tools/check_soldier_calc.py` 靠它对齐客户端）。

    `materials` 每一项要有 `lv / quality / star / key`。
    """
    gain_exp = 0
    need_money = 0
    for m in materials or []:
        m_lv = int(_field(m, "lv", default=1) or 1)
        m_quality = int(_field(m, "quality", default=1) or 1)
        m_star = int(_field(m, "star", default=1) or 1)
        gain_exp += int(_row("to_exp", m_lv).get(_qkey(m_quality, m_star)) or 0)
        need_money += card_cost(_field(m, "key", default=""))
        need_money += int(_row("to_cost", m_lv).get(_qkey(m_quality, m_star)) or 0)

    quality = int(_field(target, "quality", default=1) or 1)
    # 目标身上的 maxLv（客户端 `Soldier.maxLv`）可能没跟着存档走，
    # 缺了就按 quality/star 现查一次。
    limit = int(_field(target, "maxLv", default=0) or 0)
    if limit <= 0:
        limit = max_lv(quality, int(_field(target, "star", default=1) or 1))

    tar_lv = int(_field(target, "lv", default=1) or 1)
    tar_exp = int(_field(target, "curExp", default=0) or 0) + gain_exp

    if limit > 0:
        while True:
            if tar_lv >= limit:
                tar_lv = limit
                tar_exp = 0
                break
            need = exp_to_next(tar_lv, quality)
            if need <= 0 or tar_exp < need:
                break
            tar_exp -= need
            tar_lv += 1

    return {
        "gainExp": gain_exp,
        "needMoney": need_money,
        "tarLv": tar_lv,
        "tarExp": tar_exp,
        "tarSkillLv": int(_field(target, "skillLv", default=1) or 1),
        "tarMaxExp": exp_to_next(tar_lv, quality),
    }


def calc_upgrade(player: dict, target: dict, materials: list) -> dict:
    """复刻 `CharCenter.calcSoldierUpgrade`。

    :param materials: 客户端传来的材料（军士 id 数组，客户端解析后由调用方传进来）
    :returns: 同客户端：`{gainExp, needMoney, tarLv, tarExp, tarSkillLv, tarMaxExp, used}`
              `used` 是真正被吃掉的材料 id 列表（去重、且确实存在于玩家名下）。
    """
    resolved: list[dict] = []
    used: list[int] = []
    seen: set[int] = set()

    for raw in materials or []:
        m = _material_of(player, raw)
        if m is None:
            log.warning("培养材料 %r 不在玩家名下，跳过", raw)
            continue
        sid = int(_field(m, "id", default=0))
        if sid in seen:
            continue
        seen.add(sid)
        resolved.append(m)
        used.append(sid)

    result = _calc(target, resolved)
    result["used"] = used
    return result
