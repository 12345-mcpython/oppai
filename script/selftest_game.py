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


def main():
    cases = [
        ("agent.getlogindata", {}),
        ("agent.gettimeinfo", {}),
        ("agent.createplayer", {"activeCode": ""}),
        ("player.updateguidemark", {"mark": 3}),
        ("exchange.checkorder", {}),
        ("rank.getrankinglist", {}),
    ]
    ok = True
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

    print()
    try:
        ok = roster_check(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 名单自检异常: {exc}")
        ok = False

    print()
    try:
        ok = replenish_check(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 军士补齐自检异常: {exc}")
        ok = False

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

    print()
    try:
        ok = team_save_check(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 编成保存自检异常: {exc}")
        ok = False

    print()
    try:
        ok = subarea_check(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 分区关卡自检异常: {exc}")
        ok = False

    print()
    try:
        ok = level_result_check(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 战斗结算自检异常: {exc}")
        ok = False

    print()
    try:
        ok = subarea_achievement_check(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 分区成就自检异常: {exc}")
        ok = False

    print()
    try:
        ok = exchange_check(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 交易所自检异常: {exc}")
        ok = False

    print()
    try:
        ok = favor_check(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 好感度自检异常: {exc}")
        ok = False

    print("\n全部通过 ✅" if ok else "\n有路由没回 200 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
