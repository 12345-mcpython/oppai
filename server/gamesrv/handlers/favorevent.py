"""favorevent.* —— 宿舍事件（好感度到级解锁的剧情）。

1 条路由。反汇编来源 `src/data/favoreventcenter.jsc`（`FavorEventCenter`）
+ `src/data/favorevent.jsc`（`FavorEvent`），形状和数据在 `gamesrv/favor.py`
的「宿舍事件」那一段（那里把「字节码确定的」和「推断的」分开写了，先看那段）。

    favorevent.seteventsunlock   ["<eventKey>", ...]     **裸数组**，不是 {eventKeys:[...]}

⚠️ 请求体形状是反汇编 `submitFesRead` 读出来的：

    if (!_.isArray(eventKeys) || eventKeys.length == 0) return;
    for (var i in eventKeys) if (!this._favorEvents[eventKeys[i]]) return;   // 有一个不认识就整单不发
    server.request("favorevent.seteventsunlock", eventKeys, cb, true);

⚠️ 客户端回调**只打日志、不读 res.data**，所以「读过了」这个状态必须靠响应
推回去（`favorEvent` 键 → `cb4ResFavorEvent`），否则重进宿舍红点又回来了。
"""

from __future__ import annotations

from .. import config, favor, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.favorevent")

FAIL = 1


def _ctx(session: dict):
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return account, store.get_or_create_player(account)


@route("favorevent.seteventsunlock")
def set_events_unlock(session: dict, msg: dict, req_id):
    _, player = _ctx(session)

    # ⚠️ 裸数组。兼容一下 `{eventKeys: [...]}`，万一客户端别的地方这么发。
    keys = msg
    if isinstance(msg, dict):
        keys = msg.get("eventKeys")
    if not isinstance(keys, list) or not keys:
        return {"code": FAIL, "msg": "没有事件 key", "data": {}}

    # 客户端是先校验「全部都在我这儿」再整单发；服务端也整单校验，
    # 免得出现「一半标了已读、一半没有」的半截状态。
    missing = [k for k in keys if store.find_favor_event(player, k) is None]
    if missing:
        log.info("favorevent.seteventsunlock 有不认识的事件 %s，整单拒绝", missing)
        return {"code": FAIL, "msg": "事件不存在", "data": {}}

    changed, reward = favor.read_events(player, keys)
    if not changed:
        return {"code": CODE_OK, "msg": "", "data": {}}

    # 读完给好感度 → 好感度可能又跨过某条事件的解锁等级，顺带把新解锁的也推下去
    new_rows = favor.sync_events(player)
    store.save_player(player)

    owners = {favor.event_owner(k) for k in keys}
    data = {"favorEvent": favor.update_event_block(changed)}
    new_block = favor.new_event_block(new_rows)
    if new_block:
        data["newFavorEvent"] = new_block
    favor_rows = {ck: store.find_favor(player, ck) for ck in owners if ck}
    favor_rows = {k: v for k, v in favor_rows.items() if v is not None}
    if favor_rows:
        data["favor"] = favor_rows

    log.info("favorevent.seteventsunlock %s -> 标已读 %d 条，好感度 +%d%s",
             keys, len(changed), reward,
             "，顺带解锁 %d 条新事件" % len(new_rows) if new_rows else "")
    return {"code": CODE_OK, "msg": "", "data": data}
