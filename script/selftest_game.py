r"""不开游戏也能自测业务协议：自己按客户端格式打包一个请求发过去。

    python tools\selftest_game.py

用的是 DH 单位元当共享密钥（和服务端一致），
所以不需要客户端参与就能验证 加解密 + 路由 + code=200。

⚠️ **这个脚本会改真存档**（它跑的就是默认账号）：
   * 军士链路会**吃掉 2 个军士当材料**，但结尾有 `restore_roster()` 把它们补回来
     （`store.replenish_soldiers` 只补缺的、不动等级），所以可以反复跑。
     万一中途崩了留下缺口，再跑一次就补上了。
   * 好感度那段只读 + 花一次抚摸次数，不破坏数据。
"""

from __future__ import annotations

import base64
import copy
import json
import os
import sys
import urllib.request

# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gamesrv import config  # noqa: E402
from gamesrv.crypto.des import des_decode, des_encode  # noqa: E402



SECRET = bytes.fromhex("0100000000000000")  # DH 单位元


def call(route: str, msg: dict, req_id: int = 1):
    plain = json.dumps({"route": route, "msg": msg, "reqId": req_id},
                       ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = base64.b64encode(des_encode(SECRET, plain))
    url = f"http://127.0.0.1:{config.GAME_PORT}/"
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    text = raw.decode("utf-8", "replace")
    if '"code":' in text:
        return json.loads(text)
    return json.loads(des_decode(SECRET, base64.b64decode(raw)).decode("utf-8"))


def soldier_flow(ok: bool) -> bool:
    """军士「培养」链路：登录拿到名单 -> 喂两个材料 -> 再登录确认结果落盘。

    这条链路要的是**跨请求的持久化**（升级结果不能被下一次登录冲掉、
    被吃掉的材料不能复活），所以光看单个请求回 200 不够，得来回走一遍。
    """
    login = call("agent.getlogindata", {}, 100)
    if login.get("code") != 200:
        print("  BAD 军士链路：登录失败")
        return False
    soldiers = ((login.get("data") or {}).get("char") or {}).get("soldiers") or []
    print(f"  ..  登录拿到 {len(soldiers)} 个军士，第一个 = "
          f"{soldiers[0].get('key') if soldiers else None} lv={soldiers[0].get('lv') if soldiers else None}")
    if len(soldiers) < 3:
        print("  BAD 军士链路：军士太少，没法测培养")
        return False

    # ⚠️ **别直接拿 soldiers[0]**：这个脚本每跑一次就吃掉 2 个军士当材料，
    # 跑几轮之后第一个军士早就顶到 1 星的等级上限（`table_soldier_lv_limit[1]` = 30），
    # 于是「升级没落盘」这条会误报。挑等级最低的那个。
    from gamesrv import soldier as soldier_calc

    target = min(soldiers, key=lambda s: int(s.get("lv") or 1))
    cap = soldier_calc.max_lv(int(target.get("quality") or 1), int(target.get("star") or 1))
    mats = [s["id"] for s in soldiers if s["id"] != target["id"]][:2]
    if not mats:
        print("  BAD 军士链路：找不出材料")
        return False

    res = call("char.upgradesoldierlv",
               {"id": target["id"], "key": target["key"], "materials": mats}, 101)
    code = res.get("code")
    new_lv = ((res.get("data") or {}).get("soldier") or {}).get("lv")
    flag = "OK " if code == 200 else "BAD"
    print(f"  {flag} char.upgradesoldierlv         code={code}  "
          f"lv {target.get('lv')} -> {new_lv}（上限 {cap}）")
    if code != 200:
        return False
    if target.get("lv") == cap:
        print(f"  ..  目标已经在 {cap} 级上限，没得升 —— 跳过落盘核对")
        return ok

    after = call("agent.getlogindata", {}, 102)
    got = ((after.get("data") or {}).get("char") or {}).get("soldiers") or []
    ids = {s.get("id") for s in got}
    moved = next((s for s in got if s.get("id") == target["id"]), None)
    if moved is None or moved.get("lv") == target.get("lv"):
        print(f"  BAD 军士链路：升级没落盘（重登后 lv={moved and moved.get('lv')}）")
        return False
    if ids & set(mats):
        print(f"  BAD 军士链路：材料复活了 {sorted(ids & set(mats))}")
        return False
    print(f"  OK  重登确认：lv={moved.get('lv')} curExp={moved.get('curExp')}，"
          f"材料已消耗（{len(soldiers)} -> {len(got)} 个）")
    return ok


def roster_check(ok: bool) -> bool:
    """初始军士名单必须全是 `card_type == 1` 的自军卡。

    这条纯粹是服务端自己的不变量，不用开游戏就能查，但一旦破了现象很隐蔽：
    敌方单位（card_type 2）没有 `base_cost` 字段，客户端
    `CharCenter.calcSoldierUpgrade` 的 `gainExp` 会算成 NaN，
    表现是「培养点一下就顶到等级上限」，而且材料列表里也选不出它们。
    见 docs/protocol.md §11.2。
    """
    from gamesrv import soldier, store

    bad = []
    for key, positioning, quality in store.SOLDIER_KEYS:
        ctype = soldier.card_type(key)
        if ctype != soldier.CARD_TYPE_TEAMMATE:
            bad.append(f"{key}(card_type={ctype})")
    if bad:
        print(f"  BAD 初始名单里有非自军卡：{bad}")
        return False
    if len(store.SOLDIER_KEYS) != 18:
        print(f"  BAD 初始名单应该是 18 个，现在是 {len(store.SOLDIER_KEYS)}")
        return False
    positions = sorted({p for _, p, _ in store.SOLDIER_KEYS})
    if positions != [1, 2, 3]:
        print(f"  BAD 初始名单没覆盖三个站位：{positions}")
        return False
    print(f"  OK  初始名单 {len(store.SOLDIER_KEYS)} 个，全是自军卡，站位 {positions}")
    return ok


def replenish_check(ok: bool) -> bool:
    """军士名单「补齐」必须**保住已有的等级**。

    背景：`selftest_game.py` 自己的军士链路每跑一次吃掉 2 个军士当材料，
    把默认账号从 18 个啃到 4 个。而当时的恢复手段是 `ROSTER_VERSION` 一变就
    `player["soldiers"] = new_soldiers()` —— **整个重发，练过的等级全归零**。
    改成 `store.replenish_soldiers()`（缺的补、已有的原样留）之后，
    这条就是它的回归测试：纯内存，不需要服务端。
    """
    from gamesrv import store

    keys = [k for k, _p, _q in store.SOLDIER_KEYS]
    fake = {
        "account": "__replenish_test__",
        "rosterVersion": 0,
        "soldiers": [
            store.new_soldier(14, keys[13], 3, 4, lv=28, star=3),
            store.new_soldier(16, keys[15], 3, 4),
            # 已经不在名单里的旧 key（早期误收的敌方单位）应该被删掉
            store.new_soldier(99, "sfog", 1, 2),
            # 同一个 key 出现两次 —— 只留一个
            store.new_soldier(98, keys[15], 3, 4),
        ],
        "teams": [{"soldierKeys": [14, 99], "soldierCount": 2}],
    }
    added, dropped = store.replenish_soldiers(fake)
    rows = fake["soldiers"]
    got = [r.get("key") for r in rows]
    if len(rows) != len(keys):
        print(f"  BAD 补齐后应该是 {len(keys)} 个，实际 {len(rows)} 个")
        return False
    if sorted(got) != sorted(keys):
        print("  BAD 补齐后的 key 集合和 SOLDIER_KEYS 对不上")
        return False
    if len(set(got)) != len(got):
        print(f"  BAD 补齐后还有重复 key：{got}")
        return False
    keep = next((r for r in rows if r.get("key") == keys[13]), None)
    if not keep or keep.get("lv") != 28 or keep.get("star") != 3:
        print(f"  BAD 已有的军士等级被重置了：{keep}")
        return False
    if any(r.get("key") == "sfog" for r in rows):
        print("  BAD 旧 key 没被删掉")
        return False
    ids = [r.get("id") for r in rows]
    if len(set(ids)) != len(ids):
        print(f"  BAD 补齐后 id 有重复：{ids}")
        return False
    if 99 in (fake["teams"][0].get("soldierKeys") or []):
        print("  BAD 队伍里还留着被删掉的军士 id")
        return False
    print(f"  OK  军士补齐：补 {added} 个、删 {dropped} 个 -> {len(rows)} 个，"
          f"已有的 {keys[13]} 仍是 lv{keep.get('lv')}")
    return ok


def restore_roster() -> None:
    """把军士链路吃掉的军士补回来。

    `store.replenish_soldiers()` **只补缺的、不动已有的**（等级/星级/技能都留着），
    所以这一步是安全的，也是这个脚本能反复跑的前提 ——
    以前没有它，跑 7 轮就把默认账号从 18 个军士啃到 4 个。
    """
    from gamesrv import config, store

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    added, dropped = store.replenish_soldiers(player)
    if added or dropped:
        store.save_player(player)
        print(f"  ..  收尾：把测试吃掉的军士补回来 {added} 个"
              f"（现在 {len(player.get('soldiers') or [])} 个，已有等级没动）")


_TEAM_SNAPSHOT = None


def snapshot_teams() -> None:
    """跑「会吃军士」的用例之前，先把各队伍的上阵名单记下来。

    ⚠️ 军士升级链路会**真的把材料（军士）吃掉**，而 `handlers/char.py` 会顺手把被吃的
    军士从队伍里摘掉；`restore_roster()` 补回来的是**新的 id**，原来那支队就永远空了。
    实测：跑几轮之后玩家的编成一直是空的（用户报的「配对队伍一直消失」就是这个 +
    下面 `player.updateteams` 那个 id 不匹配，两个原因叠在一起）。

    所以这里记一份，收尾时按**还能找到的 id** 放回去。
    """
    global _TEAM_SNAPSHOT
    from gamesrv import config, store

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    _TEAM_SNAPSHOT = [(t.get("index"), [int(k) for k in (t.get("soldierKeys") or [])])
                      for t in (player.get("teams") or [])]


def restore_teams() -> None:
    """把 `snapshot_teams()` 记下的编成放回去（只放回还存在的军士）。"""
    global _TEAM_SNAPSHOT
    if not _TEAM_SNAPSHOT:
        return
    from gamesrv import config, store

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    live = {int(s["id"]) for s in (player.get("soldiers") or [])}
    teams = player.get("teams") or []
    changed = False
    for index, keys in _TEAM_SNAPSHOT:
        keep = [k for k in keys if k in live]
        for team in teams:
            if team.get("index") != index:
                continue
            if list(team.get("soldierKeys") or []) != keep:
                team["soldierKeys"] = keep
                team["soldierCount"] = len(keep)
                changed = True
    _TEAM_SNAPSHOT = None
    if changed:
        store.save_player(player)
        print("  ..  收尾：把测试摘掉的编成放回去")


def favor_check(ok: bool) -> bool:
    """好感度（宿舍）登录块的形状。

    这条只查「客户端构造 `FavorCenter` 时会不会炸 / 会不会全空」，不碰数值
    （数值在 `selftest_favor.py` 里，那个不用起服务端）：

    * `data.favor.favors` 必须是 **map**，key 是 charKey ——
      客户端 `FavorCenter._initData` 是 `favorsData[charKey]`，给数组就是全「未获得」
    * 每个有 `id` 的行才算「已获得」（`Favor._isAcquired = typeof id != "undefined"`）
    * `favorInteractChance` / `favorInteractUpdateTimeSec` 缺一个，互动次数就是 NaN
    """
    login = call("agent.getlogindata", {}, 110)
    block = ((login.get("data") or {}).get("favor")) or {}
    if not isinstance(block.get("favors"), dict):
        print(f"  BAD favor.favors 不是 map：{type(block.get('favors'))}")
        return False
    rows = block["favors"]
    if not rows:
        print("  BAD favor.favors 是空的 —— 一个角色都没获得")
        return False
    acquired = [k for k, v in rows.items() if isinstance(v, dict) and "id" in v]
    if not acquired:
        print("  BAD favor.favors 里没有任何一行带 id（客户端会全部当成未获得）")
        return False
    for key, row in rows.items():
        if not isinstance(row, dict) or row.get("charKey") != key:
            print(f"  BAD favor.favors[{key}] 的 charKey 对不上：{row!r}")
            return False
    missing = [k for k in ("favorInteractChance", "favorInteractUpdateTimeSec") if k not in block]
    if missing:
        print(f"  BAD favor 块缺字段 {missing}（互动次数会变 NaN）")
        return False
    dead = [k for k in ("isNeedAsstEff", "favorExpAdd") if k in block]
    if dead:
        print(f"  BAD favor 块里还有死键 {dead}（客户端不从 data 读，只会误导）")
        return False
    fe = (login.get("data") or {}).get("favorevent")
    if not isinstance(fe, dict):
        print(f"  BAD data.favorevent 不是 map：{type(fe)}"
              f"（FavorEventCenter._initData 要往里写 data[eventKey]）")
        return False
    ne = (login.get("data") or {}).get("newFavorEvent")
    if ne is not None and not isinstance(ne, dict):
        print(f"  BAD data.newFavorEvent 不是 map：{type(ne)}")
        return False
    r = call("favor.setdescread", {"charKey": acquired[0], "descUnlockMark": 0}, 111)
    if r.get("code") != 200:
        print(f"  BAD favor.setdescread code={r.get('code')} {r}")
        return False
    # 礼物存量：宿舍送礼面板列的是 `bag.getGifts()`（table_item.type == 30），
    # 一件都没有的话面板是空的、玩法等于没做。建号/老存档补货见 store.top_up_gifts()。
    from gamesrv import favor as favor_mod
    from gamesrv import store as store_mod

    bag = (login.get("data") or {}).get("item") or {}
    gifts = list(favor_mod._gifts())
    have = [k for k in gifts if int(bag.get(k) or 0) > 0]
    if len(have) != len(gifts):
        print(f"  BAD 礼物没发齐：{len(have)}/{len(gifts)} 种在背包里"
              f"（store.GIFT_STOCK={store_mod.GIFT_STOCK}）")
        return False
    r = call("favor.touchcharasst", {"charKey": acquired[0]}, 112)
    data = r.get("data") or {}
    if r.get("code") != 200:
        # 次数用完是正常业务拒绝，不算失败
        print(f"  ..  favor.touchcharasst 被拒（多半是互动次数用完了）：{r.get('msg')}")
    elif "favorInteractChance" not in data or "favorAdd" not in data or "favor" not in data:
        print(f"  BAD favor.touchcharasst 响应缺字段：{sorted(data)}")
        return False
    print(f"  OK  好感度登录块：{len(rows)} 个角色（{len(acquired)} 个已获得），"
          f"互动次数 {block['favorInteractChance']}，礼物 {len(have)} 种 × "
          f"{store_mod.GIFT_STOCK}")
    return ok


def team_save_check(ok: bool) -> bool:
    """编成保存（`player.updateteams`）—— 「编好的队伍一直消失」的回归测试。

    ⚠️ 客户端发的是 `{teams: [{id: "0", team: {heroKey, mechaKey, soldierKeys: [...]}}]}`
    —— **id 是服务端 teams 数组的 0 起下标**（反汇编 `Player.initTeams`：
    `new Team(this._teams[i], this._character, i)`，第三个参数就是 `for..in` 的 key）。
    服务端如果按 1 起 id 找队伍，整单会被**静默跳过**，症状就是重登后队伍又是空的。

    会临时改 0 号队再**原样还回去**（不把测试数据留给玩家）。
    """
    login = call("agent.getlogindata", {}, 119)
    pl = (login.get("data") or {}).get("player") or {}
    teams = pl.get("teams") or []
    if len(teams) < 2:
        print(f"  BAD 登录块的队伍数不对：{len(teams)}")
        return False
    if [t.get("index") for t in teams] != list(range(len(teams))):
        print(f"  BAD 队伍 index 不是 0..N-1：{[t.get('index') for t in teams]}")
        return False
    if [t.get("id") for t in teams] != list(range(len(teams))):
        print(f"  BAD 队伍 id 不是 0..N-1（客户端就是按下标认的）："
              f"{[t.get('id') for t in teams]}")
        return False

    before = dict(teams[0])
    ids = [int(s["id"]) for s in (pl.get("soldiers") or [])][:2]
    if len(ids) < 2:
        print(f"  BAD 军士不够两个：{len(ids)}")
        return False

    def patch(soldier_keys):
        body = {"heroKey": before.get("heroKey"), "mechaKey": before.get("mechaKey"),
                "soldierKeys": soldier_keys}
        return call("player.updateteams", {"teams": [{"id": "0", "team": body}]}, 120)

    res = patch(ids)
    if res.get("code") != 200:
        print(f"  BAD player.updateteams code={res.get('code')}")
        return False
    login2 = call("agent.getlogindata", {}, 121)
    teams2 = ((login2.get("data") or {}).get("player") or {}).get("teams") or [{}]
    got = sorted(int(x) for x in (teams2[0].get("soldierKeys") or []))
    if got != sorted(ids):
        print(f"  BAD 队伍没存下来：发了 {ids}，重登后是 {got}"
              f"（服务端可能是按 1 起 id 找队伍 → 整单被跳过）")
        return False
    if len(teams2) > 1 and (teams2[1].get("soldierKeys") or []):
        print(f"  BAD 写错队伍了：1 号队被写成 {teams2[1].get('soldierKeys')}")
        return False

    patch(before.get("soldierKeys") or [])       # 还原
    print(f"  OK  编成保存：id=\"0\" 的两名军士 {ids} 落盘、重登仍在，且没写错队伍")
    return ok


def subarea_check(ok: bool) -> bool:
    """分区关卡（`INSTANCE_TYPE.SUBAREA`）：登录块 + `instance.getsubarealevel`。

    客户端 `SubareaChapterMapLayer` 靠这两处数据点亮分区地图：

    * 登录块 `data.instance.subareaLevels`（`Instance.ctor` 读）
    * `instance.getsubarealevel` 的 **`data` 本身就是那份 map**（不是再包一层，
      反汇编 `Instance.updateSubareaLevel/<`：`that._subareaLevels = data.data`）

    条目形状 `{levelId, challengeTimes}`；**故意不给** `deadline` / `limitDay` /
    `limitTime` —— 客户端的 `isSubareaLevelOpen` 在这三个全缺时直接放行（永久开放），
    这样就不用编造原版的开放时间表。
    """
    login = call("agent.getlogindata", {}, 116)
    inst = (login.get("data") or {}).get("instance") or {}
    sub = inst.get("subareaLevels")
    if not isinstance(sub, dict) or not sub:
        print(f"  BAD 登录块 subareaLevels 不是非空 map：{str(sub)[:80]}")
        return False
    bad = [k for k, v in sub.items()
           if not isinstance(v, dict) or v.get("levelId") != k or "challengeTimes" not in v]
    if bad:
        print(f"  BAD subareaLevels 条目形状不对：{bad[:3]}")
        return False
    alien = [k for k in sub if not k.startswith("5")]
    if alien:
        print(f"  BAD 混进了不像分区关卡的 key：{alien[:3]}")
        return False

    res = call("instance.getsubarealevel", {}, 117)
    if res.get("code") != 200:
        print(f"  BAD instance.getsubarealevel code={res.get('code')}")
        return False
    data = res.get("data")
    if not isinstance(data, dict) or "subareaLevels" in data:
        keys = list(data)[:5] if isinstance(data, dict) else type(data).__name__
        print(f"  BAD data 应该就是那份 map（不能包一层）：{keys}")
        return False
    if set(data) != set(sub):
        print(f"  BAD 两处不一致：登录块 {len(sub)} 条 vs getsubarealevel {len(data)} 条")
        return False
    limits = sorted({v["challengeTimes"] for v in data.values()})
    if not limits or min(limits) <= 0:
        # limit=0 时客户端 `isCanBattle` 的**裸比较** `challengeTimes >= challengeTimeLimit`
        # 会变成 `0 >= 0` → 一进关就「挑战次数用完啦~TuT」（实测踩过）
        print(f"  BAD challengeTimes（每日上限）必须 > 0，实际 {limits}"
              f" —— 0 会让客户端 isCanBattle 直接判「次数用完」")
        return False

    # 分区**章节**列表：`data.activityChapters` / `instance.getactivityinstance`
    #
    # 分区界面（SubareaChapterMenuLayer）左侧的章节列表就靠它：
    #     getActivityChapterListOfType(INSTANCE_TYPE.SUBAREA) 按
    #     `table_chapter[entry.key].type == "5"` 过滤。以前这条路由回 `[]`，
    #     界面上「分区战场」是**空的**（地图和按钮都在、一个章节都没有）。
    from gamesrv import instance as ginstance

    chapters = inst.get("activityChapters")
    if not isinstance(chapters, dict) or not chapters:
        print(f"  BAD 登录块 activityChapters 不是非空 map：{str(chapters)[:80]}")
        return False
    res2 = call("instance.getactivityinstance", {}, 118)
    if res2.get("code") != 200:
        print(f"  BAD instance.getactivityinstance code={res2.get('code')}")
        return False
    data2 = res2.get("data")
    if not isinstance(data2, dict) or "activityChapters" in data2:
        keys = list(data2)[:5] if isinstance(data2, dict) else type(data2).__name__
        print(f"  BAD getactivityinstance 的 data 应该就是那份 map：{keys}")
        return False
    if set(data2) != set(chapters):
        print(f"  BAD 两处章节不一致：登录块 {sorted(chapters)} vs 路由 {sorted(data2)}")
        return False
    tbl = ginstance._chapter_table()
    alien = [k for k in data2 if str((tbl.get(k) or {}).get("t") or "") != "5"]
    if alien:
        print(f"  BAD 有不是分区章节（table_chapter.type != 5）的 key：{alien}")
        return False

    print(f"  OK  分区关卡：登录块与 getsubarealevel 一致，{len(data)} 关，"
          f"每日上限={limits[0]}；分区章节 {len(data2)} 个 {sorted(data2)}")
    return ok


def level_result_check(ok: bool) -> bool:
    """`instance.finishlevel` 的回包形状（战斗结算）。

    这一条只查**形状**和「好感度真的落到了对应角色头上」，规则细节在
    `selftest_favor.py`（那个不用起服务端，断言更细）：

    * 奖励块必须在 `data.rewards` 里 —— 客户端 `Instance.finishLevel/<` 读的是
      `res.rewards.levelReward` 和 `_dealLevelResult(res.rewards)`；
      `data.level` 只走 `Level.updateLevel()`（只认星级/次数/时间三个字段）。
      挂错层的表现就是结算面板「获得物资」永远空着。
    * 好感度只回**数量**（`rewards.levelReward.favor`），"给谁"是客户端拿自己
      `table_level.favor_char_key` 算的（没有就全队军士 + 主角）。
    * `data.favor` 必须是 `{charKey: 行}` 的 map，而且要和登录块里那份**一致**
      （响应说涨了、存档没涨 = 撒谎）。

    ⚠️ 会真的记一次通关（星级 / 次数 / 经验 / 好感都会动），所以挑的是表里
    favor 最小、且没有 favor_char_key 的那一关（全队一人一份，顺带验多行 favor 块）。
    """
    from gamesrv import instance

    tbl = instance._level_table().get("level") or {}
    cands = [(int(v.get("favor") or 0), k) for k, v in tbl.items()
             if int(v.get("favor") or 0) > 0 and not v.get("fck")]
    if not cands:
        print("  BAD 关卡表里没有「有 favor、没 favor_char_key」的关，抽表可能没跑")
        return False
    amount, level_id = min(cands)

    login = call("agent.getlogindata", {}, 113)
    before = ((login.get("data") or {}).get("favor") or {}).get("favors") or {}

    res = call("instance.finishlevel",
               {"levelId": level_id, "starMark": 7, "curTeamIdx": 0}, 114)
    if res.get("code") != 200:
        print(f"  BAD instance.finishlevel code={res.get('code')} {res}")
        return False
    data = res.get("data") or {}
    rewards = data.get("rewards") or {}
    level = data.get("level") or {}
    lr = rewards.get("levelReward") or {}
    granted = data.get("favor") or {}

    bad = []
    if lr.get("favor") != amount:
        bad.append(f"rewards.levelReward.favor={lr.get('favor')!r} 期望 {amount}")
    if not isinstance(granted, dict) or not granted:
        bad.append(f"data.favor 不是非空 map：{granted!r}")
    if "playerInfo" not in lr or "playerAttr" not in (lr.get("playerInfo") or {}):
        bad.append("rewards.levelReward.playerInfo.playerAttr 缺了（Exp+N 会不动）")
    if level.get("starMark") != 7:
        bad.append(f"data.level.starMark={level.get('starMark')!r}（星级要在这一层）")
    wrong = sorted({"dropReward", "levelReward", "appraise", "firstComplete"} & set(level))
    if wrong:
        bad.append(f"奖励块跑到 data.level 里了：{wrong}")

    after = ((call("agent.getlogindata", {}, 115).get("data") or {})
             .get("favor") or {}).get("favors") or {}
    for key, row in granted.items():
        old = before.get(key) or {}
        new = after.get(key) or {}
        if key not in before:
            bad.append(f"{key} 不在登录块的好感度表里")
            continue
        if (int(new.get("lv") or 1), int(new.get("curExp") or 0)) != \
           (int(row.get("lv") or 1), int(row.get("curExp") or 0)):
            bad.append(f"{key} 响应行与存档不一致：响应 lv{row.get('lv')}/{row.get('curExp')}"
                       f" vs 存档 lv{new.get('lv')}/{new.get('curExp')}")
        if int(old.get("curExp") or 0) == int(new.get("curExp") or 0) and \
           int(old.get("lv") or 1) == int(new.get("lv") or 1):
            bad.append(f"{key} 好感度没涨（响应说涨了 {amount}）")

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    print(f"  OK  战斗结算：level {level_id} 好感 +{amount} 落到 "
          f"{sorted(granted)}（奖励块在 data.rewards，data.level 只有星级/次数）")
    return ok


def subarea_achievement_check(ok: bool) -> bool:
    """分区成就：结算上报 → 落盘 → 领奖 → 重复领奖被拒。

    这条链路的关键是「**成就是客户端算的**」：
    `subareaAchievementManager.formatBattleInfo()` 在结算时判完条件，
    把 `newAchievements` / `modifyAchievements` 塞进 `instance.finishlevel` 的
    `subareaInfo`。所以这里照客户端那个形状自己造一份 payload：

        {"victory": true, "battleId": <levelId>,
         "newAchievements": [aid],
         "modifyAchievements": {aid: {"progress": 1, "progressInfo": {"x": true}, "complete": true}}}

    要验的四件事：
      ① 回包里 `data.updateSubareaAchievements` 带着这条行（客户端靠它刷新界面）
      ② 登录块 `data.subareaachievement.achievements` 是 **map** 且含这条行，
         而且 `completeTime` 真的写进去了（客户端靠它判「已完成」，也靠它跳过重复判定）
      ③ `subareaachievement.receivereward` 发的道具和 `table_subarea_achievement_reward`
         对得上，并且**落盘**（再登录一次还在）
      ④ 再领一次回 204（REWARD_RECEIVED），表里没有的 id 回 203

    ⚠️ 会真发道具、真记一次通关，所以开头先把这条成就**删掉**、把道具数记下来，
    收尾时恢复（这样脚本可以反复跑）。
    """
    from gamesrv import config, items, store, subarea

    table = subarea.ach_table()
    if not table:
        print("  BAD 没有 table_subarea_achievement，抽表没跑？")
        return False

    # 挑奖励件数最少的一条（少发点道具，收尾也好还原）
    aid = min(table, key=lambda k: (len(subarea.reward_rows(k)), k))
    expected = {}
    for _rtype, key, cnt in subarea.reward_rows(aid):
        expected[str(key)] = expected.get(str(key), 0) + int(cnt)

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    before_items = dict(items.items_of(player))
    old_row = subarea.rows(player).pop(aid, None)
    store.save_player(player)
    print(f"  ..  用例前先把成就 {aid}（{table[aid].get('desc')}）从存档摘掉，"
          f"期望奖励 {expected}")

    bad = []
    level_id = ""
    try:
        # 借一个真关卡来做 finishlevel（顺手把 subareaInfo 带上）
        from gamesrv import instance as inst_mod

        levels = (inst_mod._level_table().get("level") or {})
        level_id = sorted(levels)[0]

        res = call("instance.finishlevel",
                   {"levelId": level_id, "starMark": 1, "curTeamIdx": 0,
                    "subareaInfo": {"time": 1, "battleId": level_id, "victory": True,
                                    "newAchievements": [aid],
                                    "modifyAchievements": {aid: {
                                        "progress": 1, "progressInfo": {"x": True},
                                        "complete": True}}}}, 130)
        if res.get("code") != 200:
            bad.append(f"instance.finishlevel code={res.get('code')} {res}")
        data = res.get("data") or {}
        rows = data.get("updateSubareaAchievements")
        if not isinstance(rows, list) or not rows:
            bad.append(f"回包缺 data.updateSubareaAchievements：{str(data)[:120]}")
        else:
            row = rows[0]
            if row.get("id") != aid:
                bad.append(f"回包成就 id={row.get('id')!r} 期望 {aid}")
            if not row.get("completeTime"):
                bad.append("回包行没有 completeTime（客户端会当成没完成）")
            if int(row.get("isReceiveReward") or 0) != 0:
                bad.append("回包行 isReceiveReward 应该是 0")

        block = ((call("agent.getlogindata", {}, 131).get("data") or {})
                 .get("subareaachievement") or {}).get("achievements")
        if not isinstance(block, dict):
            bad.append(f"登录块 achievements 不是 map：{type(block).__name__}")
        elif aid not in block:
            bad.append(f"登录块里没有 {aid}（没落盘？）")
        elif not block[aid].get("completeTime"):
            bad.append("登录块里那条没有 completeTime")

        # 再来一场：这次只报进度增量（客户端 `recordModify` 报的是**增量**，
        # 服务端要累加，不是覆盖）。第一条已经报过 progress=1，再 +50 应该是 51。
        call("instance.finishlevel",
             {"levelId": level_id, "starMark": 1, "curTeamIdx": 0,
              "subareaInfo": {"time": 2, "battleId": level_id, "victory": True,
                              "newAchievements": [],
                              "modifyAchievements": {aid: {"progress": 50,
                                                           "progressInfo": {"y": True}}}}}, 136)
        row2 = (((call("agent.getlogindata", {}, 137).get("data") or {})
                 .get("subareaachievement") or {}).get("achievements") or {}).get(aid) or {}
        if int(row2.get("progress") or 0) != 51:
            bad.append(f"进度没累加：progress={row2.get('progress')!r} 期望 51")
        if not (row2.get("progressInfo") or {}).get("y"):
            bad.append(f"progressInfo 没合并：{row2.get('progressInfo')!r}")

        claim = call("subareaachievement.receivereward", {"achievementId": aid}, 132)
        if claim.get("code") != 200:
            bad.append(f"receivereward code={claim.get('code')} {claim}")
        elif dict(claim.get("data") or {}) != expected:
            bad.append(f"领奖回包道具 {claim.get('data')} 期望 {expected}")

        after_login = ((call("agent.getlogindata", {}, 133).get("data") or {})
                       .get("item") or {})
        for key, cnt in expected.items():
            if int(after_login.get(key) or 0) != int(before_items.get(key) or 0) + cnt:
                bad.append(f"道具 {key} 没到账：{before_items.get(key)} -> "
                           f"{after_login.get(key)}（期望 +{cnt}）")

        again = call("subareaachievement.receivereward", {"achievementId": aid}, 134)
        if again.get("code") != subarea.REWARD_RECEIVED:
            bad.append(f"重复领奖 code={again.get('code')} 期望 {subarea.REWARD_RECEIVED}")

        bogus = call("subareaachievement.receivereward",
                     {"achievementId": "999999"}, 135)
        if bogus.get("code") != subarea.ACHIEVEMENT_NOT_EXIST:
            bad.append(f"不存在的成就 code={bogus.get('code')} "
                       f"期望 {subarea.ACHIEVEMENT_NOT_EXIST}")
    finally:
        # 收尾：行恢复原样、道具恢复原数
        player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        rows_now = subarea.rows(player)
        if old_row is None:
            rows_now.pop(aid, None)
        else:
            rows_now[aid] = old_row
        live = items.items_of(player)
        for key, cnt in before_items.items():
            live[key] = cnt
        store.save_player(player)

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    print(f"  OK  分区成就：{aid}（{table[aid].get('desc')}）结算上报 → 落盘 → 领奖 "
          f"{expected} → 重复领奖 {subarea.REWARD_RECEIVED} / 不存在 "
          f"{subarea.ACHIEVEMENT_NOT_EXIST}（收尾已还原）")
    return ok


def exchange_check(ok: bool) -> bool:
    """黑市交易所：兑换一次（金条 → 萌钞），验扣钱/发货/档位/落盘。

    挑的是 `type = "30"` 里最便宜的一条（`spend = 金条 x5 → 萌钞 x500`），
    跑完把金条/萌钞/兑换行都还原，所以可以反复跑。

    要验的四件事：
      ① `exchange.exchange` 回 200，`data` 是**新的那一段行**（带 `exchangeKey`）
      ② 金条真的扣了、萌钞真的发了（数量和表里 `table_resource_exchange` 对得上）
      ③ 第 2 次的档位会往上走（`todayExchangeTimes` 累加；固定档的那条会一直用 default）
      ④ 重登后那一段行还在（落盘）
    """
    from gamesrv import config, exchange, items, store

    table = exchange.item_table()
    if not table:
        print("  BAD 没有 table_exchange_item，抽表没跑？")
        return False

    # 挑一个「非 IAP、消耗是纯道具、产出也是道具」且**玩家现在买得起**的最便宜商品
    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    have = dict(items.items_of(player))
    cands = []
    for key, item in table.items():
        if str(item.get("type")) not in exchange.IN_GAME_TYPES:
            continue
        p = exchange.plan(key, 1)
        if not p or not p["spend"] or not p["receive"]:
            continue
        if any(c < 0 for c in p["receive"].values()):     # -1 = 补满，换算麻烦，跳过
            continue
        if any(int(have.get(ik) or 0) < int(need) for ik, need in p["spend"].items()):
            continue                                       # 买不起的不测（下面专门测"不足"）
        cands.append((sum(p["spend"].values()), key, p))
    if not cands:
        print("  BAD 没有可测的兑换商品（买得起的）")
        return False
    _cost, key, p1 = min(cands)
    print(f"  ..  用例：兑换 {key}（{table[key].get('name')}）花 {p1['spend']} 得 {p1['receive']}")

    before_items = dict(items.items_of(player))
    old_row = exchange.rows(player).pop(key, None)
    store.save_player(player)

    bad = []
    try:
        res = call("exchange.exchange", {"key": key}, 140)
        if res.get("code") != 200:
            bad.append(f"exchange.exchange code={res.get('code')} {res}")
        row = res.get("data") or {}
        # 回包要带 exchangeKey（客户端 `update(data)` 按它认这是哪一段行）。
        # 注意它**不一定**等于 item key：固定档的商品 `exchange_key_default` 就是自己，
        # 带阶梯的（如 400001）第 N 次会指向另一档。
        if not row.get("exchangeKey"):
            bad.append(f"回包行缺 exchangeKey：{row}")
        elif row["exchangeKey"] != p1["ladder"]:
            bad.append(f"exchangeKey={row['exchangeKey']!r} 期望档位 {p1['ladder']!r}")
        if int(row.get("todayExchangeTimes") or 0) != 1:
            bad.append(f"todayExchangeTimes={row.get('todayExchangeTimes')!r} 期望 1")

        login_item = (call("agent.getlogindata", {}, 141).get("data") or {}).get("item") or {}
        for ik, need in p1["spend"].items():
            got = int(login_item.get(ik) or 0)
            want = int(before_items.get(ik) or 0) - int(need)
            if got != want:
                bad.append(f"金条 {ik} 扣得不对：{before_items.get(ik)} -> {got}（期望 {want}）")
        for ik, cnt in p1["receive"].items():
            got = int(login_item.get(ik) or 0)
            want = int(before_items.get(ik) or 0) + int(cnt)
            if got != want:
                bad.append(f"产出 {ik} 没到账：{before_items.get(ik)} -> {got}（期望 {want}）")

        # 再兑一次：次数要累加
        res2 = call("exchange.exchange", {"key": key}, 142)
        if res2.get("code") != 200:
            bad.append(f"第二次兑换 code={res2.get('code')} {res2}")
        elif int((res2.get("data") or {}).get("todayExchangeTimes") or 0) != 2:
            bad.append(f"第二次 todayExchangeTimes={(res2.get('data') or {}).get('todayExchangeTimes')!r} 期望 2")

        # 落盘：重登后还在
        block = ((call("agent.getlogindata", {}, 143).get("data") or {})
                 .get("exchange") or {})
        if not isinstance(block, dict) or key not in block:
            bad.append(f"登录块 exchange 里没有 {key}（没落盘？）")
        elif int(block[key].get("todayExchangeTimes") or 0) != 2:
            bad.append(f"登录块 todayExchangeTimes={block[key].get('todayExchangeTimes')!r} 期望 2")

        # 材料不足要拒绝（造一个买不起的：把金条清空后试最贵的）
        rich = max(cands, key=lambda x: x[0])
        saved = items.count_of(player, list(rich[2]["spend"])[0])
        # 直接改存档再试，避免把玩家真金条清掉后忘了还原
        p2 = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        sk = list(rich[2]["spend"])[0]
        items.items_of(p2)[sk] = 0
        store.save_player(p2)
        r3 = call("exchange.exchange", {"key": rich[1]}, 144)
        if r3.get("code") == 200:
            bad.append(f"金条为 0 时兑换 {rich[1]} 竟然成功了")
        p3 = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        items.items_of(p3)[sk] = saved
        store.save_player(p3)
    finally:
        # 收尾：道具恢复原数、兑换行恢复原样
        player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        got = items.items_of(player)
        for k, v in before_items.items():
            got[k] = v
        rows_now = exchange.rows(player)
        if old_row is None:
            rows_now.pop(key, None)
        else:
            rows_now[key] = old_row
        store.save_player(player)

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    print(f"  OK  交易所：{key} 花 {p1['spend']} 得 {p1['receive']}，次数累加 + 落盘 + "
          f"金条不足被拒（收尾已还原）")
    return ok


def module_open_check(ok: bool) -> bool:
    """「功能开启」弹窗的开关（服务端控制，不动客户端）。

    客户端 `MainLayer._updateAnimation()` 里有 `moduleManager.popModuleOpen()`：
    它把所有「已解锁但 `isOpened` 还是假」的模块挨个弹一遍动画（`table_function_open`
    32 条）。而 `isOpened` 来自登录块的 `moduleOpenMark[mark_index]`
    （`Player.initModuleState`），弹完客户端会回写 `player.setmoduleopenmark`。
    私服默认把 32 个 mark 全标成已弹过 → 一进游戏不再连弹 32 个。
    """
    login = call("agent.getlogindata", {}, 147)
    marks = (login.get("data") or {}).get("moduleOpenMark")
    if not isinstance(marks, dict):
        print(f"  BAD moduleOpenMark 不是 map：{type(marks)}（客户端是 [markIndex] 取值）")
        return False
    from gamesrv import items as items_mod

    table = items_mod.table("table_function_open") or {}
    want = sorted({str(int((row or {}).get("mi"))) for row in table.values()
                   if (row or {}).get("mi") is not None})
    missing = [mi for mi in want if not marks.get(mi)]
    if missing:
        print(f"  BAD moduleOpenMark 缺 {len(missing)} 个 mark（{missing[:6]}…）"
              f"→ 这些功能的开启弹窗还会弹")
        return False
    # 客户端弹完会回写；服务端要能收（并且别把数组当 map 回给它）
    r = call("player.setmoduleopenmark", [1, 2, 3], 148)
    if r.get("code") != 200:
        print(f"  BAD player.setmoduleopenmark code={r.get('code')} {r}")
        return False
    marks2 = ((call("agent.getlogindata", {}, 149).get("data") or {})
              .get("moduleOpenMark"))
    if not isinstance(marks2, dict) or not marks2.get("1"):
        print(f"  BAD 回写后 moduleOpenMark 形状不对：{type(marks2)} {str(marks2)[:80]}")
        return False
    print(f"  OK  功能开启弹窗：登录块 moduleOpenMark 覆盖 {len(want)} 个 mark"
          f"（默认不弹）；player.setmoduleopenmark 可回写")
    return ok


def sign_check(ok: bool) -> bool:
    """签到：登录块形状 + 领一次 + 当天不能再领。

    客户端 `SignCenter.ctor` 是 `this._signs = data.signs` 然后 `for (k in _signs)`
    —— `signs` 必须是 **map**（`{signKey: 行}`），给数组就是一条签到都没有，
    表现就是「点签到没用」。回包还要带 `data.sign`（`updateTime` 变大）让客户端
    `updateByServer` 把新 `count` 合并进同一行。

    ⚠️ 会真改签到状态和道具，跑完还原（所以可以反复跑）。
    玩家**今天真签过**也得能跑：用例先把 `lastDay` 清掉再读登录块，收尾连状态一起还原。
    """
    from gamesrv import config, items, sign, store

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    before_state = dict(sign.state(player))
    before_items = dict(items.items_of(player))
    # 先假装今天没签过，否则下面「领一次」那几步全都没法测
    sign.state(player)["lastDay"] = ""
    store.save_player(player)

    login = call("agent.getlogindata", {}, 150)
    block = (login.get("data") or {}).get("sign") or {}
    signs = block.get("signs")
    if not isinstance(signs, dict):
        print(f"  BAD data.sign.signs 不是 map：{type(signs)}（客户端当 map 用）")
        return False
    row = signs.get(sign.SIGN_KEY)
    if not isinstance(row, dict):
        print(f"  BAD 没有 {sign.SIGN_KEY} 那条签到：{sorted(signs)}")
        return False
    for field in ("signKey", "type", "count", "rewardCount", "rewards",
                  "canSignToday", "beginTimeSec", "endTimeSec"):
        if field not in row:
            print(f"  BAD 签到行缺字段 {field}（客户端 _update/_updateItems 要读）")
            return False
    reward_days = row.get("rewards")
    if not isinstance(reward_days, list) or len(reward_days) < sign.SIGN_DAYS + 1:
        print(f"  BAD rewards 不是 1 基的二维数组（要 rewards[1..{sign.SIGN_DAYS}]，下标 0 留空）："
              f"{str(reward_days)[:60]}")
        return False
    for day_no in range(1, sign.SIGN_DAYS + 1):
        day_rewards = reward_days[day_no]
        if not isinstance(day_rewards, list) or not day_rewards[1:]:
            print(f"  BAD rewards[{day_no}] 里没有 item（内层也是 1 基，下标 0 留空）："
                  f"{str(day_rewards)[:60]}")
            return False
        if day_rewards[2:]:
            # SignRewardItem._init：length === 1 用真图标，> 1 一律用 res/signcommonicon
            print(f"  BAD rewards[{day_no}] 有 {len(day_rewards[1:])} 件，每天必须**恰好一件**"
                  f"（多件时客户端 7 个格子全用同一个通用图标）：{str(day_rewards)[:60]}")
            return False
    # 客户端是 `rewards[i + 1][j]`（i 从 0、j 从 1），0 基会走到 `rewards[7]` undefined
    if reward_days[0] is not None:
        print(f"  BAD rewards[0] 应该留空（客户端不读，但别把第 1 天放这儿）：{reward_days[0]!r}")
        return False

    bad = []
    try:
        if not row.get("canSignToday"):
            bad.append("canSignToday 应该是 1（用例开头已经清掉 lastDay 了）")
        r = call("sign.receivereward", {"key": sign.SIGN_KEY}, 151)
        if r.get("code") != 200:
            bad.append(f"sign.receivereward code={r.get('code')} {r}")
        data = r.get("data") or {}
        nb = (data.get("sign") or {}).get("signs") or {}
        nrow = nb.get(sign.SIGN_KEY) or {}
        if int(nrow.get("count") or -1) != int(row.get("count") or 0) + 1:
            bad.append(f"回包签到 count={nrow.get('count')!r} 期望 {int(row.get('count') or 0) + 1}")
        if nrow.get("canSignToday"):
            bad.append("领完之后 canSignToday 应该变 0")
        # 道具真的发了
        day = int(row.get("count") or 0) % sign.SIGN_DAYS
        want = {}
        for _t, k, c in sign.SIGN_REWARDS[day]:
            want[str(k)] = want.get(str(k), 0) + int(c)
        after = ((call("agent.getlogindata", {}, 152).get("data") or {}).get("item") or {})
        for k, c in want.items():
            if int(after.get(k) or 0) != int(before_items.get(k) or 0) + c:
                bad.append(f"第 {day + 1} 天奖励 {k} 没到账：{before_items.get(k)} -> {after.get(k)}")
        # 同一天不能再领
        again = call("sign.receivereward", {"key": sign.SIGN_KEY}, 153)
        if again.get("code") == 200:
            bad.append("同一天第二次签到竟然又成功了")
    finally:
        player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        player[sign.PLAYER_KEY] = before_state
        got = items.items_of(player)
        for k in [k for k in got if k not in before_items]:
            del got[k]              # 用例期间新发的道具（第 1 天那件）
        for k, v in before_items.items():
            got[k] = v
        store.save_player(player)

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    print(f"  OK  签到：signs 是 map（{row['rewardCount']} 天、当前第 "
          f"{int(row['count']) + 1} 天）→ 领奖 code=200、道具到账、当天不能再领"
          f"（收尾已还原）")
    return ok


def detect_check(ok: bool) -> bool:
    """任务派遣（`detect.*` 6 条）：面板列表 / 主界面列表 / 上阵校验 / 派一次 / 加速 / 领取。

    客户端看的是响应里的 `data.code`（`DETECT_ERROR_CODE`），**不是 HTTP 码** ——
    失败也回 200，所以这里断言的是 `data.code`。

    ⚠️ 表里 15 个派遣都要 20/30 级军士，而默认名单是 1 级 —— 用例临时把 3 个军士
    抬到 20 级再跑（收尾连等级一起还原），所以可反复跑。
    """
    from gamesrv import config, detect, items, store

    table = detect.chapter_table()
    if not table:
        print("  BAD 没有 table_detect_chapter，抽表没跑？")
        return False

    login = call("agent.getlogindata", {}, 160)
    block = (login.get("data") or {}).get("detect") or {}
    if not isinstance(block.get("speedInfo"), dict) or not isinstance(block.get("detect"), dict):
        print(f"  BAD data.detect 形状不对：{str(block)[:80]}")
        return False

    r = call("detect.checkdetectmain", {"idlist": ["10001", "10002", "10003"]}, 161)
    d = r.get("data") or {}
    if d.get("code") != detect.OK or len(d.get("detectMainList") or []) != 3:
        print(f"  BAD checkdetectmain: {str(r)[:120]}")
        return False

    r = call("detect.getdetectlist", {"id": "10001"}, 162)
    d = r.get("data") or {}
    lst = d.get("detectList") or []
    if d.get("code") != detect.OK or not lst:
        print(f"  BAD getdetectlist: {str(r)[:120]}")
        return False
    if not all("index" in x and isinstance(x.get("data"), dict) for x in lst):
        print(f"  BAD detectList 元素形状：{str(lst)[:120]}")
        return False

    # 挑一个「任意兵种 + 只要 20 级」的派遣
    key = None
    for k, row in sorted(table.items()):
        if int(row.get("soldierType") or 0) == 0 and int(row.get("soldierLv") or 0) <= 20 \
           and int(row.get("soldierNum") or 0) <= 3:
            key = k
            break
    if key is None:
        print("  BAD 表里没有「任意兵种 20 级 3 人」的派遣")
        return False
    row = table[key]
    need = int(row.get("soldierNum") or 0)

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    before_items = dict(items.items_of(player))
    before_runs = json.loads(json.dumps(detect.state(player).get("runs") or {}))
    before_speed = dict(detect.state(player).get("speedInfo") or {})
    before_count = int(detect.state(player).get("completeCount") or 0)
    # ⚠️ 就地改这一份存档再 save（`get_or_create_player` 每次都重新 load，
    #    改旧对象再存新对象是白改 —— 这里踩过一次）
    soldiers = list(player.get("soldiers") or [])[:need]
    before_lv = {int(s["id"]): int(s.get("lv") or 1) for s in soldiers}
    ids = []
    for s in soldiers:
        s["lv"] = max(int(s.get("lv") or 1), 20)
        ids.append(int(s["id"]))
    store.save_player(player)

    bad = []
    try:
        # 先测「没选军士」：把选人记录清掉再派 → 206
        st = detect.state(player)
        st["runs"].pop(key, None)
        store.save_player(player)
        r = call("detect.godetect", {"detectkey": key}, 163)
        if (r.get("data") or {}).get("code") != detect.GODETECT_NOTSELECT:
            bad.append(f"没选军士应回 {detect.GODETECT_NOTSELECT}，实回 {str(r)[:100]}")

        r = call("detect.selectsolders", {"detectkey": key, "solders": ids}, 164)
        if (r.get("data") or {}).get("code") != detect.OK:
            bad.append(f"selectsolders: {str(r)[:100]}")
        r = call("detect.godetect", {"detectkey": key}, 165)
        if (r.get("data") or {}).get("code") != detect.OK:
            bad.append(f"godetect: {str(r)[:140]}")
        # 再派一次 → 已经在跑
        r = call("detect.godetect", {"detectkey": key}, 166)
        if (r.get("data") or {}).get("code") not in (detect.GODETECT_RUNNING,
                                                     detect.GODETECT_COMPLETE):
            bad.append(f"重复派遣应回 RUNNING/COMPLETE，实回 {str(r)[:100]}")
        # 没到时间不能领
        r = call("detect.godetectcomplete", {"detectkey": key}, 167)
        if (r.get("data") or {}).get("code") != detect.GODETECT_NOTCOMPLETE:
            bad.append(f"没到时间应回 {detect.GODETECT_NOTCOMPLETE}，实回 {str(r)[:100]}")
        # 加速跳过整段等待
        r = call("detect.subtime", {"detectkey": key, "timesed": 999999}, 168)
        if (r.get("data") or {}).get("code") != detect.OK:
            bad.append(f"subtime: {str(r)[:120]}")
        # 领
        r = call("detect.godetectcomplete", {"detectkey": key}, 169)
        d = r.get("data") or {}
        if d.get("code") != detect.OK:
            bad.append(f"godetectcomplete: {str(r)[:140]}")
        rewards = d.get("rewards") or {}
        if not rewards:
            bad.append(f"领取没有任何掉落：{str(d)[:120]}")
        after = ((call("agent.getlogindata", {}, 170).get("data") or {}).get("item") or {})
        for k, c in rewards.items():
            if int(after.get(k) or 0) != int(before_items.get(k) or 0) + int(c):
                bad.append(f"掉落 {k} 没到账：{before_items.get(k)} -> {after.get(k)}")
        if int(d.get("complateDetectCount") or 0) != before_count + 1:
            bad.append(f"complateDetectCount={d.get('complateDetectCount')!r} 期望 {before_count + 1}")
    finally:
        p = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        st = detect.state(p)
        st["runs"] = before_runs
        st["speedInfo"] = before_speed
        st["completeCount"] = before_count
        for s in (p.get("soldiers") or []):
            sid = int(s.get("id") or 0)
            if sid in before_lv:
                s["lv"] = before_lv[sid]
        got = items.items_of(p)
        for k, v in before_items.items():
            got[k] = v
        store.save_player(p)

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    print(f"  OK  派遣：面板 {len(lst)} 个任务 / 未选军士被拒 / 派 {key}"
          f"（{row.get('name')}）→ 加速 → 领取到手（收尾已还原）")
    return ok


def item_icon_check(ok: bool) -> bool:
    """奖励里不能出现「客户端画不出图标」的道具（`table_item.ic` 为空 / `q = 0`）。

    客户端 `bagconfig.ITEM_QUALITY` 只有 白/绿/蓝/紫/黄 五档，而
    `ItemIcon.updateItemIcon` **只有品质匹配上才会建 `this._iconCase`**：

        for (q in ITEM_QUALITY) if (q === quality) _iconCase = seekNodeByName(…)

    表里 `q = 0` 的道具一个档都匹配不上，循环走完 `_iconCase` 还是 undefined，
    随后 `if (iconPath) this._iconCase.addChild(sprite)` 直接
    `TypeError: this._iconCase is undefined`（itemicon.js:199，2026-09-20 真机上
    就是这么挂的）。它前面还有一发 `bag.getItemIcon()`，因为 `ic` 是空串，
    拼出来是 `res/icon/item/undefined.png` → `cc.assert(isFileExist(url))` 报
    `Assert: bag.getItemIcon() error, key is 100101`。

    这个异常会**打断调用方的整条初始化**：我当时把 `100101`（表里叫「卡槽购买次数」）
    当成签到第 7 天奖励，`data.sign.signs.normal.rewards` 里带着它，一进游戏
    `SignRewardItem._init → rewardManager.getRewardIcon → new ItemIcon(key)`
    就抛异常，主界面初始化停在一半 —— 表现是「界面点不动、服务端一条请求都收不到」，
    特别难查（服务端这边一切正常）。

    所以这里把**会被画成奖励图标**的来源全扫一遍：登录块（背包本身不算，见下）、
    签到奖励、派遣奖池、分区成就奖励、关卡掉落、商店货架、回礼表。

    `100101` / `100102` 这两个计数器是全表**仅有**的没有图标的道具：它们只能留在背包里
    （客户端 `Bag.getList(ITEM_TYPE.ALL)` 明确跳过 `type == CURRENCY`，所以背包列表
    不会画它们），**永远不要放进任何奖励/展示列表**。
    """
    from gamesrv import items, sign

    titem = items.table("table_item") or {}
    if not titem:
        print("  BAD 没有 table_item，抽表没跑？")
        return False
    dead = sorted(k for k, v in titem.items()
                  if isinstance(v, dict) and not (v.get("ic") or ""))
    bad = []

    def check(title, keys):
        hit = sorted({str(k) for k in keys if str(k) in dead})
        if hit:
            bad.append(f"{title} 里出现了画不出图标的道具 {hit}")

    # 1) 登录块 —— 一进游戏客户端就会读它建图标（签到那条链就在里面）
    login = call("agent.getlogindata", {}, 170)
    block = dict(login.get("data") or {})
    block.pop("item", None)       # 背包：计数器躺在里面是允许的（Bag.getList 跳过货币）
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node in dead:
            found.add(node)

    walk(block)
    if found:
        bad.append(f"登录块里有画不出图标的道具 {sorted(found)}")

    # 2) 各奖励/展示来源
    check("签到奖励 SIGN_REWARDS",
          [k for day in sign.SIGN_REWARDS for (_t, k, _c) in day])

    from gamesrv import detect
    check("派遣奖池 gainIcon/gainIcon2",
          [x for row in detect.chapter_table().values()
           for x in str(row.get("gainIcon2") or row.get("gainIcon") or "").split("#") if x])

    from gamesrv import subarea
    check("分区成就奖励",
          [row.get("key") for row in subarea.reward_table().values()
           if str(row.get("type")) == "2"])

    from gamesrv import instance
    lv = instance._level_table()
    check("关卡掉落 table_level_reward.reward",
          [row.get("key_%d" % i) for row in (lv.get("reward") or {}).values()
           for i in range(1, 6)])

    check("商店货架 table_shelf(good_type=i)",
          [row.get("good_key") for row in items.table("table_shelf").values()
           if str(row.get("good_type")) == "i"])

    from gamesrv import favor
    check("好感回礼 table_favor_receive",
          [row.get(field) for rows in favor._receive().values() if isinstance(rows, list)
           for row in rows if isinstance(row, dict)
           for field in ("id1", "id2")])

    from gamesrv import arena
    check("演习场奖励 pvp_rewards/fail_coins",
          [k for (_t, k, _c) in arena.pvp_rewards() + arena.fail_rewards()])

    # 3) 背包里允许躺计数器（100101/100102），但只允许这两个
    from gamesrv import config, store
    bag = items.items_of(store.get_or_create_player(config.DEFAULT_ACCOUNT))
    extra = sorted(k for k, v in bag.items()
                   if int(v or 0) > 0 and k in dead and k not in ("100101", "100102"))
    if extra:
        bad.append(f"背包里有画不出图标又不是计数器的道具 {extra}")

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    print(f"  OK  奖励图标：登录块 + 签到/派遣/分区成就/关卡掉落/商店/回礼 里没有"
          f"画不出图标的道具（全表只有 {dead} 两个计数器没有图标，它们只允许躺在背包里）")
    return ok


def arena_check(ok: bool) -> bool:
    """演习场（`arena.*` 4 条）：登录块形状 / 对手军士 key 有效 / 打一场结算 / 换一批。

    客户端这条链的要点（`src/data/arenacenter.jsc` + `src/ui/arena/*`）：

    * 登录块 `data.arena` = `{arenaInfo, rivals, resetTime, refreshTime}`（`mechaSuperSkillCorrectOwn`
      客户端压根不读，已不发）；`ArenaCenter.ctor` 读顶层 `refreshTime`，而 `updateByServer`
      读 **`arenaInfo.refreshTime`** —— 两份都得有，少一份 `_refreshTime` 会变成
      `undefined + 7200 = NaN`（倒计时乱）。
    * `arenaInfo.change` 是**今日剩余挑战次数**（不是积分变化）：`<= 0` 时客户端
      「挑战」「刷新对手」两个按钮直接 `toast(1602)` 返回，界面像哑掉。
    * 每项对手的 `soldier<i>` 是**编码串** `"key#星级#等级#技能等级"`（客户端
      `charManager.decodeSoldier()` 解，少于 4 段直接 warn + undefined），
      `asstKey` 必须是**军士卡 key**（客户端拿它走 `new ItemIcon` → `Shop.getTypeById`）。
    * **每条回包都要带 `data.arena`**：客户端这条路线的 cb 不带参数，
      数据全靠 `patch.js` 的 RESP-DISPATCH（`arena -> arenaCenter.updateByServer`）。
      结算还要平铺 `battleData.combatTime/death/rank`（缺一个结算面板就抛
      `Text_setString` 异常，玩家退不出战斗）。

    ⚠️ 会真改积分 / 挑战次数 / 对手状态 / 道具，跑完还原（所以可以反复跑）。
    """
    from gamesrv import arena, config, items, store

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    before = copy.deepcopy(player.get(arena.PLAYER_KEY))
    before_items = dict(items.items_of(player))
    bad = []

    try:
        block = ((call("agent.getlogindata", {}, 180).get("data") or {}).get("arena") or {})
        for field in ("arenaInfo", "rivals", "resetTime", "refreshTime"):
            if field not in block:
                bad.append(f"登录块 data.arena 缺 {field}（ArenaCenter.ctor 要读）")
        info = block.get("arenaInfo") or {}
        for field in ("points", "change", "wins", "rating", "refreshTime"):
            if field not in info:
                bad.append(f"arenaInfo 缺 {field}（客户端 _update/_updateWinsImage 要读；"
                           f"updateByServer 只认 arenaInfo.refreshTime）")
        # `change` 是「今日剩余挑战次数」：`<= 0` 时客户端两个按钮直接 toast 退出
        if int(info.get("change") or 0) <= 0:
            bad.append(f"arenaInfo.change={info.get('change')!r} 必须 > 0 —— "
                       f"客户端 _onClickFightButton/_onClickRefreshButton 在 change<=0 时"
                       f"直接 toast(1602) 返回，界面会像哑掉一样")
        rivals = block.get("rivals") or []
        if len(rivals) != arena.rival_count():
            bad.append(f"对手数 {len(rivals)} != table_arena_constant.rival_count "
                       f"{arena.rival_count()}")
        cards = (items.table("table_soldier") or {}).get("card") or {}
        for r in rivals:
            for field in ("index", "name", "lv", "rating", "points", "state"):
                if field not in r:
                    bad.append(f"对手 {r.get('index')} 缺字段 {field}")
            # asstKey 走 `new ItemIcon(asstKey)` → Shop.getTypeById，只认军士/道具/…的 key
            if r.get("asstKey") and r["asstKey"] not in cards:
                bad.append(f"对手 {r.get('index')} 的 asstKey={r['asstKey']!r} 不是军士卡 key"
                           f"（客户端 getTypeById 会报 key is error、头像画不出来）")
            texts = [r.get("soldier%d" % i) for i in range(1, 6) if r.get("soldier%d" % i)]
            if not texts:
                bad.append(f"对手 {r.get('index')} 一个军士都没有（详情页会空）")
            for text in texts:
                parts = str(text).split("#")
                if len(parts) < 4:
                    bad.append(f"对手 {r.get('index')} 的军士 {text!r} 段数 < 4 —— "
                               f"客户端 decodeSoldier 会 warn 并返回 undefined")
                elif parts[0] not in cards:
                    bad.append(f"对手 {r.get('index')} 的军士 {parts[0]} 不在 table_soldier 里")
        if bad:
            for one in bad:
                print(f"  BAD {one}")
            return False

        rr = call("arena.resetrivals", {"useGold": True}, 181)
        if rr.get("code") != 200 or not ((rr.get("data") or {}).get("arena")):
            bad.append(f"arena.resetrivals 回包不对：{str(rr)[:90]}")

        idx = block["rivals"][0].get("index")
        en = call("arena.enterfight", {"index": idx}, 182)
        if en.get("code") != 200:
            bad.append(f"arena.enterfight index={idx} code={en.get('code')} {str(en)[:80]}")
        ex = call("arena.exitfight", {"index": idx, "success": True,
                                      "battleInfo": {"combatTime": 23,
                                                     "ownSoldierDiedCount": 1}}, 183)
        data = ex.get("data") or {}
        if ex.get("code") != 200:
            bad.append(f"arena.exitfight code={ex.get('code')} {str(ex)[:80]}")
        for field in ("success", "rewards", "winsRewards", "scoreInfo", "battleData",
                      "winPoints", "arena"):
            if field not in data:
                bad.append(f"arena.exitfight 的 data 缺 {field}（ArenaLayer._fightResult 直接读）")
        # 结算面板三行：缺一个就是 setString(undefined) → 面板崩、退不出战斗
        for field in ("combatTime", "death", "rank"):
            if field not in (data.get("battleData") or {}):
                bad.append(f"battleData 缺 {field}（ArenaWinLayer 会 numelabed.label.string = "
                           f"undefined → Text_setString 抛异常，玩家退不出战斗）")
        letters = {"a", "b", "c", "d", "s", "ss", "sss"}
        for field in ("combatTime", "death", "rank"):
            if (data.get("scoreInfo") or {}).get(field) not in letters:
                bad.append(f"scoreInfo.{field}={(data.get('scoreInfo') or {}).get(field)!r} "
                           f"不是 a|b|c|d|s|ss|sss（客户端 ARENA_SCORE_RES 只有这 7 个）")
        if not isinstance(data.get("winsRewards"), (list, dict)):
            bad.append("winsRewards 要能 for-in（客户端走 util.objectToArray + popupReward）")
        after_rivals = (data.get("arena") or {}).get("rivals") or []
        if not after_rivals:
            bad.append("arena.exitfight 没带 data.arena.rivals（RESP-DISPATCH 刷不动界面）")
        after_info = (data.get("arena") or {}).get("arenaInfo") or {}
        npoints = after_info.get("points")
        if isinstance(npoints, int) and isinstance(info.get("points"), int):
            if npoints != info["points"] + int(data.get("winPoints") or 0):
                bad.append(f"积分对不上：{info['points']} + {data.get('winPoints')} != {npoints}")
        if int(after_info.get("change", -1)) != int(info["change"]) - 1:
            bad.append(f"挑战次数没扣 1：{info['change']} -> {after_info.get('change')}")
        mine = [r for r in after_rivals if r.get("index") == idx]
        if mine and mine[0].get("state") != 1:
            bad.append(f"赢了 index={idx} 之后 state={mine[0].get('state')!r}，应该变 1"
                       f"（客户端 1603「已经战胜过他了呢~」）")

        # 再打一场**输了**的：state 不能变成 1（输了可以再挑战），积分要往下走
        idx2 = block["rivals"][1].get("index")
        lose = call("arena.exitfight", {"index": idx2, "success": False,
                                       "battleInfo": {"combatTime": 61,
                                                      "ownSoldierDiedCount": 6}}, 184)
        ldata = lose.get("data") or {}
        if lose.get("code") != 200:
            bad.append(f"输了那场 exitfight code={lose.get('code')} {str(lose)[:80]}")
        if int(ldata.get("winPoints") or 0) > 0:
            bad.append(f"输了还给正分：winPoints={ldata.get('winPoints')!r}")
        lrivals = [r for r in ((ldata.get("arena") or {}).get("rivals") or [])
                   if r.get("index") == idx2]
        if lrivals and lrivals[0].get("state") == 1:
            bad.append(f"输了 index={idx2} 却标成 state=1（客户端会显示「已经战胜过他了呢」）")
    finally:
        player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        if before is None:
            player.pop(arena.PLAYER_KEY, None)
        else:
            player[arena.PLAYER_KEY] = before
        got = items.items_of(player)
        for k in [k for k in got if k not in before_items]:
            del got[k]
        for k, v in before_items.items():
            got[k] = v
        store.save_player(player)

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    print(f"  OK  演习场：登录块 {len(rivals)} 个对手（`soldier<i>` 都是 "
          f"\"key#星级#等级#技能等级\" 编码串、asstKey 是军士卡 key、change>0）、"
          f"resetrivals/enterfight/exitfight 都通：赢一场 {data.get('winPoints')} 分→state=1、"
          f"输一场 {ldata.get('winPoints')} 分→state 不变，挑战次数每场扣 1（收尾已还原）")
    return ok


def gacha_check(ok: bool) -> bool:
    """抽卡（`gacha.*` 3 条）：卡池三件套 / 抽一次真的发卡 / 免费池每日 1 次 / 错误码。

    客户端这边**一张 gacha 表都没有**，卡池 master 全靠登录块下发，所以检查的重点是形状：
      * `data.gacha` 里 `gachaData` / `gachaInfoList` / `gachaMasterList` **都是 map**
        （`Gacha.update(data)` 是 `this._gachaMasterObj = data.gachaMasterList` 直接赋值），
        少 `gachaInfoList`/`gachaMasterList` 的后果是 `getGachaMasterList()` 空 →
        界面「没有卡池」（旧桩就是这样）。
      * master 的 `infoKeysObj[times]` 必须指向 `gachaInfoList` 里真有的 infoKey；
        info 行的 `itemCount` + `saleInfoObj[0]` 是客户端算价格用的
        （`getGachaConsume = itemCount * sale / 100`，`saleInfoObj` 写成对象会让价格变 NaN）。
      * `getGachaDataKey(masterKey, times)` = `masterKey + "0"+times`（form=0），
        `gachaData` 的 key 要照这个来。
      * `gacha.gacha` 的失败码用客户端 `GACHA_ERROR_DICT`（202 无此池 / 204 次数用完 /
        206 资源不够 …），成功回 200 + `{gachaData, cards, itemKey, extraReward, gemGachaTimes}`。

    ⚠️ 会真抽卡（扣道具 / 加军士 / 加英雄机甲），跑完全部还原。
    """
    from gamesrv import config, gacha, items, store

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    before = {k: copy.deepcopy(player.get(k)) for k in
              ("gacha", "items", "soldiers", "heros", "mechas")}
    bad = []

    try:
        block = ((call("agent.getlogindata", {}, 190).get("data") or {}).get("gacha") or {})
        for field in ("gachaData", "gachaInfoList", "gachaMasterList"):
            if not isinstance(block.get(field), dict):
                bad.append(f"登录块 data.gacha.{field} 不是 map：{type(block.get(field))}"
                           f"（Gacha.update 直接把它赋给 _gachaXxxObj）")
        masters = block.get("gachaMasterList") or {}
        infos = block.get("gachaInfoList") or {}
        datas = block.get("gachaData") or {}
        if len(masters) != len(gacha.POOLS):
            bad.append(f"池子数 {len(masters)} != {len(gacha.POOLS)}"
                       f"（客户端 getGachaMasterList() 会少池子）")
        for key, conf in gacha.POOLS.items():
            master = masters.get(key)
            if not isinstance(master, dict):
                bad.append(f"没有池子 {key}（{conf['name']}）")
                continue
            for field in ("key", "name", "type", "form", "infoKeysObj",
                          "resIdx", "showPriority"):
                if field not in master:
                    bad.append(f"池子 {key} 的 master 缺 {field}")
            # resIdx 选 gachatypelayer<idx>.csb + gachabtnidx<idx>.png（按钮图标硬 assert）
            idx = master.get("resIdx")
            if not isinstance(idx, int) or not (1 <= idx <= 54):
                bad.append(f"池子 {key} 的 resIdx={idx!r} 必须在 1..54"
                           f"（选 csb/png 用的，客户端缺图标会 assert 崩）")
            times_keys = sorted((master.get("infoKeysObj") or {}).keys())
            if times_keys != ["1", "10"]:
                bad.append(f"池子 {key} 的 infoKeysObj 键要是 ['1','10']"
                           f"（两个按钮；键会被喂给 getGachaTimesIconUrl，只能 1..10）："
                           f"{times_keys}")
            for times in ("1", "10"):
                info_key = (master.get("infoKeysObj") or {}).get(times)
                if not info_key or info_key not in infos:
                    bad.append(f"池子 {key} x{times} 的 infoKey={info_key!r} 在 gachaInfoList "
                               f"里找不到（价格/消耗会读不到）")
                    continue
                info = infos[info_key]
                if not isinstance(info.get("saleInfoObj"), dict) or "0" not in info["saleInfoObj"]:
                    bad.append(f"{info_key} 的 saleInfoObj 要是按次数索引的 map 且带 0 基准价"
                               f"（写成 {{saleTimes,sale}} 会让 getGachaConsume 变 NaN）："
                               f"{info.get('saleInfoObj')!r}")
                cost = (conf.get("cost") or {}).get(times) or []
                if cost and int(info.get("itemCount") or 0) != int(cost[0][2]):
                    bad.append(f"{info_key} 的 itemCount={info.get('itemCount')!r} "
                               f"跟配置 {cost[0][2]} 对不上")
                if not cost and int(info.get("itemCount") or 0):
                    bad.append(f"免费池 {key} 的 itemCount 应该是 0"
                               f"（客户端 isFreeGacha 靠它判断）")
                if gacha.data_key(key, times) not in datas:
                    bad.append(f"gachaData 里没有 {gacha.data_key(key, times)}"
                               f"（客户端 getGachaDataKey 就是这么拼的）")

        # getgacha 回扁平三件套（不是套一层 gacha），并顺手点亮黑市红点
        gg = call("gacha.getgacha", {}, 191)
        gd = gg.get("data") or {}
        if not isinstance(gd.get("gachaMasterList"), dict):
            bad.append(f"gacha.getgacha 回包不对（要扁平的 gachaMasterList）：{str(gg)[:90]}")
        if not isinstance((gd.get("gacha") or {}).get("freeGachaTip"), bool):
            bad.append("gacha.getgacha 的 data.gacha.freeGachaTip 要是布尔"
                       "（黑市按钮红点靠 RESP-DISPATCH 写 _freeGachaTip）")

        # 图鉴：cards 必须是 **map**（键=角色 key）、upRate 必须是**字符串**
        lib = call("gacha.getlibraryshow", {"key": "1002"}, 192)
        ld = lib.get("data") or {}
        if not isinstance(ld.get("cards"), dict) or not ld["cards"]:
            bad.append(f"图鉴 cards 必须是 map（键=角色 key）：客户端是 `for (var k in cards)`，"
                       f"发数组会拿到下标 → getCharType(96) 报错、图鉴层崩。实得："
                       f"{type(ld.get('cards')).__name__} {str(ld.get('cards'))[:60]}")
        elif not all(isinstance(k, str) for k in ld["cards"]):
            bad.append(f"图鉴 cards 的键要是角色 key 字符串：{list(ld['cards'])[:3]!r}")
        if not isinstance(ld.get("upRate"), str):
            bad.append(f"upRate={ld.get('upRate')!r} 必须是字符串"
                       f"（客户端 upRate.split('#')，发 {{}} 会 TypeError 让图鉴层起不来）")

        # 抽一张付费单抽：扣钱 + 真发卡
        bag0 = dict(items.items_of(store.get_or_create_player(config.DEFAULT_ACCOUNT)))
        cost = gacha.consume_of("1002", "1")
        soldiers0 = len(store.ensure_soldiers(store.get_or_create_player(config.DEFAULT_ACCOUNT)))
        one = call("gacha.gacha", {"key": "1002", "times": "1"}, 193)
        d1 = one.get("data") or {}
        if one.get("code") != 200:
            bad.append(f"gacha.gacha 1002 code={one.get('code')} {str(one)[:90]}")
        cards = d1.get("cards") or []
        if len(cards) != 1:
            bad.append(f"抽一次却回了 {len(cards)} 张卡")
        if not all(isinstance(c, str) for c in cards):
            bad.append(f"cards 元素要是**角色 key 字符串**（客户端拿 key 查表画卡/播特效）："
                       f"{cards!r}")
        # gachaData 是**那一行**，不是 map
        gd_row = d1.get("gachaData")
        if not isinstance(gd_row, dict) or "totalTimes" not in gd_row:
            bad.append(f"gacha.gacha 的 gachaData 要是那一行（客户端 updateGachaData(key, "
                       f"result.gachaData)）：{str(gd_row)[:80]}")
        bag1 = dict(items.items_of(store.get_or_create_player(config.DEFAULT_ACCOUNT)))
        for (_t, k, c) in cost:
            if int(bag1.get(k) or 0) != int(bag0.get(k) or 0) - int(c):
                bad.append(f"抽卡没扣 {k}：{bag0.get(k)} -> {bag1.get(k)}")
        soldiers1 = len(store.ensure_soldiers(store.get_or_create_player(config.DEFAULT_ACCOUNT)))
        if soldiers1 <= soldiers0:
            bad.append(f"抽到的东西没进存档（军士 {soldiers0} -> {soldiers1}）"
                       f"—— 多半是 items._add_soldier 查错表（应是 table_soldier.card）")

        # 免费池每天 1 次：第一次 200、第二次 204
        # （用例前先把今天的免费次数清掉 —— 玩家自己可能已经抽过了，收尾会整体还原）
        player0 = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        gacha.reset_daily(player0, "1001", "1")["todayTimes"] = 0
        store.save_player(player0)
        free1 = call("gacha.gacha", {"key": "1001", "times": "1"}, 194)
        free2 = call("gacha.gacha", {"key": "1001", "times": "1"}, 195)
        if free1.get("code") != 200:
            bad.append(f"免费池第一次 code={free1.get('code')}（应该 200）")
        if free2.get("code") != 204:
            bad.append(f"免费池第二次 code={free2.get('code')}（应该 204 次数用完）")

        # 十连：次数 = 10、保底至少一张 S+
        ten = call("gacha.gacha", {"key": "1003", "times": "10"}, 196)
        td = ten.get("data") or {}
        if ten.get("code") != 200:
            bad.append(f"碎片十连 code={ten.get('code')} {str(ten)[:90]}")
        if len(td.get("cards") or []) != 10:
            bad.append(f"十连回了 {len(td.get('cards') or [])} 张卡")

        # 错误码
        if call("gacha.gacha", {"key": "9999", "times": "1"}, 197).get("code") != 202:
            bad.append("抽不存在的池子应该回 202（找不到抽卡信息）")
    finally:
        player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        for key, value in before.items():
            if value is None:
                player.pop(key, None)
            else:
                player[key] = value
        store.save_player(player)

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    print(f"  OK  抽卡：{len(masters)} 个池子（{', '.join(sorted(masters))}，各带 ×1/×10 两个按钮、"
          f"resIdx 合法）三件套都是 map、价格能算出来、抽一次真扣钱真发卡（cards 是角色 key、"
          f"gachaData 是那一行）、免费池每日 1 次、图鉴 upRate 是字符串、错误码 202/204 都对"
          f"（收尾已还原）")
    return ok


def char_manual_check(ok: bool) -> bool:
    """登录块 `char.charManual` 不能是空的 —— 空了「菜单 → 情报室」里一个角色都没有。

    情报室的列表是 `Illustratedcommonlayer._init` 里的
    `dataManager.character.getSoldierManualKeys()`，而它是

        for (k in _charManual)
            if (charManager.getCharType(k) === CHAR_TYPE.SOLDIER &&
                charManager.getSoldierCardType(k) === CARD_TYPE.TEAMMATE) res.push(k)

    ⇒ **`charManual` 空 = 情报室空**（实机表现就是「情报室里无角色」）。
    客户端自己按 `card_type` 过滤（自军/敌兵两个页签都吃这份），所以服务端把
    `table_soldier` 里出现过的角色 key 全发过去即可。
    """
    from gamesrv import items, store

    bad = []
    char = ((call("agent.getlogindata", {}, 200).get("data") or {}).get("char") or {})
    manual = char.get("charManual")
    if not isinstance(manual, dict) or not manual:
        bad.append(f"char.charManual 是空的（{type(manual).__name__}）→ 情报室里没角色")
    else:
        # 键必须是客户端查得到的角色 key，值要能合并（isNewHead）
        cards = (items.table("table_soldier") or {}).get("card") or {}
        known = {str(r.get("ck")) for r in cards.values() if isinstance(r, dict)}
        known |= {str(k) for k in (items.table("table_hero") or {})}
        known |= {str(k) for k in (items.table("table_mecha") or {})}
        unknown = [k for k in list(manual)[:200] if k not in known]
        if unknown:
            bad.append(f"charManual 里有客户端不认识的 key：{unknown[:5]}")
        if not all(isinstance(v, dict) for v in list(manual.values())[:20]):
            bad.append("charManual 的值要是对象（客户端读 isNewHead 并逐字段合并）")
        # 自军军士的数量应该 > 0（情报室第一个页签要有东西）
        self_keys = [k for k, v in (items.table("table_soldier") or {}).get("master", {}).items()
                     if str(v) == "1"]
        if self_keys and not any(k in manual for k in self_keys):
            bad.append("charManual 里一个「自军军士」都没有（情报室军士页会空）")

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    print(f"  OK  角色图鉴：登录块 char.charManual 有 {len(manual)} 个角色 key"
          f"（菜单→情报室的列表就是它的键，非空即不会「无角色」）")
    return ok


# 可以让 `python script\selftest_game.py --only 抽卡` 只跑其中一项：
# 自检里很多检查都会拉一次完整登录块（上百 KB，纯 Python DES 加密要 1 秒多），
# 全跑一轮 2 分半 —— 迭代时按名字跑单项能秒回。
ONLY = None


def run_check(ok: bool, name: str, fn) -> bool:
    """跑一项检查：支持 --only 过滤 + 打印用时（自检太慢，得知道时间花在哪）。"""
    if ONLY and ONLY not in name:
        return ok
    import time as _time

    print()
    t0 = _time.time()
    try:
        ok = fn(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD {name}自检异常: {exc}")
        ok = False
    short = name.replace("自检异常", "").replace("异常", "")
    print(f"  ..  [{short}] 用时 {_time.time() - t0:.1f}s")
    return ok


def main():
    global ONLY
    import argparse

    ap = argparse.ArgumentParser(description="业务协议自检（--only 只跑名字含该子串的检查）")
    ap.add_argument("--only", default=None, help="只跑名字里含这个子串的检查，例：--only 抽卡")
    args = ap.parse_args()
    ONLY = args.only

    cases = [
        ("agent.getlogindata", {}),
        ("agent.gettimeinfo", {}),
        ("agent.createplayer", {"activeCode": ""}),
        ("player.updateguidemark", {"mark": 3}),
        ("exchange.checkorder", {}),
        ("rank.getrankinglist", {}),
    ]
    ok = True
    if ONLY:
        cases = []          # --only 时不跑这批固定路由（省一次完整登录块 ≈ 省 1 秒多）
    for i, (route, msg) in enumerate(cases, 1):
        try:
            res = call(route, msg, i)
        except Exception as exc:  # noqa: BLE001
            print(f"  {route:28s} 请求失败: {exc}")
            ok = False
            continue
        code = res.get("code")
        flag = "OK " if code == 200 else "BAD"
        print(f"  {flag} {route:28s} code={code}  data={str(res.get('data'))[:70]}")
        if code != 200:
            ok = False

    ok = run_check(ok, "名单自检异常", roster_check)

    ok = run_check(ok, "军士补齐自检异常", replenish_check)

    print()
    try:
        snapshot_teams()          # 军士链路会把材料（军士）吃掉、顺带从队伍里摘掉
        ok = soldier_flow(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 军士链路异常: {exc}")
        ok = False
    try:
        restore_roster()
    except Exception as exc:  # noqa: BLE001
        print(f"  ..  收尾补军士失败（不影响结论）: {exc}")
    try:
        restore_teams()
    except Exception as exc:  # noqa: BLE001
        print(f"  ..  收尾放回编成失败（不影响结论）: {exc}")

    ok = run_check(ok, "编成保存自检异常", team_save_check)

    ok = run_check(ok, "分区关卡自检异常", subarea_check)

    ok = run_check(ok, "战斗结算自检异常", level_result_check)

    ok = run_check(ok, "分区成就自检异常", subarea_achievement_check)

    ok = run_check(ok, "交易所自检异常", exchange_check)

    ok = run_check(ok, "派遣自检异常", detect_check)

    ok = run_check(ok, "签到自检异常", sign_check)

    ok = run_check(ok, "功能开启弹窗自检异常", module_open_check)

    ok = run_check(ok, "演习场自检异常", arena_check)

    ok = run_check(ok, "角色图鉴自检异常", char_manual_check)

    ok = run_check(ok, "抽卡自检异常", gacha_check)

    ok = run_check(ok, "奖励图标自检异常", item_icon_check)

    ok = run_check(ok, "好感度自检异常", favor_check)

    print("\n全部通过 ✅" if ok else "\n有路由没回 200 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
