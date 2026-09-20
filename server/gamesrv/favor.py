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

from . import items, logx, soldier, store

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


# 衣柜 / 背景一次性发满的版本号（见 ensure_look_stock 和 differences.md §B）
LOOK_STOCK_VERSION = 1


def ensure_look_stock(player: dict) -> bool:
    """把**所有**衣服（`type==40`）和背景（`type==50`）各发一件。返回是否有改动。

    **这是私服取舍，不是原版行为。** 原版的衣服来自扭蛋和活动：
    `table_item` 里 109 件衣服 / 54 张背景，其中 `icon == "appareldefault"`
    那件是默认造型（`ensure_default_looks` 已经在发），其余全是
    quality 40 的"活动限定时装"。私服的扭蛋是空卡池（缺运营配置，见
    `handlers/gacha.py`），不主动发的话**「换装」和「换背景」两个页签
    永远只有一件**，宿舍这块等于没做。

    所以一次性发满，和天赋材料 / 装备材料 / 默认造型同一个套路。
    要还原原版就从 `LOOK_STOCK_VERSION` 那个判断里删掉，
    或者把 `LOOK_STOCK_VERSION` 保持不动、手动清背包。

    ⚠️ 衣服 `limit_count` 就是 1，而且**永远不会被消耗**，所以这个是幂等的
    （重复调用不会加数量）。发的时候**不标"新获得"**
    （`newClothes` / `newBgList` 留空）—— 一次给一百多件、每个角色都挂红点太吵。
    """
    if int(player.get("lookStockVersion") or 0) == LOOK_STOCK_VERSION:
        return False
    bag = items.items_of(player)
    n = 0
    for key, row in items.table("table_item").items():
        if str(row.get("t")) not in ("40", "50"):
            continue
        if int(bag.get(key) or 0) < 1:
            bag[key] = 1
            n += 1
    player["lookStockVersion"] = LOOK_STOCK_VERSION
    log.info("玩家 %s 发满衣柜/背景：新增 %d 件（私服取舍，见 docs/differences.md §B）",
             player.get("account"), n)
    return True


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


def gift_need_lv(gift: dict, default: int = 1) -> int:
    """这件礼物要求角色好感等级到几级才能送。

    客户端 `FavorGiftPanelWrapper.newItem` 里是

        needFavorLv = table_constant["use_gift_lv_" + quality]

    （没到就在礼物面板上显示成不可用）。抽出来的 `table_favor_constant` 里
    `use_gift_lv_10/20/30/40` **全是 1**，所以实际不拦人 —— 服务端照抄规则，
    防的是改包。
    """
    quality = str(gift.get("q") or "")
    if not quality:
        return default
    row = items.table("table_favor_constant") or {}
    try:
        return int(row.get("use_gift_lv_%s" % quality) or default)
    except (TypeError, ValueError):
        return default


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


def rows_block(player: dict, char_keys) -> dict:
    """响应里的 `favor` 块，一次给多个角色（`cb4ResFavor` 本来就是按 key 遍历的）。

    ⚠️ key **必须**是客户端也认识的角色：`cb4ResFavor` 里是
    `var favor = this._favors[i]; ... favor.lv`，不认识就直接 TypeError。
    这里只回玩家真有好感度行的那些（客户端 `_favors` 按整张表建，必然认识）。
    """
    favors = store.player_favors(player)
    return {str(k): favors[str(k)] for k in (char_keys or []) if str(k) in favors}


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


# ---------------------------------------------------------------------------
# 战斗结算的好感度（`instance.finishlevel`）
# ---------------------------------------------------------------------------
def level_favor_targets(player: dict, favor_char_key, team: dict) -> list:
    """这次战斗的好感度**给谁**。

    规则不在服务端，在客户端 —— `Instance.finishLevel/<` 把服务端回的
    `rewards.levelReward.favor`（只有数量）配上**自己关卡表里的**
    `table_level[levelId].favor_char_key` 打包成 `ret.favorObj`，
    再由 `LevelWinBase.getFavorUpCharsInfo(ret)` 决定发给谁：

        ret.favorObj.favorCharKey 有值 -> 只给他一个人，并记成 storyCharKey
        没有                            -> 当前队伍**所有军士 + heroKey** 一人一份

    ⚠️ 所以服务端必须复刻同一列（抽取时压成 `table_level_reward.json` 的
    `fck`），否则会出现「弹窗说 A 涨了、存档给 B 涨了」。

    ⚠️ 队伍里的 `soldierKeys` 是**军士 id**，要翻成角色 key
    （`Team.getSoldierKeys()` 给的是 `_soldiers[i].charKey`）。
    """
    fck = str(favor_char_key or "")
    if fck:
        return [fck]

    keys: list = []
    seen: set = set()

    def add(key):
        key = str(key or "")
        if key and key not in seen:
            seen.add(key)
            keys.append(key)

    for soldier_id in (team or {}).get("soldierKeys") or []:
        row = store.find_soldier(player, soldier_id)
        add(soldier.char_key_of((row or {}).get("key")))
    add((team or {}).get("heroKey"))
    return keys


def grant_level_favor(player: dict, level_info: dict, team: dict) -> dict:
    """关卡结算加好感度，返回 `{charKey: 加了多少}`（只含**真的加上去**的）。

    ⚠️ 只给**已经有好感度行**的角色加：`new_favor_row` 里 `id` 的有无就是客户端的
    `isAcquired`，没获得过的角色不能因为「通了他出场的那一关」就凭空变成已获得。
    所以调用方要拿返回值的**空/非空**决定要不要给客户端回 `favor`（弹窗上那个
    `+N` 就是它，回一个没真加上去的数字就是撒谎）。

    ⚠️ 每个中招的角色各加**一份**（不是一个总量大家分）—— 客户端的
    `calcFavor(charKey, favor)` 是对每个 key 都 `+= favor`。
    """
    amount = int((level_info or {}).get("favor") or 0)
    if amount <= 0:
        return {}
    favors = store.player_favors(player)
    granted: dict = {}
    for char_key in level_favor_targets(player, (level_info or {}).get("fck"), team):
        row = favors.get(char_key)
        if not isinstance(row, dict):
            continue
        add_exp(row, amount)
        granted[char_key] = amount
    return granted


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
# 守护灵（宿舍的 guard 面板 / char.upgradedaemon）
# ---------------------------------------------------------------------------
# 反汇编来源：`CharCenter.getDaemon / calcDaemonUpgrade / updateDaemon`
# + `charManager.getMaxDaemonLv / checkDaemonMaxWithFavorLv / getDaemonAttrTotal`。
#
# 规则（**数值全在客户端表里，服务端照抄**）：
#
#   上限    `table_favor_upgrade[好感度等级].max_daemon_lv`（不是固定 10！）
#           实测 getMaxDaemonLv(1)=0 / (5)=0 / (10)=4 —— 好感度不够就一级都升不了
#   曲线    `table_daemon_upgrade[lv].exp`，lv = 0..10，`[10].exp = -1` 是满级
#   材料    喂**军士**，每个给多少经验看 `table_daemon_exp[品质]`：
#             同角色 same_char > 同类型 same_type > 其它 default
#           品质 1 三个值全是 0（喂 1 星材料一点经验都没有）
#   属性    改完只是 `lv` 变了，`attrTotal` 客户端自己拿 `table_daemon[mode][lv]` 算；
#           服务端**不用**回属性。`mode` 来自 `table_soldier_master[charKey].daemon_mode`。


def _daemon_upgrade() -> dict:
    return items.table("table_daemon_upgrade")


def _daemon_exp_table() -> dict:
    return items.table("table_daemon_exp")


def _daemon_cfg() -> dict:
    """`table_soldier.json` 里的 `daemon` 段：`{charKey: {mode, type}}`。"""
    return (items.table("table_soldier") or {}).get("daemon") or {}


def daemon_mode(char_key) -> str:
    """这个角色用哪套守护灵属性（at0101 / df0101 / hp0101）。取不到回空串。"""
    return str((_daemon_cfg().get(str(char_key)) or {}).get("mode") or "")


def daemon_max_lv(favor_lv) -> int:
    """好感度这个等级能升到几级守护灵。

    ⚠️ **不是固定 MAX_DAEMON_LV(10)** —— `charManager.getMaxDaemonLv(favorLv)`
    读的是 `table_favor_upgrade[favorLv].max_daemon_lv`，前 6 级都是 0，
    也就是「好感度没到 7 级，守护灵一级都升不了」。
    """
    row = _upgrade().get(str(favor_lv)) or {}
    try:
        return int(row.get("max_daemon_lv") or 0)
    except (TypeError, ValueError):
        return 0


def daemon_max_lv_hard() -> int:
    """数值表的硬上限（`table_daemon_upgrade` 里 `exp == -1` 那一档）。"""
    best = 0
    for k, row in _daemon_upgrade().items():
        try:
            if int((row or {}).get("exp")) == -1:
                best = max(best, int(k))
        except (TypeError, ValueError):
            continue
    return best


def daemon_exp_to_next(lv) -> int | None:
    """守护灵从 lv 升到 lv+1 需要多少经验；满级回 None。"""
    row = _daemon_upgrade().get(str(lv)) or {}
    try:
        need = int(row.get("exp"))
    except (TypeError, ValueError):
        return None
    return None if need < 0 else need


def daemon_material_exp(char_key: str, soldier: dict) -> int:
    """一个军士当守护灵材料值多少经验。

    和 `CharCenter.calcDaemonUpgrade` 同一套判据：
      军士的 charKey == 目标角色 -> `same_char`
      军士的 type   == 目标 type -> `same_type`
      否则                       -> `default`
    品质查 `table_daemon_exp[品质]`（1 星全是 0）。
    """
    try:
        quality = int(soldier.get("quality") or 1)
    except (TypeError, ValueError):
        quality = 1
    row = _daemon_exp_table().get(str(quality)) or {}
    card = (items.table("table_soldier") or {}).get("card") or {}
    mat_char = str((card.get(str(soldier.get("key"))) or {}).get("ck") or "")
    tgt_type = (_daemon_cfg().get(str(char_key)) or {}).get("type")
    mat_type = (_daemon_cfg().get(mat_char) or {}).get("type")
    if mat_char and mat_char == str(char_key):
        field = "same_char"
    elif tgt_type is not None and mat_type is not None and mat_type == tgt_type:
        field = "same_type"
    else:
        field = "default"
    try:
        return int(row.get(field) or 0)
    except (TypeError, ValueError):
        return 0


def daemon_row_block(char_key: str, row: dict) -> dict:
    """`char.upgradedaemon` 响应里的 `daemon`：`updateDaemon()` 会
    `this._daemons[d.charKey] = d`，所以**整行**要带上。"""
    return dict(row)


def daemon_add_exp(row: dict, amount: int, favor_lv) -> int:
    """给守护灵加经验并结算升级，返回升了几级。

    上限取 `min(好感度允许的, 表的硬上限)`；到顶之后 `curExp` 夹回 0 ——
    满级那档 `exp == -1`，留着余额客户端算不出百分比（和好感度一个道理）。
    """
    amount = int(amount or 0)
    if amount <= 0:
        return 0
    top = min(daemon_max_lv(favor_lv) or 0, daemon_max_lv_hard())
    lv = int(row.get("lv") or 0)
    exp = int(row.get("curExp") or 0) + amount
    up = 0
    while lv < top:
        need = daemon_exp_to_next(lv)
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


# ---------------------------------------------------------------------------
# 宿舍事件（favorevent.*）
# ---------------------------------------------------------------------------
# ⚠️⚠️ **这一块的"什么时候解锁 / 读完给多少奖励"是推断的，实机要重点看。**
#
# 反汇编 + **实机量过**的（`FavorEventCenter` / `FavorEvent`）：
#
#   1. `FavorEventCenter._initData(data)` 会按 `table_favor_random_event` **整张表**
#      建出 `_favorEvents`：
#          this._eventsInTable = {};
#          this._favorEvents   = {};
#          for (var i in table_favor_random_event) {
#              this._eventsInTable[i] = table_favor_random_event[i];
#              this._eventsInTable[i].table_id = i;
#              this._favorEvents[i] = new FavorEvent(data[i] || {eventKey: i},
#                                                    this._eventsInTable[i]);
#          }
#      **所以登录块 `favorevent` 是会被读的** —— 服务端给了哪几条，那几条就是"真"的，
#      其余用 `{eventKey: i}` 占位（`_id` / `_status` / `_isToBeUnlocked` 全 undefined）。
#
#      ⚠️ 我第一版把最后那行的目标读成了参数 `data`，于是断言「登录块是 scratch 对象、
#      事件只能靠响应推」——**错的**。实机量（重启客户端走完整登录之后）：
#          Object.keys(dataManager.favorEventCenter._favorEvents).length  ->  184（整表）
#          其中 _id 有值的                                                ->  19（我们建的）
#      而 `184` 这个数只有在"写进 `_favorEvents`"时才可能出现（写进 data 的话
#      `_favorEvents` 只会剩下响应推的那几条）。
#      **教训：`setelem` 的目标分不清时别硬读字节码，去实机量一行长度。**（overview §6.8）
#
#   2. `cb4ResNewFavorEvent(res)`：`data` 是 **`{eventKey: 行}` 的 map**，
#      逐条 `this._favorEvents[行.eventKey] = new FavorEvent(行, table[eventKey])`。
#      这是「好感度涨到级、新解锁事件」的推送路径。
#
#   3. `submitFesRead(eventKeys)` 的请求体是 **裸数组**（不是 `{eventKeys: [...]}`）：
#          if (!_.isArray(eventKeys) || eventKeys.length == 0) return;
#          for (var i in eventKeys) if (!events[eventKeys[i]]) return;   // 有一个不认识就整个不发
#          server.request("favorevent.seteventsunlock", eventKeys, cb, true);
#      回调**只打日志、不读 res.data**，所以「读过了」这个状态得靠响应推回去。
#
# 推断的部分（没有字节码佐证，靠表结构反推）：
#
#   * **什么时候解锁**：`table_favor_random_event[k].favor_lv` 是该事件要求的
#     好感度等级（"1"/"3"/"5"...）。所以「好感度升到 N 级 → 解锁该角色 lv<=N 的事件」。
#   * **读完给奖励**：`reward_favor` 是纯服务端数值（客户端全库 0 命中），
#     所以服务端在读事件时把这份好感度加上。
#
# 推送策略：登录块带一份（**已实机确认会被 `_initData` 读进去**），
# 好感度涨了之后（`favor.usegift` / `favor.touchcharasst`）再带一份 `newFavorEvent`
# 推新增的那几条。**两条路都通**，登录那份不是冗余 —— 它是"已解锁事件"的唯一来源，
# 少了它玩家重登之后事件列表就空了。
#
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
    """登录包里 `favorevent` 那一块：`{eventKey: 行}`，只发已解锁的那几条。

    ⚠️ **这是"已解锁事件"的唯一来源**，别删。客户端 `FavorEventCenter._initData`
    会按 `table_favor_random_event` **整张表**建 `_favorEvents`，其中
    `_favorEvents[i] = new FavorEvent(data[i] || {eventKey: i}, table[i])`
    —— 登录块给了哪几条，那几条才有 `id` / `status` / `isToBeUnlocked`，
    其余是占位（见模块顶部「宿舍事件」那段，实测 184 条里 19 条是我们建的）。
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
