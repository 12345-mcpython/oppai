"""friendsupport.* —— 助战（好友支援）。

客户端 `FriendSupport.getRecommendList(levelId, succCb, failedCb)`：

    server.request('friendsupport.getrecommendsoldiers', {levelid: levelId}, function (err, res) {
        if (err || res.code != 200) { failedCb && failedCb(err, res); return; }
        this.clearRecommendList();
        this._recommendList = res.data;          // ← 整个 data 就是列表（数组）
    });

然后 `SupportChoiceLayer.setSupportList(list)` 遍历它，每个元素：

    SupportChoiceItem.createSolider(item, topType):
        if (item.npcId) {
            var npc = table_friend_support_npc[item.npcId];   // ← 只认 npcId！
            var info = this.getSoldierInfo(topType, npc, ...);
            var data = this.createSoldierData(info.id, info.data);   // [key, star, lv, skillLv]
            ...
        } else {
            this.getSoldierInfo(topType, null, item, ...);     // 好友：item.soldiers[]
        }

所以**服务端只需要回 npcId**，NPC 的名字/等级/各兵种军士都是客户端自己表里的。
（列表里的 NPC 就是玩家口中的"助战军士"，选一个可以带进关卡。）

抽表见 `tools/extract_client_tables.py` -> `gamesrv/data/table_friend_support_npc.json`。
"""

from __future__ import annotations

import json
import os
import threading

from .. import logx
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.friendsupport")

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "table_friend_support_npc.json")

_lock = threading.RLock()
_table: dict | None = None

# 推荐列表太长没意义（客户端要一次画完），按需给前 N 个
RECOMMEND_COUNT = int(os.environ.get("GS_SUPPORT_NPC_COUNT", "20"))


def npc_table() -> dict:
    global _table
    with _lock:
        if _table is None:
            try:
                with open(_PATH, "r", encoding="utf-8") as fh:
                    _table = json.load(fh)
            except Exception:  # noqa: BLE001
                log.warning("载入 %s 失败，助战列表会是空的", _PATH)
                _table = {}
        return _table


def recommend_list() -> list:
    """给客户端的推荐列表。

    每个元素除了 `npcId` 之外，把表行里的字段也一起带上：
    客户端 `SupportChoiceItem` 会直接读 `player_name` / `player_lv` / `last_time`，
    从条目里读还是从 `table_friend_support_npc[npcId]` 里读都能work。
    """
    out = []
    for key, row in list(npc_table().items())[:RECOMMEND_COUNT]:
        entry = dict(row) if isinstance(row, dict) else {}
        entry["npcId"] = key
        entry["playerId"] = key        # 客户端拿它查借出记录 userecord
        out.append(entry)
    return out


@route("friendsupport.getrecommendsoldiers")
def get_recommend_soldiers(session: dict, msg: dict, req_id):
    level_id = (msg or {}).get("levelid")
    items = recommend_list()
    log.info("助战推荐 levelid=%s -> %d 个 NPC", level_id, len(items))
    # ⚠️ data 本身就是列表，不是 {soldiers:[...]}
    return {"code": CODE_OK, "msg": "", "data": items}


@route("friendsupport.setfriendsupport")
def set_friend_support(session: dict, msg: dict, req_id):
    """出战前把选中的助战军士登记上去。实际加不加成不影响战斗结算，先回 200。"""
    log.info("friendsupport.setfriendsupport msg=%s", msg)
    return {"code": CODE_OK, "msg": "", "data": {}}


@route("friendsupport.delfriendsupport")
def del_friend_support(session: dict, msg: dict, req_id):
    log.info("friendsupport.delfriendsupport msg=%s", msg)
    return {"code": CODE_OK, "msg": "", "data": {}}
