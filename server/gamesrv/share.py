"""分享（`share.*`）—— 业务逻辑。

客户端 `assets/src/data/share.jsc`（反汇编）：

    Share.ctor(data)      // 登录块 `data.share`：只读 `shareCount`
        this._shareCount = data.shareCount
        this._shareRewardMaxCount = table_constant.share_reward_max_count
        this._isCanShare = false

    Share.receiveShareReward(platform, cb)
        if (this._shareCount >= this._shareRewardMaxCount) return;   // 客户端自己先挡
        server.request('share.receivesharereward',
                       {shareSuccess: true, platform: platform}, cb)

    // 回调：data.shareCount 覆盖本地；成功就 ccuiManager.popupRewardWithItems(data.rewards)
    // ⚠️ `popupRewardWithItems(items)` 是 `for (k in items) push({type: ITEM, key: k, count: items[k]})`
    //    —— 所以 `rewards` 必须是 **map** `{道具key: 数量}`，不是数组！

奖励内容**不是猜的**：`table_constant` 里原本就有两条（客户端只读 `*_max_count` 那条）：

    share_reward_key       = "100001@30"     -> 金条 x30（`<itemKey>@<count>`）
    share_reward_max_count = 1               -> 每天 1 次

所以这里照表发，连「一天几次」都和客户端同一个来源。
`shareCount` 按游戏内换日点（`quests.RESET_HOUR` = 05:00）每天清零。
"""

from __future__ import annotations

from . import items, logx, quests, store

log = logx.get("share")

CODE_FAIL = -1


def _constant(key: str, default=None):
    return items.table("table_constant").get(key, default)


def reward_map() -> dict:
    """`share_reward_key`（形如 `"100001@30"`）-> `{道具key: 数量}`。"""
    text = str(_constant("share_reward_key") or "")
    out: dict = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        seg = part.split("@")
        if len(seg) >= 2 and seg[0]:
            try:
                out[str(seg[0])] = int(seg[1] or 0)
            except ValueError:
                log.warning("share_reward_key 解析不了：%r", part)
    return out


def max_count() -> int:
    try:
        return int(_constant("share_reward_max_count") or 0)
    except (TypeError, ValueError):
        return 0


def ensure(player: dict) -> bool:
    """换日就把 `shareCount` 清零；返回是否改动过（**不落盘**：调用方 save）。

    ⚠️ 必须由 `agent.getlogindata` / `createplayer` 显式调一次并 save ——
    `block()`（在 `_module_stubs` 里）也会调，但那条路径没人 save，
    表现就是「换日之后分享次数还是 1」（和勋章 medalWear 那个坑同一类）。
    """
    st = player.get("share")
    if not isinstance(st, dict):
        st = {}
        player["share"] = st
        st["count"] = 0
        st["day"] = quests.day_str()
        return True
    st["count"] = int(st.get("count") or 0)
    today = quests.day_str()
    if st.get("day") != today:
        st["day"] = today
        st["count"] = 0
        log.info("玩家 %s 分享次数换日清零（%s）", player.get("account"), today)
        return True
    return False


def state(player: dict) -> dict:
    """`player["share"] = {count, day}`（顺手做换日检查）。"""
    ensure(player)
    return player["share"]


def block(player: dict) -> dict:
    """登录块 `data.share`（客户端只读 `shareCount`；`isCanShare` 是它自己维护的）。"""
    st = state(player)
    return {"shareCount": int(st["count"]), "isCanShare": 0}


def receive(player: dict, msg: dict) -> dict:
    """`share.receivesharereward` 的业务体（回 `{code, msg, data}`）。"""
    from .gameproto import CODE_OK

    st = state(player)
    limit = max_count()
    if msg and msg.get("shareSuccess") is False:
        log.info("分享没成功（客户端说 shareSuccess=false），不发奖")
        return {"code": CODE_FAIL, "msg": "share not success", "data": {}}
    if st["count"] >= limit:
        log.info("今天的分享奖励领完了（%s/%s）", st["count"], limit)
        return {"code": CODE_FAIL, "msg": "share count max", "data": {"shareCount": st["count"]}}
    rewards = reward_map()
    if not rewards:
        log.warning("table_constant.share_reward_key 是空的，分享奖发不出来")
        return {"code": CODE_FAIL, "msg": "no share reward", "data": {}}

    st["count"] += 1
    known = {str(k) for k in items.items_of(player)}
    for key, count in rewards.items():
        items.add_item(player, key, count)
    log.info("玩家 %s 分享领奖 -> %s（今天 %s/%s 次）",
             player.get("account"), rewards, st["count"], limit)
    return {"code": CODE_OK, "msg": "", "data": {
        "shareCount": int(st["count"]),
        # ⚠️ map，不是数组（popupRewardWithItems 是 for-in）
        "rewards": {str(k): int(v) for k, v in rewards.items()},
        "items": items.changed_block(player, known),
    }}


def save(player: dict) -> None:
    store.save_player(player)
