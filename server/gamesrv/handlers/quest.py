"""quest.* —— 任务相关路由。

客户端 src/util/server.js 里有一张 responseConfig，响应 data 里出现 `quest`
这个 key 时，客户端会自动把它喂给 QuestCenter.updateByServer()。
所以这里回的都不是裸的 quests 列表，而是 {"quest": <block>}。

route 名字是从 .jsc 里搜出来的：
    quest.getnewquest      QuestCenter.requestGetNewQuest
    quest.submitquest      QuestCenter.requestReceiveRewards（请求体 {questKey}）
    quest.schedule         QuestLayer 里上报进度
    quest.markcharstate    NoviceQuestCenter（新手任务）
    quest.markfirstani     NoviceQuestCenter
    quest.markreceivekey   NoviceQuestCenter
    quest.updateactivites  ActQuestCenter（活动任务）
"""

from __future__ import annotations

from .. import config, logx, quests, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.quest")


def _player(session: dict) -> dict:
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return store.get_or_create_player(account)


def _ok(player: dict, **extra) -> dict:
    data = {"quest": quests.block(player)}
    data.update(extra)
    return {"code": CODE_OK, "msg": "", "data": data}


@route("quest.getnewquest")
def get_new_quest(session: dict, msg: dict, req_id):
    """拉一次任务列表。登录时客户端也会打一次，服务端把主线窗口重新算一遍。"""
    player = _player(session)
    log.info("quest.getnewquest 窗口=%d 条", len(quests.active_keys(player)))
    return _ok(player)


@route("quest.submitquest")
def submit_quest(session: dict, msg: dict, req_id):
    """领奖。客户端只在 state === ACHIEVED 时才发，请求体 {"questKey": "..."}。

    回包里同时带上新的 quest 块，客户端会自动 updateByServer，
    领掉的那条从窗口消失、后面一条顶上来（变成可领）。
    """
    player = _player(session)
    result = quests.submit(player, msg)
    if result["code"] != CODE_OK:
        return result
    return _ok(player, rewards=result["data"].get("rewards") or [])


@route("quest.schedule")
def update_schedule(session: dict, msg: dict, req_id):
    """客户端上报进度。目前只把最新的窗口回给客户端，不做真实统计。"""
    player = _player(session)
    return _ok(player)


@route("quest.markcharstate")
def mark_char_state(session: dict, msg: dict, req_id):
    """新手任务：角色状态标记。先只回 200，不让客户端卡住。"""
    return {"code": CODE_OK, "msg": "", "data": {}}


@route("quest.markfirstani")
def mark_first_ani(session: dict, msg: dict, req_id):
    """新手任务：首次动画标记。"""
    return {"code": CODE_OK, "msg": "", "data": {}}


@route("quest.markreceivekey")
def mark_receive_key(session: dict, msg: dict, req_id):
    """新手任务：已领取标记。"""
    return {"code": CODE_OK, "msg": "", "data": {}}


@route("quest.updateactivites")
def update_activites(session: dict, msg: dict, req_id):
    """活动任务：刷新活动列表（暂未实现）。"""
    return {"code": CODE_OK, "msg": "", "data": {}}


@route("sync.syncupclient")
def sync_up_client(session: dict, msg: dict, req_id):
    """客户端拉「服务器上有什么变了」。

    SyncManager 的读法（`tools/jsc_strings.py syncmanager.jsc`）：

        getSyncParams(data):
            data.actquest.actUpdateTime     = dataManager.actQuestCenter.updateTime;
            data.actquest.questUpdateTime   = dataManager.questCenter.updateTime;
        requestSyncUpClient(data, cb)  -> server.request('sync.syncupclient', data, ...)
        updateByServer(data):
            if (data.mail)  dataManager.mailbox.updateByServer(data.mail);
            if (data.quest) dataManager.questCenter.updateByServer(data.quest);

    所以这里是**唯一**能把新的任务数据推给在线客户端的通道：
    客户端上报它手里的 questUpdateTime，和服务端不一致时就把整个 quest 块回过去。
    （questUpdateTime 确实被客户端塞在 actquest 里面，照抄即可。）
    """
    player = _player(session)
    act = (msg or {}).get("actquest") or {}
    client_time = act.get("questUpdateTime") or (msg or {}).get("questUpdateTime")
    data: dict = {}
    if client_time != quests.update_time(player):
        data["quest"] = quests.block(player)
        log.info("sync.syncupclient 推送 quest（客户端 %s != 服务端 %s）",
                 client_time, quests.update_time(player))
    return {"code": CODE_OK, "msg": "", "data": data}
