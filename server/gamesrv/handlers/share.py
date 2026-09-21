"""share.* —— 分享领奖路由。

只有一条：`share.receivesharereward`（`assets/src/data/share.jsc`）。
业务逻辑在 `gamesrv/share.py`（奖励内容和次数都取自客户端的 `table_constant`）。
"""

from __future__ import annotations

from .. import config, logx, share, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.share")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


@route("share.receivesharereward")
def receive_share_reward(session: dict, msg: dict, req_id):
    player = _player(session)
    result = share.receive(player, msg or {})
    if int(result.get("code") or 0) == CODE_OK:
        store.save_player(player)
    return {"code": result.get("code"), "msg": result.get("msg", ""),
            "data": result.get("data") or {}}
