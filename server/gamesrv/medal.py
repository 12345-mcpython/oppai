"""勋章 / 头像 / 衣柜（medal.*）。

## 客户端契约（`assets/src/data/medal.jsc` 逐函数反汇编，不是猜的）

    route                          请求                        成功 data
    medal.getfriendmedalinfo       {friendId}                  整个「勋章信息」对象
    medal.changeclothes            {clothesId}                 新的 clothesId（一个值）
    medal.changebg                 {bgId}                      新的 bgId
    medal.changehead               {headId, headType}          新的 headId（"<key>:<type>"）
    medal.wearmedal                {medalId, wearIdx}          整个 medalWear（map）
    medal.setclothesorbgold        {id}                        任意真值
    medal.setheadold               {headId, headType}          任意真值
    medal.setmedalold              {medalId}                   任意真值
    medal.clearallheadnew          {}                          任意真值

回调都是 `player.updateXxx(res.data)` / `updateMedalWear(res.data)` ——
**注意 change* 三条回的是「值」不是对象**，wearmedal 回的是**整张** medalWear。

## 三个数据都在哪

1. **`data.medal`**（登录块）= `{medals: {勋章id: 行}, newMedalIds: [...]}`：
   `Medal._initData` 把 `medals` 原样存进 `_medals`，`Medal._initMedalsGroup` 拿
   **`table_medal` 整张表**按 `group` 分组。行形状由 `createTempNewMedal` 给出：

       {id, progress, progressInfo: {}, completeTime?}

   ⚠️ `isMedalCompleteByGroup` 是 `_medals[id].completeTime` —— **每条勋章都得有行**，
   少一条就是 `undefined.completeTime` TypeError（和任务窗口那个坑同源）。
   完成与否看 `completeTime` 真值，`progress` 只是显示进度。

2. **`player.medalWear`** = `{勋章id: 佩戴位下标}`：`wearMedal` 先读
   `player.medalWear[medalId]` 跟 `_curWearIdx` 比（同一个位子再点一次就 return），
   `isWearMedal` 是「**同一 group 只戴一个**」的语义（组内 medals < 2 时只看自己）。

3. **衣柜/头像/勋章本体都是背包道具**（`ITEM_TYPE`：CLOTHES 40 / BG_IMG 50 /
   HEAD 60 / MEDAL 70）：`hasNewClothes`/`hasNewBg`/`hasNewByItemType`/`isNewMedal`
   全部走 `bag.getItemsByType(type)` + `item.isNew`。勋章和道具的对应关系是
   `table_medal[key].icon_id`（700000 起，`table_item` 里 `t == 70`）。
   `isNew` 是**客户端 Item 上的标记**，由服务端喂：`Item.ctor` / `updateByObj`
   见 `typeof arg === "object"` 就走 `updateByObj`，里面 `this._isNew = item.isNew`
   —— 所以登录块里把该道具写成 `{"count": n, "isNew": true}` 就能点亮 NEW。

## 进度怎么算（按 `table_medal.condition_kind`）

`condition_id` 指向的 `table_medal_condition` 里只有 `condition_ids`（那是原版
**服务端**的 condition 对象 id，客户端表里没有对应的表），所以语义只能从
`table_medal.desc`（人话）+ `times`（目标值）反推。能算的：

    1002 战斗失败 N 次        -> questStats.losses
    2001 演习 N 次            -> questStats.arenaFights
    3001 派遣 N 次            -> questStats.detects
    4001 N 个军士达到 X 级     -> 军士名单（阈值从 desc 里那串数字读）
    5001 N 个角色好感到 X 级   -> favors
    5002 送 N 个礼物          -> questStats.gifts
    5003 抚摸 N 次            -> questStats.touches
    6001 金条/碎片抽卡 N 次    -> player.gacha 的 totalTimes（按池子分）
    7002 获得指定浴衣         -> 背包里 type=40 且 desc 点名的那些
    8001 完成 N 次日常任务     -> questStats.dailyQuests（本模块新加的计数器）

算不了的（进度恒 0，不假装完成，记在 docs/differences.md §D）：
**1001 通关指定关卡**（要点名关卡 → 需要 `table_level` 的关卡名表，还没抽）、
**1003 我方军士被推倒 N 次**（战斗内部统计）、
**4002 获得指定军士**（要点名军士卡，客户端没有「卡 key ↔ 名字」表）。

`condition_kind` 是 `None` 的 25 条是**运营活动名次 / 预约人数**类勋章
（「在[超高校级劲敌？]活动中获得积分第一名」…）—— 活动早就没了，原版也拿不到，
私服直接按完成下发（见 differences §B）。
"""

from __future__ import annotations

import json
import re
import time

from . import items, logx, store

log = logx.get("medal")

ITEM_CLOTHES = "40"
ITEM_BG = "50"
ITEM_HEAD = "60"
ITEM_MEDAL = "70"

HEAD_TYPE_SOLDIER = "1"
HEAD_TYPE_OTHER = "2"

# 佩戴位下标的上限（客户端 `MedalLayer` 的勋章格子数；超范围的请求直接拒）
MAX_WEAR_IDX = 9

_LV_RE = re.compile(r"(\d+)\s*级")


# ---------------------------------------------------------------------------
# 表 / 存档
# ---------------------------------------------------------------------------
def medal_table() -> dict:
    return items.table("table_medal")


def state(player: dict) -> dict:
    """`player["medal"]` —— 服务端自己的勋章存档。

    * `complete`  勋章id -> 首次达成时间（秒）。**只有这个要落盘**：
      进度是现算的（和任务那套一样，`completeTime` 必须稳定，不然客户端
      「已完成」会闪）。
    * `newQueue`  还没播过「获得新勋章」动画的 id（发一次就清）。
    * `newItems`  当前带 NEW 标记的道具 key（走登录块的 `item` 块下发）。
    """
    st = player.get("medal")
    if not isinstance(st, dict):
        st = {}
        player["medal"] = st
    if not isinstance(st.get("complete"), dict):
        st["complete"] = {}
    if not isinstance(st.get("newQueue"), list):
        st["newQueue"] = []
    if not isinstance(st.get("newItems"), list):
        st["newItems"] = []
    return st


def new_item_keys(player: dict) -> set:
    return {str(k) for k in state(player)["newItems"]}


def _item_rows(item_type: str) -> dict:
    return {k: r for k, r in items.table("table_item").items()
            if isinstance(r, dict) and str(r.get("t")) == item_type}


def _default_head_id() -> str:
    key = str(items.table("table_constant").get("default_head_id") or "601001")
    return key + ":" + HEAD_TYPE_OTHER


def _default_clothes_id() -> str:
    lead = str(items.table("table_constant").get("lead_char_key") or "lead")
    row = items.table("table_char_clothes").get(lead) or {}
    return str(row.get("item_key") or "401001")


def _default_bg_id() -> str:
    return str(items.table("table_constant").get("default_medal_bg") or "501001")


def _is_item_of(key, item_type: str) -> bool:
    row = items.table("table_item").get(str(key))
    return isinstance(row, dict) and str(row.get("t")) == item_type


def head_id_of(player: dict) -> str:
    """`player.headId` 是 `"<itemKey>:<HEAD_TYPE>"`（`Medal.getHeadSpr` 自己 split）。

    ⚠️ 老存档里是 `1`（数字，当初瞎填的）—— `getHeadSpr` 会拿它 `.split(":")`
    直接抛 TypeError。所以这里**必须拿 table_item 校验**（"1" 也 isdigit，
    光判数字会把 `"1:2"` 这种废值放过去）。
    """
    base, _, kind = str(player.get("headId") or "").partition(":")
    if _is_item_of(base, ITEM_HEAD) and kind in (HEAD_TYPE_SOLDIER, HEAD_TYPE_OTHER):
        return base + ":" + kind
    return _default_head_id()


def clothes_id_of(player: dict) -> str:
    val = str(player.get("medalClothesId") or "")
    return val if _is_item_of(val, ITEM_CLOTHES) else _default_clothes_id()


def bg_id_of(player: dict) -> str:
    val = str(player.get("medalBgId") or "")
    return val if _is_item_of(val, ITEM_BG) else _default_bg_id()


def medal_wear(player: dict) -> dict:
    """佩戴表（服务端内部一律用 dict）。

    ⚠️ **上行的形状是 JSON 字符串**：客户端 `Player._getMedalWear` 是
    `JSON.parse(this._medalWear)`、`updateMedalWear(v)` 是 `JSON.stringify(v)`
    —— 转换点只有一处：`agent._player_block()`（发字符串）、
    `medal.wear_medal()` 的回包（发对象，客户端自己 stringify）。
    存档里存 dict，但**这里也认字符串**：devtools 的存档编辑器 / 老存档
    可能把它写成串，别因为格式差异整个面板崩掉。
    """
    wear = player.get("medalWear")
    if isinstance(wear, str):
        try:
            wear = json.loads(wear)
        except ValueError:
            log.warning("存档里的 medalWear 不是合法 JSON：%r", wear[:60])
            wear = {}
        player["medalWear"] = wear
    if not isinstance(wear, dict):
        wear = {}
        player["medalWear"] = wear
    return wear


# ---------------------------------------------------------------------------
# 进度
# ---------------------------------------------------------------------------
def _stats(player: dict) -> dict:
    st = player.get("questStats")
    return st if isinstance(st, dict) else {}


def _soldier_levels(player: dict) -> list:
    out = []
    for row in store.ensure_soldiers(player):
        if isinstance(row, dict):
            try:
                out.append(int(row.get("lv") or 0))
            except (TypeError, ValueError):
                continue
    return out


def _favor_levels(player: dict) -> list:
    out = []
    for row in store.player_favors(player).values():
        if isinstance(row, dict):
            try:
                out.append(int(row.get("lv") or 0))
            except (TypeError, ValueError):
                continue
    return out


def _gacha_total(player: dict) -> dict:
    """`{masterKey: 累计抽卡次数}`（`player["gacha"]` 里一行一个「池子+次数档」）。"""
    out: dict = {}
    rows = player.get("gacha")
    if not isinstance(rows, dict):
        return out
    for row in rows.values():
        if not isinstance(row, dict):
            continue
        key = str(row.get("masterKey") or "")
        try:
            out[key] = out.get(key, 0) + int(row.get("totalTimes") or 0)
        except (TypeError, ValueError):
            continue
    return out


def _threshold(row: dict, default: int) -> int:
    """从 desc 里抠阈值（"使1个任意军士达到70级" -> 70 / "好感度达到15级" -> 15）。"""
    m = _LV_RE.search(str(row.get("desc") or ""))
    return int(m.group(1)) if m else default


def _yukata_keys(row: dict) -> list:
    """7002 类勋章 desc 点名的衣服 -> 道具 key（按 `table_item.name` 子串匹配）。"""
    desc = str(row.get("desc") or "")
    want = [x.strip() for x in re.split(r"[、,，\s]+", desc) if x.strip()]
    rows = _item_rows(ITEM_CLOTHES)
    out = []
    for name in want:
        key = name.rstrip("。.")
        for item_key, item_row in rows.items():
            if key and key in str(item_row.get("name") or ""):
                out.append(item_key)
                break
    return out


def progress_of(player: dict, key: str) -> int:
    """某条勋章当前进度（`progress` 字段）。拿不到的恒 0，不假装达成。"""
    row = medal_table().get(str(key)) or {}
    kind = str(row.get("condition_kind") or "")
    st = _stats(player)

    if kind == "1002":          # 战斗失败 N 次
        return int(st.get("losses") or 0)
    if kind == "2001":          # 演习 N 次
        return int(st.get("arenaFights") or 0)
    if kind == "3001":          # 任务派遣 N 次
        return int(st.get("detects") or 0)
    if kind == "4001":          # N 个军士达到 X 级
        lv = _threshold(row, 70)
        return sum(1 for x in _soldier_levels(player) if x >= lv)
    if kind == "5001":          # N 个角色好感度达到 X 级
        lv = _threshold(row, 15)
        return sum(1 for x in _favor_levels(player) if x >= lv)
    if kind == "5002":          # 累计赠送 N 个礼物
        return int(st.get("gifts") or 0)
    if kind == "5003":          # 累计抚摸 N 次
        return int(st.get("touches") or 0)
    if kind == "6001":          # 累计抽卡 N 次（按 desc 里的池子名分）
        desc = str(row.get("desc") or "")
        totals = _gacha_total(player)
        if "碎片" in desc:
            return int(totals.get("1003") or 0)
        if "金条" in desc:
            return int(totals.get("1002") or 0)
        return sum(totals.values())
    if kind == "7002":          # 获得 desc 点名的那些衣服
        bag = items.items_of(player)
        return sum(1 for k in _yukata_keys(row) if int(bag.get(str(k)) or 0) > 0)
    if kind == "8001":          # 完成 N 次日常任务
        return int(st.get("dailyQuests") or 0)
    # 1001 通关指定关卡 / 1003 我方军士被推倒 / 4002 获得指定军士 —— 见模块注释
    return 0


def target_of(key: str) -> int:
    row = medal_table().get(str(key)) or {}
    try:
        return int(row.get("times") or 0)
    except (TypeError, ValueError):
        return 0


def _activity_medal(row: dict) -> bool:
    """`condition_kind` 空的 25 条 = 运营活动名次 / 预约人数（原版靠活动，已绝版）。"""
    return not str(row.get("condition_kind") or "")


def is_complete(player: dict, key: str) -> bool:
    row = medal_table().get(str(key)) or {}
    if _activity_medal(row):
        return True
    target = target_of(key)
    return target > 0 and progress_of(player, key) >= target


# ---------------------------------------------------------------------------
# 初始化：发衣柜 / 发勋章本体 / 补默认值 / 记达成时间
# ---------------------------------------------------------------------------
def ensure(player: dict, now: int | None = None) -> bool:
    """幂等初始化。**不落盘**（由调用方 save，和 quests 保持一致）。

    做四件事：
    1. 头像/衣柜/勋章**道具**补齐（私服一次性发满，和 `favor.ensure_look_stock`
       一个路子）：头像 23 件、勋章按「已达成」发本体；
    2. `player.headId` / `medalClothesId` / `medalBgId` 补成合法值；
    3. 新达成的勋章记 `completeTime`（首次达成时间，稳定不闪）；
    4. 新发的道具加点 NEW 标记（客户端 `item.isNew`，点过就由 4 条 route 清掉）。
    """
    ts = int(now if now is not None else time.time())
    st = state(player)
    bag = items.items_of(player)
    changed = False

    # 1) 道具：头像全发；勋章本体只发「已达成」的
    want = list(_item_rows(ITEM_HEAD))
    for key in medal_table():
        icon = str((medal_table()[key] or {}).get("icon_id") or "")
        if icon and is_complete(player, key):
            want.append(icon)
    for key in want:
        if int(bag.get(str(key)) or 0) <= 0:
            bag[str(key)] = 1
            st["newItems"].append(str(key))
            changed = True
            log.info("玩家 %s 获得 %s（%s）", player.get("account"), key,
                     (items.table("table_item").get(str(key)) or {}).get("name"))
    if changed:
        st["newItems"] = sorted({str(k) for k in st["newItems"]})

    # 2) 三个默认值
    if str(player.get("headId") or "") != head_id_of(player):
        player["headId"] = head_id_of(player)
        changed = True
    if str(player.get("medalClothesId") or "") != clothes_id_of(player):
        player["medalClothesId"] = clothes_id_of(player)
        changed = True
    if str(player.get("medalBgId") or "") != bg_id_of(player):
        player["medalBgId"] = bg_id_of(player)
        changed = True
    medal_wear(player)

    # 3) 记达成时间（首次达成时间要稳定：客户端「已完成」看的就是它）
    for key, row in medal_table().items():
        if not isinstance(row, dict):
            continue
        if is_complete(player, key) and not st["complete"].get(str(key)):
            st["complete"][str(key)] = ts
            changed = True
            # ⚠️ **首次批量补的时候不进「新勋章」队列**：一次几十条会把
            #    客户端的获得动画连播几十遍（`showGetNewMedalEffect` 是队列）。
            log.info("玩家 %s 勋章达成 %s（%s）", player.get("account"), key,
                     row.get("name"))
    return changed


def take_new_medals(player: dict) -> list:
    """还没播过动画的新勋章 id（发一次就清）。"""
    st = state(player)
    out = [k for k in st["newQueue"] if k in medal_table()]
    st["newQueue"] = []
    return out


# ---------------------------------------------------------------------------
# 给客户端的块
# ---------------------------------------------------------------------------
def medal_rows(player: dict, now: int | None = None) -> dict:
    """`data.medal.medals` —— **`table_medal` 每一条都要有行**（见模块注释）。"""
    st = state(player)
    out = {}
    for key, row in medal_table().items():
        if not isinstance(row, dict):
            continue
        k = str(key)
        out[k] = {
            "id": k,
            "progress": progress_of(player, k),
            "progressInfo": {},
            "completeTime": int(st["complete"].get(k) or 0),
        }
    return out


def complete_count(player: dict) -> int:
    return sum(1 for key in medal_table() if is_complete(player, key))


def block(player: dict, now: int | None = None) -> dict:
    """`data.medal`（登录块；`Medal._initData` 读 `medals` / `newMedalIds`）。"""
    ensure(player, now)
    return {
        "medals": medal_rows(player, now),
        "newMedalIds": take_new_medals(player),
    }


def item_block(player: dict, known: dict | None = None) -> dict:
    """登录块的 `item` 块：带 NEW 标记的道具写成对象（`{"count": n, "isNew": true}`）。

    `Item.ctor` / `Item.updateByObj` 都认这个形状（`typeof arg === "object"`），
    普通道具保持原来的「key -> 数字」，别动（`Bag` 那边两条路都走得到）。
    """
    bag = known if known is not None else items.items_of(player)
    marks = new_item_keys(player)
    out = {}
    for key, count in bag.items():
        k = str(key)
        if k in marks:
            out[k] = {"count": int(count or 0), "isNew": True}
        else:
            out[k] = int(count or 0)
    return out


# ---------------------------------------------------------------------------
# 操作（每个都回 {"code","msg","data"}）
# ---------------------------------------------------------------------------
def _ok(data) -> dict:
    from .gameproto import CODE_OK

    return {"code": CODE_OK, "msg": "", "data": data}


# 失败码：和 quests 一样用 -1（客户端只看 200/非 200；medal 这几条没有
# FRIEND_ERROR_CODE 那样的码表，非 200 时回调拿 failCb 而已）
CODE_FAIL = -1


def _fail(why: str) -> dict:
    log.info("勋章操作失败：%s", why)
    return {"code": CODE_FAIL, "msg": why, "data": {}}


def _owned(player: dict, key) -> bool:
    return int(items.items_of(player).get(str(key)) or 0) > 0


def _num(msg, field: str, default=None):
    val = (msg or {}).get(field)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def change_head(player: dict, msg: dict) -> dict:
    """`medal.changehead {headId, headType}` -> 新的 `"<key>:<type>"`。"""
    ensure(player)
    head_id = str((msg or {}).get("headId") or "")
    head_type = str((msg or {}).get("headType") or "")
    if head_type not in (HEAD_TYPE_SOLDIER, HEAD_TYPE_OTHER):
        return _fail("headType 不合法：%r" % head_type)
    if not head_id:
        return _fail("没有 headId")
    # SOLDIER 类头像的 key 是军士（不是道具），只校验 OTHER 这类道具头像
    if head_type == HEAD_TYPE_OTHER and not _owned(player, head_id):
        return _fail("没有这个头像：%s" % head_id)
    player["headId"] = head_id + ":" + head_type
    log.info("玩家 %s 换头像 -> %s", player.get("account"), player["headId"])
    return _ok(player["headId"])


def change_clothes(player: dict, msg: dict) -> dict:
    """`medal.changeclothes {clothesId}` -> 新的 clothesId。"""
    ensure(player)
    clothes_id = str((msg or {}).get("clothesId") or "")
    if not clothes_id or not _owned(player, clothes_id):
        return _fail("没有这件衣服：%s" % clothes_id)
    player["medalClothesId"] = clothes_id
    log.info("玩家 %s 换衣服 -> %s", player.get("account"), clothes_id)
    return _ok(clothes_id)


def change_bg(player: dict, msg: dict) -> dict:
    """`medal.changebg {bgId}` -> 新的 bgId。"""
    ensure(player)
    bg_id = str((msg or {}).get("bgId") or "")
    if not bg_id or not _owned(player, bg_id):
        return _fail("没有这张背景：%s" % bg_id)
    player["medalBgId"] = bg_id
    log.info("玩家 %s 换背景 -> %s", player.get("account"), bg_id)
    return _ok(bg_id)


def wear_medal(player: dict, msg: dict) -> dict:
    """`medal.wearmedal {medalId, wearIdx}` -> **整张** medalWear。

    ⚠️ 客户端 `isWearMedal` 是「**同一 group 只能戴一个**」的语义，所以戴上新的
    要把同组别的先摘掉（不然界面上会出现"两组都戴着"的鬼状态）。
    """
    ensure(player)
    medal_id = str((msg or {}).get("medalId") or "")
    wear_idx = _num(msg, "wearIdx", 0)
    row = medal_table().get(medal_id)
    if not isinstance(row, dict):
        return _fail("没有这个勋章：%s" % medal_id)
    if wear_idx is None or wear_idx < 0 or wear_idx > MAX_WEAR_IDX:
        return _fail("佩戴位不合法：%r" % wear_idx)
    if not _owned(player, row.get("icon_id")):
        return _fail("还没拿到这个勋章：%s" % medal_id)
    wear = medal_wear(player)
    group = row.get("group")
    for other in list(wear):
        other_row = medal_table().get(str(other)) or {}
        if str(other_row.get("group")) == str(group):
            wear.pop(other, None)
    wear[medal_id] = int(wear_idx)
    log.info("玩家 %s 佩戴勋章 %s 到 %d 位（同组先摘）", player.get("account"), medal_id, wear_idx)
    return _ok(wear)


# --- 4 条「清 NEW 标记」 -----------------------------------------------------
def _clear_new(player: dict, keys) -> bool:
    st = state(player)
    marks = {str(k) for k in st["newItems"]}
    before = len(marks)
    marks -= {str(k) for k in keys}
    if len(marks) != before:
        st["newItems"] = sorted(marks)
        return True
    return False


def clear_all_head_new(player: dict) -> dict:
    """`medal.clearallheadnew` —— 清掉所有头像/军士头像的 NEW 标记。"""
    ensure(player)
    keys = list(_item_rows(ITEM_HEAD))
    _clear_new(player, keys)
    # 军士头像的 NEW 在 `character` 那边（客户端自己清），服务端只需要回真值
    log.info("玩家 %s 清空头像 NEW 标记（%d 件道具头像）", player.get("account"), len(keys))
    return _ok({"ok": 1})


def set_clothes_or_bg_old(player: dict, msg: dict) -> dict:
    """`medal.setclothesorbgold {id}` —— 清掉某件衣服/背景的 NEW。"""
    ensure(player)
    item_id = str((msg or {}).get("id") or "")
    if not item_id:
        return _fail("没有 id")
    _clear_new(player, [item_id])
    log.info("玩家 %s 清 %s 的 NEW 标记", player.get("account"), item_id)
    return _ok({"ok": 1})


def set_head_old(player: dict, msg: dict) -> dict:
    """`medal.setheadold {headId, headType}` —— 清掉某个头像的 NEW。"""
    ensure(player)
    head_id = str((msg or {}).get("headId") or "")
    if not head_id:
        return _fail("没有 headId")
    _clear_new(player, [head_id])
    log.info("玩家 %s 清头像 %s 的 NEW 标记", player.get("account"), head_id)
    return _ok(head_id)


def set_medal_old(player: dict, msg: dict) -> dict:
    """`medal.setmedalold {medalId}` —— 客户端是按**整组**发的，这里也清整组。"""
    ensure(player)
    medal_id = str((msg or {}).get("medalId") or "")
    row = medal_table().get(medal_id)
    if not isinstance(row, dict):
        return _fail("没有这个勋章：%s" % medal_id)
    group = row.get("group")
    keys = [str(r.get("icon_id")) for r in medal_table().values()
            if isinstance(r, dict) and str(r.get("group")) == str(group)]
    _clear_new(player, keys)
    log.info("玩家 %s 清勋章组 %s 的 NEW 标记（%d 个）", player.get("account"), group, len(keys))
    return _ok({"ok": 1})


# ---------------------------------------------------------------------------
# 好友的勋章信息（`medal.getfriendmedalinfo`）
# ---------------------------------------------------------------------------
def friend_medal_info(player: dict, msg: dict, now: int | None = None) -> dict:
    """`ui/friend/deletefriendpanel._getFriendMedalInfoSuccCb(data)`：

        data.lv / data.name / data.numberId = 好友的；
        new MedalLayer(data)               = 用整个 data 当勋章信息画。

    好友是 NPC（见 `friends.py`），勋章给他一份**确定性**的：按 numberId 取
    前 K 条勋章算达成，并按组各戴一个 —— 随机数也行，但确定性更好排查。
    """
    from . import friends

    ts = int(now if now is not None else time.time())
    friend_id = _num(msg, "friendId", 0)
    info = friends.npc_info(friend_id, ts) if friend_id else None
    if not info:
        return _fail("找不到这个好友：%r" % friend_id)
    keys = [k for k, r in medal_table().items() if isinstance(r, dict)]
    keys.sort(key=lambda k: (str(medal_table()[k].get("group")), str(k)))
    count = 1 + (int(friend_id) % 12)
    done = keys[:count]
    medals = {}
    for key in keys:
        complete = key in done
        medals[str(key)] = {
            "id": str(key),
            "progress": target_of(key) if complete else 0,
            "progressInfo": {},
            "completeTime": ts - 86400 if complete else 0,
        }
    wear = {}
    seen_group = set()
    for key in done:
        group = str((medal_table().get(key) or {}).get("group"))
        if group in seen_group:
            continue
        seen_group.add(group)
        wear[str(key)] = len(wear) % 3
        if len(wear) >= 3:
            break
    out = dict(info)
    out.update({
        "completeCount": len(done),
        "medals": medals,
        "medalWear": wear,
    })
    log.info("玩家 %s 查看好友 %s 的勋章（完成 %d 条，佩戴 %d 个）",
             player.get("account"), friend_id, len(done), len(wear))
    return _ok(out)
