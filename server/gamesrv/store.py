"""玩家数据存储。

单机模拟服，先用 JSON 文件存，够用且方便手改。
字段命名参考客户端 src/data/player.js 里 updateByServer / _getXxx 的用法。
"""

from __future__ import annotations

import json
import os
import threading
import time

from . import config, logx

log = logx.get("store")

_path = os.path.join(config.DATA_DIR, "players.json")
_lock = threading.RLock()


def now_ms() -> int:
    return int(time.time() * 1000)


def time_str(ts: int | None = None) -> str:
    """客户端 Player.ctor 会用 util.getTimeByDateStr() 解析时间字段，
    所以时间字段必须是 "YYYY-MM-DD HH:MM:SS" 字符串而不是时间戳。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts if ts is not None else time.time()))


def time_obj() -> dict:
    return {
        "timeSec": int(time.time()),
        "timezoneOffset": 0,
        "now": now_ms(),
    }


def _load() -> dict:
    if not os.path.exists(_path):
        return {}
    try:
        with open(_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        log.exception("players.json 损坏，重建")
        return {}


def _save(db: dict) -> None:
    tmp = _path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(db, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, _path)


# 默认角色 / 机甲（key 必须存在于客户端 table_hero / table_mecha 里）
# table_hero: hadf(阿呆芙·洛玛) / haysdn
# table_mecha: madflj(六酱) / madfxdlj / madftiger / madfewt
HERO_KEY = "hadf"
MECHA_KEY = "madflj"
CHAR_TYPE_HERO = "h"
CHAR_TYPE_MECHA = "m"
CHAR_TYPE_SOLDIER = "s"

# 背包里的道具 key，取自客户端 ITEM_KEY：
#   GEM 100001 / MONEY 100002 / ACTION_POINT 100003（行动力，玩家口中的"甜甜圈"）
ITEM_GEM = "100001"
ITEM_MONEY = "100002"
ITEM_ACTION_POINT = "100003"

# 新手引导位掩码全 1 = 所有引导都已完成。见 new_player() 里的说明。
GUIDE_MARK_DONE = 0x7FFFFFFF

# 玩家「指挥部」初始等级。客户端按等级解锁功能，最靠前的门槛是编成里的
# 「培养 / 升级军士」= 6 级，所以默认给 30 一步到位（顺带过了 25 级那批）。
MIN_PLAYER_LV = 30

# 初始士兵名单的版本号。改动 SOLDIER_KEYS 时 +1，老存档的军士会被整个重发
# （`_migrate` 里判断）—— 注意这会重置军士等级。
ROSTER_VERSION = 3

# 初始士兵。(key, 站位, 品质)，key 取自客户端 table_soldier。
#
# 站位必须三个都有：SOLDIER_POSITIONING = {FRONT:1, MIDDLE:2, BACK:3}，
# 「编成 -> 上阵队伍 -> 加号」的队员选择界面是按站位分页的，
# 只有前锋的话切到「炮弹」页就是「没有符合要求的军士哦~」。
#
# 而且每个士兵的 char_key 必须互不相同 —— 客户端规则是
# 「相同的角色只能选择一个作为上阵军士」，同一个 char_key 只会出现一个。
# 之前给的是 sads010101~04，四个全是同一个角色 sads，所以列表里基本是空的。
#
# ⚠️⚠️ **必须是「玩家可获得」的军士**，判据是
#     table_soldier_master[char_key].card_type === CARD_TYPE.TEAMMATE(1)
# `Soldier._loadMasterAttr` 就一句 `this._cardType = table_soldier_master[char_key].card_type`，
# 只有 1 是自军卡（2=ENEMY，3=EXP，4=SKILL）。
#
# 早先这份名单是按「能 new 出来」挑的，结果挑中一堆 card_type==2 的**敌方单位**
# （sfog 是先代巫女 BOSS，sads/safj/sbdsl 等也全是敌人），后果有两个：
#   1. 「编成 -> 培养」选不出材料 —— 材料列表只收 cardType==TEAMMATE，
#      18 个里只剩 7 个；
#   2. `CharCenter.calcSoldierUpgrade` 的 gainExp 算成 NaN —— 那些行没有
#      `base_exp` 字段，`table_soldier[key].base_exp` 是 undefined，
#      加法得 NaN，`while (gainExp < maxExp)` 永远为假，直接顶到 maxLv。
# 现在按 card_type==1 + quality==4 + 三站位各 6 个挑，实测 18 个全部构造成功且 ct=1。
#
# ⚠️ 一个士兵构造失败会**整份士兵列表全丢**（CharCenter 里是整体 try），
# 所以只能用实测能 new 出来的 key。lfcz01/lfcz02/lfcz03（废柴子系）
# 在客户端会抛 "TypeError: row is undefined"，千万别放进来。
SOLDIER_KEYS = [
    # 前锋 FRONT = 1
    ("sasm010104", 1, 4),      # 阿斯麦
    ("sbns010104", 1, 4),      # 柏妮丝
    ("sglrs010104", 1, 4),
    ("shx010104", 1, 4),
    ("sjm010104", 1, 4),
    ("skdln010104", 1, 4),
    # 中卫 MIDDLE = 2
    ("sbd010104", 2, 4),       # 巴度
    ("sbq010104", 2, 4),       # 贝琪
    ("sflr010104", 2, 4),
    ("sfn010104", 2, 4),
    ("shs010104", 2, 4),
    ("sjlt010104", 2, 4),
    # 后卫 BACK = 3
    ("saf010104", 3, 4),       # 爱芙
    ("same010104", 3, 4),      # 爱莫儿
    ("scyy010104", 3, 4),      # 长月遥
    ("sda010104", 3, 4),
    ("sdde010104", 3, 4),
    ("sdfn010104", 3, 4),
]


# 功能模块（主界面按钮）开启状态。key 是客户端 table_function_open /
# table_main_layer.module_key 里的数字编号，客户端有两处读它：
#
#   moduleManager.isModuleUnlock(key):
#       var module = dataManager.player.moduleState[key];
#       return module.isUnlock;
#   mainlayer._initModuleButtons:
#       var modules = dataManager.player.moduleState;   // ← 缺失时是 undefined
#       ...
#       var module = modules[row.module_key];
#       var isUnlock = module.isUnlock;                 // ← 抛 TypeError
#
# 缺这个字段主界面构造就会死在
#   TypeError: modules is undefined @ src/ui/main/mainlayer.js:188
# 表现就是登录后黑屏。全 1 = 所有功能都开放。
MODULE_KEYS = [str(100001 + i) for i in range(32)]


def new_module_state() -> dict:
    return {k: {"isUnlock": 1, "unlockLv": 1} for k in MODULE_KEYS}


def new_hero() -> dict:
    """主角。字段名来自客户端 src/data/hero.js 的 Hero。"""
    return {
        "id": 1,
        "key": HERO_KEY,
        "charType": CHAR_TYPE_HERO,
        "curMechaKey": MECHA_KEY,
        "mechaKeys": ["madflj", "madfxdlj", "madftiger", "madfewt"],
        "favorLv": 1,
        "favorCurExp": 0,
        "talentsLv": {},
        "fashionKey": "",
        "recCount": 0,
        "lastRecTime": time_str(),
        "isCastEnabled": 0,
        "isAsstEnabled": 0,
    }


def new_soldier(index: int, key: str, positioning: int = 1, quality: int = 1,
                lv: int = 1, star: int = 1) -> dict:
    """士兵。字段名来自客户端 src/data/soldier.js 的 Soldier（_init 里读的）。

    客户端 Soldier._init 会拿 key 去查 table_soldier 取 char_key / quality /
    各项属性，所以这里只需要给出会变的那几个字段；positioning / quality 也照
    table_soldier 填一份，免得客户端某处直接读服务端这份。

    ⚠️ `skillLv` 不能省。客户端 `Soldier._init` 第一句是
        this._originData = util.encodeOriginData(args);
    把**服务端原始数据**存成快照（数字 <<5 之后 JSON），
    之后 `isNormalData()` 拿它跟本地值逐项比对（key/quality/star/lv/skillLv），
    其中 lv 是 `this._mainSkill.lv = args.skillLv || 1` —— 默认 1。
    不给 skillLv 的话快照里根本没有这个字段，比对永远 `undefined != 1`，
    表现就是编好队一点「战斗」弹「队伍数据异常，请重新登陆」，然后闪退。
    （空队伍不会触发：`Team.isNormalData()` 是遍历队员逐个查的。）
    """
    return {
        "id": index,
        "key": key,
        "charType": CHAR_TYPE_SOLDIER,
        "lv": lv,
        "star": star,
        "curExp": 0,
        "quality": quality,
        "positioning": positioning,
        "skillLv": 1,
        # 技能等级表：客户端 _loadSkill 按 table_soldier.skill_index 逐位取，
        # 数量对得上才会把技能建出来（table 里 skill_index 形如 "1#2"，两位）
        "skillLvList": [1, 1],
        "equipments": [],
        "isLock": 0,
        "isDel": 0,
        "isNew": 1,
        "isDetect": 0,
        "createTimeSec": int(time.time()),
    }


def new_soldiers() -> list:
    """初始士兵列表（三个站位各 6 个，角色互不重复）。"""
    return [new_soldier(i + 1, key, pos, quality)
            for i, (key, pos, quality) in enumerate(SOLDIER_KEYS)]


def ensure_soldiers(player: dict) -> list:
    """玩家的军士列表。

    ⚠️ 军士必须**存在玩家身上**，不能每次登录现生成 ——
    不然「军士升级」升完一重登就没了（任务 210001「首次升级」也会跟着回退）。
    第一次登录时按初始名单发一份，之后就一直是玩家的。
    """
    soldiers = player.get("soldiers")
    if not isinstance(soldiers, list) or not soldiers:
        soldiers = new_soldiers()
        player["soldiers"] = soldiers
    return soldiers


def find_soldier(player: dict, soldier_id) -> dict | None:
    try:
        soldier_id = int(soldier_id)
    except (TypeError, ValueError):
        return None
    for s in ensure_soldiers(player):
        if int(s.get("id") or 0) == soldier_id:
            return s
    return None


def new_mecha() -> dict:
    """默认机甲。字段名来自客户端 src/data/mecha.js 的 Mecha。"""
    return {
        "id": 1,
        "key": MECHA_KEY,
        "uuid": MECHA_KEY,
        "lv": 1,
        "charType": CHAR_TYPE_MECHA,
        "hidden": 0,
        "positioning": 1,
        "scale": 1000,
        "type": 1,
        "maxLv": 1,
        "maxSkillLv": 1,
        "minRotation": -20,
        "maxRotation": 80,
    }


def new_team(index: int) -> dict:
    """空队伍。字段名来自客户端 src/data/team.js 的 Team。"""
    return {
        "id": index + 1,
        "index": index,
        "hero": new_hero(),
        "heroKey": HERO_KEY,
        "mechaKey": MECHA_KEY,
        "soldierKeys": [],
        "soldiers": [],
        "soldierCount": 0,
        "spInit": 250,
        "spRate": 10,
        "spMax": 1800,
    }


def new_player(account: str) -> dict:
    """新建玩家。

    ⚠️ 等级直接给 MIN_PLAYER_LV 而不是 1：客户端一大堆功能是按「指挥部等级」
    解锁的（编成里培养/升级军士要 6 级，有的入口要 25 级，提示语在
    table_dictionary[2401]「指挥部等级#@1@#开启」/ [4207]），
    1 级进去点什么都提示"指挥部等级不足哦~OAQ"。
    私服没必要让人从 1 级刷起，想体验原版就从 MIN_PLAYER_LV 改回去。
    """
    now = int(time.time())
    return {
        "id": 1,
        "account": account,
        "name": account,
        "lv": MIN_PLAYER_LV,
        "curExp": 0,
        "maxSoldiersCount": 50,
        "actionPoint": 100,
        "maxActionPoint": 100,
        "actionPointTime": time_str(now),
        "selfDesc": "",
        "headId": 1,
        "medalClothesId": 0,
        "medalBgId": 0,
        "curTeamIdx": 0,
        "moduleState": new_module_state(),
        # 军士（18 个初始军士）。**建号时就发**，不是等第一次登录现生成 ——
        # `ensure_soldiers` 只在内存里补，登录接口不写盘，所以「现生成」的版本
        # 每次都可能是新的，军士升级/突破的结果会莫名其妙回退。
        "soldiers": new_soldiers(),
        "rosterVersion": ROSTER_VERSION,
        # 新手引导位掩码（客户端 guideManager.checkGuide 用 id & (1 << n) 判断）。
        # 全 1 表示所有引导都已完成 —— 否则 GuideLayer 会一直拦着菜单点击：
        #   op.uiLoader.addTouchEventListener 里，只要
        #   GuideLayer.getInstance().isGuide() 为真，就先把点击交给
        #   GuideLayer.nextStep()，正常回调永远走不到。
        "guideMark": GUIDE_MARK_DONE,
        "createTime": time_str(now),
        # 客户端 Player.initTeams() 会按 TEAM_COUNT_LIMIT 建队，队伍数量给足
        "teams": [new_team(i) for i in range(5)],
        "character": {},
        "asst": {},
        "asstKey": "",
        "guild": {},
        "gachaTimes": 0,
        "worldChatTime": time_str(now),
        "worldChatCount": 0,
        "monthCardDueTimeSec": time_str(now),
        "msgPushMark": 0,
        # 任务进度。放在 new_player 里（而不是等第一次用到再 setdefault），
        # 是为了让 updateTime 稳定：客户端会拿它跟服务端比来决定要不要拉新数据，
        # 每次请求现生成的话 sync.syncupclient 会永远认为「有变化」。
        # 新手引导 / 任务见 gamesrv/quests.py。
        "quests": {"done": [], "updateTime": time_str(now)},
    }


def _migrate(player: dict) -> bool:
    """给老存档补齐后来才加上的字段。

    客户端对这些字段是「直接读属性」的，缺一个就在主界面抛 JS 异常，
    所以宁可在这里无条件补齐。返回是否改动过。
    """
    fresh = new_player(player.get("account", "player"))
    changed = False
    # 军士名单要在下面「按 key 补齐」之前处理 —— rosterVersion 也是 new_player
    # 里的字段，先补齐的话这里就永远看不出「版本变了」。
    #
    # 军士列表必须是真列表。踩过一次：存档里存成了 null，
    # `find_soldier` 于是每次现发一份新的，升级结果一重登就回退。
    #
    # ROSTER_VERSION 变了就整个重发：名单本身改了（比如把敌方单位换成自军卡），
    # 老存档里存的还是旧 key，留着没用。**代价是军士等级会重置**，
    # 所以版本号只在真的换名单时才动。
    if (not isinstance(player.get("soldiers"), list) or not player["soldiers"]
            or int(player.get("rosterVersion") or 0) != ROSTER_VERSION):
        player["soldiers"] = new_soldiers()
        player["rosterVersion"] = ROSTER_VERSION
        changed = True
        log.info("玩家 %s 军士名单重建（roster v%s），共 %d 个",
                 player.get("account"), ROSTER_VERSION, len(player["soldiers"]))
    for key, value in fresh.items():
        if key not in player:
            player[key] = value
            changed = True
    # moduleState 要按 key 合并，不能整个覆盖掉玩家已有的开启状态
    state = player.get("moduleState")
    if not isinstance(state, dict):
        state = {}
        player["moduleState"] = state
        changed = True
    for key, value in new_module_state().items():
        if key not in state:
            state[key] = value
            changed = True
    # 老存档是 1 级建的，光靠 new_player 改默认值救不回来，这里补一次升级。
    # 见 new_player 的注释：指挥部等级不够，编成里「培养」「升级军士」直接不给点。
    if int(player.get("lv") or 1) < MIN_PLAYER_LV:
        player["lv"] = MIN_PLAYER_LV
        changed = True
        log.info("玩家 %s 指挥部等级补到 %d", player.get("account"), MIN_PLAYER_LV)
    return changed


def player_exists(account: str) -> bool:
    with _lock:
        return account in _load()


def get_or_create_player(account: str) -> dict:
    with _lock:
        db = _load()
        if account not in db:
            db[account] = new_player(account)
            _save(db)
            log.info("创建玩家 %s", account)
        elif _migrate(db[account]):
            _save(db)
            log.info("补齐玩家 %s 缺失的字段", account)
        return db[account]


def update_player(account: str, **fields) -> dict:
    with _lock:
        db = _load()
        player = db.setdefault(account, new_player(account))
        _migrate(player)
        player.update(fields)
        _save(db)
        return player


def save_player(player: dict) -> None:
    """把改过的 player 写回 players.json。

    ⚠️ 这个很容易踩：`get_or_create_player()` 每次都从文件重新 load，
    返回的是**临时副本**，直接改它、不写回去就全丢了。
    主线任务领奖曾经就栽在这里 —— 每次 `done` 都从空开始，
    日志里永远是「累计 1 条」，客户端下次刷新又看到同一条能领，
    表现就是「反复刷新，顶上一直是这两条任务」。
    """
    account = player.get("account") or config.DEFAULT_ACCOUNT
    save_player_dict(account, player)


def save_player_dict(account: str, player: dict) -> None:
    """按账号整份覆盖存档。

    `save_player` 是「拿传入的这份去合并覆盖」，调用方必须先
    `get_or_create_player()` 拿到当前存档、改完再传进来。
    devtools 的存档编辑面板拿到的是一整个 JSON，直接用这个更直白：
    传进来什么就是什么（仍然带上 `account`，免得两份数据对不上号）。
    """
    player = dict(player)
    player["account"] = account
    with _lock:
        db = _load()
        db[account] = player
        _save(db)


def all_players() -> dict:
    with _lock:
        return _load()
