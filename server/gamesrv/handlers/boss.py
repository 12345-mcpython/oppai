"""boss.* —— 好友 BOSS（列表 / 开打 / 结算 / 挑战记录 / 分享）。

六条 route（`assets/src/data/bosscenter.jsc` 反汇编）：

    boss.getbosslist       BossCenter.syncBossList / _getBossList  {type}
                           -> data = {friendBossList, bossRecordList}   ← **不是** {boss:{…}}
    boss.startfight        BossCenter.startFight   {id, curTeamIdx}
    boss.finishfight       BossCenter.finishFight  {id, harm}
    boss.getbossfightlog   BossCenter.syncBossFightLog {id}  -> data = 记录数组
    boss.randomsharefriend BossCenter.randomShareFriend {id} -> data = {shareFriends: […]}
    boss.shareboss         BossCenter.shareBoss / cancelShareBoss {id}  ← 分享/取消**同一条**

⚠️ 回包形状很容易搞错，反汇编里是：

    BossCenter.syncBossList/<   self.update(res.data)     // 整个 data，不是 data.boss
    BossCenter.update(data)     data.friendBossList / data.bossRecordList

（以前这里回的是 `{"boss": {…}}` —— 那是 `updateByServer` 的键，而
 `BossCenter.updateByServer(data)` 只认 `data.rewardList`（击杀奖励），
 好友 BOSS 列表**永远刷不出来**。）

⚠️ **每条回包都带 `items`**：BP 消耗和奖励都是服务端扣/发的，客户端背包靠
`patch.js` 的 RESP-DISPATCH（`data.items -> bag.updateItems`）才动 —— 不带的话
打完 BOSS 顶部货币条和 BP 数字要重登才对。

业务逻辑在 `gamesrv/boss.py`（行形状、刷新规则、失败码和私服取舍都写在那儿）。
"""

from __future__ import annotations

from .. import boss, config, items, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.boss")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


def _bag_snapshot(player: dict) -> dict:
    return {str(k): int(v or 0) for k, v in items.items_of(player).items()}


def _items_block(player: dict, before: dict) -> dict:
    """`items` 变更块：**只回这次真的变了的、而且改动前就有的 key**。

    ⚠️ 不能直接把 `items.changed_block()` 拿来用 —— 那个回的是「改动前就有的
    **全部** key」（500 多个），而 `boss.getbosslist` 在 BOSS 界面里是**每分钟轮询**的
    （`BossCenter.registerSyncBossList`），每次带上整个背包纯属浪费。
    这里自己做 diff，语义仍然是 `Bag.updateItems` 要的那套（只碰已有 key，见 items.py）。
    """
    out = {}
    for key, count in items.items_of(player).items():
        k = str(key)
        if k in before and int(count or 0) != before[k]:
            out[k] = int(count or 0)
    return out


def _reply(player: dict, result: dict, before: dict) -> dict:
    """统一收尾：成功才发 `items` 变更块 + 落盘。

    ⚠️ `boss.getbossfightlog` 的 `data` **是数组**（不是对象），所以这里不能无脑
    套 `dict(...)` —— 只有 dict 才塞 `items`。
    """
    raw = result.get("data")
    data = dict(raw) if isinstance(raw, dict) else raw
    if int(result.get("code") or 0) == CODE_OK:
        if isinstance(data, dict):
            data["items"] = _items_block(player, before)
        store.save_player(player)
    return {"code": result.get("code"), "msg": result.get("msg", ""), "data": data}


@route("boss.getbosslist")
def get_boss_list(session: dict, msg: dict, req_id):
    """好友 BOSS 列表（`{friendBossList, bossRecordList}`，两个都是 map）。"""
    player = _player(session)
    before = _bag_snapshot(player)
    result = {"code": CODE_OK, "msg": "", "data": boss.list_route(player)}
    log.info("boss.getbosslist type=%s 在场 %d 只", (msg or {}).get("type"),
             len((result["data"] or {}).get("friendBossList") or {}))
    return _reply(player, result, before)


@route("boss.startfight")
def start_fight(session: dict, msg: dict, req_id):
    """开始挑战：校验 +（非首战）扣 BP；战斗本身在客户端打。"""
    player = _player(session)
    before = _bag_snapshot(player)
    return _reply(player, boss.start_fight(player, msg or {}), before)


@route("boss.finishfight")
def finish_fight(session: dict, msg: dict, req_id):
    """结算：扣血 + 伤害奖励（打死了再加击杀奖励）。"""
    player = _player(session)
    before = _bag_snapshot(player)
    return _reply(player, boss.finish_fight(player, msg or {}), before)


@route("boss.getbossfightlog")
def get_boss_fight_log(session: dict, msg: dict, req_id):
    """挑战记录（`data` 就是数组，客户端直接存成 `logList`）。"""
    player = _player(session)
    before = _bag_snapshot(player)
    return _reply(player, boss.log_route(player, msg or {}), before)


@route("boss.randomsharefriend")
def random_share_friend(session: dict, msg: dict, req_id):
    """随机挑几个萌友（分享面板的候选名单）。"""
    player = _player(session)
    before = _bag_snapshot(player)
    return _reply(player, boss.random_share_friend(player, msg or {}), before)


@route("boss.shareboss")
def share_boss(session: dict, msg: dict, req_id):
    """分享 / 取消分享（同一条 route 的开关语义，见 gamesrv/boss.py）。"""
    player = _player(session)
    before = _bag_snapshot(player)
    return _reply(player, boss.share_boss(player, msg or {}), before)
