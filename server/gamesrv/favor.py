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
* **宿舍事件**（`favorevent`）整块的「触发时机 + 奖励」都是推断的 ——
  形状是反汇编钉死的，但「什么时候算解锁」「`newFavorEvent` 从哪条路推下去」
  没有字节码佐证。单独写在下面「宿舍事件」那一段的最前面，实机时先看那段。
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


# ---------------------------------------------------------------------------
# 角色的默认衣服
# ---------------------------------------------------------------------------
# ⚠️⚠️ **这个不是可选的**。踩过一次：`curClothes` 给空串，于是
#
#     favorManager.createExpSpriteEx(expKey, parentNode, favor):
#         var item = dataManager.bag.getItem(favor.curClothes);   // getItem("") -> undefined
#         if (!item) { cc.log("favorManager.createExpSprite error, clothes item not found");
#                      return; }                                  // ← 返回 undefined
#
# 抚摸特效拿表情立绘的时候拿到 undefined 就不往下走了 ——
# **表现是「摸角色完全没反应」：不扣互动次数、不加好感度**，
# 而 logcat 里只有一行 `createExpSprite error, clothes item not found`（没有 JS 异常）。
#
# 判据（实机在 59 个有衣服的角色上验过，58 个唯一命中）：
#   * `icon == "appareldefault"` —— 图标就叫「默认服装」（如 400001 阿呆芙军装）
#   * 或 `replace_key == char_key` —— 等于「不替换立绘」，也就是原版默认造型
# 两个都没命中（实测只有 `slys`）就退到 quality 最低的那件；
# 一件都没有才回空串（那时客户端本来就无解，只能留着旧行为）。


def default_clothes(char_key) -> str:
    """角色自带的默认衣服 key。取不到回空串。"""
    ck = str(char_key)
    cands = [k for k, r in items.table("table_item").items()
             if str(r.get("t")) == "40" and str(r.get("ck")) == ck]
    if not cands:
        return ""
    for k in cands:
        if str(items.table("table_item")[k].get("ic")) == "appareldefault":
            return k
    for k in cands:
        if str(items.table("table_item")[k].get("rk")) == ck:
            return k
    return sorted(cands, key=lambda k: (int(items.table("table_item")[k].get("q") or 0), k))[0]


def ensure_default_looks(player: dict) -> bool:
    """给每个有衣服的角色发一件**默认衣服**，并把 `curClothes` 补上。返回是否有改动。

    * 只补到 1 件（`limit_count` 就是 1），不会覆盖玩家自己换过的
    * `curClothes` 只在「空」或「指向的东西不在背包里」时才重置成默认的
    * 顺带把默认背景（`default_favor_bg_item_key`）也补一件 —— 不然
      「换背景」那个页签是空的，而 `curBg` 又已经指着它了
    """
    changed = False
    bag = items.items_of(player)
    for char_key, row in store.player_favors(player).items():
        key = default_clothes(char_key)
        if key:
            if int(bag.get(key) or 0) < 1:
                bag[key] = 1
                changed = True
            cur = str(row.get("curClothes") or "")
            if not cur or int(bag.get(cur) or 0) < 1:
                row["curClothes"] = key
                changed = True
        elif not row.get("curClothes"):
            log.warning("角色 %s 在 table_item 里一件衣服都没有，curClothes 只能留空"
                        "（抚摸特效会拿不到表情立绘）", char_key)
    bg = default_bg_key()
    if bg and int(bag.get(bg) or 0) < 1:
        bag[bg] = 1
        changed = True
    if changed:
        log.info("玩家 %s 补默认造型（%d 个角色的默认衣服 + 默认背景 %s）",
                 player.get("account"), len(store.player_favors(player)), bg)
    return changed


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


# ---------------------------------------------------------------------------
# 宿舍事件（favorevent.*）
# ---------------------------------------------------------------------------
# ⚠️⚠️ **这一块是本次适配里推断最多的地方，实机要重点看。**
#
# 反汇编能确定的（`FavorEventCenter` / `FavorEvent`）：
#
#   1. 登录块 `data.favorevent` 是个 **scratch 对象**，不是事件列表。
#      `_initData(data)` 干的是（逐条反汇编）：
#          this._eventsInTable = {};
#          this._favorEvents   = {};                       // ← 只在**响应推送**里填
#          for (var i in table_favor_random_event) {
#              this._eventsInTable[i] = table_favor_random_event[i];
#              this._eventsInTable[i].table_id = i;
#              data[i] = new FavorEvent(data[i] || {eventKey: i}, this._eventsInTable[i]);
#          }
#      注意最后一行写回的是 **`data[i]`**，而 `_favorEvents` 全程是空的 ——
#      也就是说**登录块里给什么都不影响界面**，事件只能靠响应键 `newFavorEvent` 推。
#
#   2. `cb4ResNewFavorEvent(res)`：`data` 是 **`{eventKey: 行}` 的 map**，
#      逐条 `this._favorEvents[行.eventKey] = new FavorEvent(行, table[eventKey])`。
#
#   3. `submitFesRead(eventKeys)` 的请求体是 **裸数组**（不是 `{eventKeys: [...]}`）：
#          if (!_.isArray(eventKeys) || eventKeys.length == 0) return;
#          for (var i in eventKeys) if (!events[eventKeys[i]]) return;   // 有一个不认识就整个不发
#          server.request("favorevent.seteventsunlock", eventKeys, cb, true);
#      回调**只打日志、不读 res.data**，所以「读过了」这个状态也得靠响应推回去。
#
# 推断的部分（没有字节码佐证，靠表结构反推）：
#
#   * **什么时候解锁**：`table_favor_random_event[k].favor_lv` 是该事件要求的
#     好感度等级（"1"/"3"/"5"...）。所以「好感度升到 N 级 → 解锁该角色 lv<=N 的事件」。
#   * **读完给奖励**：`reward_favor` 是纯服务端数值（客户端全库 0 命中），
#     所以服务端在读事件时把这份好感度加上。
#   * **`newFavorEvent` 从哪来**：客户端只在 `request` 的响应派发里认这个键，
#     而 `dataManager.isLogin` 那道闸让**首次登录的响应派发不进**（否则
#     `dataManager.favorCenter` 还是 null，`cb4ResFavor` 会直接抛）。
#     所以这里**两处都发**（双保险）：
#        - 登录响应里带一份（万一派发其实是通的）
#        - 好感度涨了之后（`favor.usegift` / `favor.touchcharasst`）带上新增的那几条
#     实机验证方法：登录后看 `Object.keys(dataManager.favorEventCenter._favorEvents).length`，
#     是 0 就说明登录那条路不通、只能靠好感度涨了才推。
#   * `level_key` 有 getter 但**没人读**，服务端不用管。


def _event_table() -> dict:
    return items.table("table_favor_event")


def event_need_lv(event_key) -> int:
    """这个事件要求的好感度等级。表里是字符串（"1"/"3"），坏值当 1。"""
    row = _event_table().get(str(event_key)) or {}
    try:
        return int(row.get("lv") or 1)
    except (TypeError, ValueError):
        return 1


def event_reward(event_key) -> int:
    """读完这个事件给多少好感度（`reward_favor`，纯服务端数值）。"""
    row = _event_table().get(str(event_key)) or {}
    try:
        return int(row.get("rf") or 0)
    except (TypeError, ValueError):
        return 0


def event_owner(event_key) -> str:
    row = _event_table().get(str(event_key)) or {}
    return str(row.get("ck") or "")


def sync_events(player: dict) -> list:
    """按「拥有的角色 + 当前好感度等级」补该解锁的事件，返回**新增的那些行**。

    ⚠️ 和 `ensure_favors` 不同，这个**每次好感度变了都要调**（送礼/抚摸之后），
    否则升到 3 级解锁的事件要等下次登录才出现。
    """
    table = _event_table()
    events = store.player_favor_events(player)
    new_rows = []
    for char_key, favor_row in store.player_favors(player).items():
        try:
            lv = int(favor_row.get("lv") or 1)
        except (TypeError, ValueError):
            lv = 1
        for event_key, cfg in table.items():
            if str(cfg.get("ck")) != str(char_key):
                continue
            if event_key in events:
                continue
            if event_need_lv(event_key) > lv:
                continue
            row = store.new_favor_event_row(event_key, store.next_favor_event_id(player), lv)
            events[event_key] = row
            new_rows.append(row)
    if new_rows:
        log.info("玩家 %s 解锁 %d 条宿舍事件", player.get("account"), len(new_rows))
    return new_rows


def event_block(player: dict) -> dict:
    """登录包里 `favorevent` 那一块。

    ⚠️ 只发已解锁的事件（按 eventKey 索引）。**首次登录时客户端并不会用它** ——
    见上面「登录块是 scratch 对象」那段；真正的推送靠 `new_favor_event_block()`。
    之所以还发一份，是因为 `_initData` 会 `data[i] = new FavorEvent(data[i] || ...)`，
    给了正确的行它构造出来的 `FavorEvent` 就是对的（万一别处要用）。
    """
    return dict(store.player_favor_events(player))


def new_event_block(rows) -> dict:
    """响应里的 `newFavorEvent` 块：`{eventKey: 行}`。空列表回空 dict。"""
    return {str(r.get("eventKey")): r for r in (rows or [])}


def update_event_block(rows) -> dict:
    """响应里的 `favorEvent` 块：`{eventKey: {status, isToBeUnlocked}}`。

    `cb4ResFavorEvent` 接受「map 或数组」，逐条读 `eventKey` / `status`
    再 `obj[i].update(...)` —— 所以每条都要带 `eventKey`，
    这里直接把整行推回去最省事（`FavorEvent.update` 只认这几个字段）。
    """
    return {str(r.get("eventKey")): r for r in (rows or [])}


def read_events(player: dict, event_keys) -> tuple:
    """把事件标成「已读」并发奖励。返回 (改动过的行, 本次加的好感度)。

    ⚠️ 客户端那边 `submitFesRead` 是**先校验全部存在再整单发**，
    所以服务端也整单处理：有不认识的 key 就整单拒绝（见 handlers/favorevent.py）。

    ⚠️ 奖励**只发一次**：靠 `status` 从 `ACCEPTABLE(1)` 变 `ACCEPTED(2)` 判断，
    重复提交同一个 key 不会再给好感度。
    """
    changed = []
    total = 0
    for key in event_keys or []:
        row = store.find_favor_event(player, key)
        if row is None:
            continue
        if int(row.get("status") or 0) == store.FAVOR_EVENT_ACCEPTED and not row.get("isToBeUnlocked"):
            continue                       # 已经读过，幂等
        row["status"] = store.FAVOR_EVENT_ACCEPTED
        row["isToBeUnlocked"] = 0
        reward = event_reward(key)
        if reward:
            owner = event_owner(key)
            favor_row = store.find_favor(player, owner)
            if favor_row is not None:
                add_exp(favor_row, reward)
                total += reward
        changed.append(row)
    return changed, total
