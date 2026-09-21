"""friendsupport.* —— 助战（好友支援）。

客户端 `FriendSupport.getRecommendList(levelId, succCb, failedCb)`：

    server.request('friendsupport.getrecommendsoldiers', {levelid: levelId}, function (err, res) {
        if (err || res.code != 200) { failedCb && failedCb(err, res); return; }
        this.clearRecommendList();
        this._recommendList = res.data;          // ← 整个 data 存下来
        succCb && succCb(res.data);              // ← 回调也只传 data 本身
    });

⚠️⚠️ **回包形状是 `{recommendList: [...]}`，不是裸数组**（2026-09-21 才定位到）：

    SupportChoiceLayer._init/</   →   function (data) { this._initUI(data.recommendList) }

真正画列表的**层**读的是 `data.recommendList`。发裸数组的话它是 undefined →
`_initUI(undefined)` → `setSupportList(undefined)` 第一句 `if (!list) return;` 直接退出
—— 症状就是「助战弹窗打得开、里面一个军士都没有」。
（`_recommendList = res.data` 存的是整个 data，所以查数据类会以为"收到了 20 个"，
  之前就是被那句带偏、判成「data 本身就是列表」。）

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

所以**服务端只需要回 npcId**（外加 `playerId`：层会拿它查 `userecord`），
NPC 的名字/等级/各兵种军士都是客户端自己表里的。
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
    # ⚠️⚠️ **必须是 `{recommendList: [...]}`，不能发裸数组**（2026-09-21 才定位到）。
    # 反汇编这条链：
    #
    #   FriendSupport.getRecommendList/<  成功回调 → succCb(res.data)      // 整个 data
    #   SupportChoiceLayer._init/</       function (data) { this._initUI(data.recommendList) }
    #
    # 也就是**层**（真正画列表的那个）读的是 `data.recommendList`。发裸数组的话
    # `data.recommendList` 是 undefined → `_initUI(undefined)` → `setSupportList(undefined)`
    # 里第一句 `if (!list) return;` 直接退出 ⇒ 弹窗能打开、里面**一个军士都没有**。
    # （`FriendSupport._recommendList = res.data` 那句确实是把整个 data 存下来的，
    #   所以数据类那边看着"有 20 个"，层这边却是空的 —— 之前就是被这句带偏了。）
    return {"code": CODE_OK, "msg": "", "data": {"recommendList": items}}


@route("friendsupport.setfriendsupport")
def set_friend_support(session: dict, msg: dict, req_id):
    """出战前把选中的助战军士登记上去。实际加不加成不影响战斗结算，先回 200。"""
    log.info("friendsupport.setfriendsupport msg=%s", msg)
    return {"code": CODE_OK, "msg": "", "data": {}}


@route("friendsupport.delfriendsupport")
def del_friend_support(session: dict, msg: dict, req_id):
    log.info("friendsupport.delfriendsupport msg=%s", msg)
    return {"code": CODE_OK, "msg": "", "data": {}}
