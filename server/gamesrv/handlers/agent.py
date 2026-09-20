"""agent.* —— 登录 / 建号相关路由。

route 名字来自客户端 src/manager/datamanager.js：

    playerLogin()  -> server.request('agent.getlogindata', {}, cb)
    playerCreate() -> server.request('agent.createplayer', {activeCode}, cb)
"""

from __future__ import annotations

from ..gameproto import CODE_OK
from .. import (arena, config, detect, exchange, favor, gacha, instance, logx, quests,
                sign, store, subarea)
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
            # 英雄 / 机甲存在存档里（抽卡会往里加，见 store.add_hero/add_mecha）
            "heros": store.player_heros(player) if player else [store.new_hero()],
            # ⚠️ 军士存在玩家存档里（升级过就不能每次登录现生成，否则升级会回退）
            "soldiers": store.ensure_soldiers(player) if player else store.new_soldiers(),
            "mechas": store.player_mechas(player) if player else [store.new_mecha()],
            # 守护灵（宿舍 guard 面板）。**按 charKey 索引的 map，不是数组** ——
            # `CharCenter.getDaemon(k)` 是 `this._daemons[k]`，给数组的话恒 undefined。
            # 行只有 `{charKey, lv, curExp}`，属性/上限客户端自己算。
            # 只有军士角色有守护灵（主角没有 daemon_mode）。见 store.player_daemons。
            "daemons": store.player_daemons(player),
            "maxSoldiersCount": 50,
            # 角色图鉴（菜单 → 情报室）。**空 map = 情报室里一个角色都没有**：
            # 列表就是 `CharCenter.getSoldierManualKeys()` = 这个 map 的键（按 card_type 过滤）。
            "charManual": store.player_char_manual(player) if player else {},
            "skillComb": {},
        },
        # 抽卡。**客户端一张 gacha 表都没有**，卡池 master 全在这一块里下发：
        # `gachaData`（玩家次数）/ `gachaInfoList`（消耗行）/ `gachaMasterList`（池子定义）
        # 三个都是 **map**，`Gacha.update(data)` 直接吃。见 gamesrv/gacha.py。
        "gacha": gacha.login_block(player) if player else {
            "gachaData": {}, "gachaInfoList": {}, "gachaMasterList": {},
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
        # 宿舍事件。⚠️ **这块是「已解锁事件」的唯一来源，别删** ——
        # `FavorEventCenter._initData` 按 `table_favor_random_event` 整张表建
        # `_favorEvents`，其中 `_favorEvents[i] = new FavorEvent(data[i] || {eventKey:i}, ...)`
        # 用的是**这里给的行**；没给的用占位。
        # （我一度以为它是 scratch 对象、不读 —— 实机量过：184 条里 19 条带 id，
        #   正好是这里建的那 19 条。见 favor.py「宿舍事件」段 + overview §6.8）
        "favorevent": favor.event_block(player),
        "friend": {"friendMapList": [], "recommendationList": [], "isNeedShowTip": 0},
        # 功能开启标记**不在这里**：客户端读的是 `data.player.moduleOpenMark`
        # （`Player.ctor`），放顶层等于没发 —— 见 `_player_block()`。
        # 黑市交易所 / 充值页。形状 `{<itemKey>: 行}` —— 客户端 `_exchangeData` 直接用它，
        # 行里的 `todayExchangeTimes` 决定下一次兑换用哪一档（见 gamesrv/exchange.py）。
        "exchange": exchange.block(player),
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
        # 签到。**形状是 `{updateTime, signs: {signKey: 行}}`**（`signs` 是 map 不是数组！
        # 客户端 `SignCenter.ctor` 是 `this._signs = data.signs` + `for (k in _signs)`，
        # 给数组就是一条签到都没有 —— 表现就是「点签到没用」）。
        # 排期/奖励服务端自己定（客户端没有签到表），见 gamesrv/sign.py。
        "sign": sign.block(player),
        "shop": {"shopObj": {}, "buyRecordObj": {}, "activityShopList": []},
        # 演习场。形状是 `{arenaInfo, rivals, resetTime, refreshTime}`（`ArenaCenter.ctor`
        # 原样读；`mechaSuperSkillCorrectOwn` 客户端全库 0 命中，不用发）。
        # 对手的 `soldier<i>` 是「军士编码串」`"key#星级#等级#技能等级"`、`asstKey` 要军士卡 key。
        # 见 gamesrv/arena.py 的模块注释（`refreshTime` 要发两份，否则倒计时变 NaN）。
        "arena": arena.block(player),
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
        # 任务派遣（主界面「派遣」）。形状 `{speedInfo: {分类: 已用免费加速次数},
        # detect: {章节key: {beginTimeSec, waitTime, subCD, speedCount}}}` —— 都是**秒**，
        # 客户端 `Detect.ctor` 读 `detect.speedInfo` + `detect.detect`（见 gamesrv/detect.py）。
        "detect": detect.block(player),
        "medal": {"medals": [], "clothes": [], "bgs": [], "heads": []},
        # 装备。形状见 store.equipment_block() —— 是 maxEquipmentCount / equipments /
        # equipmentGroups 三个键。以前这里是 `{equipments: [], suits: {}}`，两个问题：
        #   1. 少了 maxEquipmentCount 和 equipmentGroups（客户端 ctor 要读）
        #   2. `suits` 客户端**压根不读**（我在 equipmentcenter.jsc 里搜过）
        # 另外每条装备的 firstAttrKeys / secondAttrKeys 必须是数组，否则
        # initEquipment() 读 .length 时 TypeError。
        "equipment": store.equipment_block(player),
        "share": {"shareCount": 0, "isCanShare": 0},
        # 分区成就。`{"achievements": {<id>: 行}}` —— 行由客户端在战斗结算时上报
        # （见 gamesrv/subarea.py）。以前这里是 `{"achievements": []}`（空数组），
        # 客户端 `_initData` 直接把它当 map 用，`_achivevements[id] = 行` 写进数组里，
        # 分组/领奖都对不上。
        "subareaachievement": subarea.block(player),
        "consumeactivity": {"activityInfo": {}},
        "diary": {"storyDiarys": [], "levels": [], "newLevels": []},
        "friendsupport": {"soldiers": [], "userecord": {}},
        "novicequest": {"noviceQuest": {"chars": [], "lines": []}, "lines": [], "chars": []},
    }


def _player_block(player: dict) -> dict:
    """`data.player` —— 顺手把「功能开启」标记灌进去。

    ⚠️⚠️ **`moduleOpenMark` 必须挂在玩家对象里，不能放 `data` 顶层。**
    客户端 `assets/src/data/player.jsc`：

        Player.ctor(data):         this._moduleOpenMark = data.moduleOpenMark
        Player.initModuleState():  module.isOpened = moduleOpenMark[module.markIndex] > 0
        moduleManager.popModuleOpen(): 把 !isOpened 的模块挨个弹「xxx开启」

    `Player` 拿到的是 **`data.player` 那一块**，所以顶层那个键谁都读不到
    （`jsc_find moduleOpenMark` 只有 `Player.ctor` / `initModuleState` 两处，
     都在玩家块上）。

    2026-09-20 踩的坑：原来 `moduleOpenMark` 是放在 `_module_stubs()`（= `data` 顶层），
    而**存档里那份老的 mark 是随 `player` 原样下发的** —— 于是行为完全由存档决定：
    存档里只有 `{"1": 1}` 时，`_moduleOpenMark` 就只认 1 号，`popModuleOpen()`
    一口气弹了 31 个（logcat 里能看到紧接着的
    `player.setmoduleopenmark [2,3,…,32]` 回写）。
    """
    player["moduleOpenMark"] = _module_open_mark(player)
    return player


def _module_open_mark(player: dict) -> dict:
    """登录块 `data.player.moduleOpenMark` —— 客户端拿它决定「功能开启」弹窗要不要弹。

    `Player.initModuleState()`：`module.isOpened = moduleOpenMark[module.markIndex] > 0`；
    `moduleManager.popModuleOpen()` 会把所有 `!isOpened` 且已解锁的模块挨个弹一遍
    （`table_function_open` 32 条，我们建号就全解锁 + 30 级 → 一进游戏连弹）。
    弹完客户端会把这些 mark 写回 `player.setmoduleopenmark`，服务端存下来。

    私服默认（`store.MODULE_OPEN_POPUP_SKIP`）把 mark **全标成已弹过** → 不弹。
    形状是 `{markIndex: 1}`（客户端是 `moduleOpenMark[module.markIndex]` 取值判断），
    markIndex 就是 `table_function_open[key].mark_index`（这张表 32 条，1..32 连续）。
    """
    marks = player.get("moduleOpenMark")
    out: dict = {}
    if isinstance(marks, dict):
        out = {str(k): v for k, v in marks.items()}
    elif isinstance(marks, (list, tuple)):
        # 老存档里存的是客户端直接报上来的**数组** `[1,2,3,…]`。
        # ⚠️ 不能原样回给客户端：它读的是 `moduleOpenMark[module.markIndex]`
        # （markIndex 从 1 起），拿数组当下标会整体错一位。
        out = {str(mi): mi for mi in marks}
    # ⚠️⚠️ **每项的值必须等于它自己的键**（`{"7": 7}`，不是 `{"7": 1}`）。
    # 客户端 `Player.initModuleState` 开头是这么拷贝的（反汇编 + 实机都验过）：
    #     var moduleOpenMark = {};
    #     for (var k in this._moduleOpenMark) moduleOpenMark[this._moduleOpenMark[k]] = this._moduleOpenMark[k];
    # 也就是说它拿**值**当新键 —— 全发 1 会被塌缩成单个 `{"1": 1}`，
    # 于是 32 个模块里只有 1 号算"已开"，其余 31 个照弹。
    # 实机对照：`{i: 1}` → isOpened 1/32、`{i: i+6}` → 26/32、`{i: i}` → 32/32。
    out = {str(k): int(k) for k, v in out.items()
           if v and str(k).lstrip("-").isdigit()}
    if not store.MODULE_OPEN_POPUP_SKIP:
        return out
    from .. import items as items_mod

    for row in (items_mod.table("table_function_open") or {}).values():
        mi = (row or {}).get("mi")
        if mi is not None:
            out[str(mi)] = int(mi)
    return out


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
    # 默认衣服。**不补的话抚摸完全没反应**（客户端拿不到表情立绘直接 return），
    # 见 gamesrv/favor.py 里 default_clothes 那段。
    if favor.ensure_default_looks(player):
        store.save_player(player)
    # 衣柜/背景一次性发满（私服取舍：原版靠扭蛋和活动，我们扭蛋是空卡池）
    if favor.ensure_look_stock(player):
        store.save_player(player)
    # 守护灵行：只给「有 daemon_mode 的角色」建（主角没有）
    if store.ensure_daemons(player):
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
        "player": _player_block(player),
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
    if favor.ensure_default_looks(player):
        store.save_player(player)
    if favor.ensure_look_stock(player):
        store.save_player(player)
    if store.ensure_daemons(player):
        store.save_player(player)
    new_events = favor.sync_events(player)
    if new_events:
        store.save_player(player)

    t = store.time_obj()
    data = {
        "agent": _agent_block(),
        "timeObj": t,
        "serverTime": store.now_ms(),
        "player": _player_block(player),
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
