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

# 新手引导位掩码全 1 = 所有引导都已完成。见 new_player() 里的说明。
GUIDE_MARK_DONE = 0x7FFFFFFF

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
# ⚠️ 一个士兵构造失败会**整份士兵列表全丢**（CharCenter 里是整体 try），
# 所以只能用实测能 new 出来的 key。lfcz01/lfcz02/lfcz03（废柴子系）
# 在客户端会抛 "TypeError: row is undefined"，千万别放进来。
SOLDIER_KEYS = [
    # 前锋 FRONT = 1
    ("sfog010104", 1, 4),      # SFOG
    ("sads010104", 1, 4),      # 阿达斯-中士
    ("safj010104", 1, 4),      # 金-观众
    ("sasm010104", 1, 4),      # 阿斯麦
    ("sbdsl010104", 1, 4),     # 小兵巴蒂萨雷-中士
    ("sbns010104", 1, 4),      # 柏妮丝
    # 中卫 MIDDLE = 2
    ("safdf010104", 2, 4),     # 戴夫-观众
    ("sbd010104", 2, 4),       # 巴度
    ("sbe010104", 2, 4),       # 贝尔-中士
    ("sbq010104", 2, 4),       # 贝琪
    ("scsflkl010104", 2, 4),   # 富兰克林-厨师
    ("scslsbs010104", 2, 4),   # 丽思贝丝-厨师
    # 后卫 BACK = 3
    ("saf010104", 3, 4),       # 爱芙
    ("salks010104", 3, 4),     # 艾丽科思-中士
    ("same010104", 3, 4),      # 爱莫儿
    ("sbl010104", 3, 4),       # 伯伦-普通
    ("scsslbs010104", 3, 4),   # 沙隆巴斯-厨师
    ("scyy010104", 3, 4),      # 长月遥
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
        "skillLvList": [],
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
    """新建一个 1 级玩家。"""
    now = int(time.time())
    return {
        "id": 1,
        "account": account,
        "name": account,
        "lv": 1,
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
    }


def _migrate(player: dict) -> bool:
    """给老存档补齐后来才加上的字段。

    客户端对这些字段是「直接读属性」的，缺一个就在主界面抛 JS 异常，
    所以宁可在这里无条件补齐。返回是否改动过。
    """
    fresh = new_player(player.get("account", "player"))
    changed = False
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


def all_players() -> dict:
    with _lock:
        return _load()
