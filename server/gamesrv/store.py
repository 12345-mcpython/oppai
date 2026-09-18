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

# 初始士兵。key 都取自客户端 table_soldier（表里有 1558 条），
# 挑了几个不同品质的，方便在「编成」里看出差别。
# 之前 soldiers 一直是空列表，导致编成界面加了人也不显示。
SOLDIER_KEYS = [
    "sads010101",   # quality 1
    "sads010102",   # quality 2
    "sads010103",   # quality 3
    "sads010104",   # quality 4
]


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


def new_soldier(index: int, key: str, lv: int = 1, star: int = 1) -> dict:
    """士兵。字段名来自客户端 src/data/soldier.js 的 Soldier（_init 里读的）。

    客户端 Soldier._init 会拿 key 去查 table_soldier 取 char_key / quality /
    各项属性，所以这里只需要给出会变的那几个字段。
    """
    return {
        "id": index,
        "key": key,
        "charType": CHAR_TYPE_SOLDIER,
        "lv": lv,
        "star": star,
        "curExp": 0,
        "quality": 1,
        "positioning": 1,
        "skillLvList": [],
        "equipments": [],
        "isLock": 0,
        "isDel": 0,
        "isNew": 1,
        "isDetect": 0,
        "createTimeSec": int(time.time()),
    }


def new_soldiers() -> list:
    """初始士兵列表（每样一个）。"""
    return [new_soldier(i + 1, k) for i, k in enumerate(SOLDIER_KEYS)]


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
        return db[account]


def update_player(account: str, **fields) -> dict:
    with _lock:
        db = _load()
        player = db.setdefault(account, new_player(account))
        player.update(fields)
        _save(db)
        return player


def all_players() -> dict:
    with _lock:
        return _load()
