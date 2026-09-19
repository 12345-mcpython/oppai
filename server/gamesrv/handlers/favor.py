"""favor.* —— 好感度（宿舍）。

5 条路由，全是反编译 `src/data/favor.jsc` 的 `Favor.submit*` / `touchCharAsst`
读出来的。共同点：**成功的返回码是 200，而且真正改状态靠响应里的 `favor` 块**
（`responseConfig.favor -> FavorCenter.cb4ResFavor`），请求成功本身只让客户端
接着走它自己的回调。

    favor.setdescread     {charKey, descUnlockMark}   响应被回调忽略（只清本地脏标记）
    favor.usegift         {charKey, items:{礼物key:个数}, isBirthday}
    favor.setclothes      {charKey, itemKey}          回调读 res.data.favorValue
    favor.setcurbg        {charKey, itemKey}          回调读 res.data.favorValue
    favor.touchcharasst   {charKey, isBirthday}

⚠️ 请求体里那个 `isBirthday` 是**客户端算的布尔**（`Favor._isFavorBirthday()`
拿 `table_char_desc[k].birthday` 跟服务器日期比）。生日是白给经验的，所以服务端
一律自己按同一张表重算一遍，不采信请求里的值（见 `favor.is_birthday`）。

⚠️ `favor.setclothes` / `favor.setcurbg` 的 `favorValue`：客户端把它喂给
`playChangeClothesCb` / `replaceBgCb` 去播「好感度 +N」。**原版给多少没有依据**
（没有换装表，`table_constant` 里也没有对应项），这里给 0 = 换装不加好感度。
要改成加经验，只要在 `_set_look` 里给 `gain` 一个值。

尚未实现：`favorevent.seteventsunlock`（宿舍事件，`FavorEventCenter`）。
登录块里 `favorevent` 目前还是空桩，够用 —— 没有事件就没有红点。
"""

from __future__ import annotations

import time

from .. import config, favor, items, logx, store
from ..gameproto import CODE_OK
from . import route

log = logx.get("handler.favor")

# 失败码。客户端回调都是 `if (err || res.code != 200) { failCb(err, res) }`，
# 非 200 时只走失败分支（`FavorLayer._touchCharAsst/ok<` 里是 `util.print_r`）。
FAIL = 1


def _ctx(session: dict):
    account = (session.get("info") or {}).get("account") or config.DEFAULT_ACCOUNT
    return account, store.get_or_create_player(account)


def _row(player: dict, msg: dict):
    """按 msg.charKey 取好感度行。取不到回 (None, 错误响应)。"""
    char_key = str((msg or {}).get("charKey") or "")
    if not char_key:
        return None, {"code": FAIL, "msg": "缺少 charKey", "data": {}}
    row = store.find_favor(player, char_key)
    if row is None:
        log.info("请求了没有好感度行的角色 %s（msg=%s），先补一份", char_key, msg)
        favor.ensure_favors(player)
        row = store.find_favor(player, char_key)
    if row is None:
        return None, {"code": FAIL, "msg": "角色不存在", "data": {}}
    return row, None


def _ok(**data) -> dict:
    return {"code": CODE_OK, "msg": "", "data": data}


# ---------------------------------------------------------------------------
# 已读标记
# ---------------------------------------------------------------------------
@route("favor.setdescread")
def set_desc_read(session: dict, msg: dict, req_id):
    """把角色的资料（年龄 / 三围 / 喜欢的食物…）标记成「已读」。

    客户端 `Favor.submitDescUnlockMark()` 发的是**整个位掩码**（不是单个 bit）：

        var param = {charKey: this._charKey, descUnlockMark: this._descUnlockMark};
        server.request("favor.setdescread", param, function (err, res) {
            if (err || res.code != 200) cc.log(JSON.stringify(err || res));
            self._isDescUnlockMarkChanged = false;      // ← 响应体压根没读
        });

    位序是 `FAVOR_DESC_ATTR_SORTED`（实机取过，16 位）：
        age constellation birthday bloodType statureAndWeight cup interest dream
        like nauseous love cvJp desc1 desc2 desc1Title desc2Title
    其中 `FAVOR_DESC_NEED_LOCK`（需要在界面点开才算「已读」的 9 项）是
        statureAndWeight cup interest dream like nauseous love desc1 desc2
    —— 这 9 位由客户端自己算好再整份发上来，服务端**只负责存**，不重算。
    """
    _, player = _ctx(session)
    row, err = _row(player, msg)
    if row is None:
        return err
    try:
        mark = int((msg or {}).get("descUnlockMark") or 0)
    except (TypeError, ValueError):
        mark = 0
    row["descUnlockMark"] = mark
    store.save_player(player)
    return _ok()


# ---------------------------------------------------------------------------
# 送礼物
# ---------------------------------------------------------------------------
@route("favor.usegift")
def use_gift(session: dict, msg: dict, req_id):
    """`Favor.submitUseGift(items, succCb, failCb)`。

        var param = {charKey: this._charKey, items: items, isBirthday: this._isBirthday};
        server.request("favor.usegift", param, function (err, res) {
            if (err || res.code != 200) { failCb(err, res); }
            else { succCb(res.data); }
        }, false);

    `items` 是 `{礼物key: 个数}`（礼物面板 `giveAwayGift` 里 `items[key] = count`）。

    成功回调（`FavorGiftPanelWrapper/giveAwayGift/<`）读的字段：

        data.preference    偏好档位，喂 `favorManager.getPreferenceWithSendGift`
        data.favorValue    这次加的好感度（喂 `CharFavorUpLayer.pop` 播动画）
        data.birthdayAdd   生日额外那一份
        data.returnItems   回礼 `{道具key: 个数}`（喂 `popupRewardWithItems(..., RETURN_GIFT)`）
        data.useGiftStatus -> Player.cb4UseGiftStatus，读 usedGiftCount / lastGiftTimeSec
        data.favor         整行推给 FavorCenter（`cb4ResFavor`）
    """
    _, player = _ctx(session)
    row, err = _row(player, msg)
    if row is None:
        return err

    char_key = str(row.get("charKey"))
    raw_items = (msg or {}).get("items") or {}
    if not isinstance(raw_items, dict) or not raw_items:
        return {"code": FAIL, "msg": "没有选礼物", "data": {}}

    # 先整单校验再扣，避免「扣了第一件、第二件不合法」这种半截状态。
    plan = []
    bag = items.items_of(player)
    for item_key, count in raw_items.items():
        gift = favor.gift_row(item_key)
        if not gift:
            return {"code": FAIL, "msg": "不是礼物：%s" % item_key, "data": {}}
        try:
            count = int(count)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            continue
        if int(bag.get(str(item_key)) or 0) < count:
            return {"code": FAIL, "msg": "礼物不够：%s" % item_key, "data": {}}
        plan.append((str(item_key), count, gift))
    if not plan:
        return {"code": FAIL, "msg": "礼物数量不对", "data": {}}

    birthday = favor.is_birthday(char_key)
    total = 0
    best_pref = 3
    unit_pref = 3
    for item_key, count, gift in plan:
        # 偏好按「礼物类型」算，同一件礼物给几份就是几倍
        unit_pref = favor.preference_of(char_key, gift.get("gt"), birthday)
        # 1(生日) > 2(喜欢) > 3(普通) > 4(讨厌)：多件礼物时按最好的那档决定回礼和台词
        if unit_pref < best_pref:
            best_pref = unit_pref
        total += favor.gift_value(char_key, gift, unit_pref) * count

    for item_key, count, _gift in plan:
        items.sub_item(player, item_key, count)

    up = favor.add_exp(row, total)
    bonus = favor.birthday_bonus(total) if birthday else 0
    if bonus:
        favor.add_exp(row, bonus)

    st = store.favor_gift_state(player)
    st["usedCount"] += 1
    st["lastTimeSec"] = int(time.time())
    player["usedGiftCount"] = st["usedCount"]
    player["lastGiftTimeSec"] = st["lastTimeSec"]
    store.save_player(player)

    log.info("favor.usegift %s 礼物 %s 好感 +%d%s（升 %d 级 -> lv%d）",
             char_key, [(k, c) for k, c, _ in plan], total,
             "（生日 +%d）" % bonus if bonus else "", up, row.get("lv"))

    return _ok(
        charKey=char_key,
        preference=unit_pref,
        favorValue=total,
        birthdayAdd=bonus,
        returnItems=favor.roll_return_items(char_key, best_pref),
        useGiftStatus={"usedGiftCount": st["usedCount"], "lastGiftTimeSec": st["lastTimeSec"]},
        favor=favor.row_block(char_key, row),
    )


# ---------------------------------------------------------------------------
# 换衣服 / 换背景
# ---------------------------------------------------------------------------
def _set_look(player: dict, msg: dict, field: str, item_type: int, label: str):
    """`Favor.submitSetClothes` / `submitSetCurBg` 的公共实现。

    ⚠️ **道具必须在背包里**才允许换。客户的换装列表来自
    `dataManager.bag.getClothes(charKey)` / `getBgItem(...)`，也就是背包里有的才列出来；
    服务端不校验的话，随便编一个 itemKey 就能换上去。
    （`ITEM_TYPE`：CLOTHES = 40，BG_IMG = 50。）

    换上去之后要把这一件从 `newClothes` / `newBgList` 里摘掉 —— 那两个是
    「新到还没看过」的集合，客户端 `getHaveNewClothes()` 靠它出红点：
        for (var k in this._newClothes) { return true; }
    """
    row, err = _row(player, msg)
    if row is None:
        return err
    char_key = str(row.get("charKey"))
    item_key = str((msg or {}).get("itemKey") or "")
    if not item_key:
        return {"code": FAIL, "msg": "缺少 itemKey", "data": {}}

    cfg = items.table("table_item").get(item_key) or {}
    try:
        kind = int(cfg.get("t", cfg.get("type")) or 0)
    except (TypeError, ValueError):
        kind = 0
    if kind != item_type:
        return {"code": FAIL, "msg": "%s 不是%s" % (item_key, label), "data": {}}
    if items.count_of(player, item_key) <= 0:
        return {"code": FAIL, "msg": "还没有这件%s" % label, "data": {}}

    row[field] = item_key
    new_list = row.get("newClothes" if field == "curClothes" else "newBgList")
    if not isinstance(new_list, dict):
        new_list = {}
    new_list.pop(item_key, None)
    if field == "curClothes":
        row["newClothes"] = new_list
    else:
        row["newBgList"] = new_list

    store.save_player(player)
    log.info("favor %s %s -> %s", field, char_key, item_key)
    return _ok(charKey=char_key, favorValue=0, favor=favor.row_block(char_key, row))


@route("favor.setclothes")
def set_clothes(session: dict, msg: dict, req_id):
    """换上衣服。`Favor.submitSetClothes(itemKey, succCb, failCb)` → `{charKey, itemKey}`。"""
    _, player = _ctx(session)
    return _set_look(player, msg, "curClothes", 40, "衣服")


@route("favor.setcurbg")
def set_cur_bg(session: dict, msg: dict, req_id):
    """换宿舍背景。`Favor.submitSetCurBg(itemKey, succCb, failCb)` → `{charKey, itemKey}`。"""
    _, player = _ctx(session)
    return _set_look(player, msg, "curBg", 50, "背景")


# ---------------------------------------------------------------------------
# 抚摸 / 互动
# ---------------------------------------------------------------------------
@route("favor.touchcharasst")
def touch_char_asst(session: dict, msg: dict, req_id):
    """`Favor.touchCharAsst(data, succCb, failCb)`。

        var param = {charKey: this._charKey, isBirthday: this._isBirthday};
        server.request("favor.touchcharasst", param, function (err, res) {
            if (err || res.code != 200) { failCb(err, res); }
            else { dataManager.favorCenter.setFavorInteract(res.data); succCb(res.data); }
        }, false);

    注意 `setFavorInteract(res.data)` 读的是 `favorInteractChance` /
    `favorInteractUpdateTimeSec` —— **这两个字段必须在响应里**，否则客户端的
    互动次数会停在旧值（它自己只会做加法、不推进基准时间）。

    成功回调（`FavorLayer._touchCharAsst/ok<`）另外读：
        data.favorAdd      这次加的好感度
        data.birthdayAdd   生日额外那一份
        data.charKey       喂 `CharFavorUpLayer.pop`
        data.returnItems   回礼（恒为空 map，见 favor.py 的说明）
        data.favor         整行推给 FavorCenter
    """
    _, player = _ctx(session)
    row, err = _row(player, msg)
    if row is None:
        return err
    char_key = str(row.get("charKey"))

    if favor.sync_interact(player) < 1:
        store.save_player(player)
        return {"code": FAIL, "msg": "互动次数不够", "data": {}}
    left = favor.spend_interact(player)

    add = favor.touch_add(int(row.get("lv") or 1))
    up = favor.add_exp(row, add)
    birthday = favor.is_birthday(char_key)
    bonus = favor.birthday_bonus(add) if birthday else 0
    if bonus:
        favor.add_exp(row, bonus)

    store.save_player(player)
    st = store.favor_interact(player)
    log.info("favor.touchcharasst %s 好感 +%d%s（升 %d 级 -> lv%s，剩 %d 次）",
             char_key, add, "（生日 +%d）" % bonus if bonus else "", up, row.get("lv"), left)

    return _ok(
        charKey=char_key,
        favorAdd=add,
        birthdayAdd=bonus,
        returnItems={},
        favorInteractChance=st["chance"],
        favorInteractUpdateTimeSec=st["updateTimeSec"],
        favor=favor.row_block(char_key, row),
    )
