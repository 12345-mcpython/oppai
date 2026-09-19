"""好感度（宿舍 / favor.*）+ 宿舍事件（favorevent.*）自测。
**不需要模拟器，也不需要服务端在跑。**

    python script\\selftest_favor.py

在**进程内**直接调 handler（和 `selftest_game.py` 那种走 HTTP 的不是一回事，
两者互补：那个验协议和落盘，这个验数值和分支），存档指向临时目录，
不碰 `server/var/data/players.json`。

## 为什么要有它

好感度这套东西**大部分逻辑在服务端**（加多少经验、升不升级、回不回礼、
抚摸次数怎么回、宿舍事件什么时候解锁、读完给多少奖励），而客户端只拿
`favorValue` / `favorAdd` 去播动画 —— 也就是说算错了界面上多半只是「数字不对」，
不会报错，极难靠看画面发现。所以公式必须在这儿钉死。

⚠️ handler 内部是 `store.get_or_create_player()`，**每次都从盘上重新 load**。
所以「改内存里的 player」必须 `save_player` 之后再由 handler 重新读，
否则测出来的是假的。`call()` / `save()` / `reload_()` 三个小工具就是干这个的。
"""

from __future__ import annotations

import os
import sys
import tempfile

# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

sys.path.insert(0, _paths.SERVER)

from gamesrv import favor, items, store  # noqa: E402
from gamesrv.handlers import agent, favor as hfavor  # noqa: E402
from gamesrv.handlers import favorevent as hfavor_event  # noqa: E402
from gamesrv.handlers import load_all  # noqa: E402

load_all()

# 存档改到临时文件 —— 这个脚本会建号、发礼物、改好感度，不能写进真存档
store._path = os.path.join(tempfile.mkdtemp(prefix="favor-selftest-"), "players.json")

ACC = "t"
SESS = {"info": {"account": ACC}}
_ok = _fail = 0
player = None


def check(name, cond, extra=""):
    global _ok, _fail
    if cond:
        _ok += 1
        print("  ok   %s %s" % (name, extra))
    else:
        _fail += 1
        print("  FAIL %s %s" % (name, extra))


def reload_():
    global player
    player = store.get_or_create_player(ACC)
    return player


def call(fn, msg, rid):
    reload_()
    return fn(SESS, msg, rid)


def save():
    store.save_player(player)


def table_check():
    print("== 抽出来的表 ==")
    check("table_favor_upgrade 15 档", len(favor._upgrade()) == 15)
    check("max_lv = 15（靠 favor == -1 认）", favor.max_lv() == 15, "= %s" % favor.max_lv())
    check("interact_max = 5", favor.interact_max() == 5)
    check("interact_cooldown = 3600 秒", favor.interact_cooldown() == 3600)
    check("default_bg_key = 500001", favor.default_bg_key() == "500001")
    check("exp_to_next(1) = 500", favor.exp_to_next(1) == 500)
    check("exp_to_next(15) = None（满级）", favor.exp_to_next(15) is None)
    check("touch_add(1) = 22", favor.touch_add(1) == 22)
    check("礼物表 47 件", len(favor._gifts()) == 47, "= %d" % len(favor._gifts()))
    check("回礼表 63 个角色", len(favor._receive()) == 63)


def favor_rows_check():
    print("== 好感度行 ==")
    changed = favor.ensure_favors(player)
    keys = sorted(store.player_favors(player))
    check("首轮会补行", changed)
    check("角色数 = 19（18 初始军士 + hadf）", len(keys) == 19, "= %d %s" % (len(keys), keys[:6]))
    check("用 char_key 不是军士 key", "sasm" in keys and "sasm010104" not in keys)
    check("再调一次不动", favor.ensure_favors(player) is False)

    row = store.find_favor(player, "hadf")
    check("有 id（= 客户端 isAcquired）", isinstance(row.get("id"), int))
    check("lv 初始 1", row.get("lv") == 1)
    check("curBg = 默认背景", row.get("curBg") == "500001")
    check("字段名不带下划线（客户端自己加 _ 前缀）",
          set(row) == {"charKey", "id", "lv", "curExp", "curClothes",
                       "newClothes", "curBg", "newBgList", "descUnlockMark"},
          str(sorted(row)))


def login_block_check():
    print("== 登录块 ==")
    block = favor.favor_block(player)
    check("就三个键", set(block) == {"favors", "favorInteractChance", "favorInteractUpdateTimeSec"},
          str(sorted(block)))
    check("favors 是 map（不是数组）", isinstance(block["favors"], dict))
    check("favors[hadf].charKey", block["favors"]["hadf"].get("charKey") == "hadf")
    check("favors[hadf] 有 id", "id" in block["favors"]["hadf"])
    check("favorInteractChance = 5", block["favorInteractChance"] == 5)
    check("favorInteractUpdateTimeSec 是 epoch 秒", block["favorInteractUpdateTimeSec"] > 1_600_000_000)
    check("isNeedAsstEff / favorExpAdd 不再出现（死键）",
          "isNeedAsstEff" not in block and "favorExpAdd" not in block)

    res = call(agent.get_login_data, {}, 1)
    check("登录包 code = 200", res["code"] == 200)
    check("data.favor 就是那个 block",
          res["data"]["favor"]["favors"]["hadf"]["charKey"] == "hadf")
    check("player 顶层有 usedGiftCount / lastGiftTimeSec",
          "usedGiftCount" in res["data"]["player"] and "lastGiftTimeSec" in res["data"]["player"])


def gift_math_check():
    print("== 礼物数值 ==")
    # hadf 的 love_type 是 "5,6"；sasm 是 "1,2,7"
    check("gt5 对 hadf = 喜欢(2)", favor.preference_of("hadf", "5") == 2)
    check("gt1 对 hadf = 普通(3)", favor.preference_of("hadf", "1") == 3)
    check("gt3 对 sasm = 普通(3)", favor.preference_of("sasm", "3") == 3)
    check("生日 -> 1", favor.preference_of("hadf", "1", True) == 1)
    love = favor.gift_row("305101")
    cmn = favor.gift_row("301101")
    check("喜欢取 favor_love", favor.gift_value("hadf", love, 2) == love["fl"], str(love))
    check("普通取 favor", favor.gift_value("hadf", cmn, 3) == cmn["f"], str(cmn))
    check("生日档按喜欢算", favor.gift_value("hadf", cmn, 1) == cmn["fl"])
    check("非礼物回空", favor.gift_row("100001") == {})


def touch_check():
    print("== 抚摸 favor.touchcharasst ==")
    r = call(hfavor.touch_char_asst, {"charKey": "hadf"}, 2)
    check("code = 200", r["code"] == 200, str(r)[:160])
    d = r["data"]
    check("favorAdd = 22（touch_favor_add）", d.get("favorAdd") == 22, str(d.get("favorAdd")))
    check("charKey", d.get("charKey") == "hadf")
    check("returnItems = {}（空 map 不是 undefined）", d.get("returnItems") == {})
    check("favorInteractChance = 4", d.get("favorInteractChance") == 4, str(d.get("favorInteractChance")))
    check("favorInteractUpdateTimeSec 是 int", isinstance(d.get("favorInteractUpdateTimeSec"), int))
    check("birthdayAdd = 0", d.get("birthdayAdd") == 0)
    check("favor 块是整行", d["favor"]["hadf"]["curExp"] == 22, str(d["favor"]["hadf"]))
    reload_()
    check("落盘了", store.find_favor(player, "hadf")["curExp"] == 22)


def use_gift_check():
    print("== 送礼 favor.usegift ==")
    items.add_item(player, "305101", 3)   # 喜欢的礼物（gt5）
    items.add_item(player, "301101", 2)   # 普通礼物（gt1）
    save()
    reload_()
    before = store.find_favor(player, "hadf")["curExp"]

    r = call(hfavor.use_gift, {"charKey": "hadf", "items": {"305101": 2}}, 3)
    check("code = 200", r["code"] == 200, str(r)[:200])
    d = r["data"]
    expect = favor.gift_row("305101")["fl"] * 2
    check("favorValue = favor_love * 个数", d.get("favorValue") == expect,
          "%s vs %s" % (d.get("favorValue"), expect))
    check("preference = 2", d.get("preference") == 2)
    check("useGiftStatus 键 = 客户端 cb4UseGiftStatus 读的那两个",
          set(d.get("useGiftStatus", {})) == {"usedGiftCount", "lastGiftTimeSec"},
          str(d.get("useGiftStatus")))
    check("favor 整行回来且经验已加", d["favor"]["hadf"]["curExp"] == before + expect,
          "%s vs %s" % (d["favor"]["hadf"]["curExp"], before + expect))
    check("returnItems 是 map", isinstance(d.get("returnItems"), dict), str(d.get("returnItems")))
    reload_()
    check("扣了 2 件", items.count_of(player, "305101") == 1, str(items.count_of(player, "305101")))
    check("player.usedGiftCount = 1", player["usedGiftCount"] == 1)
    check("player.lastGiftTimeSec > 0", player["lastGiftTimeSec"] > 0)

    bad = call(hfavor.use_gift, {"charKey": "hadf", "items": {"100001": 1}}, 4)
    check("非礼物被拒", bad["code"] != 200, str(bad)[:120])
    bad = call(hfavor.use_gift, {"charKey": "hadf", "items": {"305101": 99}}, 5)
    check("礼物不够被拒", bad["code"] != 200, str(bad)[:120])
    reload_()
    check("整单校验：被拒时一件没扣", items.count_of(player, "305101") == 1)


def level_check():
    print("== 升级结算 ==")
    row = store.find_favor(player, "hadf")
    row["lv"], row["curExp"] = 1, 0
    up = favor.add_exp(row, 500 + 700 + 10)
    check("1 级 +1210 经验 -> 连升两级到 3 级余 10", up == 2 and row["lv"] == 3 and row["curExp"] == 10,
          "lv=%s exp=%s up=%s" % (row["lv"], row["curExp"], up))
    row["lv"], row["curExp"] = 14, 0
    favor.add_exp(row, 90_000)
    check("顶到 15 级", row["lv"] == 15, "lv=%s" % row["lv"])
    row["lv"], row["curExp"] = 15, 0
    favor.add_exp(row, 999_999)
    check("满级把 curExp 夹回 0（满级那档 favor = -1，留着算不出百分比）",
          row["lv"] == 15 and row["curExp"] == 0, "lv=%s exp=%s" % (row["lv"], row["curExp"]))
    row["lv"], row["curExp"] = 1, 0
    save()


def desc_check():
    print("== 已读标记 favor.setdescread ==")
    r = call(hfavor.set_desc_read, {"charKey": "hadf", "descUnlockMark": 4080}, 6)
    check("code = 200", r["code"] == 200)
    reload_()
    check("整份位掩码存下来了", store.find_favor(player, "hadf")["descUnlockMark"] == 4080)


def look_check():
    print("== 换装 / 换背景 ==")
    reload_()
    # ⚠️ 别用 key 最小的那件衣服 —— `ensure_default_looks` / `ensure_look_stock`
    # 会把衣柜发满，一件不剩。这里自己先摘掉两件来测「没拥有就拒绝」那条分支。
    all_clothes = [k for k, v in items.table("table_item").items() if v.get("t") == 40]
    all_bgs = [k for k, v in items.table("table_item").items() if v.get("t") == 50]
    not_owned = all_clothes[0]
    bg_key = all_bgs[0]
    items.items_of(player).pop(not_owned, None)
    items.items_of(player).pop(bg_key, None)
    save()
    reload_()
    check("已经摘掉一件衣服", items.count_of(player, not_owned) == 0)
    check("已经摘掉一张背景", items.count_of(player, bg_key) == 0)
    r = call(hfavor.set_clothes, {"charKey": "hadf", "itemKey": not_owned}, 7)
    check("背包里没有就换 -> 被拒", r["code"] != 200, str(r)[:120])
    items.add_item(player, not_owned, 1)
    items.add_item(player, bg_key, 1)
    save()
    r = call(hfavor.set_clothes, {"charKey": "hadf", "itemKey": not_owned}, 8)
    check("拥有后能换", r["code"] == 200, str(r)[:160])
    check("回调要的 favorValue 在", "favorValue" in r["data"])
    reload_()
    check("curClothes 落盘", store.find_favor(player, "hadf")["curClothes"] == not_owned)
    r = call(hfavor.set_clothes, {"charKey": "hadf", "itemKey": bg_key}, 9)
    check("拿背景当衣服 -> 被拒", r["code"] != 200, str(r)[:120])
    r = call(hfavor.set_cur_bg, {"charKey": "hadf", "itemKey": bg_key}, 10)
    check("能换背景", r["code"] == 200, str(r)[:160])
    reload_()
    check("curBg 落盘", store.find_favor(player, "hadf")["curBg"] == bg_key)


def return_item_check():
    print("== 回礼 ==")
    n_common = n_love = 0
    for _ in range(400):
        if favor.roll_return_items("hadf", 3):
            n_common += 1
        if favor.roll_return_items("hadf", 2):
            n_love += 1
    check("preference 3（普通）从不回礼 —— 表里那两行压根没有 return_item_* 字段",
          n_common == 0, str(n_common))
    check("preference 2（喜欢）会回礼", n_love > 0, str(n_love))


def default_look_check():
    print("== 默认衣服（抚摸能不能动的前提）==")
    check("hadf 的默认衣服 = 400001（军装）", favor.default_clothes("hadf") == "400001",
          "= %s" % favor.default_clothes("hadf"))
    check("sasm 也有默认衣服", bool(favor.default_clothes("sasm")),
          "= %s" % favor.default_clothes("sasm"))
    check("没衣服的角色回空串", favor.default_clothes("madflj") == "",
          "= %r" % favor.default_clothes("madflj"))
    d = favor.default_clothes("hadf")
    cfg = items.table("table_item").get(d) or {}
    check("默认衣服是 CLOTHES(40)", cfg.get("t") == 40)
    check("默认衣服的 replace_key == charKey（= 不替换立绘）", cfg.get("rk") == "hadf",
          "= %s" % cfg.get("rk"))

    reload_()
    # 把玩家弄成「旧存档」的样子：没有衣服、curClothes 是空串
    bag = items.items_of(player)
    bag.pop("400001", None)
    bag.pop("500001", None)
    store.find_favor(player, "hadf")["curClothes"] = ""
    save()
    reload_()
    check("补之前 curClothes 是空的", store.find_favor(player, "hadf")["curClothes"] == "")
    check("补之前背包里没有默认衣服", items.count_of(player, "400001") == 0)

    changed = favor.ensure_default_looks(player)
    check("补了（返回 True）", changed)
    check("curClothes 补成默认衣服", store.find_favor(player, "hadf")["curClothes"] == "400001",
          "= %s" % store.find_favor(player, "hadf")["curClothes"])
    check("默认衣服进背包了", items.count_of(player, "400001") == 1)
    check("默认背景也进背包了", items.count_of(player, "500001") == 1)
    check("每个有衣服的角色都补了",
          all(items.count_of(player, favor.default_clothes(k)) >= 1
              for k in store.player_favors(player) if favor.default_clothes(k)))
    check("再调一次不动（幂等）", favor.ensure_default_looks(player) is False)

    # 玩家自己换过的不该被覆盖
    reload_()
    store.find_favor(player, "hadf")["curClothes"] = "400002"
    items.add_item(player, "400002", 1)
    save()
    reload_()
    favor.ensure_default_looks(player)
    check("换过的衣服不会被默认衣服顶掉",
          store.find_favor(player, "hadf")["curClothes"] == "400002",
          "= %s" % store.find_favor(player, "hadf")["curClothes"])

    # 登录路径上会自动补
    reload_()
    bag = items.items_of(player)
    bag.pop("400001", None)
    store.find_favor(player, "hadf")["curClothes"] = ""
    save()
    res = call(agent.get_login_data, {}, 30)
    check("登录包里 hadf.curClothes 有值",
          res["data"]["favor"]["favors"]["hadf"]["curClothes"] == "400001",
          "= %s" % res["data"]["favor"]["favors"]["hadf"]["curClothes"])


def look_stock_check():
    print("== 衣柜 / 背景发满（私服取舍）==")
    reload_()
    all_looks = [k for k, v in items.table("table_item").items() if v.get("t") in (40, 50)]
    check("表里有 109 件衣服 + 54 张背景", len(all_looks) == 163, "= %d" % len(all_looks))
    # 把版本号抹掉，模拟老存档
    player.pop("lookStockVersion", None)
    for k in all_looks:
        items.items_of(player).pop(k, None)
    save()
    reload_()
    check("清空后一件都没有", sum(1 for k in all_looks if items.count_of(player, k)) == 0)
    check("发满返回 True", favor.ensure_look_stock(player) is True)
    have = [k for k in all_looks if items.count_of(player, k) >= 1]
    check("163 件全到手", len(have) == 163, "= %d" % len(have))
    check("版本号写上了", player.get("lookStockVersion") == favor.LOOK_STOCK_VERSION)
    check("再调一次不动（幂等）", favor.ensure_look_stock(player) is False)
    check("衣服不会被重复加量（limit_count=1）",
          all(items.count_of(player, k) == 1 for k in all_looks))
    # 换装用得到的那些
    mine = [k for k in all_looks
            if (items.table("table_item")[k].get("ck") or "") in store.player_favors(player)]
    check("玩家拥有的角色有衣服可换", len(mine) > 0, "%d 件" % len(mine))
    # 登录路径也会自动发
    reload_()
    player.pop("lookStockVersion", None)
    for k in all_looks:
        items.items_of(player).pop(k, None)
    save()
    res = call(agent.get_login_data, {}, 40)
    check("登录包 code=200", res["code"] == 200)
    reload_()
    check("登录后就发满了", all(items.count_of(player, k) >= 1 for k in all_looks))


def favor_event_check():
    print("== 宿舍事件 favorevent.* ==")
    table = favor._event_table()
    check("table_favor_event 184 条", len(table) == 184, "= %d" % len(table))
    check("410101 属于 hadf", favor.event_owner("410101") == "hadf")
    check("410101 要求好感 1 级", favor.event_need_lv("410101") == 1, "= %s" % favor.event_need_lv("410101"))
    check("410102 要求好感 3 级", favor.event_need_lv("410102") == 3, "= %s" % favor.event_need_lv("410102"))
    check("410101 奖励好感 100", favor.event_reward("410101") == 100, "= %s" % favor.event_reward("410101"))

    # 好感度全压回 1 级，看看 lv1 该解锁哪些
    reload_()
    for r in store.player_favors(player).values():
        r["lv"], r["curExp"] = 1, 0
    player["favorEvents"] = {}
    save()
    reload_()
    rows = favor.sync_events(player)
    keys = {r["eventKey"] for r in rows}
    check("1 级时解锁了事件", len(rows) > 0, "%d 条" % len(rows))
    check("410101（hadf lv1）在内", "410101" in keys)
    check("410102（hadf lv3）不在内", "410102" not in keys)
    check("解锁的都是 lv<=1 的", all(favor.event_need_lv(k) <= 1 for k in keys))
    check("再调一次不重复建", favor.sync_events(player) == [])

    row = store.find_favor_event(player, "410101")
    check("行字段对得上 FavorEvent._initData 读的那几个",
          set(row) == {"id", "eventKey", "lv", "status", "isToBeUnlocked", "createTimeSec"},
          str(sorted(row)))
    check("新建 = ACCEPTABLE(1)", row["status"] == store.FAVOR_EVENT_ACCEPTABLE)
    check("新建带红点（isToBeUnlocked=1）", row["isToBeUnlocked"] == 1)

    block = favor.new_event_block(rows)
    check("newFavorEvent 是 {eventKey: 行} 的 map", isinstance(block, dict) and "410101" in block)
    check("block 里每条都带 eventKey（客户端拿它当索引）",
          all(v.get("eventKey") == k for k, v in block.items()))

    # 登录响应里也要带一份（双保险，见 favor.py 的说明）
    res = call(agent.get_login_data, {}, 20)
    check("登录包 code=200", res["code"] == 200)
    check("登录包 data.favorevent 是 map", isinstance(res["data"].get("favorevent"), dict))

    for r in store.player_favors(player).values():
        r["lv"], r["curExp"] = 1, 0
    player["favorEvents"] = {}
    save()
    res = call(agent.get_login_data, {}, 21)
    check("登录响应里带了 newFavorEvent", isinstance(res["data"].get("newFavorEvent"), dict),
          str(sorted(res["data"].keys()))[:80])

    # seteventsunlock：裸数组
    reload_()
    before_exp = store.find_favor(player, "hadf")["curExp"]
    r = call(hfavor_event.set_events_unlock, ["410101"], 22)
    check("seteventsunlock code=200", r["code"] == 200, str(r)[:160])
    d = r["data"]
    check("响应里有 favorEvent 块（回调不读，但状态得推回去）", "favorEvent" in d, str(sorted(d)))
    check("favorEvent 里那条是已读", d["favorEvent"]["410101"]["isToBeUnlocked"] == 0)
    reload_()
    row = store.find_favor_event(player, "410101")
    check("落盘：status=ACCEPTED(2)", row["status"] == store.FAVOR_EVENT_ACCEPTED)
    check("落盘：红点没了", row["isToBeUnlocked"] == 0)
    after_exp = store.find_favor(player, "hadf")["curExp"]
    check("读完发了 reward_favor=100", after_exp == before_exp + 100,
          "%s -> %s" % (before_exp, after_exp))

    # 幂等：再读一次不再给奖励
    before_exp = store.find_favor(player, "hadf")["curExp"]
    r = call(hfavor_event.set_events_unlock, ["410101"], 23)
    check("重复读 code=200", r["code"] == 200)
    reload_()
    check("重复读不再给奖励", store.find_favor(player, "hadf")["curExp"] == before_exp)

    bad = call(hfavor_event.set_events_unlock, ["999999"], 24)
    check("不认识的事件 -> 整单拒绝", bad["code"] != 200, str(bad)[:120])
    bad = call(hfavor_event.set_events_unlock, [], 25)
    check("空数组 -> 拒绝", bad["code"] != 200, str(bad)[:120])

    # 好感度涨到 3 级 -> 410102 解锁
    reload_()
    row = store.find_favor(player, "hadf")
    row["lv"], row["curExp"] = 3, 0
    save()
    reload_()
    rows = favor.sync_events(player)
    check("升到 3 级解锁 410102", any(r["eventKey"] == "410102" for r in rows),
          str(sorted(r["eventKey"] for r in rows))[:80])

    # 送礼响应里应该带上新解锁的事件
    reload_()
    row = store.find_favor(player, "hadf")
    row["lv"], row["curExp"] = 1, 0
    player["favorEvents"] = {}
    save()
    items.add_item(player, "305101", 20)
    save()
    r = call(hfavor.use_gift, {"charKey": "hadf", "items": {"305101": 1}}, 26)
    check("送礼 code=200", r["code"] == 200, str(r)[:120])
    check("送礼后好感度涨了", r["data"]["favor"]["hadf"]["curExp"] > 0)
    check("送礼响应里带了新解锁事件（登录那条路不通时的兜底）",
          "newFavorEvent" in r["data"], str(sorted(r["data"]))[:90])


def interact_check():
    print("== 抚摸次数节流 ==")
    reload_()
    st = store.favor_interact(player)
    st["chance"], st["updateTimeSec"] = 0, int(store.time.time()) - 3600
    check("过 1 小时回 1 次", favor.sync_interact(player) == 1)
    st["chance"], st["updateTimeSec"] = 0, int(store.time.time()) - 3600 * 9
    check("过 9 小时也只回到上限 5", favor.sync_interact(player) == 5, str(st["chance"]))
    check("满的时候基准时间被推到 now（否则花掉一个会瞬间回满）",
          abs(st["updateTimeSec"] - int(store.time.time())) <= 2)
    st["chance"], st["updateTimeSec"] = 5, int(store.time.time()) - 3600 * 9
    check("花掉一次剩 4", favor.spend_interact(player) == 4)
    check("刚花完不会再被补满", favor.sync_interact(player) == 4, str(st["chance"]))
    st["chance"] = 0
    check("0 次时不再扣", favor.spend_interact(player) == 0)


def main() -> int:
    table_check()
    reload_()
    favor_rows_check()
    login_block_check()
    gift_math_check()
    touch_check()
    use_gift_check()
    level_check()
    desc_check()
    look_check()
    return_item_check()
    default_look_check()
    look_stock_check()
    favor_event_check()
    interact_check()
    print()
    print("通过 %d，失败 %d" % (_ok, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
