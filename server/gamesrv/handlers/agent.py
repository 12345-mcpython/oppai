"""agent.* —— 登录 / 建号相关路由。

route 名字来自客户端 src/manager/datamanager.js：

    playerLogin()  -> server.request('agent.getlogindata', {}, cb)
    playerCreate() -> server.request('agent.createplayer', {activeCode}, cb)
"""

from __future__ import annotations

from ..gameproto import CODE_OK
from .. import config, favor, instance, logx, quests, store
from . import route

log = logx.get("handler.agent")


def _module_stubs(player: dict | None = None) -> dict:
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
        # ⚠️⚠️ 这里**不能**放 "instance"。
        #
        # `get_login_data` / `create_player` 都是：
        #     data = {..., "instance": instance.login_block(player)}   # 真实关卡进度
        #     data.update(_module_stubs(player))                       # 把桩并进去
        # 早期这里放了一份桩 `{"instance": {"levels": []}}`，update 时**把真进度整个盖掉**。
        # 后果不是"少个字段"而是整条关卡线索断掉：
        #   * 客户端 `Instance._updateLevels` 收不到任何关卡 →
        #     1142 个 Level 全部停在 `_starMark = -1`
        #   * 关卡列表不显示已通关、章节星级恒为 0
        #   * 按通关解锁的功能永远锁着 —— 比如「萌源增幅」（天赋），
        #     `table_function_open[100014].unlock_level_key = "100316"`（3-6 日不落的方向），
        #     `layerjumpmanager.checkLevel` 要求 `getStarsCount() > 0`，
        #     而 `_starMark = -1` 时 `getStarsCount()` 返回 -1。
        # 所以关卡相关的键一律只由 `instance.login_block()` 提供，这里别碰。
        # 背包。**这个 key 直接就是「itemKey -> 数量」的平铺映射，不是嵌套结构**：
        # `dataManager.initUserData` 里是 `this.bag = new Bag(data.item)`，
        # 而 `Bag.ctor(items)` 直接 `for (key in table_item) _items[key] = _createItem(key, items[key] || 0)`。
        # 早先按 `{items:{...}, package:{}, limitTimeItems:[]}` 给，items[key] 全是 undefined，
        # 结果就是所有道具都是 0（表现：进关卡弹「没有甜甜圈了 是否需要补充行动力」）。
        #   100001 钻石 / 100002 萌钞 / 100003 行动力（"甜甜圈"）
        # 背包。以前是写死的 {GEM:100000, MONEY:10000000, AP:999}，
        # 现在读存档 —— 不然买了东西一发一扣，重登又变回去了。
        "item": store.player_items(player),
        "char": {
            "heros": [store.new_hero()],
            # ⚠️ 军士存在玩家存档里（升级过就不能每次登录现生成，否则升级会回退）
            "soldiers": store.ensure_soldiers(player) if player else store.new_soldiers(),
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
        # 任务（主线）单独算：见 gamesrv/quests.py
        # 形状必须和 quest.getnewquest 回的一致，客户端走的是同一套
        # responseConfig -> QuestCenter.updateByServer()。
        "actquest": {"activites": [], "updateTime": t, "updateList": []},
        # 好感度（宿舍）。形状（`{favors: {charKey: 行}, favorInteractChance,
        # favorInteractUpdateTimeSec}`）和那三个坑见 gamesrv/favor.py 的模块 docstring。
        # 以前这里是 `{"favors": [], "isNeedAsstEff": 0, "favorExpAdd": 0}`：
        #   1. `favors` 得是 **map** —— 客户端拿 charKey 去索引（`favorsData[charKey]`），
        #      给数组的话 63 个角色全部退化成「未获得」
        #   2. 少了 favorInteractChance / favorInteractUpdateTimeSec →
        #      `updateFavorInteract()` 里 undefined + add = NaN，互动次数显示 NaN
        #   3. isNeedAsstEff / favorExpAdd 是**死键**：`FavorCenter._initData` 把这两个
        #      写死成 false / 0，压根不从 data 读
        "favor": favor.favor_block(player),
        # 宿舍事件。⚠️ **登录块这块是 scratch 对象，客户端不会拿它填界面** ——
        # `FavorEventCenter._initData` 把 `data[i]` 覆写成 FavorEvent，
        # 真正的事件表 `_favorEvents` 只在响应键 `newFavorEvent` 里填。
        # 所以真正推送在下面 data["newFavorEvent"]（登录响应里带一份，双保险）。
        # 详见 gamesrv/favor.py「宿舍事件」那一段。
        "favorevent": favor.event_block(player),
        "friend": {"friendMapList": [], "recommendationList": [], "isNeedShowTip": 0},
        "exchange": {},
        # 天赋（培养）。**形状由客户端字节码定死**，反汇编摘录见 store.new_talents()：
        # `{type: {curTalentKey, lv}}` —— 每个 value 必须是对象。
        #
        # 早先这里给的是 `{"101": "1001"}` 这种「type -> 天赋key」的平铺字符串映射，
        # 是猜的，猜错了：`_initTalentTypes` 读 `args[k].curTalentKey` / `args[k].lv`
        # 全是 undefined → `cc.assert` 失败 → `_initTalents()` 接着用
        # `k + "#" + lv` 拼出 `"1001#undefined"` 去查 table_talent（那表的 key 是
        # `"1001#0"`~`"1001#10"`）→ 整条链断掉，天赋面板空白，异常被构造容错吞掉。
        # key 是 101/102/103（table_talent_type），**不是** 1001/2001/3001（那是 master 的 key）。
        "talents": store.player_talents(player),
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
        # 装备。形状见 store.equipment_block() —— 是 maxEquipmentCount / equipments /
        # equipmentGroups 三个键。以前这里是 `{equipments: [], suits: {}}`，两个问题：
        #   1. 少了 maxEquipmentCount 和 equipmentGroups（客户端 ctor 要读）
        #   2. `suits` 客户端**压根不读**（我在 equipmentcenter.jsc 里搜过）
        # 另外每条装备的 firstAttrKeys / secondAttrKeys 必须是数组，否则
        # initEquipment() 读 .length 时 TypeError。
        "equipment": store.equipment_block(player),
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
    # 好感度行是「按玩家真的拥有的角色」生成的，拥有关系随时会变（抽卡 / 领奖发军士），
    # 所以放在登录时补，而不是像军士名单那样建号时一次定死。补了就要写盘，
    # 不然 `get_or_create_player()` 每次重新 load，id 每次都是新的。
    if favor.ensure_favors(player):
        store.save_player(player)
    # 宿舍事件同理：好感度到级就解锁，登录时把还没建的补上。
    new_events = favor.sync_events(player)
    if new_events:
        store.save_player(player)
    t = store.time_obj()
    log.info("agent.getlogindata account=%s playerId=%s", account, player["id"])
    data = {
        "agent": _agent_block(),
        "timeObj": t,
        "serverTime": store.now_ms(),
        "player": player,
        # 主线任务窗口（quests.py 里算，和 quest.getnewquest 同一个函数）
        "quest": quests.block(player),
        # 关卡进度（关卡表在客户端自己那儿，服务端只给"哪些关通了、几星、打了几次"）
        "instance": instance.login_block(player),
    }
    data.update(_module_stubs(player))
    # 宿舍事件**必须靠响应键推**（登录块那块客户端不读，见 _module_stubs 里的说明）。
    # 首次登录时 `dataManager.isLogin` 那道闸很可能不让响应派发进来，所以
    # favor.usegift / favor.touchcharasst 那边也会带 —— 双保险。
    block = favor.new_event_block(new_events)
    if block:
        data["newFavorEvent"] = block
    return {"code": CODE_OK, "msg": "", "data": data}


@route("agent.createplayer")
def create_player(session: dict, msg: dict, req_id):
    """建号：客户端在没有角色时会请求这里，成功后就该播开场/新手引导了。"""
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    active_code = (msg or {}).get("activeCode", "")
    log.info("agent.createplayer account=%s activeCode=%s", account, active_code)
    player = store.get_or_create_player(account)
    if favor.ensure_favors(player):
        store.save_player(player)
    new_events = favor.sync_events(player)
    if new_events:
        store.save_player(player)

    t = store.time_obj()
    data = {
        "agent": _agent_block(),
        "timeObj": t,
        "serverTime": store.now_ms(),
        "player": player,
        "isNewPlayer": True,
        "newPlayerGuide": 0,   # 0 = 不是新号，跳过新手引导
        "quest": quests.block(player),
        "instance": instance.login_block(player),
    }
    data.update(_module_stubs(player))
    block = favor.new_event_block(new_events)
    if block:
        data["newFavorEvent"] = block
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
