"""score.* —— 活动积分（积分商店 / 积分奖励）。

客户端调用点：`src/data/score.jsc`。
route 名从原子表里搜出来的：`score.getactivityscorelist`、`score.getscorereward`。

**`score` 在客户端的 responseConfig 里**，所以响应 data 里带上 `score` 这个 key，
客户端会自动喂给 `ScoreCenter.updateByServer(data.score)`。
而登录包里 score 块的形状（见 `handlers/agent.py::_module_stubs`）是

    "score": {"scoreObj": {}, "scoreInfoObj": {}, "lastUpdateTime": <ms>}

所以这里回**同一个形状**就一定能被解析 —— 不需要再逆一遍 updateByServer。
"""

from __future__ import annotations

from .. import logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.score")


def _score_block() -> dict:
    """和登录包里 score 块保持完全一致（客户端走的是同一个 updateByServer）。"""
    return {"scoreObj": {}, "scoreInfoObj": {}, "lastUpdateTime": store.now_ms()}


@route("score.getactivityscorelist")
def get_activity_score_list(session: dict, msg: dict, req_id):
    """活动积分列表。私服没开活动，回空对象但形状要对。"""
    log.info("score.getactivityscorelist（回空积分块）")
    return {"code": CODE_OK, "msg": "", "data": {"score": _score_block()}}


@route("score.getscorereward")
def get_score_reward(session: dict, msg: dict, req_id):
    """领积分奖励。没有活动也就没有可领的，回空积分块让客户端刷新一次。"""
    log.info("score.getscorereward msg=%s（回空积分块）", msg)
    return {"code": CODE_OK, "msg": "", "data": {"score": _score_block()}}
