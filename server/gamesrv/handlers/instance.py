"""instance.* —— 关卡 / 副本相关路由。

route 名字是从 .jsc 里搜出来的（`(instance)\\.[a-z]+`）：

    instance.startlevel              开打前报到（主线走 BattleScene.combat，不走这里）
    instance.finishlevel             ★ 战斗结算，主线任务进度就靠它
    instance.getlevelpassinfo        查某关的通关信息
    instance.getchapterstarsreward   领章节星级奖励
    instance.openalllevels           调试用：全开
    instance.resetlevelsstarmark     重置关卡星级
    instance.resetlevelstimes        重置关卡次数
    instance.getactivityinstance     活动副本
    instance.getsubarealevel         分区关卡

只有 finishlevel 是必需的（见 gamesrv/instance.py 的说明），其余先给能用的最小实现。
"""

from __future__ import annotations

from .. import config, instance, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.instance")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


@route("instance.finishlevel")
def finish_level(session: dict, msg: dict, req_id):
    """战斗结算。

    客户端 `Instance.finishLevel` 拼的包：
        {levelId, starMark, rewards, battleInfo, subareaInfo,
         curTeamIdx, curExp, lv, actionPoint}

    回包里的 `data.level` 会被 `Instance._updateResult()` 拿去
    `level.updateLevel()`，所以星级/次数要放在那儿；`data.quest` 由客户端的
    RESP-DISPATCH 派发（一次胜利就是一次主线任务进度）。
    """
    player = _player(session)
    data = instance.finish_level(player, msg)
    if data is None:
        log.warning("finishlevel 没有 levelId: %s", msg)
        return {"code": CODE_OK, "msg": "", "data": {}}
    store.save_player(player)
    return {"code": CODE_OK, "msg": "", "data": data}


@route("instance.startlevel")
def start_level(session: dict, msg: dict, req_id):
    """开打前的报到。主线战斗其实是客户端本地起的（BattleScene.combat），
    只有子区域/活动那类才走这里，所以先只回 200 别让它卡住。"""
    player = _player(session)
    level_id = str((msg or {}).get("levelId") or "")
    log.info("instance.startlevel levelId=%s", level_id)
    return {"code": CODE_OK, "msg": "", "data": {"level": instance._record(player).get(level_id, {})}}


@route("instance.getlevelpassinfo")
def get_level_pass_info(session: dict, msg: dict, req_id):
    player = _player(session)
    return {"code": CODE_OK, "msg": "", "data": {"levels": instance._record(player)}}


@route("instance.getchapterstarsreward")
def get_chapter_stars_reward(session: dict, msg: dict, req_id):
    """章节星级奖励。奖励本身还没实现，先回 200 把流程走通。"""
    log.info("instance.getchapterstarsreward msg=%s", msg)
    return {"code": CODE_OK, "msg": "", "data": {"rewards": []}}


@route("instance.getactivityinstance")
def get_activity_instance(session: dict, msg: dict, req_id):
    return {"code": CODE_OK, "msg": "", "data": {"activityChapters": []}}


@route("instance.getsubarealevel")
def get_subarea_level(session: dict, msg: dict, req_id):
    """分区关卡的「每日次数 / 开放时间」map。

    ⚠️ `data` **本身就是那份 map**（不是 `{subareaLevels: ...}`）——
    反汇编 `Instance.updateSubareaLevel/<`：
        that._subareaLevels = data.data;      // ← 直接用
    形状与 `gamesrv/instance.py:subarea_levels` 的说明一致。
    """
    player = _player(session)
    block = instance.subarea_levels(player)
    log.info("instance.getsubarealevel -> %d 个分区关卡", len(block))
    return {"code": CODE_OK, "msg": "", "data": block}


@route("instance.openalllevels")
def open_all_levels(session: dict, msg: dict, req_id):
    """调试接口：客户端有个「全开」按钮会用这个。"""
    log.info("instance.openalllevels")
    return {"code": CODE_OK, "msg": "", "data": {}}


@route("instance.resetlevelsstarmark")
def reset_levels_star_mark(session: dict, msg: dict, req_id):
    player = _player(session)
    level_ids = (msg or {}).get("levelIds") or []
    rec = instance._record(player)
    for level_id in level_ids:
        if str(level_id) in rec:
            rec[str(level_id)]["starMark"] = -1
    store.save_player(player)
    return {"code": CODE_OK, "msg": "", "data": {"levels": rec}}


@route("instance.resetlevelstimes")
def reset_levels_times(session: dict, msg: dict, req_id):
    player = _player(session)
    level_ids = (msg or {}).get("levelIds") or []
    rec = instance._record(player)
    for level_id in level_ids:
        if str(level_id) in rec:
            rec[str(level_id)]["challengeTimes"] = 0
    store.save_player(player)
    return {"code": CODE_OK, "msg": "", "data": {"levels": rec}}
