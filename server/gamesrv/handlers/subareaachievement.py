"""subareaachievement.* —— 分区成就领奖。

客户端只有**一条**路由（`jsc_find.py --regex "subareaachievement\\.[a-z]+"`）：

    SubareaAchievement.receiveReward(achievementId, succCb)
        -> server.request('subareaachievement.receivereward', {achievementId}, cb)

成就的**判定与上报**不在这里：客户端 `subareaAchievementManager` 在战斗结算时
自己算，把 `newAchievements` / `modifyAchievements` 塞进 `instance.finishlevel` 的
`subareaInfo`（详见 `gamesrv/subarea.py` 的模块注释）。

回包形状（客户端 `receiveReward/<`）：

    失败 → 非 200，`code` 必须是 `SubareaAchievement.ERROR_CODE` 里那几个
           （201 参数错 / 202 入库错 / 203 成就不存在 / 204 已领过 / 205 没奖励 / 206 领奖失败），
           客户端照 `table_dictionary[10000/10001/4200/4201/4202]` 弹提示
    成功 → `data` = 「道具key -> 数量」的 map，客户端直接
           `ccuiManager.popupReward(util.objectToArray(data))`
"""

from __future__ import annotations

from .. import config, logx, store, subarea
from . import route

log = logx.get("handler.subareaachievement")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


@route("subareaachievement.receivereward")
def receive_reward(session: dict, msg: dict, req_id):
    player = _player(session)
    result = subarea.claim(player, (msg or {}).get("achievementId"))
    if result.get("code") != 200:
        return result
    # ⚠️ `_player()` 拿到的存档是 load 出来的副本，改完必须写回
    # （不然这次领奖下次刷新就「忘了」，表现：能反复领同一份奖励）。
    store.save_player(player)
    return result
