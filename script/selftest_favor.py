"""好感度（宿舍 / favor.*）自测。**不需要模拟器，也不需要服务端在跑。**

    python script\\selftest_favor.py

在**进程内**直接调 handler（和 `selftest_game.py` 那种走 HTTP 的不是一回事，
两者互补：那个验协议和落盘，这个验数值和分支），存档指向临时目录，
不碰 `server/var/data/players.json`。

## 为什么要有它

好感度这套东西**大部分逻辑在服务端**（加多少经验、升不升级、回不回礼、
抚摸次数怎么回），而客户端只拿 `favorValue` / `favorAdd` 去播动画 ——
也就是说算错了界面上多半只是「数字不对」，不会报错，极难靠看画面发现。
所以公式必须在这儿钉死。

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
    clothes = [k for k, v in items.table("table_item").items() if v.get("t") == 40]
    bgs = [k for k, v in items.table("table_item").items() if v.get("t") == 50]
    check("table_item 抽到了衣服", len(clothes) > 0, "%d 件" % len(clothes))
    r = call(hfavor.set_clothes, {"charKey": "hadf", "itemKey": clothes[0]}, 7)
    check("背包里没有就换 -> 被拒", r["code"] != 200, str(r)[:120])
    items.add_item(player, clothes[0], 1)
    items.add_item(player, bgs[0], 1)
    save()
    r = call(hfavor.set_clothes, {"charKey": "hadf", "itemKey": clothes[0]}, 8)
    check("拥有后能换", r["code"] == 200, str(r)[:160])
    check("回调要的 favorValue 在", "favorValue" in r["data"])
    reload_()
    check("curClothes 落盘", store.find_favor(player, "hadf")["curClothes"] == clothes[0])
    r = call(hfavor.set_clothes, {"charKey": "hadf", "itemKey": bgs[0]}, 9)
    check("拿背景当衣服 -> 被拒", r["code"] != 200, str(r)[:120])
    r = call(hfavor.set_cur_bg, {"charKey": "hadf", "itemKey": bgs[0]}, 10)
    check("能换背景", r["code"] == 200, str(r)[:160])
    reload_()
    check("curBg 落盘", store.find_favor(player, "hadf")["curBg"] == bgs[0])


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
    interact_check()
    print()
    print("通过 %d，失败 %d" % (_ok, _fail))
    return 1 if _fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
