"""agent.* —— 登录 / 建号相关路由。

route 名字来自客户端 src/manager/datamanager.js：

    playerLogin()  -> server.request('agent.getlogindata', {}, cb)
    playerCreate() -> server.request('agent.createplayer', {activeCode}, cb)
"""

from __future__ import annotations

from ..gameproto import CODE_OK
from .. import config, logx, store
from . import route

log = logx.get("handler.agent")


def _module_stubs() -> dict:
    """各模块的初始数据。

    **key 名不是模块名**：从客户端 dataManager 里 `new Xxx(data.<短名>)` 的
    实际调用抓出来（用探针包装各个 Class 的构造函数，打印它收到的参数）：

        Bag          <- data.item              CharCenter  <- data.char
        Mailbox      <- data.mail              ActQuestCenter <- data.actquest
        QuestCenter  <- data.quest             FavorCenter <- data.favor
        FavorEventCenter <- data.favorevent    ExchangeCenter <- data.exchange
        TalentCenter <- data.talents           SignCenter  <- data.sign
        ArenaCenter  <- data.arena             SocietyClg  <- data.societyclg
        BossCenter   <- data.boss              EquipmentCenter <- data.equipment
        ConsumeActivity <- data.consumeactivity  FriendSupport <- data.friendsupport
        NoviceQuestCenter <- data.novicequest
        其余同名：player / instance / gacha / friend / shop / rank / score /
                  society / detect / medal / share / subareaachievement / diary
    """
    t = store.time_str()
    return {
        "instance": {
            "levels": [],
            "activityChapters": [],
            "subareaLevels": [],
            "appearBossKey": {},
            "newActChapterFlag": {},
            "chapters": [],
            "chapterStars": {},
        },
        "item": {"items": [], "package": {}, "limitTimeItems": []},
        "char": {
            "heros": [store.new_hero()],
            "soldiers": store.new_soldiers(),
            "mechas": [store.new_mecha()],
            "daemons": [],
            "maxSoldiersCount": 50,
            "charManual": {},
            "skillComb": {},
        },
        "gacha": {
            "gachaData": {},
            "gachaLibCards": {},
            "lastUpdateInfoTime": t,
            "freeGachaTip": {},
            "activityTimes": {},
        },
        "mail": {"mails": [], "updateTime": t, "remindCount": 0},
        "quest": {
            "quests": [],
            "finishQuests": [],
            "finishQuestsId": [],
            "dailyQuestsTime": t,
            "remindCount": 0,
        },
        "actquest": {"activites": [], "updateTime": t, "updateList": []},
        "favor": {"favors": [], "isNeedAsstEff": 0, "favorExpAdd": 0},
        "favorevent": {"favorEvents": [], "events": [], "removedFeEventKeys": []},
        "friend": {"friendMapList": [], "recommendationList": [], "isNeedShowTip": 0},
        "exchange": {},
        "talents": {"talentTypes": [], "talents": {}, "updateByPlayer": 0},
        "sign": {
            "signs": [],
            "normalSigns": [],
            "eventSigns": [],
            "birthdaySigns": [],
            "noviceSigns": [],
        },
        "shop": {"shopObj": {}, "buyRecordObj": {}, "activityShopList": []},
        "arena": {"arenaInfo": {}, "mechaSuperSkillCorrectOwn": {}},
        "rank": {"rankInfoObj": {}, "lastUpdateTimeObj": {}},
        "score": {"scoreObj": {}, "scoreInfoObj": {}, "lastUpdateTime": t},
        "society": {"societyLv": 0, "society": {}},
        "societyclg": {"bossList": [], "records": [], "historyRecords": [], "playerInfoList": []},
        "boss": {
            "bossShareObj": {},
            "lastFightBossId": 0,
            "friendBossFlag": {},
            "bossKillRewardList": [],
        },
        "chat": {"channels": [], "panels": []},
        "detect": {"completeCount": 0, "allDetect": [], "dropInfo": {}, "speedCount": {}},
        "medal": {"medals": [], "clothes": [], "bgs": [], "heads": []},
        "equipment": {"equipments": [], "suits": {}},
        "share": {"shareCount": 0, "isCanShare": 0},
        "subareaachievement": {"achievements": []},
        "consumeactivity": {"activityInfo": {}},
        "diary": {"storyDiarys": [], "levels": [], "newLevels": []},
        "friendsupport": {"soldiers": [], "userecord": {}},
        "novicequest": {"noviceQuest": {"chars": [], "lines": []}, "lines": [], "chars": []},
    }


def _agent_block() -> dict:
    """initUserData 第一句就是 syncTime(data.agent, ...)，
    syncTime 读的是 data.agent.timeSec / data.agent.timezoneOffset（不是嵌套的 timeObj）。
    后面 chat.init 还会用 msgCenterAddr / worldChatLimit。
    """
    t = store.time_obj()
    return {
        "timeSec": t["timeSec"],
        "timezoneOffset": t["timezoneOffset"],
        "now": t["now"],
        "serverTime": store.now_ms(),
        "reqTime": 0,
        "reqFinishTime": 0,
        "timeObj": t,
        "msgCenterAddr": "",
        "worldChatLimit": 0,
    }


@route("agent.getlogindata")
def get_login_data(session: dict, msg: dict, req_id):
    """返回玩家登录数据。

    客户端 `dataManager.initUserData(data)` 会遍历这些 key 构造各模块对象。

    ⚠️ **必须始终返回完整角色，不能回 `player: null`。**

    反汇编 `dataManager.playerLogin/</<`：
        var code = data.code;
        if (code === 200) cb4AfterLogin(null, data.data);
        else              cb4AfterLogin(data, null);

    它拿到 `data.data` 就直接喂给 `initUserData`，**自己不处理「还没有角色」
    的情况**。回 null 的话 `new Player(null)` 会把后续状态搞坏
    （表现为 `CB4 ERR: this._lvEncrp is null`、`SWITCH ERR: this._teams is null`）。
    所以新号第一次登录时，服务端直接把角色建好返回。
    """
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    if not store.player_exists(account):
        log.info("账号 %s 还没有角色，服务端自动建号", account)

    player = store.get_or_create_player(account)
    t = store.time_obj()
    log.info("agent.getlogindata account=%s playerId=%s", account, player["id"])
    data = {
        "agent": _agent_block(),
        "timeObj": t,
        "serverTime": store.now_ms(),
        "player": player,
    }
    data.update(_module_stubs())
    return {"code": CODE_OK, "msg": "", "data": data}


@route("agent.createplayer")
def create_player(session: dict, msg: dict, req_id):
    """建号：客户端在没有角色时会请求这里，成功后就该播开场/新手引导了。"""
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    active_code = (msg or {}).get("activeCode", "")
    log.info("agent.createplayer account=%s activeCode=%s", account, active_code)
    player = store.get_or_create_player(account)

    t = store.time_obj()
    data = {
        "agent": _agent_block(),
        "timeObj": t,
        "serverTime": store.now_ms(),
        "player": player,
        "isNewPlayer": True,
        "newPlayerGuide": 1,
    }
    data.update(_module_stubs())
    return {"code": CODE_OK, "msg": "", "data": data}


@route("agent.gettimeinfo")
def get_time_info(session: dict, msg: dict, req_id):
    """时间同步。

    反汇编 dataManager.syncTime 的回调：

        server.request('agent.gettimeinfo', null, function (err, res, reqTime, reqFinishTime) {
            syncTime(res.data, reqTime, reqFinishTime);   // ← 直接吃 res.data
            if (cb) cb();
        });

    而 syncTime(timeObj, ...) 读的是 `timeObj.timeSec` / `timeObj.timezoneOffset`，
    所以 data 必须**直接**带这两个字段（不能套一层 timeObj）。
    给错了会算成 NaN，客户端会不停重试（表现为一直转加载圈 + 刷 gettimeinfo）。
    """
    if isinstance(req_id, int) and req_id % 200 == 1:      # 客户端刷得很快，日志只留个采样
        log.info("agent.gettimeinfo msg=%s reqId=%s", msg, req_id)
    t = store.time_obj()
    return {
        "code": CODE_OK,
        "msg": "",
        "data": {
            "timeSec": t["timeSec"],
            "timezoneOffset": t["timezoneOffset"],
            "now": t["now"],
            "serverTime": store.now_ms(),
        },
    }


@route("agent.getserverlist")
def get_server_list(session: dict, msg: dict, req_id):
    return {"code": CODE_OK, "msg": "", "data": {"servers": []}}


@route("agent.logout")
def logout(session: dict, msg: dict, req_id):
    log.info("agent.logout")
    return {"code": CODE_OK, "msg": "", "data": {}}
