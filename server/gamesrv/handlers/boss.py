"""boss.* —— 好友 BOSS。

客户端调用点：`src/data/bosscenter.jsc`。
原子表里这一批 route：

    boss.getbosslist        {"type": "friend"}
    boss.startfight         {"bossId": ...}
    boss.finishfight
    boss.getbossfightlog    {"bossId": ...}
    boss.randomsharefriend
    boss.shareboss

先实现 `getbosslist` —— 它是**客户端每次登录都会打两次**的那条
（日志窗口里 260 次未实现警告，全是它）。其他的等真要做 BOSS 玩法再说。

## 响应形状

`BossCenter.update(data)` 读两个字段（从原子表按顺序读出来的）：

    BossCenter<.update      data
      friendBossList        -> _updateBossObj(...) / _updateFriendBossFlag(...)
      bossRecordList        -> _updateBossRecords(...)
      dispatchPropEvent(BOSS_LIST_UPDATE_EVENT)

`boss` 又在客户端的 responseConfig 里，所以响应要回 `{"boss": {...}}`。

⚠️ 登录包里的 boss 块（`agent.py::_module_stubs`）只有
`bossShareObj / lastFightBossId / friendBossFlag / bossKillRewardList`，
**缺 `friendBossList` 和 `bossRecordList`** —— 这两个恰恰是 `update()` 要读的。
所以这里两个都带上（空数组），顺带把登录块缺的也补齐了。

## 为什么回空是「对」的

`{"type": "friend"}` 要的是**好友的** BOSS 列表。私服只有一个账号、没有好友，
所以这个列表**本来就该是空的**。客户端拿到空表会显示「暂无好友 BOSS」，
而现在的行为是回 `{}` → `data.boss` 是 undefined → `update(undefined)`
在 `_updateBossObj` 里抛异常（logcat 里能看到），界面状态不对。
"""

from __future__ import annotations

from .. import logx
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.boss")


def _boss_block(friend_boss_list=None, boss_record_list=None) -> dict:
    """好友 BOSS 列表块。字段名对照 BossCenter.update() 的读法。"""
    return {
        "friendBossList": friend_boss_list or [],
        "bossRecordList": boss_record_list or [],
        # 下面几个是登录包里就有的，一并带上，别让客户端这次刷新反而丢字段
        "bossShareObj": {},
        "lastFightBossId": 0,
        "friendBossFlag": {},
        "bossKillRewardList": [],
    }


@route("boss.getbosslist")
def get_boss_list(session: dict, msg: dict, req_id):
    """好友 BOSS 列表。私服无好友，空的。"""
    boss_type = (msg or {}).get("type", "friend")
    log.info("boss.getbosslist type=%s（私服无好友 BOSS，回空表）", boss_type)
    return {"code": CODE_OK, "msg": "", "data": {"boss": _boss_block()}}
