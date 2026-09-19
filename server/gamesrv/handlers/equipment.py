"""equipment.* —— 装备。

结论全部来自反编译 `src/data/equipmentcenter.jsc`（`EquipmentCenter`，19KB，能过
`node --check`）+ 逐函数反汇编。三个形状：

## 登录块（`EquipmentCenter.ctor(data)`）

    {maxEquipmentCount, equipments, equipmentGroups}

`equipments` 是**数组**，每项
`{id, key, soldierId, isLock, isNew, lv, firstAttrKeys[], secondAttrKeys[]}`。

⚠️ `firstAttrKeys` / `secondAttrKeys` **必须是数组**，`name / type / quality / suitKey`
**不用发** —— 客户端 `initEquipment(eq)` 自己从 `table_equipment[eq.key]` 补，
但它第一件事就是读 `eq.secondAttrKeys.length`，缺了直接 TypeError。

## 响应推送（`EquipmentCenter.updateByServer(data)`；`equipment` 在 responseConfig 里）

    {maxEquipmentCount?, equipmentAdd?, updateList?, removeList?, equipmentGroups?}

* `equipmentAdd` / `updateList` 都是 `this._equipments[row.id] = new Equipment(row)`
  —— 所以**整行**要带上，不是只给 id
* `removeList` 只放 id
* ⚠️ 客户端只把「请求成功了吗」当事（`code == 200` 弹个 toast），**真正改状态靠
  响应里的 `equipment` 块**。所以每条路由都必须回 `{"equipment": {...}}`，
  回 `{}` 的话界面不会动。

## 7 条路由的请求体（都是字节码里读出来的）

    equipment.putonequipment       { <装备id>: <军士id> }   动态 key
    equipment.tookoffequipment     [<装备id>, ...]          裸数组
    equipment.upgradeequipment     {id, upLv}               upLv = 升几级
    equipment.decomposeequipments  [<装备id>, ...]          裸数组
    equipment.setlock              { <装备id>: 0|1 }        动态 key
    equipment.setnew               { <装备id>: 0|1 }        和 setlock 同形状
                                                            （它的原子表里就是 checkSetLock）
    equipment.replaceequipmentgroup  编组，形状没能确定，见该路由的说明
"""

from __future__ import annotations

from .. import config, items, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.equipment")

# 失败码。客户端回调只在 `code == 200` 时才弹成功提示、才认为改了本地状态，
# 其余情况弹一个 popupTop(table_dictionary[634] + ",code:" + code)。
FAIL = 1


def _ctx(session: dict):
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return account, store.get_or_create_player(account)


def _payload(**kw) -> dict:
    """响应里的 `equipment` 块。值为 None 的字段不发（客户端按 `if (data.xxx)` 判断）。"""
    equip = {k: v for k, v in kw.items() if v is not None}
    return {"code": CODE_OK, "msg": "", "data": {"equipment": equip}}


def _cfg(row: dict) -> dict:
    """`table_equipment[row.key]`。服务端抄的表只留了 `n/t/q/m` 四个字段。"""
    return items.table("table_equipment").get(str(row.get("key"))) or {}


def _type_of(row: dict) -> str:
    return str(_cfg(row).get("t") or "")


def _quality_of(row: dict) -> str:
    return str(_cfg(row).get("q") or "1")


def _max_lv_of(row: dict) -> int:
    try:
        return int(_cfg(row).get("m") or store.EQUIPMENT_MAX_LV)
    except (TypeError, ValueError):
        return store.EQUIPMENT_MAX_LV


def _level_row(row: dict, lv: int) -> dict:
    """`table_equipment_level["<quality>#<lv>"]` —— 升级消耗 / 分解产出都在这。"""
    return items.table("table_equipment_level").get(f"{_quality_of(row)}#{lv}") or {}


def _copy(row: dict) -> dict:
    """回给客户端的整行。深拷一层，免得后续改动影响已发出的包。"""
    out = dict(row)
    out["firstAttrKeys"] = list(row.get("firstAttrKeys") or [])
    out["secondAttrKeys"] = list(row.get("secondAttrKeys") or [])
    return out


# ---------------------------------------------------------------------------
# 穿 / 脱
# ---------------------------------------------------------------------------
@route("equipment.putonequipment")
def put_on_equipment(session: dict, msg: dict, req_id):
    """装到军士身上。请求体是**动态 key**：`{"3": "12"}`（装备id -> 军士id）。

    一个 type 只能挂一件：装新的会把同一军士身上同 type 的旧件摘下来
    （编组的形状就是 `equipments: {type: 装备id}`，一个 type 一个坑）。
    """
    account, player = _ctx(session)
    rows = store.player_equipments(player)
    changed = []
    for eq_id, soldier_id in (msg or {}).items():
        row = store.find_equipment(player, eq_id)
        if row is None:
            log.warning("putonequipment 没有这件装备 id=%s（msg=%s）", eq_id, msg)
            continue
        soldier_id = str(soldier_id or "")
        eq_type = _type_of(row)
        if soldier_id and eq_type:
            for other in rows:
                if (other is not row
                        and str(other.get("soldierId") or "") == soldier_id
                        and _type_of(other) == eq_type):
                    other["soldierId"] = ""
                    changed.append(_copy(other))
        row["soldierId"] = soldier_id
        row["isNew"] = 0
        changed.append(_copy(row))

    if not changed:
        return {"code": FAIL, "msg": "no such equipment", "data": {}}
    store.save_player(player)
    log.info("equipment.putonequipment account=%s %s", account,
             [(r.get("id"), r.get("soldierId")) for r in changed])
    return _payload(updateList=changed)


@route("equipment.tookoffequipment")
def took_off_equipment(session: dict, msg: dict, req_id):
    """摘下来。请求体是**裸数组**：`[3, 4]`。"""
    account, player = _ctx(session)
    ids = msg if isinstance(msg, list) else (msg or {}).get("idList") or []
    changed = []
    for eq_id in ids:
        row = store.find_equipment(player, eq_id)
        if row is None:
            log.warning("tookoffequipment 没有这件装备 id=%s", eq_id)
            continue
        row["soldierId"] = ""
        changed.append(_copy(row))

    if not changed:
        return {"code": FAIL, "msg": "no such equipment", "data": {}}
    store.save_player(player)
    log.info("equipment.tookoffequipment account=%s 摘下 %s", account,
             [r.get("id") for r in changed])
    return _payload(updateList=changed)


# ---------------------------------------------------------------------------
# 升级
# ---------------------------------------------------------------------------
@route("equipment.upgradeequipment")
def upgrade_equipment(session: dict, msg: dict, req_id):
    """升级。请求体 `{"id": 3, "upLv": 1}`。

    消耗来自客户端 `table_equipment_level["<quality>#<lv>"]` 的
    `upgrade_money` + `upgrade_material`（材料 = `equipment_level_up_key` = 100401）。
    等级上限是 `table_equipment[key].max_lv`（客户端表里 15）。
    """
    account, player = _ctx(session)
    row = store.find_equipment(player, (msg or {}).get("id"))
    if row is None:
        log.warning("upgradeequipment 没有这件装备 id=%s", (msg or {}).get("id"))
        return {"code": FAIL, "msg": "no such equipment", "data": {}}

    try:
        want = int((msg or {}).get("upLv") or 1)
    except (TypeError, ValueError):
        want = 1
    want = max(1, want)

    cur = int(row.get("lv") or 0)
    max_lv = _max_lv_of(row)

    # 逐级算总账，**先算再扣** —— 扣一半才发现差钱的话材料就白没了
    money = material = steps = 0
    while steps < want and cur + steps < max_lv:
        cost = _level_row(row, cur + steps)
        if not cost:
            break
        money += int(cost.get("upgrade_money") or 0)
        material += int(cost.get("upgrade_material") or 0)
        steps += 1

    if steps <= 0:
        log.info("equipment.upgradeequipment id=%s 已经满级（%s）", row.get("id"), max_lv)
        return {"code": FAIL, "msg": "max lv", "data": {}}

    need = []
    if money:
        need.append((store.ITEM_MONEY, money))
    if material:
        need.append((store.EQUIPMENT_UPGRADE_ITEM, material))
    if need and not items.can_afford(player, need):
        log.info("equipment.upgradeequipment id=%s 资源不够 需要=%s 现有=%s",
                 row.get("id"), need,
                 {k: items.count_of(player, k) for k, _ in need})
        return {"code": FAIL, "msg": "not enough", "data": {}}

    for key, count in need:
        items.sub_item(player, key, count)
    row["lv"] = cur + steps
    row["isNew"] = 0
    # ⚠️ `firstAttrKeys` 是随等级变的（同一组 lv0~lv15 各一行），必须重算 ——
    # 不然属性面板不更新，而且旧 key 在表里可能已经不存在了。
    store.refresh_equipment_attrs(player)
    store.save_player(player)
    log.info("equipment.upgradeequipment account=%s id=%s lv %s -> %s（升 %s 级，扣 %s）",
             account, row.get("id"), cur, row["lv"], steps, need or "无")
    return _payload(updateList=[_copy(row)])


# ---------------------------------------------------------------------------
# 分解
# ---------------------------------------------------------------------------
@route("equipment.decomposeequipments")
def decompose_equipments(session: dict, msg: dict, req_id):
    """分解。请求体是**裸数组**：`[5, 6]`。

    产出 `table_equipment_level["<quality>#<lv>"].decomposes_material`
    （quality 1 每件 10 个升级材料）。锁定 / 装在军士身上的不能分解 ——
    客户端的 `checkDecomposeEquipment` 也是这么拦的（toast 4030 / 4028）。
    """
    account, player = _ctx(session)
    ids = msg if isinstance(msg, list) else (msg or {}).get("idList") or []
    rows = store.player_equipments(player)

    removed, gain_material, gain_money = [], 0, 0
    for eq_id in ids:
        row = store.find_equipment(player, eq_id)
        if row is None:
            log.warning("decomposeequipments 没有这件装备 id=%s", eq_id)
            continue
        if int(row.get("isLock") or 0) == 1:
            log.info("decomposeequipments id=%s 锁着，跳过", eq_id)
            continue
        if str(row.get("soldierId") or ""):
            log.info("decomposeequipments id=%s 装在军士身上，跳过", eq_id)
            continue
        cost = _level_row(row, int(row.get("lv") or 0))
        gain_material += int(cost.get("decomposes_material") or 0)
        gain_money += int(cost.get("decompose_money") or 0)
        removed.append(row)

    if not removed:
        return {"code": FAIL, "msg": "nothing to decompose", "data": {}}

    gone = {id(r) for r in removed}
    player["equipments"] = [r for r in rows if id(r) not in gone]
    if gain_material:
        items.add_item(player, store.EQUIPMENT_UPGRADE_ITEM, gain_material)
    if gain_money:
        items.add_item(player, store.ITEM_MONEY, gain_money)
    store.save_player(player)
    log.info("equipment.decomposeequipments account=%s 分解 %s，得 %s 个 %s / %s 萌钞",
             account, [r.get("id") for r in removed], gain_material,
             store.EQUIPMENT_UPGRADE_ITEM, gain_money)
    return _payload(removeList=[r.get("id") for r in removed])


# ---------------------------------------------------------------------------
# 锁定 / 标记已读
# ---------------------------------------------------------------------------
@route("equipment.setlock")
def set_lock(session: dict, msg: dict, req_id):
    """上锁/解锁。请求体是**动态 key**：`{"3": 1}`（装备id -> 0|1）。"""
    return _set_flag(session, msg, "isLock", "setlock")


@route("equipment.setnew")
def set_new(session: dict, msg: dict, req_id):
    """清掉「新」标记。请求体和 `setlock` **同形状** ——
    反汇编里 `setNew` 的原子表第一个就是 `checkSetLock`，客户端用同一个校验。"""
    return _set_flag(session, msg, "isNew", "setnew")


def _set_flag(session: dict, msg: dict, field: str, route_name: str):
    account, player = _ctx(session)
    changed = []
    for eq_id, value in (msg or {}).items():
        row = store.find_equipment(player, eq_id)
        if row is None:
            log.warning("equipment.%s 没有这件装备 id=%s", route_name, eq_id)
            continue
        try:
            row[field] = int(value)
        except (TypeError, ValueError):
            log.warning("equipment.%s id=%s 的值不认识: %r", route_name, eq_id, value)
            continue
        changed.append(_copy(row))

    if not changed:
        return {"code": FAIL, "msg": "no such equipment", "data": {}}
    store.save_player(player)
    log.info("equipment.%s account=%s %s", route_name, account,
             [(r.get("id"), r.get(field)) for r in changed])
    return _payload(updateList=changed)


# ---------------------------------------------------------------------------
# 编组
# ---------------------------------------------------------------------------
@route("equipment.replaceequipmentgroup")
def replace_equipment_group(session: dict, msg: dict, req_id):
    """替换装备编组。

    ⚠️ **形状没能完全确定**：客户端 `replaceEquipmentGroup(params)` 是原样转发的，
    而 `EquipmentReplaceLayer` 里的调用点被反编译器丢掉了（`$slot` 占位）。
    已知的只有客户端那边的消费形状：`_initGroupEquipments` 收的是
    `[{index, equipments: {type: 装备id}}]`。

    所以这里宽容处理三种入参，任何一种都存得下：
      * 数组           -> 当作整份编组列表
      * {equipmentGroups: [...]} -> 同上
      * {index, equipments}      -> 只替换那一组
      * {type: 装备id} 这种平铺   -> 落到第 1 组
    拿到真实抓包后可以把这里收紧。
    """
    account, player = _ctx(session)
    groups = store.player_equipment_groups(player)

    if isinstance(msg, list):
        groups = list(msg)
    elif isinstance(msg, dict) and isinstance(msg.get("equipmentGroups"), list):
        groups = list(msg["equipmentGroups"])
    elif isinstance(msg, dict) and "from" in msg and "to" in msg:
        # ✅ 这才是真实的形状，从 logcat 抓到的：`{from: 1, to: 1}`
        #    （`from`/`to` 是编组 index，语义是两组之间的装备互换。
        #      `equipment_max_group = 1`，所以实际多半是 1 <-> 1，等于是空操作。）
        src, dst = int(msg.get("from") or 1), int(msg.get("to") or 1)
        by_idx = {int(g.get("index") or 0): g for g in groups}
        a, b = by_idx.get(src), by_idx.get(dst)
        if a is not None and b is not None:
            a["equipments"], b["equipments"] = (dict(b.get("equipments") or {}),
                                                dict(a.get("equipments") or {}))
        else:
            log.warning("replaceequipmentgroup from=%s to=%s 有编组不存在（现有 %s）",
                        src, dst, sorted(by_idx))
    elif isinstance(msg, dict) and "index" in msg:
        idx = int(msg.get("index") or 1)
        for g in groups:
            if int(g.get("index") or 0) == idx:
                g["equipments"] = dict(msg.get("equipments") or {})
                break
        else:
            groups.append({"index": idx, "equipments": dict(msg.get("equipments") or {})})
    elif isinstance(msg, dict) and msg:
        for g in groups:
            if int(g.get("index") or 0) == 1:
                g["equipments"] = dict(msg)
                break
    else:
        log.warning("replaceequipmentgroup 请求体不认识: %r", msg)
        return {"code": FAIL, "msg": "bad params", "data": {}}

    player["equipmentGroups"] = groups
    store.save_player(player)
    log.info("equipment.replaceequipmentgroup account=%s -> %s", account, groups)
    return _payload(equipmentGroups=groups)
