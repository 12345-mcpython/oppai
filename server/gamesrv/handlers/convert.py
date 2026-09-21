"""convert.convert —— 使用礼包（背包里 type=80 的道具）。

客户端（`assets/src/data/item/package.jsc` + `bag.jsc::_package`，反汇编）：

    Package._loadTable()   this._convertKey = table_item[key].convert_key
    Bag._package(item, cb, msgData)
        server.request('convert.convert', {key: item._convertKey, count: item.count}, cb)

    // 回调：成功 -> RewardBoxLayer.popReward({reward: res.data, cb, csb:"rewardgifteffect",
    //                                        key: item.key}, 9999)
    // ⚠️ `res.data` **本身就是奖励数组**（`_.map(params.reward, …)` 逐项读 `.type`），
    //    不是 `{rewards: [...]}`。
    // 失败：`cc.log("error")` 就完了 —— 界面上**没有任何提示**，所以非 200 要慎用。

三个礼包的 `convert_key`（照客户端全表抄的，我们抽出来的 `table_item.json` 是压缩字段，
没有 `convert_key` 这一列 —— 见 `CONVERT_KEYS` 的注释）：

    800001 神秘行李箱     -> 10300001
    800002 萨曼达的礼物盒 -> 10300002
    800003 萨曼达的神秘箱 -> 10300003

消耗多少、能换什么在 `table_convert_reward`（**客户端一行都不读**，原版的服务端数据）：

    10300001: {consume: "800001#1#i", reward_key: "10300001"}

⚠️ `reward_key` 指向的**奖励内容**客户端表里没有（全库 0 命中，原版在服务端）——
所以那一份是私服自己定的，见 `CONVERT_REWARDS` 和 docs/differences.md §D。
"""

from __future__ import annotations

from .. import config, items, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.convert")

CODE_FAIL = -1

# `table_item[key].convert_key` —— 抽出来的表是压缩字段（`ck`/`ic`/`t`…），没有这一列。
# 值是从客户端**全表**（`tableitem.jsc` 的原始字段名）里抄的，三个礼包而已；
# 表里补上 `convert_key` 之后这段可以删（`_convert_key()` 已经优先读表）。
CONVERT_KEYS = {
    "800001": "10300001",
    "800002": "10300002",
    "800003": "10300003",
}

# `reward_key` -> 奖励行 `[(type, key, count)]`（type 用 REWARD_TYPE 的数字：2 = ITEM）。
# ⚠️ **私服自己定的**：客户端表里没有这份内容（`table_convert_reward` 只给
# consume/reward_key），按礼盒品质（20/30/40）给三档。改这里就能调。
CONVERT_REWARDS = {
    "10300001": [("2", "100001", 30), ("2", "100002", 5000)],                    # 绿：金条+萌钞
    "10300002": [("2", "100001", 80), ("2", "100002", 15000), ("2", "100003", 30)],
    "10300003": [("2", "100001", 200), ("2", "100002", 40000), ("2", "100003", 80),
                 ("2", "100016", 5)],                                            # 紫：加好人卡
}


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


def convert_table() -> dict:
    return items.table("table_convert_reward")


def _convert_key(item_key) -> str:
    """礼包道具 -> convert_key。优先读表（表里有就自动跟上），没有才用抄来的那份。"""
    row = items.table("table_item").get(str(item_key)) or {}
    return str(row.get("convert_key") or CONVERT_KEYS.get(str(item_key)) or "")


def _consume_of(row: dict) -> tuple:
    """`"800001#1#i"` -> `("800001", 1)`（第三段 `i` 是「道具」的意思，没用上）。"""
    seg = str(row.get("consume") or "").split("#")
    if len(seg) < 2 or not seg[0]:
        return "", 0
    try:
        return str(seg[0]), max(1, int(seg[1] or 1))
    except ValueError:
        return str(seg[0]), 1


def _reward_rows(reward_key: str) -> list:
    return list(CONVERT_REWARDS.get(str(reward_key)) or [])


@route("convert.convert")
def do_convert(session: dict, msg: dict, req_id):
    """`{key: <convert_key>, count: n}` -> **奖励数组**（`RewardBoxLayer.popReward` 直接吃）。"""
    player = _player(session)
    key = str((msg or {}).get("key") or "")
    try:
        count = max(1, int((msg or {}).get("count") or 1))
    except (TypeError, ValueError):
        count = 1

    if not key:
        return {"code": CODE_FAIL, "msg": "no key", "data": []}
    # 客户端发的 key 是 `convert_key`；也认「直接发礼包道具 key」的情况
    row = convert_table().get(key)
    if row is None:
        item_key = key if _convert_key(key) else ""
        if item_key:
            row = convert_table().get(_convert_key(item_key))
    if not isinstance(row, dict):
        log.info("convert.convert 没有这个礼包：key=%s", key)
        return {"code": CODE_FAIL, "msg": "no convert row", "data": []}

    item_key, per = _consume_of(row)
    reward_rows = _reward_rows(row.get("reward_key"))
    if not item_key or not reward_rows:
        log.warning("convert.convert %s 的配置不全：consume=%r reward=%s",
                    key, row.get("consume"), reward_rows)
        return {"code": CODE_FAIL, "msg": "convert row incomplete", "data": []}

    need = per * count
    have = items.count_of(player, item_key)
    if have < need:
        log.info("convert.convert %s 道具不够：要 %s x%s 有 %s", key, item_key, need, have)
        return {"code": CODE_FAIL, "msg": "not enough", "data": []}

    known = {str(k) for k in items.items_of(player)}
    items.sub_item(player, item_key, need)
    rewards = [(t, k, int(c) * count) for (t, k, c) in reward_rows]
    granted = items.settle(player, rewards)
    store.save_player(player)
    log.info("convert.convert key=%s x%s -> 消耗 %s x%s，发 %s",
             key, count, item_key, need, rewards)
    return {"code": CODE_OK, "msg": "", "data": [
        # 客户端 `RewardBoxLayer` 逐项读 `.type`（再过 REWARD_TYPE_SWITCH），
        # 所以这里给的是服务端标准奖励行 `{type, key, count}`
        {"type": str(t), "key": str(k), "count": int(c)} for (t, k, c) in rewards
    ], "items": items.changed_block(player, known), "convertResult": granted.get("items") or {}}
