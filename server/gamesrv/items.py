"""道具 / 背包 / 奖励发放。

这一层是好几条路由的共同前置：`mail` 的附件、`sign` 的签到奖、`shop` 的购买、
`gacha` 的抽卡结果、`convert` 的礼包兑换，全都要「扣道具 / 发道具 / 给经验」。
没有它，那些路由只能回空 data —— 看着 200 成功，实际什么都没发生。

## 物品在存档里长什么样

就是一个平铺字典 `{itemKey: count}`，登录包里 `item` 那个块就是这个形状：

    "item": {"100001": 100000, "100002": 10000000, "100003": 999}

客户端 `Bag.ctor(items)` 拿它 + 自己的 `table_item` 建出 Item 对象
（`type` 决定类：CURRENCY→Currency、GIFT→Gift、PACKAGE→Package …）。
所以服务端**不需要**知道物品的属性，只需要知道 key 和数量。

## 奖励字符串的格式（实测，不是猜的）

用**客户端自己的解析器** `rewardManager.getRewardsWithString()` 验过：

    '1#100'                 -> [{type:"1", count:100}]
    '2#100001#500'          -> [{type:"2", key:"100001", count:500}]
    '5#sasm010104#1'        -> [{type:"5", key:"sasm010104", count:1}]
    '1#100,2#100001#500'    -> 上面两条

    `,` 分隔多个奖励，`#` 分隔字段
      PLAYER_EXP   <type>#<count>
      其余         <type>#<key>#<count>
    type 用 REWARD_TYPE 的**数字值**（ITEM = "2"），不是名字。

⚠️ 踩过的坑：按「逗号分隔字段、像 CSV 那样写」实现，被客户端解析器当场打脸 ——
   `'ITEM,100001,500'` 它直接解析成 `[]`。字段顺序/分隔符必须照上面来。
"""

from __future__ import annotations

import json
import os

from . import config, logx, store

log = logx.get("items")

# 客户端 REWARD_TYPE（值就是奖励字符串里该写的数字）
REWARD_TYPE = {
    "PLAYER_EXP": "1",
    "ITEM": "2",
    "HERO": "3",
    "MECHA": "4",
    "SOLDIER": "5",
    "GUILD_EXP": "6",
    "GUILD_ACTIVE_VALUE": "7",
    "EQUIPMENT": "8",
    "SCORE": "9",
}
TYPE_NAME = {v: k for k, v in REWARD_TYPE.items()}

# 只有 PLAYER_EXP 是 `<type>#<count>`，其余都是 `<type>#<key>#<count>`
_NO_KEY = {REWARD_TYPE["PLAYER_EXP"], REWARD_TYPE["GUILD_EXP"]}


# ---------------------------------------------------------------------------
# 奖励字符串
# ---------------------------------------------------------------------------
def parse_rewards(text: str) -> list:
    """解析奖励字符串 -> [(type, key, count)]。

    容错：不认识的 type / 段数不对的条目**跳过并记日志**，不抛异常 ——
    奖励字符串是配置数据，一条坏的没理由让整个请求 500。
    """
    out = []
    if not text:
        return out
    for part in str(text).split(","):
        part = part.strip()
        if not part:
            continue
        seg = part.split("#")
        rtype = seg[0].strip()
        if rtype not in TYPE_NAME:
            log.warning("奖励字符串里有不认识的 type=%r（整条跳过）: %r", rtype, part)
            continue
        if rtype in _NO_KEY:
            if len(seg) < 2:
                log.warning("奖励条目字段不够: %r", part)
                continue
            out.append((rtype, "", int(seg[1] or 0)))
        else:
            if len(seg) < 3:
                log.warning("奖励条目字段不够: %r", part)
                continue
            out.append((rtype, seg[1], int(seg[2] or 0)))
    return out


def format_rewards(rewards) -> str:
    """[(type, key, count)] -> 奖励字符串。"""
    parts = []
    for rtype, key, count in rewards:
        rtype = str(rtype)
        if rtype in _NO_KEY:
            parts.append(f"{rtype}#{count}")
        else:
            parts.append(f"{rtype}#{key}#{count}")
    return ",".join(parts)


# ---------------------------------------------------------------------------
# 背包
# ---------------------------------------------------------------------------
def items_of(player: dict) -> dict:
    """拿到玩家的背包（缺就补上默认货币）。"""
    items = player.get("items")
    if not isinstance(items, dict):
        items = store.default_items()
        player["items"] = items
    return items


def count_of(player: dict, key) -> int:
    return int(items_of(player).get(str(key)) or 0)


def add_item(player: dict, key, count: int) -> None:
    """加道具。客户端 `Bag._addCount` 会按 `table_item.limit_count` 截断，
    服务端这里也照做，免得存进去一个客户端不认的数。

    ⚠️ **只夹「往上加」这一侧，绝不因为已超上限就把玩家的存量缩回去**。
    `table_item.json` 是后补的，补上之前 `_item_limit()` 一律返回 0（不限），
    存档里可能已经躺着超上限的数（比如行动力道具 `100003` 上限 300、
    而初始背包发的是 999）。不判断的话，玩家打一关领奖励会把 999 直接削到 300。
    """
    items = items_of(player)
    k = str(key)
    cur = int(items.get(k) or 0)
    limit = _item_limit(k)
    new = cur + int(count)
    if limit > 0:
        if cur > limit:
            log.warning("道具 %s 存量 %d 已超上限 %d，本次只加不夹", k, cur, limit)
        else:
            new = min(new, limit)
    items[k] = new
    if new != cur:
        log.info("发道具 %s: %d -> %d", k, cur, new)


def changed_block(player: dict, known_keys) -> dict:
    """响应里该带的 `items` 块（让客户端 `Bag` 当场刷新，而不是重登才看到）。

    客户端 `patch.js` 的 RESP-DISPATCH 里 `items -> bag.updateItems(res.data.items)`，
    而 `Bag.updateItems` 是：

        for (k in items) {
            var item = this._items[k];
            if (typeof items[k] === "object") item.updateByObj(items[k]);
            else                              item.count = items[k];   // ← 直接写
        }

    ⚠️ **新入手的道具不能塞进来**：key 不在客户端那份 bag 里时 `item` 是 undefined，
    `undefined.count = n` 直接抛 TypeError。所以这里只回**客户端本来就有**的 key
    （`known_keys` = 这次改动**之前** `items_of(player)` 的 key 集合）。
    新道具仍然进存档、也会在奖励弹窗里显示，只是要重登才会出现在背包列表里。
    """
    bag = items_of(player)
    return {str(k): int(v) for k, v in bag.items() if str(k) in known_keys}


def sub_item(player: dict, key, count: int) -> bool:
    """扣道具。不够就返回 False 且**不改动**。"""
    items = items_of(player)
    k = str(key)
    cur = int(items.get(k) or 0)
    need = int(count)
    if cur < need:
        return False
    items[k] = cur - need
    log.info("扣道具 %s: %d -> %d", k, cur, items[k])
    return True


def can_afford(player: dict, cost) -> bool:
    """cost 形如 [(itemKey, count)]。"""
    return all(count_of(player, k) >= int(c) for k, c in cost)


# ---------------------------------------------------------------------------
# 发奖励
# ---------------------------------------------------------------------------
def settle(player: dict, rewards) -> dict:
    """把奖励发进存档，返回一个摘要（给日志/回包用）。

    能发的：ITEM（背包）、PLAYER_EXP（经验）、SOLDIER（加进名单）。
    发不了的：HERO / MECHA / EQUIPMENT / SCORE / GUILD_* —— 这些要么需要
    装备/机甲/公会那一层的数据结构，要么属于还没做的系统。**不假装发成功**：
    记 warning 并写进 `pendingRewards`，以后补的时候能捡回来。
    """
    granted = {"items": {}, "exp": 0, "soldiers": [], "pending": []}
    for rtype, key, count in rewards:
        name = TYPE_NAME.get(str(rtype), str(rtype))
        if name == "ITEM":
            if icon_of(key) == "":
                log.warning("奖励里的道具 %s 在 table_item 里图标为空（ic=\"\"）—— "
                            "客户端 ItemIcon 会抛 TypeError 打断界面初始化，"
                            "奖励列表里不能出现它（见 items.icon_of）", key)
            add_item(player, key, count)
            granted["items"][str(key)] = granted["items"].get(str(key), 0) + int(count)
        elif name == "PLAYER_EXP":
            player["curExp"] = int(player.get("curExp") or 0) + int(count)
            granted["exp"] += int(count)
        elif name == "SOLDIER":
            sid = _add_soldier(player, key)
            if sid is None:
                granted["pending"].append((rtype, key, count))
            else:
                granted["soldiers"].append(sid)
        else:
            log.warning("奖励类型 %s 还没实现发放（key=%s count=%s），记账等补",
                        name, key, count)
            pending = player.setdefault("pendingRewards", [])
            pending.append({"type": name, "key": key, "count": int(count)})
            granted["pending"].append((rtype, key, count))
    return granted


def _add_soldier(player: dict, key: str):
    """按 key 加一个军士到名单里，返回新 id；拿不到属性就返回 None。"""
    try:
        from . import soldier as soldier_mod

        row = soldier_mod._row("table_soldier", key) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("查 table_soldier[%s] 失败：%s", key, exc)
        row = {}
    if not row:
        log.warning("table_soldier 里没有 %s，发不了这个军士", key)
        return None
    soldiers = store.ensure_soldiers(player)
    used = {int(s.get("id") or 0) for s in soldiers}
    new_id = 1
    while new_id in used:
        new_id += 1
    positioning = int(row.get("positioning") or 1)
    quality = int(row.get("quality") or 1)
    soldiers.append(store.new_soldier(new_id, key, positioning=positioning, quality=quality))
    log.info("发军士 %s -> id=%d（站位 %d 品质 %d）", key, new_id, positioning, quality)
    return new_id


# ---------------------------------------------------------------------------
# 客户端表
# ---------------------------------------------------------------------------
_TABLES: dict = {}


def table(name: str) -> dict:
    """读一份抽出来的客户端表（`gamesrv/data/<name>.json`）。

    表是 `extract_client_tables.py` 从客户端里抽的 —— 抽之前是空的，
    服务端这边要能接受"表不存在"（回空 dict）而不是崩。
    """
    if name in _TABLES:
        return _TABLES[name]
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", name + ".json")
    data: dict = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.warning("读表 %s 失败：%s", path, exc)
    else:
        log.warning("表 %s 还没抽（跑 script/extract_client_tables.py）", name)
    _TABLES[name] = data
    return data


def icon_of(key):
    """道具图标名（`table_item.ic`）。**空串 = 客户端画不出来**，不是道具回 `None`。

    客户端 `Bag` 会给 `table_item` 里**每一条**预先建行（`_icon` 直接取 `ic`），
    `ItemIcon.updateItemIcon` 遇到图标为空的道具会走
    `bag.getItemIcon(key)` → 模板替换出 `res/icon/item/undefined.png` →
    `cc.assert(fileUtils.isFileExist(url))` 失败 → 返回坏路径 → 后面用
    `this._iconCase` 时它**还没建**，直接 `TypeError`（itemicon.js:199）。

    这个异常是**打断调用方整条初始化**的（比如一进游戏跑
    `SignRewardItem._init -> rewardManager.getRewardIcon`），表现就是
    「界面点不动、服务端一条请求也收不到」。所以**任何会被显示出来的奖励
    列表里都不能出现 `ic` 为空的道具**。

    全表 481 条里只有两个没有图标，都是计数器（不是真道具）：
    `100101 卡槽购买次数` / `100102 装备槽购买次数`。
    """
    row = table("table_item").get(str(key))
    if not isinstance(row, dict):
        return None
    return str(row.get("ic") or "")


def _item_limit(key: str) -> int:
    """`table_item[key].limit_count`。表里没有就当不限。

    ⚠️ 抽出来的那份表字段是压缩过的（`lc` = limit_count，见
    `script/extract_client_tables.py` 的 ITEM_JS），这里两种名字都认。
    """
    row = table("table_item").get(str(key)) or {}
    try:
        return int(row.get("lc", row.get("limit_count")) or 0)
    except (TypeError, ValueError):
        return 0
