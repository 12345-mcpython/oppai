"""签到（`sign.*`）。

客户端只有**一条**路由：

    SignCenter.requestReceiveRewards(sign)  ->  server.request('sign.receivereward', {key}, cb)

其它全靠**登录块** `data.sign`：

```js
SignCenter.ctor(data):   this._signs = data.signs;   // ★ 是 {signKey: 行} 的 map
                         this._updateTime = data.updateTime;
SignCenter._init():      for (k in _signs) 按 row.type（SIGN_TYPE 1 普通 / 2 活动 /
                         3 生日 / 4 新手 / 5 新新手）分到 4 个列表 + 算红点
SignCenter.updateByServer(data):
                         if (data.updateTime <= _updateTime) return;      // ← 时间必须变大
                         for (k in data.signs)
                             if (!_signs[k]) 整条塞进 _signs 并 push 进对应列表
                             else            **逐字段合并**进同一条对象里
SignNormalLayer._update():  读 sign.{signKey, count, rewardCount, rewards, canSignToday,
                                 beginTimeSec, endTimeSec, dialogue, soldierKey}
SignNormalLayer._updateItems(): rewards 是**按天分组的二维数组**，
                                第 i 天那组里 item 数 > 1 时用一个通用图标
                                （`rewardManager.getRewardIcon` 认 {type,key,count}）
                                并把 `i < sign.count` 的那些天标成已领取
SignNormalLayer.receiveRewards(): if (!sign.canSignToday) return;  ← 今天签过了就不发请求
                                  requestReceiveRewards({key: sign.signKey})
```

⚠️ **客户端表里没有签到奖励表**（`jsc_find table_sign*` 0 命中），排期和奖励全是服务端数据、
原版怎么发的无从考证 —— 所以下面 `SIGN_REWARDS` 是**自己定的一套 7 天循环**，
记得在 `differences.md` §D 里标出来。想改成别的，只改这张表即可。

⚠️ 领取回包必须带 `data.sign`（`updateTime` 比上次大）—— 客户端靠它把新 `count`
**合并进同一个行对象**（layer 持的就是那个对象的引用，所以界面上的「第 N 天」和
已领取标记会当场变）。回包还带 `data.items` 让背包/顶部货币条刷新。
"""

from __future__ import annotations

import time

from . import items, logx, store

log = logx.get("sign")

# 客户端 SIGN_TYPE
SIGN_TYPE_NORMAL = "1"

SIGN_KEY = "normal"          # 我们只发这一条普通签到
SIGN_DAYS = 7                # 一轮 7 天

# 7 天奖励（**自己定的**，见模块注释）。每天一组 `(REWARD_TYPE, key, count)`，
# 和 `table_level_reward` 那套奖励串同格式：type 2 = 道具。
SIGN_REWARDS = (
    (("2", "100001", 200), ("2", "100003", 30)),      # 金条 + 行动力
    (("2", "100001", 300), ("2", "301201", 1)),       # 金条 + 萌军刊物
    (("2", "100002", 5000), ("2", "100003", 30)),     # 萌钞 + 行动力
    (("2", "100001", 500), ("2", "200101", 1)),       # 金条 + 小颗糖果
    (("2", "100002", 8000), ("2", "100201", 1)),      # 萌钞 + BP
    (("2", "100001", 800), ("2", "301301", 1)),       # 金条 + 小黄瓜
    (("2", "100001", 1000), ("2", "100016", 5)),      # 第 7 天：金条 + 好人卡
)

# ⚠️ 第 7 天最初发的是 `100101`（表里名字是「卡槽购买次数」）—— 那是**计数器**，
# `table_item.ic` 是空串，客户端 `ItemIcon` 画不出来：`bag.getItemIcon()` 会
# `cc.assert` 失败、`this._iconCase` 没建，直接 `TypeError`（itemicon.js:199）。
# `SignRewardItem._init -> rewardManager.getRewardIcon -> bag.getItemIcon` 这条链
# 在**一进游戏**就会跑（主界面的签到按钮），异常打断主界面初始化 ——
# 表现就是「界面点不动、服务端一条请求都收不到」。全表只有 100101/100102
# 这两个计数器没有图标，奖励列表里**永远不要出现它们**（见 items.icon_of 与
# `script/selftest_game.py` 的 item_icon_check）。

PLAYER_KEY = "sign"


def state(player: dict) -> dict:
    """`{"count": 已签到天数, "lastDay": "YYYY-MM-DD"}`。"""
    st = player.get(PLAYER_KEY)
    if not isinstance(st, dict):
        st = {}
        player[PLAYER_KEY] = st
    try:
        st["count"] = int(st.get("count") or 0)
    except (TypeError, ValueError):
        st["count"] = 0
    if not isinstance(st.get("lastDay"), str):
        st["lastDay"] = ""
    return st


def _today(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))


def _day_window(now: float | None = None) -> tuple:
    """今天 00:00 与明天 00:00 的秒级时间戳（客户端 `_update` 会 ×1000 再算剩余）。"""
    t = time.localtime(time.time() if now is None else now)
    start = int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)))
    return start, start + 86400


def can_sign_today(player: dict) -> bool:
    return state(player)["lastDay"] != _today()


def _soldier_key(player: dict) -> str:
    """立绘用的角色 key（`charManager.createCharHalfImage(soldierKey)`）。

    挑玩家**拥有的**第一个角色 —— 用固定 key 万一那个角色没获得，
    `createCharHalfImage` 会拿到空贴图。拿不到就回空串（客户端不画立绘）。
    """
    try:
        from . import favor

        keys = favor.owned_char_keys(player)
        return str(keys[0]) if keys else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("签到挑立绘角色失败：%s", exc)
        return ""


def row(player: dict) -> dict:
    """登录块 / 推送里的那一条签到行。

    `count` = 已签到天数：客户端把它之前的天数都标成"已领取"，
    下一个可领的就是第 `count` 天（0 起）。签满 7 天后下一轮从 0 重新开始。
    """
    st = state(player)
    count = st["count"]
    if count >= SIGN_DAYS:
        count = 0
    start, end = _day_window()
    return {
        "signKey": SIGN_KEY,
        "type": SIGN_TYPE_NORMAL,
        "count": count,
        "rewardCount": SIGN_DAYS,
        "rewards": [[{"type": t, "key": k, "count": c} for (t, k, c) in day]
                    for day in SIGN_REWARDS],
        "canSignToday": 1 if can_sign_today(player) else 0,
        "beginTimeSec": start,
        "endTimeSec": end,
        "dialogue": "",
        "soldierKey": _soldier_key(player),
    }


def block(player: dict) -> dict:
    """登录块 `data.sign`（/ 推送块）。`signs` 是 `{signKey: 行}` 的 **map**。

    `updateTime` 每次请求都往前推一点：客户端 `updateByServer` 只接受
    **更大**的时间戳（`if (data.updateTime <= _updateTime) return;`）。
    """
    return {
        "updateTime": int(time.time()),
        "signs": {SIGN_KEY: row(player)},
    }


def receive(player: dict, key) -> dict:
    """`sign.receivereward {key}` 的业务体（返回完整响应 dict）。

    客户端 `SignNormalLayer.receiveRewards()` 在 `!sign.canSignToday` 时**根本不会发请求**，
    所以走到这儿基本只有两种：正常领，或者改包/重放。

    失败用非 200 —— 客户端 `requestReceiveRewardsCb` 把非 200 当作
    `isTimeout = true`，弹的是 `table_dictionary[3002]`「已经过期」（文案不完全贴切，
    但比成功好；成功必须是 200，否则界面不会播领奖动画）。
    """
    key = str(key or "")
    if key != SIGN_KEY:
        log.warning("sign.receivereward 未知 key=%s", key)
        return {"code": 1, "msg": "没有这个签到", "data": {}}
    if not can_sign_today(player):
        return {"code": 2, "msg": "今天已经签过了", "data": {}}

    st = state(player)
    day = st["count"] % SIGN_DAYS
    known = {str(k) for k in items.items_of(player)}
    granted = items.settle(player, list(SIGN_REWARDS[day]))
    st["count"] = st["count"] + 1
    st["lastDay"] = _today()
    log.info("sign.receivereward 第 %d 天 -> %s（累计 %d 天）",
             day + 1, granted.get("items"), st["count"])
    return {
        "code": 200,
        "msg": "",
        "data": {
            "sign": block(player),
            "items": items.changed_block(player, known),
        },
    }
