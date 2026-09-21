"""friend.* —— 好友系统路由。

客户端 `src/data/friend.jsc` 里打出去的 9 条路由（route 名是从字节码原子表里
搜出来的，不是猜的）：

    friend.getfriendlist          Friend.updateFriendMapList   {}                -> update(res.data)
    friend.getrecommendationlist  Friend.updateRecommendationList {}             -> update({recommendationList})
    friend.searchplayer           Friend.searchPlayer          {numberId}        -> data.playerInfo
    friend.applyfor               Friend.applyFor              {numberId}        -> data.friendMap
    friend.agreeapplication       Friend.agreeApplication      {numberId}        -> data.friendMap
    friend.refuseapplication      Friend.refuseApplication     {numberId}        -> data.friendMap
    friend.deletefriend           Friend.deleteFriend          {numberId}        -> data.friendMap
    friend.sendmaterials          Friend.sendMaterials         {numberId}        -> data.friendMap
    friend.takematerials          Friend.takeMaterials         {numberId}        -> data.friendMap + reward

⚠️ **`getfriendlist` 的字段必须在 `data` 顶层**：回调是 `this.update(res.data)`，
`Friend.update` 读的是 `data.friendMapList` / `data.recommendationList` /
`data.takeMaterialsCount`。套一层 `{friend: {...}}` 的话这三个全是 undefined，
`update` 直接 return —— 表现是「好友面板打开是空的、但服务端日志里请求是 200」。

业务逻辑全在 `gamesrv/friends.py`（NPC 好友、位掩码、上限、换日都写在那边）。
"""

from __future__ import annotations

from .. import config, friends, items, logx, quests, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.friend")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


def _reply(player: dict, result: dict, extra: dict | None = None,
           touched: bool = False) -> dict:
    """统一回包：成功（或 ensure 改了状态）就落盘，再按需带上别的块。

    ⚠️ `_player()` 拿到的 player 是**从 players.json load 出来的临时副本**
    （见 store.save_player 的注释），改完不 save 的话下次请求全丢。
    """
    code = int(result.get("code") or 0)
    if code == CODE_OK or touched:
        store.save_player(player)
    data = dict(result.get("data") or {})
    if code == CODE_OK and extra:
        data.update(extra)
    return {"code": code, "msg": result.get("msg", ""), "data": data}


def _run(session: dict, fn, *args, extra=None) -> dict:
    """跑一个 friends.py 里的操作：ensure -> 操作 -> 落盘 -> 带上附加块。"""
    player = _player(session)
    touched = friends.ensure(player)
    known = {str(k) for k in items.items_of(player)}    # 改动前的背包 key
    result = fn(player, *args) if args else fn(player)
    add = {}
    if extra == "quest":
        add["quest"] = quests.block(player)
    elif extra == "items":
        add["items"] = items.changed_block(player, known)
    return _reply(player, result, add, touched=touched)


@route("friend.getfriendlist")
def get_friend_list(session: dict, msg: dict, req_id):
    """拉好友列表 + 推荐列表 + 今日收取次数（客户端进面板时打的就是这条）。"""
    player = _player(session)
    touched = friends.ensure(player)
    result = friends.friend_list(player)
    log.info("friend.getfriendlist 好友 %d 个（上限 %d）",
             friends.friend_count(player), friends.friend_limit(player))
    # 回包形状 = 登录块，直接平铺（见文件头注释）
    return _reply(player, result, touched=touched)


@route("friend.getrecommendationlist")
def get_recommendation_list(session: dict, msg: dict, req_id):
    """拉推荐好友列表（ADD_FRIEND 页签，客户端有 10 秒冷却）。"""
    return _run(session, friends.recommend_list_route)


@route("friend.searchplayer")
def search_player(session: dict, msg: dict, req_id):
    """按数字 ID 搜人。搜不到回 222（客户端弹「你确定他在这片大陆上吗~」）。"""
    return _run(session, friends.search, msg)


@route("friend.applyfor")
def apply_for(session: dict, msg: dict, req_id):
    """申请加好友。成功的 `friendMap` 会被客户端并进本地列表。"""
    return _run(session, friends.apply_for, msg)


@route("friend.agreeapplication")
def agree_application(session: dict, msg: dict, req_id):
    """同意申请。"""
    return _run(session, friends.agree_application, msg)


@route("friend.refuseapplication")
def refuse_application(session: dict, msg: dict, req_id):
    """拒绝申请。"""
    return _run(session, friends.refuse_application, msg)


@route("friend.deletefriend")
def delete_friend(session: dict, msg: dict, req_id):
    """删好友。"""
    return _run(session, friends.delete_friend, msg)


@route("friend.sendmaterials")
def send_materials(session: dict, msg: dict, req_id):
    """送物资。顺带把 `quest` 块带上 —— 日常任务 18201「给好友送物资」当场变可领。"""
    return _run(session, friends.send_materials, msg, extra="quest")


@route("friend.takematerials")
def take_materials(session: dict, msg: dict, req_id):
    """收物资。回 `reward = {itemKey: count}`，顺手刷新背包块。"""
    return _run(session, friends.take_materials, msg, extra="items")
