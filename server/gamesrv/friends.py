"""好友系统（friend.*）。

## 客户端契约（反汇编来的，不是猜的）

`assets/src/data/friend.jsc`（逐函数反汇编）+ `assets/src/config/friendconfig.jsc`：

    路由                            请求            成功 data
    friend.getfriendlist            {}              {friendMapList, recommendationList, takeMaterialsCount}
    friend.getrecommendationlist    {}              {recommendationList}
    friend.searchplayer             {numberId}      {playerInfo}
    friend.applyfor                 {numberId}      {friendMap}
    friend.agreeapplication         {numberId}      {friendMap}
    friend.refuseapplication        {numberId}      {friendMap}
    friend.deletefriend             {numberId}      {friendMap}
    friend.sendmaterials            {numberId}      {friendMap}
    friend.takematerials            {numberId}      {friendMap, takeMaterialsCount, reward}

`friendMap` 是**一条**好友记录（不是整个列表）：

    {numberId1, numberId2, status, updateTimeSec,
     player: {numberId, name, lv, headId, lastLoginTimeSec}}

`Friend.updateFriendMap(map)` 会拿 `map.numberId1/numberId2` 拼 key（`"a#b"`），
把这条并进本地 `_friendMapList`；**`player` 字段必给** —— 好友列表每一条都走
`FriendItem._updateFriendLabel -> _updateInfo(this._info.player)`，`player` 是
undefined 的话 `info.lv` 直接 TypeError（整页列不出来）。

`recommendationList` 的每一条是**平铺**的（没有 player 外层）：
`FriendItem._updateRecommendationLabel -> _updateInfo(this._info)`，
`getNumberId()` 也直接读 `_info.numberId`。两种条目形状不一样，别混。

## 位掩码（`FRIEND_MAP_STATUS`，见 config/friendconfig.jsc）

    DELETE 1 / A_APPLY_FOR_B 2 / B_APPlY_FOR_A 4 / BE_FRIEND 8
    A_SEND_MATERIALS_B 16 / B_SEND_MATERIALS_A 32
    A_TAKE_MATERIALS_B 64 / B_TAKE_MATERIALS_A 128

⚠️ 名字里的 A/B 指的是 **key 里的第一/第二个人**，不是「我/他」。
本服的约定是**自己永远是 A**（key = `"<我的numberId>#<对方numberId>"`），
所以：我申请 = A_APPLY_FOR_B、他申请 = B_APPlY_FOR_A、我送 = A_SEND_MATERIALS_B、
他送我 = B_SEND_MATERIALS_A、我收 = A_TAKE_MATERIALS_B。客户端 `isBeApplyFor` /
`isGetMaterials` 这些函数就是按「我在 numberId1 还是 numberId2」分支查对应位的
（反汇编核对过），自己恒为 A 时每条分支都落在 A 那一支，逻辑最直白。

## 上限来自客户端表，别自己编

`Friend.isFriendListFull()`  / `.isTakeMaterialsFull()` 读的是
`table_player_level_function[玩家等级].friend_limit` / `.take_materials_limit`
（抽表见 `data/table_player_level_function.json`，1~120 级全有）：
1 级 18/4，每 10 级各 +2/+1，120 级 42/16。
服务端用同一张表算上限，和界面上的「3/18」「4/4」才能对上。

## 私服取舍（原版这些数据在别的玩家身上，这里没有别的玩家）

好友全是 **NPC**：`table_friend_support_npc` 的 101 行（助战列表用的同一张表，
行里有 `player_name` / `player_lv` / `last_time`）。numberId 用
`NPC_ID_BASE + 表内下标`（900001 起）稳定映射，反查回表行。

* 建号第一次打开好友面板时**送 5 个好友 + 2 条待处理申请**（否则列表是空的，
  送/收物资、同意/拒绝、删除这些功能一个都点不到）；
* 「申请」出去之后 NPC 会在 `ACCEPT_DELAY_SEC` 秒后自动同意（下次拉列表时结算），
  这样「申请 -> 变成战友」这条链路能自己走通；
* NPC 好友会送我物资（`B_SEND_MATERIALS_A`），收了给 `TAKE_REWARD`（行动力）；
* 换日（`quests.RESET_HOUR`）清空四个物资位 + `takeMaterialsCount`，并让几个
  NPC 重新送一轮 —— 不然第二天好友面板就没什么可做的了。

原来的跨账号好友（两个真实玩家互相加）**还没做**：`searchplayer` 只认 NPC 的
numberId，见 `docs/differences.md` §D。
"""

from __future__ import annotations

import os
import re
import threading
import time

from . import items, logx, quests

log = logx.get("friends")

# ---------------------------------------------------------------------------
# 客户端常量（config/friendconfig.jsc 反编译，逐字节核过）
# ---------------------------------------------------------------------------
DELETE = 1
A_APPLY_FOR_B = 2
B_APPLY_FOR_A = 4
BE_FRIEND = 8
A_SEND_MATERIALS_B = 16
B_SEND_MATERIALS_A = 32
A_TAKE_MATERIALS_B = 64
B_TAKE_MATERIALS_A = 128

APPLY_BITS = A_APPLY_FOR_B | B_APPLY_FOR_A
MATERIAL_BITS = (A_SEND_MATERIALS_B | B_SEND_MATERIALS_A
                 | A_TAKE_MATERIALS_B | B_TAKE_MATERIALS_A)

# 错误码。客户端 `FRIEND_ERROR_CODE = {201: 10000, 210: 1401, ...}` 把码映射到
# `table_dictionary` 的文案，回调里 `FRIEND_ERROR_CODE[code] || data` 弹 toast：
#   210 伦家已经是你的战友啦~      211 申请重复啦~        212 已经送过啦~
#   213 已经收过啦~                221 找不到申请哦~      222 你确定他在这片大陆上吗~
#   223 找不到这位指挥官哦~        231 你的萌友达到上限了~
#   232 收取物资次数达到上限了     233 他的小伙伴已经满员啦  234 你还没收到物资哦~
#   405 更新数据错误               406 查询数据错误
CODE_PARAM = 201
CODE_ALREADY_FRIEND = 210
CODE_APPLY_DUP = 211
CODE_ALREADY_SEND = 212
CODE_ALREADY_TAKE = 213
CODE_NO_APPLY = 221
CODE_NOT_FOUND = 222
CODE_NO_PLAYER = 223
CODE_LIST_FULL = 231
CODE_TAKE_LIMIT = 232
CODE_TARGET_FULL = 233
CODE_NOTHING_TO_TAKE = 234

# NPC 好友的 numberId 基址（表内下标 + 基址，ai001 -> 900001）
NPC_ID_BASE = 900001

# 建号时送的好友 / 待处理申请条数（都在 NPC 表里挑前几个）
SEED_FRIENDS = 5
SEED_APPLIES = 2
# 每天换日时让几个好友给我送物资
SEED_SENDS = 2
# 推荐列表长度（客户端 ADD_FRIEND 页签一次画完，太长没意义）
RECOMMEND_COUNT = 6
# 「申请」之后 NPC 多久自动同意（秒）。0 = 立即
ACCEPT_DELAY_SEC = int(os.environ.get("GS_FRIEND_ACCEPT_DELAY", "60"))

# 收物资给什么。客户端 `takeMaterials` 请求体里**只有 numberId**，没有任何道具参数，
# 说明给什么是服务端说了算 —— 这里给行动力（原版好友物资就是行动力一类的东西）。
TAKE_REWARD_KEY = os.environ.get("GS_FRIEND_MATERIAL_KEY", "100003")
TAKE_REWARD_COUNT = int(os.environ.get("GS_FRIEND_MATERIAL_COUNT", "2"))

# 表里查不到等级行时的兜底（表全 1~120 级都有，这里只是防御）
DEFAULT_FRIEND_LIMIT = 18
DEFAULT_TAKE_LIMIT = 4

# 供 `checkHeadId` 之外的直接显示用的头像。**必须是 `table_item` 里
# `type=60`（头像）且图标 png 在 `res/charimage/` 里真的存在的**：
# 客户端 `Medal.getHeadSpr("601003:2")` -> `new ItemIcon("601003")` ->
# `bag.getItemIcon()` 里 `cc.assert(fileUtils.isFileExist(url))`。
# 表里 23 条头像里只有 601005(`gifttulip`) 的图标不在 charimage 下，排除它。
_HEAD_ICON_EXCLUDE = {"gifttulip"}
_HEAD_TYPE_OTHER = "2"          # HEAD_TYPE = {SOLDIER: "1", OTHER: "2"}

# "2小时前" / "8分钟前" -> 秒
_AGO_RE = re.compile(r"(\d+)\s*(分钟|小时|天|秒)前")


# ---------------------------------------------------------------------------
# NPC 表
# ---------------------------------------------------------------------------
_npc_lock = threading.RLock()
_npc_rows: list | None = None       # [(numberId:int, key:str, row:dict, headId:str)]
_npc_by_id: dict | None = None


def _parse_ago(text) -> int:
    """`last_time`（"2小时前"）-> 秒。认不出就给个固定值（1 小时）。"""
    m = _AGO_RE.search(str(text or ""))
    if not m:
        return 3600
    n = int(m.group(1))
    unit = m.group(2)
    return n * {"秒": 1, "分钟": 60, "小时": 3600, "天": 86400}[unit]


def npc_rows() -> list:
    """NPC 好友候选表：[(numberId, npcKey, 行, headId)]，按 numberId 稳定排序。

    `headId` 是**写死的头像串** `"<itemKey>:2"`（`HEAD_TYPE.OTHER`），
    按表内下标轮流分配 —— 全网 NPC 一个脸太假，全用默认头像也一样假。
    """
    global _npc_rows, _npc_by_id
    with _npc_lock:
        if _npc_rows is not None:
            return _npc_rows
        heads = []
        for key, row in sorted(items.table("table_item").items()):
            if not isinstance(row, dict) or str(row.get("t")) != "60":
                continue
            icon = str(row.get("ic") or "")
            if not icon or icon in _HEAD_ICON_EXCLUDE:
                continue
            heads.append(str(key))
        table = items.table("table_friend_support_npc")
        rows = []
        for i, key in enumerate(sorted(table)):
            row = table[key] if isinstance(table[key], dict) else {}
            head = heads[i % len(heads)] + ":" + _HEAD_TYPE_OTHER if heads else ""
            rows.append((NPC_ID_BASE + i, key, row, head))
        _npc_rows = rows
        _npc_by_id = {number_id: (key, row, head) for number_id, key, row, head in rows}
        if not rows:
            log.warning("table_friend_support_npc 是空的（跑 script/extract_client_tables.py），"
                        "好友推荐列表会是空的")
        return _npc_rows


def npc_info(number_id, now: int | None = None) -> dict | None:
    """NPC 的「玩家信息」，就是客户端要的那四个字段。不在表里返回 None。"""
    npc_rows()
    found = (_npc_by_id or {}).get(int(number_id or 0))
    if not found:
        return None
    key, row, head = found
    ts = int(now if now is not None else time.time())
    return {
        "numberId": int(number_id),
        "name": str(row.get("player_name") or key),
        "lv": int(row.get("player_lv") or 1),
        # 头像和「最后登录」都用表里的原值。`last_time` 是显示用字符串
        # （"2小时前"），换算成秒再给客户端 —— `getLoginTimeDesc` 收的就是秒。
        "headId": head,
        "lastLoginTimeSec": ts - _parse_ago(row.get("last_time")),
    }


# ---------------------------------------------------------------------------
# 存档结构：player["friend"] = {map: {"<我>#<他>": {status, updateTimeSec}}, day, takeCount}
# ---------------------------------------------------------------------------
def _state(player: dict) -> dict:
    st = player.get("friend")
    if not isinstance(st, dict):
        st = {}
        player["friend"] = st
    if not isinstance(st.get("map"), dict):
        st["map"] = {}
    st["takeCount"] = int(st.get("takeCount") or 0)
    if not isinstance(st.get("day"), str):
        st["day"] = ""
    return st


def player_number_id(player: dict) -> int:
    """自己的数字 ID。

    客户端 `Player.ctor` 直接读 `data.numberId`，`Friend.getFriendNumberId` /
    `isBeApplyFor` / `isGetMaterials` 全拿它跟 `friendMap` 的 key 比 ——
    没有它的话这些判断一律走到「两边都不等」的兜底分支（按钮状态全错）。
    见 `store._migrate` 里分配 numberId 的那段。
    """
    try:
        n = int(player.get("numberId") or 0)
    except (TypeError, ValueError):
        n = 0
    return n or (100000 + int(player.get("id") or 1))


def key_of(me: int, other) -> str:
    """好友记录的 key：**自己永远在前**（见模块注释里的 A/B 约定）。"""
    return "%d#%d" % (int(me), int(other))


def _other_of(key: str, me: int) -> int | None:
    parts = str(key).split("#")
    if len(parts) != 2:
        return None
    try:
        a, b = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None
    return b if a == int(me) else (a if b == int(me) else None)


def _status_of(entry) -> int:
    if not isinstance(entry, dict):
        return 0
    try:
        return int(entry.get("status") or 0)
    except (TypeError, ValueError):
        return 0


def is_friend(status: int) -> bool:
    """`Friend.isFriend`：BE_FRIEND 且没被 DELETE 标记。"""
    return bool(status & BE_FRIEND) and not (status & DELETE)


def is_be_apply_for(status: int) -> bool:
    """对方申请了我（自己恒为 A -> 看 B_APPlY_FOR_A）。"""
    return bool(status & B_APPLY_FOR_A)


def entry_out(me: int, other: int, status: int, update_time: int,
              now: int | None = None) -> dict:
    """一条好友记录（客户端 `friendMap`）。`player` 必须给，见模块注释。"""
    info = npc_info(other, now) or {
        "numberId": int(other), "name": str(other), "lv": 1,
        "headId": "", "lastLoginTimeSec": int(now if now is not None else time.time()),
    }
    return {
        "numberId1": int(me),
        "numberId2": int(other),
        "status": int(status),
        "updateTimeSec": int(update_time or 0),
        "player": info,
    }


# ---------------------------------------------------------------------------
# 上限 / 计数（都用客户端那张等级表）
# ---------------------------------------------------------------------------
def _level_row(player: dict) -> dict:
    row = items.table("table_player_level_function").get(str(int(player.get("lv") or 1)))
    return row if isinstance(row, dict) else {}


def _limit(player: dict, field: str, default: int) -> int:
    try:
        value = int(_level_row(player).get(field) or 0)
    except (TypeError, ValueError):
        value = 0
    return value or default


def friend_limit(player: dict) -> int:
    return _limit(player, "friend_limit", DEFAULT_FRIEND_LIMIT)


def take_materials_limit(player: dict) -> int:
    return _limit(player, "take_materials_limit", DEFAULT_TAKE_LIMIT)


def friend_count(player: dict) -> int:
    st = _state(player)
    return sum(1 for e in st["map"].values() if is_friend(_status_of(e)))


def _npc_ids() -> list:
    return [number_id for number_id, _k, _r, _h in npc_rows()]


# ---------------------------------------------------------------------------
# 初始化 / 换日 / NPC 自动同意
# ---------------------------------------------------------------------------
def _seed(player: dict, st: dict, now: int) -> bool:
    """建号第一次：送几个 NPC 好友 + 几条待处理申请。

    申请那几条用 `B_APPlY_FOR_A`（对方申请我）—— 好友面板的「申请」页签要
    有东西可点，否则同意/拒绝这两个按钮永远验证不到。
    """
    ids = _npc_ids()
    if not ids:
        return False
    me = player_number_id(player)
    for i, npc_id in enumerate(ids[:SEED_FRIENDS]):
        status = BE_FRIEND
        if i < SEED_SENDS:
            status |= B_SEND_MATERIALS_A        # NPC 给我送了物资，可以收
        st["map"][key_of(me, npc_id)] = {
            "status": status,
            # 错开一点，客户端的「最后登录」排序看着才自然
            "updateTimeSec": now - (i * 300),
        }
    for j, npc_id in enumerate(ids[SEED_FRIENDS:SEED_FRIENDS + SEED_APPLIES]):
        st["map"][key_of(me, npc_id)] = {
            "status": B_APPLY_FOR_A,
            "updateTimeSec": now - 600 - j * 60,
        }
    log.info("玩家 %s 初始化好友：%d 个好友（其中 %d 个送了物资）+ %d 条申请",
             player.get("account"), SEED_FRIENDS, SEED_SENDS, SEED_APPLIES)
    return True


def _daily(player: dict, st: dict, now: int) -> bool:
    """换日：清物资位 + 收取次数归零 + 让几个好友重新送一轮。"""
    st["takeCount"] = 0
    for entry in st["map"].values():
        if isinstance(entry, dict):
            entry["status"] = _status_of(entry) & ~MATERIAL_BITS
    # 每天送物资的好友换一批（按 day 轮换，别老是同两个人）
    sent = 0
    ids = _npc_ids()
    shift = sum(ord(c) for c in st["day"]) % max(1, len(ids))
    for npc_id in ids[shift:] + ids[:shift]:
        if sent >= SEED_SENDS:
            break
        entry = st["map"].get(key_of(player_number_id(player), npc_id))
        if entry is not None and is_friend(_status_of(entry)):
            entry["status"] = _status_of(entry) | B_SEND_MATERIALS_A
            sent += 1
    log.info("玩家 %s 好友换日（%s）：收取次数归零，%d 个好友送了物资",
             player.get("account"), st["day"], sent)
    return True


def _auto_accept(player: dict, st: dict, now: int) -> bool:
    """我申请过的 NPC，到点自动同意（模拟对方上线点了同意）。

    名单满了就先挂着（不清申请位）—— 客户端还会显示「已申请」，
    等玩家删掉一个好友，下次拉列表时再补上。
    """
    me = player_number_id(player)
    changed = False
    room = friend_limit(player) - friend_count(player)
    for key, entry in list(st["map"].items()):
        status = _status_of(entry)
        if not (status & A_APPLY_FOR_B) or (status & BE_FRIEND):
            continue
        if now - int(entry.get("updateTimeSec") or 0) < ACCEPT_DELAY_SEC:
            continue
        if room <= 0:
            continue
        other = _other_of(key, me)
        if other is None or npc_info(other, now) is None:
            continue
        entry["status"] = (status & ~APPLY_BITS) | BE_FRIEND | B_SEND_MATERIALS_A
        entry["updateTimeSec"] = now
        room -= 1
        changed = True
        log.info("玩家 %s 的好友申请被 NPC %s 同意（自动）", player.get("account"), other)
    return changed


def ensure(player: dict, now: int | None = None) -> bool:
    """幂等初始化：换日 / 首次送好友 / NPC 自动同意。返回是否有改动。

    **不落盘**：由调用方（handler / agent.getlogindata）决定什么时候 save，
    和 `quests` 里除 `ensure_daily_reset` 之外的函数保持一致。
    """
    ts = int(now if now is not None else time.time())
    st = _state(player)
    changed = False
    today = quests.day_str()
    if st["day"] != today:
        st["day"] = today
        changed = _daily(player, st, ts) or changed
    if not st["map"]:
        changed = _seed(player, st, ts) or changed
    if _auto_accept(player, st, ts):
        changed = True
    return changed


# ---------------------------------------------------------------------------
# 给客户端的块
# ---------------------------------------------------------------------------
def recommendation_list(player: dict, now: int | None = None) -> list:
    """推荐列表：不是好友、也没有待处理申请/已申请的 NPC。

    注销过的好友（只剩 DELETE 位）会重新回到推荐里 —— 否则删掉就再也加不回来。
    条目是**平铺**的（见模块注释）。
    """
    ts = int(now if now is not None else time.time())
    st = _state(player)
    me = player_number_id(player)
    busy = set()
    for key, entry in st["map"].items():
        status = _status_of(entry)
        if status & (BE_FRIEND | APPLY_BITS):
            busy.add(key)
    out = []
    for npc_id in _npc_ids():
        if key_of(me, npc_id) in busy:
            continue
        info = npc_info(npc_id, ts)
        if info:
            out.append(info)
        if len(out) >= RECOMMEND_COUNT:
            break
    return out


def friend_map_list(player: dict, now: int | None = None) -> dict:
    """`friendMapList`：key -> 记录。DELETE 标记的行不发（客户端只是拿来过滤）。"""
    ts = int(now if now is not None else time.time())
    st = _state(player)
    me = player_number_id(player)
    out = {}
    for key, entry in st["map"].items():
        status = _status_of(entry)
        if not status or (status & DELETE) == status:
            continue
        other = _other_of(key, me)
        if other is None:
            log.warning("好友记录 key 不合法，跳过：%r", key)
            continue
        out[key] = entry_out(me, other, status, int(entry.get("updateTimeSec") or 0), ts)
    return out


def block(player: dict, now: int | None = None) -> dict:
    """`data.friend`（登录块 / getfriendlist 回包）。

    形状 = `Friend.update(data)` 读的三个键 + 两个红点提示键：
    `Friend.updateByServer(data)` 只认 `recevieMaterialsTip` / `applicationTip`
    （`util/server.jsc` 的 responseConfig 里有 `friend` 这一项，响应里带 `friend`
    就会派发过去）。有可收的物资 / 有待处理申请时才给，红点才亮。
    """
    ensure(player, now)
    ts = int(now if now is not None else time.time())
    st = _state(player)
    me = player_number_id(player)
    can_take = False
    pending_apply = False
    for entry in st["map"].values():
        status = _status_of(entry)
        if (status & B_SEND_MATERIALS_A) and not (status & A_TAKE_MATERIALS_B):
            can_take = True
        if status & B_APPLY_FOR_A and not (status & DELETE):
            pending_apply = True
    out = {
        "friendMapList": friend_map_list(player, ts),
        "recommendationList": recommendation_list(player, ts),
        "takeMaterialsCount": int(st["takeCount"] or 0),
    }
    if can_take:
        out["recevieMaterialsTip"] = 1
    if pending_apply:
        out["applicationTip"] = 1
    return out


# ---------------------------------------------------------------------------
# 操作：每个都返回 {"code", "msg", "data"}（handler 直接原样回给客户端）
# ---------------------------------------------------------------------------
def _ok(data: dict) -> dict:
    from .gameproto import CODE_OK

    return {"code": CODE_OK, "msg": "", "data": data}


def _fail(code: int, why: str) -> dict:
    log.info("好友操作失败 code=%s（%s）", code, why)
    return {"code": int(code), "msg": "", "data": {}}


def _entry(player: dict, number_id):
    st = _state(player)
    me = player_number_id(player)
    return st, me, st["map"].get(key_of(me, number_id))


def _number_id(msg) -> int:
    try:
        return int((msg or {}).get("numberId") or 0)
    except (TypeError, ValueError):
        return 0


def search(player: dict, msg) -> dict:
    """`friend.searchplayer` —— 按数字 ID 找人，回 `data.playerInfo`。"""
    number_id = _number_id(msg)
    if not number_id:
        return _fail(CODE_PARAM, "没有 numberId")
    info = npc_info(number_id)
    if not info or number_id == player_number_id(player):
        return _fail(CODE_NOT_FOUND, "numberId=%s 不是可加的玩家" % number_id)
    return _ok({"playerInfo": info})


def apply_for(player: dict, msg, now: int | None = None) -> dict:
    """`friend.applyfor` —— 申请加好友。"""
    ts = int(now if now is not None else time.time())
    ensure(player, ts)
    number_id = _number_id(msg)
    st, me, entry = _entry(player, number_id)
    if not number_id or number_id == me:
        return _fail(CODE_PARAM, "numberId=%s 不合法" % number_id)
    if npc_info(number_id, ts) is None:
        return _fail(CODE_NO_PLAYER, "numberId=%s 不在 NPC 表里" % number_id)
    status = _status_of(entry)
    if is_friend(status):
        return _fail(CODE_ALREADY_FRIEND, "已经是好友")
    if status & A_APPLY_FOR_B:
        return _fail(CODE_APPLY_DUP, "已经申请过")
    if friend_count(player) >= friend_limit(player):
        return _fail(CODE_LIST_FULL, "好友满了（%d）" % friend_limit(player))
    if status & B_APPLY_FOR_A:
        # 对方本来就在申请我 -> 互相申请，直接成为战友（客户端也会把这条
        # 从「申请」页签里挪到好友页签）
        status = (status & ~APPLY_BITS) | BE_FRIEND | B_SEND_MATERIALS_A
    else:
        status |= A_APPLY_FOR_B
    st["map"][key_of(me, number_id)] = {"status": status, "updateTimeSec": ts}
    log.info("玩家 %s 申请加 %s 为好友（status=%d）", player.get("account"), number_id, status)
    return _ok({"friendMap": entry_out(me, number_id, status, ts, ts)})


def agree_application(player: dict, msg, now: int | None = None) -> dict:
    """`friend.agreeapplication` —— 同意对方的申请。"""
    ts = int(now if now is not None else time.time())
    ensure(player, ts)
    number_id = _number_id(msg)
    st, me, entry = _entry(player, number_id)
    status = _status_of(entry)
    if not number_id or not is_be_apply_for(status):
        return _fail(CODE_NO_APPLY, "没有待处理的申请")
    if is_friend(status):
        return _fail(CODE_ALREADY_FRIEND, "已经是好友")
    if friend_count(player) >= friend_limit(player):
        return _fail(CODE_LIST_FULL, "好友满了（%d）" % friend_limit(player))
    status = (status & ~APPLY_BITS) | BE_FRIEND | B_SEND_MATERIALS_A
    st["map"][key_of(me, number_id)] = {"status": status, "updateTimeSec": ts}
    log.info("玩家 %s 同意 %s 的申请（status=%d）", player.get("account"), number_id, status)
    return _ok({"friendMap": entry_out(me, number_id, status, ts, ts)})


def refuse_application(player: dict, msg, now: int | None = None) -> dict:
    """`friend.refuseapplication` —— 拒绝申请。

    ⚠️ 拒绝后必须把申请位**清掉**再标 DELETE：客户端筛「申请列表」看的是
    `isBeApplyFor`（只看申请位，**不看 DELETE**），只标 DELETE 的话这条会一直
    赖在申请页签里。
    """
    ts = int(now if now is not None else time.time())
    ensure(player, ts)
    number_id = _number_id(msg)
    st, me, entry = _entry(player, number_id)
    status = _status_of(entry)
    if not number_id or not is_be_apply_for(status):
        return _fail(CODE_NO_APPLY, "没有待处理的申请")
    if is_friend(status):
        return _fail(CODE_ALREADY_FRIEND, "已经是好友，不用拒绝申请")
    status = (status & ~(APPLY_BITS | MATERIAL_BITS)) | DELETE
    st["map"][key_of(me, number_id)] = {"status": status, "updateTimeSec": ts}
    log.info("玩家 %s 拒绝 %s 的申请", player.get("account"), number_id)
    return _ok({"friendMap": entry_out(me, number_id, status, ts, ts)})


def delete_friend(player: dict, msg, now: int | None = None) -> dict:
    """`friend.deletefriend` —— 删好友（删了会重新出现在推荐列表里）。"""
    ts = int(now if now is not None else time.time())
    ensure(player, ts)
    number_id = _number_id(msg)
    st, me, entry = _entry(player, number_id)
    status = _status_of(entry)
    if not number_id or not is_friend(status):
        return _fail(CODE_NO_PLAYER, "不是好友，删不了")
    status = (status & ~(BE_FRIEND | MATERIAL_BITS)) | DELETE
    st["map"][key_of(me, number_id)] = {"status": status, "updateTimeSec": ts}
    log.info("玩家 %s 删除了好友 %s", player.get("account"), number_id)
    return _ok({"friendMap": entry_out(me, number_id, status, ts, ts)})


def send_materials(player: dict, msg, now: int | None = None) -> dict:
    """`friend.sendmaterials` —— 给好友送物资（每天每人一次）。

    请求体里只有 `numberId`，没有道具参数：**送的东西由服务端说了算**，
    这里不扣任何道具（私服取舍，见 docs/differences.md §D），
    只置位 + 记日常任务 18201 的计数。
    """
    ts = int(now if now is not None else time.time())
    ensure(player, ts)
    number_id = _number_id(msg)
    st, me, entry = _entry(player, number_id)
    status = _status_of(entry)
    if not number_id or not is_friend(status):
        return _fail(CODE_NO_PLAYER, "不是好友，送不了")
    if status & A_SEND_MATERIALS_B:
        return _fail(CODE_ALREADY_SEND, "今天已经送过了")
    status |= A_SEND_MATERIALS_B
    st["map"][key_of(me, number_id)] = {"status": status, "updateTimeSec": ts}
    quests.on_friend_send(player)
    log.info("玩家 %s 给好友 %s 送了物资", player.get("account"), number_id)
    return _ok({"friendMap": entry_out(me, number_id, status, ts, ts)})


def take_materials(player: dict, msg, now: int | None = None) -> dict:
    """`friend.takematerials` —— 收好友送来的物资。

    `data.reward` 的形状是 **`{itemKey: count}`**（客户端 `showTakeMaterialsPanel`
    拿 `for (key in reward)` 迭代，包成 `{key, count, type: REWARD_TYPE.ITEM}`）。
    """
    ts = int(now if now is not None else time.time())
    ensure(player, ts)
    number_id = _number_id(msg)
    st, me, entry = _entry(player, number_id)
    status = _status_of(entry)
    if not number_id or not is_friend(status):
        return _fail(CODE_NO_PLAYER, "不是好友，收不了")
    if not (status & B_SEND_MATERIALS_A):
        return _fail(CODE_NOTHING_TO_TAKE, "对方没送物资")
    if status & A_TAKE_MATERIALS_B:
        return _fail(CODE_ALREADY_TAKE, "这份已经收过了")
    limit = take_materials_limit(player)
    if int(st["takeCount"] or 0) >= limit:
        return _fail(CODE_TAKE_LIMIT, "今天收了 %d 次（上限 %d）" % (st["takeCount"], limit))
    # ⚠️ 收完**不清对方的「已送」位**，只置自己的「已收」位。
    # 客户端显示状态用的就是这两个位：`isGetMaterials`（可收）+ `isTakeMaterials`
    # （已收）同时为真时 `isCanTakeMaterials` = `isGetMaterials && !isTakeMaterials`
    # 才是 false（按钮变灰、排序靠后），`_getReceiveSortIdx` 也是先看已收再看可收 ——
    # 清了「已送」位的话「已收」这个状态在界面上根本无从体现，而且再点一次会回
    # 234「还没收到物资」而不是 213「已经收过啦」。
    status |= A_TAKE_MATERIALS_B
    st["map"][key_of(me, number_id)] = {"status": status, "updateTimeSec": ts}
    st["takeCount"] = int(st["takeCount"] or 0) + 1
    reward = {str(TAKE_REWARD_KEY): int(TAKE_REWARD_COUNT)}
    items.settle(player, [("2", str(TAKE_REWARD_KEY), int(TAKE_REWARD_COUNT))])
    log.info("玩家 %s 收了 %s 的物资 -> %s（今天第 %d/%d 次）",
             player.get("account"), number_id, reward, st["takeCount"], limit)
    return _ok({
        "friendMap": entry_out(me, number_id, status, ts, ts),
        "takeMaterialsCount": int(st["takeCount"]),
        "reward": reward,
    })


def friend_list(player: dict, now: int | None = None) -> dict:
    """`friend.getfriendlist` —— 回包和登录块同一个形状（客户端 `update(data)`）。"""
    return _ok(block(player, now))


def recommend_list_route(player: dict, now: int | None = None) -> dict:
    """`friend.getrecommendationlist` —— 只回推荐列表。"""
    ts = int(now if now is not None else time.time())
    ensure(player, ts)
    return _ok({"recommendationList": recommendation_list(player, ts)})
