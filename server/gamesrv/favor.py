"""好感度（宿舍 / favor.*）的业务逻辑。

反编译来源：`src/data/favor.jsc`（`Favor` 实体）+ `src/data/favorcenter.jsc`
（`FavorCenter`）+ `src/manager/favormanager.jsc` + `src/data/item/gift.jsc`。
抽表见 `script/extract_client_tables.py`（`--only favor` / `--only char_desc`）。

## 登录块

```js
FavorCenter._initData(data) {
    var favorsData = data.favors;              // ← map！key 是 charKey
    this._favorInteractChance      = data.favorInteractChance;
    this._favorInteractUpdateTimeSec = data.favorInteractUpdateTimeSec;
    ...
    for (charKey in table_soldier_master)      // card_type==1 的（61 个）
        this._favors[charKey] = new Favor(已有的行 || {charKey});
    for (charKey in table_hero)                // 2 个
        this._favors[charKey] = new Favor(已有的行 || {charKey});
    this._isNeedAsstEff = false;               // ← 写死，**不从 data 里读**
    this._favorExpAdd   = 0;                   // ← 同上
}
```

⚠️ 三条容易踩的：

1. **`favors` 是 map 不是数组**。`existedKeys.indexOf(charKey) === -1 ? {charKey} : favorsData[charKey]`
   —— 回数组的话所有角色都取不到自己的行，全变「未获得」。
2. `isNeedAsstEff` / `favorExpAdd` 在构造函数里被**写死成 false/0**，登录包里给不给
   都一样（它们只由响应键 `favorAsstRefreshed` 驱动，见 `cb4ResFavorAsstRefreshed`）。
   以前登录包里挂的那两个键是死键，已删。
3. 缺 `favorInteractChance` 会一路传染：
   `updateFavorInteract()` 里 `undefined >= 5` 是 false → `_favorInteractChance + add` = NaN
   → 界面上互动次数显示 NaN。

## 好感度等级

数值全在 `table_favor_upgrade`（服务端已抽成 `data/table_favor_upgrade.json`）：

    "1".."15"  ->  {favor, touch_favor, touch_favor_add, max_daemon_lv, ...}
    favor      = 升到下一级还需要多少经验；**最后一档是 -1**，
                 客户端 `favorconfig.js` 就是靠 `favor == -1` 认出 MAX_FAVOR_LV=15
    touch_favor / touch_favor_add
               = 抚摸给的加值。**客户端全库 0 命中**（jsc_find），纯服务端数值；
                 表里每一级两列都恒等于 22，所以取哪个都一样，这里取 `touch_favor_add`。

⚠️ **加多少好感度是服务端说了算**：客户端只负责把 `favorValue` / `favorAdd` 拿去做动画
（`CharFavorUpLayer.pop`）。客户端唯一暴露线索的是
`favorManager.getPreferenceWithSendGift(favor, item)`，实机问过它，返回的是「偏好档位」：

    giftType ∈ love_type -> 2      giftType ∈ hate_type -> 4      其余 -> 3

所以礼物加多少也按同一套偏好去 gift 行的三个字段里挑（`favor` / `favor_love` / `favor_hate`）。

## 本模块里「猜」的部分（实机验证时重点看这几处）

* `FAVOR_BIRTHDAY_MULTIPLE`：生日当天额外再加一份等量经验。**没有表能佐证**，
  只是「`birthdayAdd` 这个字段得有点意义」；单旋钮，改一个数就能调。
* `RETURN_ITEM_PR_BASE`：`table_char_favor_receive_talk` 里 `return_item_pr_N` 全是 4，
  取值域无从校准，按「十分之几」算（40%）。同样单旋钮。
* 抚摸（`touchcharasst`）的 `returnItems` 恒给空 map —— 那张回礼表是「收到礼物的反应」
  （`table_char_favor_receive_talk`），跟抚摸无关。空 map 而不是 undefined，
  是因为客户端拿到之后直接 `popupRewardWithItems(returnItems, ...)`，
  给 undefined 更危险。
"""

from __future__ import annotations

import random
import time

from . import items, logx, store

log = logx.get("favor")

# 生日当天的好感激增倍数（见模块 docstring 里「猜的部分」）
FAVOR_BIRTHDAY_MULTIPLE = 2
# `return_item_pr_N` 的取值域：按「十分之几」算，4 -> 40%
RETURN_ITEM_PR_BASE = 10


# ---------------------------------------------------------------------------
# 表
# ---------------------------------------------------------------------------
def _upgrade() -> dict:
    return items.table("table_favor_upgrade")


def _common() -> dict:
    return items.table("table_favor_common")


def _gift_type() -> dict:
    return items.table("table_favor_gift_type")


def _gifts() -> dict:
    return items.table("table_favor_gift")


def _desc() -> dict:
    return items.table("table_char_desc")


def _receive() -> dict:
    return items.table("table_favor_receive")


def _const() -> dict:
    return items.table("table_favor_constant")


def _const_int(name: str, default: int) -> int:
    try:
        return int(_const().get(name, default))
    except (TypeError, ValueError):
        return default


def interact_max() -> int:
    return _const_int("max_favor_interact_times", store.FAVOR_INTERACT_MAX)


def interact_cooldown() -> int:
    return _const_int("favor_interact_cooldown_time", store.FAVOR_INTERACT_COOLDOWN)


def default_bg_key() -> str:
    v = _const().get("default_favor_bg_item_key")
    return str(v) if v else store.FAVOR_DEFAULT_BG_KEY


def max_lv() -> int:
    """MAX_FAVOR_LV。客户端 `favorconfig.js`：`if (table_favor_upgrade[k].favor == -1) MAX_FAVOR_LV = k`。"""
    best = 1
    for k, row in _upgrade().items():
        try:
            if int((row or {}).get("favor")) == -1:
                best = max(best, int(k))
        except (TypeError, ValueError):
            continue
    return best


def exp_to_next(lv: int):
    """升到 lv+1 需要多少经验；满级回 None。"""
    row = _upgrade().get(str(lv)) or {}
    try:
        need = int(row.get("favor"))
    except (TypeError, ValueError):
        return None
    return None if need < 0 else need


def touch_add(lv: int) -> int:
    row = _upgrade().get(str(lv)) or {}
    try:
        return int(row.get("touch_favor_add") or 0)
    except (TypeError, ValueError):
        return 0


def gift_row(item_key) -> dict:
    """`table_favor_gift[<key>]`（抽取时只留了礼物要用的字段）。不是礼物回空 dict。"""
    return _gifts().get(str(item_key)) or {}


def _split_types(text) -> set:
    if not text:
        return set()
    return {p.strip() for p in str(text).split(",") if p.strip()}


def preference_of(char_key: str, gift_type, is_birthday: bool = False) -> int:
    """偏好档位 1/2/3/4，对应 `table_char_favor_receive_talk` 里的 preference。

    1 = 生日（推断出来的：`getPreferenceWithSendGift` 只有 love/hate 两条分支，
        永远回不了 1，而回礼表里 1/2 两档才有东西；请求体里又带着 `isBirthday`）
    2 = 喜欢（giftType ∈ love_type）
    3 = 普通
    4 = 讨厌（giftType ∈ hate_type）
    """
    if is_birthday:
        return 1
    row = _gift_type().get(str(char_key)) or {}
    gt = str(gift_type)
    if gt in _split_types(row.get("love_type")):
        return 2
    if gt in _split_types(row.get("hate_type")):
        return 4
    return 3


def gift_value(char_key: str, gift: dict, preference: int) -> int:
    """一件礼物加多少好感度。字段含义见模块 docstring。"""
    if preference in (1, 2):
        try:
            return int(gift.get("fl") or 0)
        except (TypeError, ValueError):
            return 0
    if preference == 4:
        try:
            return int(gift.get("fh") or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(gift.get("f") or 0)
    except (TypeError, ValueError):
        return 0


def is_birthday(char_key: str, now: float | None = None) -> bool:
    """角色生日。客户端 `Favor._isFavorBirthday()`：`table_char_desc[k].birthday` 是 "月.日"。

    ⚠️ **服务端自己算，不信请求体里的 `isBirthday`** —— 那是个客户端可改的布尔，
    而生日加成是白给的经验。
    """
    row = _desc().get(str(char_key)) or {}
    if not row.get("is_favor_char"):
        return False
    birthday = row.get("birthday")
    if not birthday:
        return False
    parts = str(birthday).split(".")
    if len(parts) < 2:
        return False
    try:
        month, day = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return False
    t = time.localtime(time.time() if now is None else now)
    return t.tm_mon == month and t.tm_mday == day


def _parse_range(text, default=(1, 1)):
    """`return_item_count_N` 形如 "1,10"。"""
    try:
        lo, hi = str(text).split(",")[:2]
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError):
        return default
    return (min(lo, hi), max(lo, hi))


def roll_return_items(char_key: str, preference: int) -> dict:
    """回礼。`{itemKey: count}`，没中就是空 map。

    ⚠️ 表里只有 preference 1 和 2 有回礼（各 63 行），3/4 那两行压根没有
    `return_item_*` 字段 —— 也就是说「普通礼物」和「讨厌的礼物」不回礼。
    """
    rows = _receive().get(str(char_key)) or []
    row = None
    for r in rows:
        try:
            if int((r or {}).get("p")) == int(preference):
                row = r
                break
        except (TypeError, ValueError):
            continue
    if not row:
        return {}
    out: dict = {}
    for n in (1, 2):
        item_id = row.get("id%d" % n)
        if not item_id:
            continue
        try:
            pr = int(row.get("pr%d" % n) or 0)
        except (TypeError, ValueError):
            pr = 0
        if pr <= 0 or random.randint(1, RETURN_ITEM_PR_BASE) > pr:
            continue
        lo, hi = _parse_range(row.get("c%d" % n))
        key = str(item_id)
        out[key] = out.get(key, 0) + random.randint(lo, hi)
    return out


# ---------------------------------------------------------------------------
# 角色集合 / 行
# ---------------------------------------------------------------------------
def owned_char_keys(player: dict) -> list:
    """玩家能有好感度的角色 key，按客户端 `FavorCenter._initData` 的同一套规则挑。

    它会为 **61 个 `card_type == CARD_TYPE.TEAMMATE(1)` 的军士 + 2 个 hero**
    各建一个 `Favor`，没有数据的就用 `{charKey: charKey}`（= 未获得）。
    服务端只需要给**玩家真的拥有的**那些发 `id`。

    ⚠️ 军士要的是 **`char_key`**（`sasm`），不是 `table_soldier` 的 key
    （`sasm010104`）—— `table_favor_gift_type` / `table_char_desc` 都是按 char_key 索引的。
    """
    out: list = []
    seen: set = set()

    def add(key):
        key = str(key or "")
        if key and key not in seen:
            seen.add(key)
            out.append(key)

    chars = player.get("soldiers")
    card = (items.table("table_soldier") or {}).get("card") or {}
    if isinstance(chars, list):
        for row in chars:
            if not isinstance(row, dict) or row.get("isDel"):
                continue
            cfg = card.get(str(row.get("key"))) or {}
            add(cfg.get("ck") or row.get("charKey") or row.get("key"))
    # ⚠️ 主角**不在** player 里：`_module_stubs()` 每次登录现调 `store.new_hero()`
    # 造一个（`char.heros`），存档里没有 heros 这个字段。
    # 所以这里直接认 `store.HERO_KEY`；以后真要把主角也持久化，再来读 player。
    add(store.HERO_KEY)
    heros = player.get("heros")
    if isinstance(heros, list):
        for row in heros:
            if isinstance(row, dict):
                add(row.get("key"))
    return out


def ensure_favors(player: dict) -> bool:
    """按拥有的角色补好感度行。返回是否有改动。

    ⚠️ 只在**玩家真的拥有那个角色**时才建行、才有 `id` —— `id` 的有无就是
    客户端的 `isAcquired`（见 `store.new_favor_row`）。

    ⚠️ **只加不删**：军士被当材料吃掉（`char.upgradesoldierlv`）之后，那一行留着。
    这是故意的 —— 好感度是「角色关系」不是「某张卡」，见过面就一直有；
    原版把重复卡喂掉也不会掉好感度。
    """
    favors = store.player_favors(player)
    changed = False
    for char_key in owned_char_keys(player):
        if char_key in favors:
            continue
        favors[char_key] = store.new_favor_row(char_key, store.next_favor_id(player))
        changed = True
    if changed:
        log.info("玩家 %s 补好感度行 -> %d 个角色", player.get("account"), len(favors))
    return changed


def favor_block(player: dict) -> dict:
    """登录包里 `favor` 那一块。形状见模块 docstring。

    ⚠️ 客户端 `cb4ResFavor` 里是 `var favor = this._favors[i]; ... favor.update(info)`
    —— i 不在 `this._favors` 里就会 `favor.lv` 抛 TypeError。
    所以响应里的 `favor` 块**只能放客户端也认识的角色 key**。
    """
    st = store.favor_interact(player)
    return {
        "favors": store.player_favors(player),
        "favorInteractChance": st["chance"],
        "favorInteractUpdateTimeSec": st["updateTimeSec"],
    }


def row_block(char_key: str, row: dict) -> dict:
    """响应里的 `favor` 块（触发客户端 `cb4ResFavor`：整行 update）。"""
    return {str(char_key): row}


# ---------------------------------------------------------------------------
# 好感度经验
# ---------------------------------------------------------------------------
def add_exp(row: dict, amount: int) -> int:
    """加经验并结算升级。返回升了几级。

    ⚠️ 满级（`table_favor_upgrade[max].favor == -1`）之后把 `curExp` 夹回 0：
    客户端的进度条是 `setExp(curExp, table_favor_upgrade[lv].favor)`，
    满级那一档的 maxExp 是 **-1**，留着余额只会算出乱七八糟的百分比。
    """
    amount = int(amount or 0)
    if amount <= 0:
        return 0
    lv = int(row.get("lv") or 1)
    exp = int(row.get("curExp") or 0) + amount
    top = max_lv()
    up = 0
    while lv < top:
        need = exp_to_next(lv)
        if not need or exp < need:
            break
        exp -= need
        lv += 1
        up += 1
    if lv >= top:
        exp = 0
    row["lv"] = lv
    row["curExp"] = exp
    return up


def sync_interact(player: dict, now: int | None = None) -> int:
    """把抚摸次数按时间推进到「现在」，返回可用的次数。

    ⚠️ 满的时候要把基准时间挪到 now：否则「满 → 花掉一个 → 立刻又按积攒的时间补回满」。
    客户端 `updateFavorInteract()` 只做加法、**不推进 `_favorInteractUpdateTimeSec`**，
    所以基准时间一律由服务端在响应里给（`setFavorInteract(res.data)`）。
    """
    now = int(time.time()) if now is None else int(now)
    st = store.favor_interact(player)
    top = interact_max()
    cd = interact_cooldown()
    chance = st["chance"]
    base = st["updateTimeSec"]
    if chance >= top:
        st["updateTimeSec"] = now
        return chance
    gained = (now - base) // cd if cd > 0 else 0
    if gained > 0:
        chance = min(chance + gained, top)
        st["chance"] = chance
        st["updateTimeSec"] = now if chance >= top else base + gained * cd
    return chance


def spend_interact(player: dict) -> int:
    """花掉一次抚摸机会。返回剩余次数；不够就原样返回（< 1）。

    ⚠️ 自己先 `sync_interact()` 一次，不依赖调用方先同步 —— 否则「满次数但基准时间
    已经陈旧」的状态（比如手改存档、或者以后有别的地方直接用）会出现
    「花掉一次 → 下一次同步又按积攒的时间瞬间补回满」。
    """
    sync_interact(player)
    st = store.favor_interact(player)
    if st["chance"] < 1:
        return st["chance"]
    st["chance"] -= 1
    return st["chance"]


def birthday_bonus(base: int) -> int:
    """生日额外加的那一份（`birthdayAdd`）。见 FAVOR_BIRTHDAY_MULTIPLE 的说明。"""
    extra = max(0, FAVOR_BIRTHDAY_MULTIPLE - 1)
    return int(base or 0) * extra
