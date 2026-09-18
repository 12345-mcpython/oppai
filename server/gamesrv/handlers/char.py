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
    """军士升级。

    msg（默认按「一键升满」处理）::

        {"id": 军士id, "key": 军士key, "materials": {...升级材料...}}

    客户端的 `calcSoldierUpgrade(data, soldier)` 会本地算 `tarLv/tarExp/tarSkillLv`，
    服务端这边没复刻那套经验曲线，就先按「每次升 1 级」推进（材料也只做扣减记录）。
    """
    player = _player(session)
    soldier = store.find_soldier(player, (msg or {}).get("id"))
    if soldier is None:
        log.warning("upgradesoldierlv 找不到军士 id=%s", (msg or {}).get("id"))
        return {"code": FAIL, "msg": "no such soldier", "data": {}}

    old_lv = int(soldier.get("lv") or 1)
    materials = (msg or {}).get("materials") or {}
    # 客户端是「一键升到目标等级」，materials 里带着它算出来的目标；能读到就用，读不到就 +1
    try:
        target = int(materials.get("tarLv") or materials.get("lv") or 0)
    except (TypeError, ValueError):
        target = 0
    new_lv = max(old_lv + 1, target) if target else old_lv + 1

    soldier["lv"] = new_lv
    soldier["curExp"] = 0
    # 技能等级跟着等级走（客户端的 calcSoldierUpgrade 也是这么给的）
    soldier["skillLv"] = int(soldier.get("skillLv") or 1)

    # 任务：13203「首次军士升级」/ 13102「某军阶军士等级达到 N 级」
    quests.on_soldier_upgrade(player, new_lv, soldier.get("quality"))
    store.save_player(player)

    log.info("军士升级 id=%s key=%s lv %s -> %s（任务进度已推进）",
             soldier.get("id"), soldier.get("key"), old_lv, new_lv)
    return {"code": CODE_OK, "msg": "", "data": {"soldier": _soldier_payload(soldier)}}


@route("char.improvesoldierstar")
def improve_soldier_star(session: dict, msg: dict, req_id):
    """军士突破（升星）。任务 210003「首次突破」靠它。"""
    player = _player(session)
    soldier = store.find_soldier(player, (msg or {}).get("id"))
    if soldier is None:
        return {"code": FAIL, "msg": "no such soldier", "data": {}}

    old_star = int(soldier.get("star") or 1)
    soldier["star"] = old_star + 1
    quests.on_soldier_star(player)
    store.save_player(player)

    log.info("军士突破 id=%s star %s -> %s", soldier.get("id"), old_star, soldier["star"])
    return {"code": CODE_OK, "msg": "", "data": {"soldier": _soldier_payload(soldier)}}


@route("char.upgradesoldierskill")
def upgrade_soldier_skill(session: dict, msg: dict, req_id):
    player = _player(session)
    soldier = store.find_soldier(player, (msg or {}).get("id"))
    if soldier is None:
        return {"code": FAIL, "msg": "no such soldier", "data": {}}
    soldier["skillLv"] = int(soldier.get("skillLv") or 1) + 1
    store.save_player(player)
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
    """分解军士。先只标记 isDel，不发奖励（背包奖励以后再说）。"""
    player = _player(session)
    for key in (msg or {}).get("keys") or []:
        soldier = store.find_soldier(player, key)
        if soldier is not None:
            soldier["isDel"] = 1
    store.save_player(player)
    return {"code": CODE_OK, "msg": "", "data": {"rewards": []}}
