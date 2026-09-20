"""sign.* —— 签到（见 `gamesrv/sign.py` 的模块注释）。

客户端只有一条路由：`sign.receivereward {key}`（`SignCenter.requestReceiveRewards`）。
排期/奖励全在登录块 `data.sign` 里，而那张表客户端没有 → 服务端自己定（§D）。

⚠️ 回包必须带 `data.sign`（`updateTime` 比上次大）：客户端 `SignCenter.updateByServer`
   是按 key **逐字段合并**进同一个行对象的，界面上的「第 N 天 / 已领取」靠它刷新。
"""

from __future__ import annotations

from .. import config, logx, sign, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.sign")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


@route("sign.receivereward")
def receive_reward(session: dict, msg: dict, req_id):
    player = _player(session)
    result = sign.receive(player, (msg or {}).get("key"))
    if result.get("code") == CODE_OK:
        store.save_player(player)
    return result
