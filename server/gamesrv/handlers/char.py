"""char.* —— 军士 / 角色养成。

反汇编 `src/data/soldier.jsc` 的 `Soldier.requestUpgradeLv(data, cb)`：

    if (this.checkRequestUpgrade(data)) {
        if (dataManager.isLogin) {
            server.request('char.upgradesoldierlv',
                           {id: this._id, key: this._key, materials: data}, cb);
        } else {                       // 离线分支，正好把回包形状告诉我们
            var res = dataManager.character.calcSoldierUpgrade(data, this);
            this.requestUpgradeCb(null, {
                code: 200,
                data: {soldier: {lv: res.tarLv, curExp: res.tarExp, skillLv: res.tarSkillLv}}
            });
        }
    }

所以回包要的是：

    {"code":200,"msg":"","data":{"soldier":{"lv":新等级,"curExp":经验,"skillLv":技能等级}}}

（`requestUpgradeCb` 拿它更新本地那个 Soldier，同时会重算属性。）

⚠️ 军士存在**玩家存档**里（`store.ensure_soldiers`），不是每次登录现生成，
否则升级一重登就回退了。
"""

from __future__ import annotations

from .. import config, logx, quests, store
from .. import soldier as soldier_calc
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.char")

FAIL = 1


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


def _soldier_payload(soldier: dict) -> dict:
    return {
        "id": soldier.get("id"),
        "key": soldier.get("key"),
        "lv": soldier.get("lv") or 1,
        "curExp": soldier.get("curExp") or 0,
        "star": soldier.get("star") or 1,
        "quality": soldier.get("quality") or 1,
        "skillLv": soldier.get("skillLv") or 1,
        "skillLvList": soldier.get("skillLvList") or [],
    }


@route("char.upgradesoldierlv")
def upgrade_soldier_lv(session: dict, msg: dict, req_id):
    """军士升级（界面上的「培养」）。

    反汇编 `Soldier.requestUpgradeLv` + `SoldierDetailLayer._onClickUpgradeButton`，
    请求就是三个字段::

        {"id": 目标军士id, "key": 目标军士key, "materials": [被吃掉的军士id, ...]}

    ⚠️ `materials` 是**军士 id 的数组**（不是对象）——
    `SoldierCheckBoxListLayer._targets` 是以 id 为下标的稀疏数组，
    确认时 `for (k in _targets) if (_targets[k]) out.push(k)`。
    这些材料军士会被**真的吃掉**：客户端在 `requestUpgradeCb` 里立刻调
    `character.removeSoldier(materials)`，所以服务端也必须删，
    否则一重登材料又回来了。

    回包要 `{"code":200,"msg":"","data":{"soldier":{lv,curExp,skillLv}}}`。
    等级/经验不是"每次 +1"，是按客户端的经验曲线算的（见 gamesrv/soldier.py），
    不然面板预览和点完的结果会对不上。
    """
    player = _player(session)
    materials = (msg or {}).get("materials") or []
    soldier = store.find_soldier(player, (msg or {}).get("id"))
    if soldier is None:
        log.warning("upgradesoldierlv 找不到军士 id=%s", (msg or {}).get("id"))
        return {"code": FAIL, "msg": "no such soldier", "data": {}}
    if soldier.get("id") in materials:
        log.warning("upgradesoldierlv 把目标自己当材料了 id=%s", soldier.get("id"))
        return {"code": FAIL, "msg": "bad materials", "data": {}}

    old_lv = int(soldier.get("lv") or 1)
    result = soldier_calc.calc_upgrade(player, soldier, materials)
    if not result["used"]:
        log.warning("upgradesoldierlv 一个有效材料都没有 msg=%s", msg)
        return {"code": FAIL, "msg": "no materials", "data": {}}

    soldier["lv"] = result["tarLv"]
    soldier["curExp"] = result["tarExp"]
    soldier["skillLv"] = result["tarSkillLv"]
    soldier["isNew"] = 0

    # 材料被吃掉。客户端已经本地删了，服务端不删的话重登就复活。
    eaten = set(result["used"])
    player["soldiers"] = [s for s in store.ensure_soldiers(player)
                          if int(s.get("id") or 0) not in eaten]
    # 上阵队伍里如果带着被吃掉的军士，一并摘掉（否则战斗时找不到人会崩）
    for team in player.get("teams") or []:
        keys = team.get("soldierKeys")
        if isinstance(keys, list) and any(int(k) in eaten for k in keys if isinstance(k, int)):
            team["soldierKeys"] = [k for k in keys if not (isinstance(k, int) and k in eaten)]
            team["soldierCount"] = len(team["soldierKeys"])

    # 任务：13203「首次军士升级」/ 13102「某军阶军士等级达到 N 级」
    quests.on_soldier_upgrade(player, soldier["lv"], soldier.get("quality"))
    store.save_player(player)

    log.info("军士培养 id=%s key=%s lv %s -> %s (exp %s) 吃掉 %s 个材料、%s 萌钞",
             soldier.get("id"), soldier.get("key"), old_lv, soldier["lv"],
             result["gainExp"], len(result["used"]), result["needMoney"])
    return {"code": CODE_OK, "msg": "", "data": {"soldier": _soldier_payload(soldier)}}


@route("char.improvesoldierstar")
def improve_soldier_star(session: dict, msg: dict, req_id):
    """军士突破（升星）。

    请求只有 id：`Soldier.requestImproveStar` 发的是 `{id: this._id}`，
    回包要 `{"data":{"soldier":{"star":新星级}}}`（`requestImproveStarCb` 只读 star）。

    星级上限取自 `table_soldier_constant.max_star_<quality>`（品质 4 是 5 星），
    等级上限按 `table_soldier_lv_limit[star]` 涨。任务 210003「首次突破」靠它。
    """
    player = _player(session)
    soldier = store.find_soldier(player, (msg or {}).get("id"))
    if soldier is None:
        return {"code": FAIL, "msg": "no such soldier", "data": {}}

    quality = int(soldier.get("quality") or 1)
    cap = soldier_calc.max_star(quality)
    old_star = int(soldier.get("star") or 1)
    if cap and old_star >= cap:
        log.info("军士突破 id=%s 已经满星 %s/%s", soldier.get("id"), old_star, cap)
        return {"code": FAIL, "msg": "max star", "data": {}}

    soldier["star"] = old_star + 1
    quests.on_soldier_star(player)
    store.save_player(player)

    log.info("军士突破 id=%s star %s -> %s", soldier.get("id"), old_star, soldier["star"])
    return {"code": CODE_OK, "msg": "", "data": {"soldier": _soldier_payload(soldier)}}


@route("char.upgradesoldierskill")
def upgrade_soldier_skill(session: dict, msg: dict, req_id):
    """军士技能升级。

    请求是 `{id: 目标军士id, materialId: 被吃掉的军士id}`
    （`Soldier.requestUpgradeSkill` 发的是 `data[0]`，也就是技能卡 id）。

    回包只要 `code==200` —— `requestUpgradeSkillCb` 只判断 code，
    成功后自己把 `_mainSkill.lv` +1 并调 `_updateForChange`。
    技能等级上限取自 `table_soldier_constant.max_skill_lv_<quality>`。
    """
    player = _player(session)
    soldier = store.find_soldier(player, (msg or {}).get("id"))
    if soldier is None:
        return {"code": FAIL, "msg": "no such soldier", "data": {}}

    quality = int(soldier.get("quality") or 1)
    cap = soldier_calc.max_skill_lv(quality)
    old_lv = int(soldier.get("skillLv") or 1)
    if cap and old_lv >= cap:
        log.info("军士技能 id=%s 已经满级 %s/%s", soldier.get("id"), old_lv, cap)
        return {"code": FAIL, "msg": "max skill lv", "data": {}}

    material_id = (msg or {}).get("materialId")
    material = store.find_soldier(player, material_id)
    if material_id is not None and material is None:
        log.warning("upgradesoldierskill 材料 %r 不在玩家名下", material_id)
        return {"code": FAIL, "msg": "no such material", "data": {}}

    soldier["skillLv"] = old_lv + 1
    # 技能卡同样会被吃掉（客户端 requestUpgradeSkillCb 也会本地 removeSoldier）
    if material is not None:
        eaten = {int(material.get("id") or 0)}
        player["soldiers"] = [s for s in store.ensure_soldiers(player)
                              if int(s.get("id") or 0) not in eaten]
    # skillLvList 是客户端 isNormalData 之外还会读的一份，长度跟着 skill_index 走，
    # 这里把所有位数同步到同一个值，免得某个技能位还是老等级。
    soldier["skillLvList"] = [soldier["skillLv"]] * max(1, len(soldier.get("skillLvList") or [1]))

    store.save_player(player)
    log.info("军士技能升级 id=%s skillLv %s -> %s（吃材料 %s）",
             soldier.get("id"), old_lv, soldier["skillLv"], material_id)
    return {"code": CODE_OK, "msg": "", "data": {"soldier": _soldier_payload(soldier)}}


@route("char.setsoldiermark")
def set_soldier_mark(session: dict, msg: dict, req_id):
    """锁定 / 标记。客户端只看 code==200。"""
    player = _player(session)
    soldier = store.find_soldier(player, (msg or {}).get("id"))
    if soldier is not None:
        for field in ("isLock", "isNew", "isDetect"):
            if field in (msg or {}):
                soldier[field] = (msg or {})[field]
        store.save_player(player)
    return {"code": CODE_OK, "msg": "", "data": {}}


@route("char.sellsoldiers")
def sell_soldiers(session: dict, msg: dict, req_id):
    """分解军士。

    ⚠️ 字段名是 **`materials`** 不是 `keys` ——
    `CharCenter.requestSellSoldiers` 发的是 `{materials: [军士id, ...]}`，
    和培养走的是同一套「id 数组」（都来自 `SoldierCheckBoxListLayer._targets`）。
    这里直接从名单里删掉（客户端 `requestSellSoldiersCb` 也是本地删）。

    分解奖励暂时不发（`table_soldier_to_price` 那套还没接），先回空数组。
    """
    player = _player(session)
    ids = set()
    for raw in (msg or {}).get("materials") or (msg or {}).get("keys") or []:
        try:
            ids.add(int(raw))
        except (TypeError, ValueError):
            continue
    if ids:
        player["soldiers"] = [s for s in store.ensure_soldiers(player)
                              if int(s.get("id") or 0) not in ids]
        for team in player.get("teams") or []:
            keys = team.get("soldierKeys")
            if isinstance(keys, list):
                team["soldierKeys"] = [k for k in keys
                                       if not (isinstance(k, int) and k in ids)]
                team["soldierCount"] = len(team["soldierKeys"])
        store.save_player(player)
    log.info("分解军士 %s 个：%s", len(ids), sorted(ids))
    return {"code": CODE_OK, "msg": "", "data": {"rewards": []}}
