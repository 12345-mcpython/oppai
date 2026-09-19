"""rank.* —— 排行榜。

客户端调用点只有一个文件：`src/data/rank.jsc`。

反汇编（`tools/jsc_strings.py rank.jsc`）：

    Rank<._getRankList/<        err data T code _update data
        server.request('rank.getrankinglist', null, function (err, data) {
            if (data.code == 200) self._update(data.data);      // ← 直接吃 data.data
        });

    Rank<.getCurrentRank        key cb self rankInfo now closureTimeSec lastUpdateTime
                                _rankInfoObj util.getServerTime closureTimeSec
                                _sortRank cRank rank _lastUpdateTimeObj
        server.request('rank.getcurrentrank', {key: key}, function (err, data) { ... })

    Rank<._update               读的是 `data.rankInfo`（按 key 铺开的一张表），
                                每条带 `closureTimeSec` / `lastUpdateTime`，
                                分别落到 `_rankInfoObj[key]` 和 `_lastUpdateTimeObj[key]`。

两个要点：

1. **`rank` 不在客户端的 responseConfig 里**（那张表只有 favor/player/quest/mail/
   gacha/friend/items/char/sign/arena/score/society/detect/boss/actquest/medals/
   societyclg/equipment/exchange/... ），所以排行榜**不能**靠自动派发，
   必须由回调自己读 `data.rankInfo`。回 `{"rank": {...}}` 是没用的。

2. 私服里只有一个玩家，本来就没有「榜」。所以回**空表**
   （`rankInfo = {}`）是**正确**的，而不是偷懒 —— 客户端 `_update` 遍历空表，
   之后 `getCurrentRank(key)` 拿到空对象，界面显示「暂无排名」。
   比回 `{}`（现在这样）好：那样 `data.rankInfo` 是 undefined，
   `_update` 里遍历它会抛 TypeError，只在 logcat 里留一条 JS 异常。
"""

from __future__ import annotations

from .. import logx
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.rank")


def _empty() -> dict:
    return {"code": CODE_OK, "msg": "", "data": {"rankInfo": {}}}


@route("rank.getrankinglist")
def get_ranking_list(session: dict, msg: dict, req_id):
    """客户端每次进主界面都会拉一次。私服没有榜，回空表。"""
    log.info("rank.getrankinglist（私服无榜，回空表）")
    return _empty()


@route("rank.getcurrentrank")
def get_current_rank(session: dict, msg: dict, req_id):
    """请求体 {"key": <榜 key>}。同样回空表。"""
    log.info("rank.getcurrentrank key=%s（回空表）", (msg or {}).get("key"))
    return _empty()
