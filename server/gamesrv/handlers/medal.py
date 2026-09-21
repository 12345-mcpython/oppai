"""medal.* —— 勋章 / 头像 / 衣柜路由。

route 名是从 `assets/src/data/medal.jsc` 的字节码原子表里搜出来的，9 条：

    medal.getfriendmedalinfo   查看好友的勋章（好友面板「访问」用）
    medal.changehead           换头像      {headId, headType}
    medal.changeclothes        换衣服      {clothesId}
    medal.changebg             换背景      {bgId}
    medal.wearmedal            佩戴勋章    {medalId, wearIdx}
    medal.setclothesorbgold    清衣服/背景的 NEW  {id}
    medal.setheadold           清头像的 NEW       {headId, headType}
    medal.setmedalold          清勋章组的 NEW     {medalId}
    medal.clearallheadnew      清全部头像 NEW     {}

⚠️ 三条 change* 的**回包 data 是「值」不是对象**（回调直接
`player.updateMedalClothesId(res.data)`），`wearmedal` 回的是**整张** medalWear。
业务逻辑全在 `gamesrv/medal.py`。
"""

from __future__ import annotations

from .. import config, logx, medal, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.medal")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


def _run(session: dict, fn, *args) -> dict:
    player = _player(session)
    touched = medal.ensure(player)          # 幂等：发衣柜/勋章、补默认值、记达成
    result = fn(player, *args) if args else fn(player)
    if int(result.get("code") or 0) == CODE_OK or touched:
        store.save_player(player)
    return {"code": result.get("code"), "msg": result.get("msg", ""),
            "data": result.get("data") or {}}


@route("medal.getfriendmedalinfo")
def get_friend_medal_info(session: dict, msg: dict, req_id):
    """看好友的勋章（`deletefriendpanel._onClickVisit` 调的）。"""
    return _run(session, medal.friend_medal_info, msg)


@route("medal.changehead")
def change_head(session: dict, msg: dict, req_id):
    return _run(session, medal.change_head, msg)


@route("medal.changeclothes")
def change_clothes(session: dict, msg: dict, req_id):
    return _run(session, medal.change_clothes, msg)


@route("medal.changebg")
def change_bg(session: dict, msg: dict, req_id):
    return _run(session, medal.change_bg, msg)


@route("medal.wearmedal")
def wear_medal(session: dict, msg: dict, req_id):
    return _run(session, medal.wear_medal, msg)


@route("medal.setclothesorbgold")
def set_clothes_or_bg_old(session: dict, msg: dict, req_id):
    return _run(session, medal.set_clothes_or_bg_old, msg)


@route("medal.setheadold")
def set_head_old(session: dict, msg: dict, req_id):
    return _run(session, medal.set_head_old, msg)


@route("medal.setmedalold")
def set_medal_old(session: dict, msg: dict, req_id):
    return _run(session, medal.set_medal_old, msg)


@route("medal.clearallheadnew")
def clear_all_head_new(session: dict, msg: dict, req_id):
    return _run(session, medal.clear_all_head_new)
