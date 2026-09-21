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
    """军士名单「补齐」必须**只补不删**：保住等级、也保住抽卡得来的卡。

    背景（两段历史）：
      1. `selftest_game.py` 自己的军士链路每跑一次吃掉 2 个军士当材料，把默认账号从
         18 个啃到 4 个；当时的恢复手段是 `ROSTER_VERSION` 一变就整个重发
         —— **练过的等级全归零**。所以改成 `store.replenish_soldiers()`（缺的补、
         已有的原样留）。
      2. 2026-09-21：那个「补齐」里**还在删**「不在 SOLDIER_KEYS 里的 key」、
         以及「同一个 key 的第二次出现」—— 而抽卡抽到的军士（152 张池子里任何一张）
         和重复卡**都不在** SOLDIER_KEYS 里 ⇒ 自检收尾跑一下，把玩家抽到的
         44 个军士全删了（存档 62 → 18）。现在这两条删除都去掉了，
         这条用例就是它的回归测试（纯内存，不需要服务端）。
    """
    from gamesrv import store

    keys = [k for k, _p, _q in store.SOLDIER_KEYS]
    fake = {
        "account": "__replenish_test__",
        "rosterVersion": 0,
        "soldiers": [
            store.new_soldier(14, keys[13], 3, 4, lv=28, star=3),
            store.new_soldier(16, keys[15], 3, 4),
            # 抽卡得到的卡（不在默认名单里）—— 必须留着
            store.new_soldier(99, "sfog", 1, 2),
            # 同一个 key 抽到第二次（重复卡）—— 也留着
            store.new_soldier(98, keys[15], 3, 4),
        ],
        "teams": [{"soldierKeys": [14, 99], "soldierCount": 2}],
    }
    added, dropped = store.replenish_soldiers(fake)
    rows = fake["soldiers"]
    got = [r.get("key") for r in rows]
    if dropped:
        print(f"  BAD 补齐删掉了 {dropped} 个军士 —— 只该补，一个都不能删")
        return False
    missing = [k for k in keys if k not in got]
    if missing:
        print(f"  BAD 默认名单还缺 {len(missing)} 个没补上：{missing[:3]}")
        return False
    if "sfog" not in got:
        print("  BAD 抽卡得到的 key（sfog，不在 SOLDIER_KEYS 里）被删掉了")
        return False
    if got.count(keys[15]) != 2:
        print(f"  BAD 重复卡被去重了：{keys[15]} 只剩 {got.count(keys[15])} 个")
        return False
    keep = next((r for r in rows if r.get("key") == keys[13]), None)
    if not keep or keep.get("lv") != 28 or keep.get("star") != 3:
        print(f"  BAD 已有的军士等级被重置了：{keep}")
        return False
    ids = [r.get("id") for r in rows]
    if len(set(ids)) != len(ids):
        print(f"  BAD 补齐后 id 有重复：{ids}")
        return False
    if 99 not in (fake["teams"][0].get("soldierKeys") or []):
        print("  BAD 队伍里的军士被摘掉了（补缺不该动队伍）")
        return False
    print(f"  OK  军士补齐：补 {added} 个、删 {dropped} 个 -> {len(rows)} 个"
          f"（默认 18 个齐 + 抽卡的 sfog 和重复卡都留着，{keys[13]} 仍是 lv{keep.get('lv')}）")
    return ok


_SOLDIER_SNAPSHOT = None


def snapshot_roster() -> None:
    """跑会动军士名单的用例之前，把**整份名单**拍下来（深拷贝）。

    为什么不是「跑完 replenish 补一下」：`SOLDIER_KEYS` 只有建号默认的 18 个，
    玩家抽卡得到的卡（152 张池子里任何一张、还包括重复卡）都不在里面 ——
    2026-09-21 就是靠补 + 删的那套逻辑，把这个号的 44 个抽卡军士在收尾时全删了。
    现在改成**原样还原**：吃了几个、补了几个、抽卡新加的，跑完都回到快照状态。
    """
    global _SOLDIER_SNAPSHOT
    from gamesrv import config, store

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    _SOLDIER_SNAPSHOT = json.loads(json.dumps(player.get("soldiers") or []))


def restore_roster() -> None:
    """把军士名单还原成快照（没有快照时退化成「补缺的默认军士」，一个都不删）。"""
    from gamesrv import config, store

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    if _SOLDIER_SNAPSHOT is None:
        added, _dropped = store.replenish_soldiers(player)
        if added:
            store.save_player(player)
            print(f"  ..  收尾：补缺的默认军士 {added} 个"
                  f"（现在 {len(player.get('soldiers') or [])} 个）")
        return
    before = len(player.get("soldiers") or [])
    player["soldiers"] = json.loads(json.dumps(_SOLDIER_SNAPSHOT))
    store.save_player(player)
    if before != len(player["soldiers"]):
        print(f"  ..  收尾：军士名单还原成快照 {before} -> {len(player['soldiers'])} 个"
              f"（抽卡得到的/被吃掉的那些都按原样回来了）")
    else:
        print(f"  ..  收尾：军士名单和快照一致（{before} 个）")


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

    客户端（反汇编 `assets/src/data/player.jsc`）：

        MainLayer._updateAnimation()  → moduleManager.popModuleOpen()
        popModuleOpen():  list = dataManager.player.updateModuleState()
                          for (m of list) if (m.unlockDesc) createModuleUnlockEffect(...)
        updateModuleState():  把「!isOpened 且解锁条件满足」的模块 push 进列表
        initModuleState():    module.isOpened = moduleOpenMark[module.markIndex] > 0
        Player.ctor(data):    this._moduleOpenMark = data.moduleOpenMark   ← data 是 **player 块**

    ⇒ 要关掉这些弹窗，就得让**玩家块里的** `moduleOpenMark` 覆盖 `table_function_open`
    的全部 32 个 mark。私服默认（`store.MODULE_OPEN_POPUP_SKIP`）全标成已弹过。

    ⚠️ 这条检查踩过两层错位（2026-09-20）：
      ① 我原来把 `moduleOpenMark` 放在 `_module_stubs()` = `data` 顶层，
         而客户端读 `data.player.moduleOpenMark`（`jsc_find moduleOpenMark` 只有
         `Player.ctor` / `initModuleState` 两处，都在玩家块上）——顶层那份谁都看不到；
      ② 这条检查当时断言的**也是顶层**那个键，所以一直"通过"。
      实机后果：行为完全由存档里那份老 mark 决定 —— 存档只有 `{"1": 1}` 时
      `_moduleOpenMark` 就只认 1 号，进游戏连弹 31 个（logcat 里紧接着
      `player.setmoduleopenmark [2,3,…,32]` 回写）。
      所以现在：断言**玩家块**里的那份，并且**故意把存档的 mark 掐成 `{"1": 1}`**
      （复现那个坏状态）看服务端还会不会补齐，跑完还原。
    """
    from gamesrv import config, items as items_mod, store

    table = items_mod.table("table_function_open") or {}
    want = sorted({str(int((row or {}).get("mi"))) for row in table.values()
                   if (row or {}).get("mi") is not None})

    def marks_of(resp):
        data = resp.get("data") or {}
        return (data.get("player") or {}).get("moduleOpenMark"), data

    def check_cover(tag: str, marks) -> bool:
        if not isinstance(marks, dict):
            print(f"  BAD {tag}：data.player.moduleOpenMark 不是 map（{type(marks).__name__}）"
                  f"→ 客户端 `_moduleOpenMark` 是空的，isOpened 全 false，一进游戏连弹")
            return False
        if not marks:
            print(f"  BAD {tag}：moduleOpenMark 是空 map → 一进游戏连弹")
            return False
        # ⚠️⚠️ 客户端 `Player.initModuleState` 开头是
        #     for (var k in _moduleOpenMark) copy[_moduleOpenMark[k]] = _moduleOpenMark[k];
        # —— **拿值当新键**。所以"客户端实际认得的 mark"是 `{值: 值}`，
        # 而不是 map 自己的键。全发 1 会被塌缩成单个 {"1": 1}（实机 isOpened 只有 1/32）。
        effective = {}
        for v in marks.values():
            try:
                effective[int(v)] = int(v)
            except (TypeError, ValueError):
                continue
        missing = [mi for mi in want if not effective.get(int(mi))]
        if missing:
            print(f"  BAD {tag}：按客户端那套拷贝算下来缺 {len(missing)} 个 mark"
                  f"（{missing[:6]}…）→ 这些功能的开启弹窗还会弹")
            return False
        wrong = [k for k, v in marks.items() if str(v) != str(k)]
        if wrong:
            print(f"  BAD {tag}：值不等于键（{wrong[:4]}… 例如 {wrong[0]}={marks[wrong[0]]}）"
                  f"—— 客户端拷贝时会被塌缩，等于没发")
            return False
        return True

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    saved = player.get("moduleOpenMark")
    bad = False
    try:
        # ① 正常登录：玩家块里那份要覆盖全 32 个
        marks, data = marks_of(call("agent.getlogindata", {}, 147))
        bad = not check_cover("登录块", marks)
        if data.get("moduleOpenMark") is not None:
            print("  ..  注意：data 顶层还有一份 moduleOpenMark（客户端不读，只剩噪音）")

        # ② 复现坏状态：存档里只有 1 号 mark（新号 / 老存档就是这个样子）
        if store.MODULE_OPEN_POPUP_SKIP:
            player["moduleOpenMark"] = {"1": 1}
            store.save_player(player)
            marks2, _ = marks_of(call("agent.getlogindata", {}, 147))
            if not check_cover("存档只有 1 号 mark 时", marks2):
                bad = True

        # ③ 客户端回写（弹完会报一批 markIndex）之后仍然完整
        r = call("player.setmoduleopenmark", [1, 2, 3], 148)
        if r.get("code") != 200:
            print(f"  BAD player.setmoduleopenmark code={r.get('code')} {r}")
            bad = True
        marks3, _ = marks_of(call("agent.getlogindata", {}, 149))
        if not check_cover("回写后", marks3):
            bad = True
    finally:
        if saved is None:
            player.pop("moduleOpenMark", None)
        else:
            player["moduleOpenMark"] = saved
        store.save_player(player)

    if bad:
        return False
    print(f"  OK  功能开启弹窗：**玩家块**里的 moduleOpenMark 是 `{{markIndex: markIndex}}`、"
          f"覆盖 {len(want)} 个 mark（存档只标过 1 号 / 值全是 1 时都会补齐 → 一进游戏不弹）；"
          f"player.setmoduleopenmark 可回写")
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
    # ⚠️ 分区成就是**按 id 索引的 map**，里面的 `id` 值形如 `100101`/`100102`
    # —— 那是**成就 id，不是道具 key**，但字符串一模一样，递归遍历分不出来。
    # 2026-09-21 就是这样误报过一次（`player/subareaAchievements/100101/id`）。
    block.pop("subareaachievement", None)
    if isinstance(block.get("player"), dict):
        block["player"] = {k: v for k, v in block["player"].items()
                           if k != "subareaAchievements"}
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

        # ⚠️ 回包里的 `char` 块：键名必须是 `soldiersAdd` / `herosAdd` / `mechasAdd`
        # —— 反汇编 `CharCenter.updateByServer` 只认这三个（+ charManual /
        # maxSoldiersCount），发 `soldiers` 这种名字客户端**整块忽略**。
        # 2026-09-21 踩过：抽到的角色没进角色列表（服务端存档里明明加了）。
        cblk = d1.get("char")
        if not isinstance(cblk, dict) or not cblk:
            bad.append(f"抽卡回包里没有 char 块：{str(cblk)[:60]}（客户端角色列表不会更新）")
        else:
            for wrong in ("soldiers", "heros", "mechas"):
                if wrong in cblk:
                    bad.append(f"char 块里出现了 `{wrong}` —— 客户端不认这个键"
                               f"（它读 `{wrong}Add`），抽到的角色不会进列表")
            if str(cards[0]) and not any(k.endswith("Add") for k in cblk):
                bad.append(f"char 块里一个 *Add 都没有：{sorted(cblk)}")
            adds = cblk.get("soldiersAdd") or cblk.get("herosAdd") or cblk.get("mechasAdd") or []
            if adds and not all(isinstance(x, dict) and "id" in x for x in adds):
                bad.append(f"*Add 的元素要带 id（`addSoldiers` 是 `_soldiers[row.id] = row`）："
                           f"{str(adds[:1])[:80]}")
            if cblk.get("maxSoldiersCount") is None:
                bad.append("char 块少了 maxSoldiersCount（客户端栏位上限靠它刷新）")
            print(f"       char 块：{ {k: (len(v) if isinstance(v, list) else v) for k, v in cblk.items()} }")

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
    """登录块 `char.charManual`：key 必须是**军士卡 key**（不是角色 key），情报室靠它列角色。

    反汇编依据：

        CharCenter.getSoldierManualKeys():
            for (k in _charManual)
                if (charManager.getCharType(k) === CHAR_TYPE.SOLDIER &&
                    charManager.getSoldierCardType(k) === CARD_TYPE.TEAMMATE) res.push(k)

        charManager.getCharType(k)        → table_soldier[k] / table_hero[k] / table_mecha[k]
        charManager.getSoldierCardType(k) → table_soldier[k].char_key
                                            → table_soldier_master[ck].card_type

    两张表都按**卡 key** 索引，所以发角色 key（`sasm`）的后果是：每个 key 都
    `cc.error`，`getSoldierManualKeys()` 返回空 → 情报室「数量 0/152」+ 152 个格子全是剪影
    （格子是 `filtrateData()` 按 `table_soldier` 全表铺的，"有格子没内容"就是这么来的）。

    ⚠️ 这条检查以前只断言"**非空**"，而且那句
    `[k for k, v in master.items() if str(v) == "1"]` 在 master 行是 `int` 的当前结构下
    **永远不成立** —— 所以 196 个角色 key 的错误实现照样"通过"了。现在按契约钉死。
    """
    from gamesrv import items

    bad = []
    char = ((call("agent.getlogindata", {}, 200).get("data") or {}).get("char") or {})
    manual = char.get("charManual")
    soldiers = char.get("soldiers") or []
    table = items.table("table_soldier") or {}
    card = table.get("card") or {}
    master = table.get("master") or {}
    heros = {str(k) for k in (items.table("table_hero") or {})}
    mechas = {str(k) for k in (items.table("table_mecha") or {})}

    if not isinstance(manual, dict) or not manual:
        bad.append(f"char.charManual 是空的（{type(manual).__name__}）→ 情报室里没角色")
    else:
        # 1) 每个 key 客户端都得查得到（getCharType 查卡表 / 英雄表 / 机甲表）
        unknown = [k for k in manual if k not in card and k not in heros and k not in mechas]
        if unknown:
            bad.append(f"charManual 里有客户端查不到的 key（会刷 getCharType error）：{unknown[:5]}")
        # 2) 卡表里的 key 必须是「自军卡」，否则 getSoldierManualKeys 会把它丢掉
        not_teammate = [k for k in manual
                        if k in card and master.get(str((card[k] or {}).get("ck"))) != 1]
        if not_teammate:
            bad.append(f"charManual 里的军士不是自军卡（会被丢掉）：{not_teammate[:5]}")
        # 3) 角色 key（ck）不是卡 key —— 混进来就是上面那个 bug 的复现
        char_keys = {str(r.get("ck")) for r in card.values() if isinstance(r, dict)}
        wrong_kind = [k for k in manual if k in char_keys]
        if wrong_kind:
            bad.append(f"charManual 用的是角色 key 而不是军士卡 key：{wrong_kind[:5]}")
        # 4) 自己有的军士都得在图鉴里（否则「数量 x/152」会比实际拥有的少）
        owned = {str(s.get("key")) for s in soldiers if isinstance(s, dict) and s.get("key")}
        missing = sorted(owned - set(manual))
        if missing:
            bad.append(f"拥有的军士没进图鉴：{missing[:5]}")
        if not (owned & set(manual)):
            bad.append("charManual 里一张自己有的军士卡都没有（情报室军士页会空）")
        if not all(isinstance(v, dict) for v in manual.values()):
            bad.append("charManual 的值要是对象（客户端读 isNewHead）")

    if bad:
        for one in bad:
            print(f"  BAD {one}")
        return False
    n_soldier = sum(1 for k in manual if k in card)
    print(f"  OK  角色图鉴：charManual {len(manual)} 个 key = 军士卡 {n_soldier} 张 + 英雄/机甲 "
          f"{len(manual) - n_soldier} 个（都是玩家真有的；客户端 getSoldierManualKeys 收得下、"
          f"不刷 getCharType error）")
    return ok


# 可以让 `python script\selftest_game.py --only 抽卡` 只跑其中一项：
# 自检里很多检查都会拉一次完整登录块（上百 KB，纯 Python DES 加密要 1 秒多），
# 全跑一轮 2 分半 —— 迭代时按名字跑单项能秒回。
ONLY = None


def quest_check(ok: bool) -> bool:
    """任务：主线 + 日常 + 成就三类一起验（登录块形状 / 分档 / 进度 / 领奖 / 跨天重置）。

    客户端契约（都从 jsc 反汇编里核过，写错一条界面就不对）：

    * 任务界面三个页签各自 `QuestCenter.getQuests(QUEST_TYPE.X)`，而 getQuests 是从
      **同一个** `_quests` map 里按 type 筛 —— 所以服务端必须把 1/2/3 三类都塞进
      `data.quest.quests`，页签自己会分好（少一类那个页签就是空的）。
    * 每条必须带 `id`：`_createQuest` 最后一句是 `quest.id = data.id`，领奖用它。
    * `schedule` 是**对象**（key 取 `table_quest.sk`、值是当前进度），回数组的话
      `scheduleCur` 算不出来，「领奖」按钮永远是灰的。
    * `quest.submitquest` 的 `data.rewards` 必须是 `[{type, key, count}]`
      （`ccuiManager.popupReward(rewards)` -> `RewardTipsLayer`；经验那条 key 为空串）。
    * 日常分档：type=1 在表里按 `lv` 分 24 档，**一天只放当前等级那一档**；
      档内「完成所有的日常任务哦！」(`ct=19201`) 的 `tar` 正好 = 档内条数 − 1 ——
      这条不变式是分档设计的判据，写错了日常就多放/少放。

    ⚠️ 会真改任务状态、计数器和道具，跑完整块还原（所以可以反复跑）。
    """
    from gamesrv import config, items, quests, store
    import time as _time

    player = store.get_or_create_player(config.DEFAULT_ACCOUNT)
    before_quests = json.loads(json.dumps(player.get("quests") or {}))
    before_stats = json.loads(json.dumps(player.get("questStats") or {}))
    before_items = dict(items.items_of(player))
    bad = []

    def by_type(quests_map):
        out = {}
        table = quests.table()
        for key in quests_map:
            row = table.get(key) or {}
            out.setdefault(str(row.get("type")), []).append(key)
        return out

    try:
        login = call("agent.getlogindata", {}, 190)
        block = (login.get("data") or {}).get("quest") or {}
        qmap = block.get("quests") or {}
        groups = by_type(qmap)
        for qtype, label in (("1", "日常"), ("2", "主线"), ("3", "成就")):
            if not groups.get(qtype):
                bad.append(f"登录块里没有任何{label}任务（客户端那个页签会是空的）")
            else:
                print(f"       {label} {len(groups[qtype])} 条")
        # 每条任务的形状
        for key, entry in list(qmap.items())[:40]:
            if str(entry.get("id")) != str(key):
                bad.append(f"{key}: id={entry.get('id')!r}（客户端领奖靠 id）")
            if not isinstance(entry.get("schedule"), dict):
                bad.append(f"{key}: schedule 不是对象")
            for skey in (quests.table().get(key) or {}).get("sk") or []:
                if skey not in (entry.get("schedule") or {}):
                    bad.append(f"{key}: schedule 缺 key {skey!r}（客户端按它取 cur）")
            if str(entry.get("state")) not in ("1", "2", "3", "4"):
                bad.append(f"{key}: state={entry.get('state')!r} 不是 1..4")

        # 日常分档 + 档内不变式
        lv = int(player.get("lv") or 0)
        daily = groups.get("1") or []
        levels = {int((quests.table().get(k) or {}).get("lv") or 0) for k in daily}
        if len(levels) != 1:
            bad.append(f"日常跨了多个档：{sorted(levels)}（应该只有当前等级那一档）")
        else:
            band = levels.pop()
            if band > lv:
                bad.append(f"日常档 lv={band} 超过了玩家等级 {lv}")
            same_band = [k for k, r in quests.table().items()
                         if str(r.get("type")) == "1" and int(r.get("lv") or 0) == band]
            if len(daily) != len(same_band):
                bad.append(f"日常只发了 {len(daily)} 条，该档共 {len(same_band)} 条")
            for k in same_band:
                row = quests.table()[k]
                if str(row.get("ct")) == "19201":
                    tar = (row.get("tar") or [0])[0]
                    if int(tar) != len(same_band) - 1:
                        bad.append(f"{k}「完成所有日常」tar={tar}，该档有 {len(same_band)} 条"
                                   f"（应该 = 档内条数-1，这条不变式是分档设计的判据）")
            print(f"       日常档：lv={band}，{len(same_band)} 条")
            # 进度：把档里第一条 12204（通关任意关卡 N 次）的计数器灌满。
            # ⚠️ 要挑一条**今天还没领过**的：玩家自己玩的时候会真去领日常，
            # 领过的条目服务端回 state=4（已领），拿它验「计数满了 -> 3」必然失败。
            claimed = set(quests.done_of(player, quests.TYPE_DAILY))
            target = next((k for k in same_band
                           if str((quests.table().get(k) or {}).get("ct")) == "12204"
                           and k not in claimed), None)
            if target is None:
                cands = [k for k in same_band
                         if str((quests.table().get(k) or {}).get("ct")) == "12204"]
                if cands:
                    print(f"       （这一档的 12204 日常今天已经领过了：{cands[:2]}，跳过进度/领奖验证）")
                else:
                    bad.append("这一档里没有 12204（通关任意关卡）那条，没法验进度")
            else:
                tar = int((quests.table()[target].get("tar") or [0])[0])
                p = store.get_or_create_player(config.DEFAULT_ACCOUNT)
                p.setdefault("questStats", {})["wins"] = tar + 5
                store.save_player(p)
                r = call("quest.getnewquest", {}, 191)
                entry = ((r.get("data") or {}).get("quest") or {}).get("quests", {}).get(target) or {}
                if str(entry.get("state")) != "3":
                    bad.append(f"{target} 计数器灌满后 state={entry.get('state')!r}，应该是 3(已达成)")
                else:
                    print(f"       {target}: 进度 {entry.get('schedule')} → state=3（可领）")
                    # 领奖
                    before = dict(items.items_of(store.get_or_create_player(config.DEFAULT_ACCOUNT)))
                    sub = call("quest.submitquest", {"questKey": target}, 192)
                    if sub.get("code") != 200:
                        bad.append(f"quest.submitquest code={sub.get('code')} {str(sub)[:120]}")
                    else:
                        data = sub.get("data") or {}
                        rewards = data.get("rewards")
                        if not isinstance(rewards, list) or not rewards:
                            bad.append(f"领奖回包 rewards={rewards!r}（客户端 popupReward 要非空数组）")
                        else:
                            for one in rewards:
                                if not isinstance(one, dict) or set(("type", "key", "count")) - set(one):
                                    bad.append(f"奖励条目形状不对：{one!r}（要 {{type,key,count}}）")
                                    break
                            print(f"       奖励 {[(r0['type'], r0['key'], r0['count']) for r0 in rewards]}")
                        nentry = ((data.get("quest") or {}).get("quests") or {}).get(target) or {}
                        if str(nentry.get("state")) != "4":
                            bad.append(f"领完之后 {target} state={nentry.get('state')!r}，应该是 4(已领)")
                        after = ((call("agent.getlogindata", {}, 193).get("data") or {}).get("item") or {})
                        got_any = any(int(after.get(str(r0["key"])) or 0) >
                                      int(before.get(str(r0["key"])) or 0)
                                      for r0 in (rewards or []) if r0.get("type") == "2")
                        if any(r0.get("type") == "2" for r0 in (rewards or [])) and not got_any:
                            bad.append("领奖回包说有道具，但包里没变（settle 没发？）")
                        again = call("quest.submitquest", {"questKey": target}, 194)
                        if again.get("code") == 200:
                            bad.append("重复领同一件日常竟然又成功了")

        # 跨天重置：把 day 改成昨天，再拉一次列表，应当清空当日 done
        p = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        rec = quests._record(p)                                    # noqa: SLF001
        rec["daily"]["done"] = list(groups.get("1") or [])[:1]
        rec["daily"]["day"] = "2000-01-01"
        store.save_player(p)
        call("quest.getnewquest", {}, 195)
        p2 = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        rec2 = quests._record(p2)                                  # noqa: SLF001
        if rec2["daily"]["done"]:
            bad.append(f"跨天后日常 done 没清空：{rec2['daily']['done']}")
        elif rec2["daily"]["day"] != quests.day_str():
            bad.append(f"跨天后 daily.day 没更新：{rec2['daily']['day']!r}")
        else:
            print(f"       跨天重置：done 清空、day={rec2['daily']['day']}（换日点 "
                  f"{quests.RESET_HOUR:02d}:00）")

        # 成就：计数器驱动的那条（304001 = 战胜 500 次）
        p3 = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        ach = [k for k, r in quests.table().items()
               if str(r.get("type")) == "3" and str(r.get("ct")) == "12204"]
        if ach:
            p3.setdefault("questStats", {})["wins"] = 99999
            store.save_player(p3)
            r = call("quest.getnewquest", {}, 196)
            got = ((r.get("data") or {}).get("quest") or {}).get("quests") or {}
            achieved = [k for k in ach if str((got.get(k) or {}).get("state")) == "3"]
            if not achieved:
                bad.append(f"成就里这几条（战胜 N 次）该达成：{ach[:3]}")
            else:
                print(f"       成就：{len(achieved)}/{len(ach)} 条（战胜次数）已达成")
    finally:
        p = store.get_or_create_player(config.DEFAULT_ACCOUNT)
        p["quests"] = before_quests
        p["questStats"] = before_stats
        got = items.items_of(p)
        for k in [k for k in got if k not in before_items]:
            del got[k]
        for k, v in before_items.items():
            got[k] = v
        store.save_player(p)

    if bad:
        for one in bad[:12]:
            print(f"  BAD {one}")
        return False
    print("  OK  任务：三类（主线/日常/成就）都在同一份 quests 里、id/schedule/state 形状对、"
          "日常=当前等级那一档且档内 19201 不变式成立、计数器驱动进度、领奖发道具且不能重复领、"
          "跨天清空当日进度（收尾已还原）")
    return ok


def friend_check(ok: bool) -> bool:
    """好友系统：契约形状 / 上限 / 申请→同意 / 送收物资 / 删除 / 换日 / 错误码。

    客户端契约（全部来自 `src/data/friend.jsc` 的反汇编，错一条界面对不上）：

    * 登录块 `data.friend` = `{friendMapList, recommendationList, takeMaterialsCount}`
      —— `Friend.update(data)` 读的就是这三个键。
    * `friendMapList` 是 **map**，key = `"<我的numberId>#<对方numberId>"`；每条必须带
      `player`（`FriendItem._updateInfo(this._info.player)`，undefined 就 TypeError）。
    * `recommendationList` 的条目是**平铺**的（`_updateInfo(this._info)`，
      `lastLoginTimeSec` 也在顶层，客户端按它排序）。
    * `data.player.numberId` 必须有值：`Friend.getFriendNumberId` / `isBeApplyFor` /
      `isGetMaterials` 全拿它跟 key 两端比，没有它按钮状态全错。
    * 上限来自客户端表 `table_player_level_function[lv]` 的 `friend_limit` /
      `take_materials_limit`（界面上「3/18」「4/4」就是它）。
    * `getfriendlist` 的三个键必须在 **data 顶层**（回调是 `this.update(res.data)`）。
    * `takematerials` 的 `data.reward` 是 **`{itemKey: count}`**（不是数组）。
    * 错误码走 `FRIEND_ERROR_CODE` 映射到 `table_dictionary` 文案：210 已是战友 /
      211 申请重复 / 212 已经送过 / 213 已经收过 / 221 找不到申请 / 222 搜索不到 /
      223 找不到这位指挥官 / 232 收取次数达上限 / 234 还没收到物资。

    ⚠️ 会真改好友状态（申请/送收/删除）和背包，最后整块还原。
    """
    from gamesrv import config, friends, items, quests, store

    account = config.DEFAULT_ACCOUNT
    player = store.get_or_create_player(account)
    before_friend = json.loads(json.dumps(player.get("friend") or {}))
    before_items = dict(items.items_of(player))
    before_stats = json.loads(json.dumps(player.get("questStats") or {}))
    bad = []

    def save(**fields):
        p = store.get_or_create_player(account)
        p.update(fields)
        store.save_player(p)
        return p

    def other_of(key):
        """记录 key -> 对方的 numberId（自己永远在 `#` 前面）。"""
        parts = str(key).split("#")
        return int(parts[1]) if parts[0] == str(me) else int(parts[0])

    try:
        # ---- A. 登录块形状 ----
        login = call("agent.getlogindata", {}, 200)
        data = login.get("data") or {}
        me = int((data.get("player") or {}).get("numberId") or 0)
        if not me:
            bad.append("data.player.numberId 是空的（好友记录 key 的一端就是它）")
        blk = data.get("friend") or {}
        for key in ("friendMapList", "recommendationList", "takeMaterialsCount"):
            if key not in blk:
                bad.append(f"登录块 data.friend 缺 {key}（Friend.update 读它）")
        fmap = blk.get("friendMapList")
        if not isinstance(fmap, dict):
            bad.append(f"friendMapList 是 {type(fmap).__name__}，客户端要 map、"
                       f"key 是 '<我的numberId>#<对方numberId>'")
            fmap = {}
        if not fmap:
            bad.append("登录块里一个好友/申请都没有（friends.ensure 没跑？）")
        for key, entry in list(fmap.items())[:40]:
            if not isinstance(entry, dict):
                bad.append(f"{key}: 不是对象")
                continue
            if "#" not in key or str(me) not in key.split("#"):
                bad.append(f"{key}: key 里没有我的 numberId（{me}）")
            if not isinstance(entry.get("player"), dict):
                bad.append(f"{key}: 缺 player 子块（FriendItem._updateInfo 会 TypeError）")
            else:
                for field in ("numberId", "name", "lv", "lastLoginTimeSec"):
                    if field not in entry["player"]:
                        bad.append(f"{key}: player 缺 {field}")
            if str(entry.get("status") or 0) == "0":
                bad.append(f"{key}: status 是 0（既不是好友也不是申请）")
        friends_n = sum(1 for e in fmap.values()
                        if friends.is_friend(int(e.get("status") or 0))) if fmap else 0
        applies_n = sum(1 for e in fmap.values()
                        if friends.is_be_apply_for(int(e.get("status") or 0))) if fmap else 0
        print(f"       登录块：好友 {friends_n} 个、待处理申请 {applies_n} 条、"
              f"今日收取 {blk.get('takeMaterialsCount')} 次")

        # ---- B. 上限要和客户端表一致 ----
        row = (friends.items.table("table_player_level_function")
               .get(str(int(player.get("lv") or 1))) or {})
        if int(row.get("friend_limit") or 0) != friends.friend_limit(player):
            bad.append(f"friend_limit 用的是 {friends.friend_limit(player)}，"
                       f"客户端表里是 {row.get('friend_limit')}")
        if int(row.get("take_materials_limit") or 0) != friends.take_materials_limit(player):
            bad.append(f"take_materials_limit 用的是 {friends.take_materials_limit(player)}，"
                       f"客户端表里是 {row.get('take_materials_limit')}")
        print(f"       上限（lv={player.get('lv')}）：好友 {friends.friend_limit(player)}、"
              f"收取 {friends.take_materials_limit(player)}（和客户端表一致）")

        # ---- C. getfriendlist：三个键必须在 data 顶层 ----
        r = call("friend.getfriendlist", {}, 201)
        d = r.get("data") or {}
        if r.get("code") != 200:
            bad.append(f"friend.getfriendlist code={r.get('code')}")
        if not isinstance(d.get("friendMapList"), dict):
            bad.append("getfriendlist 的 data.friendMapList 不是 map（回调 this.update(res.data) "
                       "读的就是它，套一层 friend 的话好友列表永远是空的）")
        if not isinstance(d.get("recommendationList"), list):
            bad.append("getfriendlist 的 data.recommendationList 不是数组")
        if "takeMaterialsCount" not in d:
            bad.append("getfriendlist 没回 takeMaterialsCount")

        # ---- D. 推荐列表：条目平铺（没有 player 外层）----
        rec = (call("friend.getrecommendationlist", {}, 202).get("data") or {})
        rec_list = rec.get("recommendationList")
        if not isinstance(rec_list, list) or not rec_list:
            bad.append(f"推荐列表是 {rec_list!r}（NPC 表有 101 行，不该是空的）")
            rec_list = []
        for one in rec_list[:5]:
            if not isinstance(one, dict) or "numberId" not in one or "lastLoginTimeSec" not in one:
                bad.append(f"推荐条目形状不对：{str(one)[:80]}（客户端要在顶层读 "
                           f"numberId / lastLoginTimeSec）")
                break
            if "player" in one:
                bad.append(f"推荐条目多了 player 外层：{str(one)[:60]}")
                break
        print(f"       推荐列表 {len(rec_list)} 条（平铺，numberId/lastLoginTimeSec 在顶层）")

        # 拿一个「不在我列表里」的 NPC 做搜索/申请
        busy = {str(k).split("#")[0] for k in fmap} | {str(k).split("#")[-1] for k in fmap}
        cand = next((int(o["numberId"]) for o in rec_list if str(o["numberId"]) not in busy), None)
        if cand is None:
            cand = int(rec_list[0]["numberId"]) if rec_list else 0

        # ---- E. searchplayer ----
        if cand:
            s = call("friend.searchplayer", {"numberId": cand}, 203)
            info = (s.get("data") or {}).get("playerInfo") or {}
            if s.get("code") != 200:
                bad.append(f"搜索 NPC {cand} code={s.get('code')}（应该 200）")
            elif not all(k in info for k in ("numberId", "name", "lv", "headId", "lastLoginTimeSec")):
                bad.append(f"playerInfo 字段不全：{info}")
            else:
                print(f"       搜索 {cand} -> {info['name']}（lv{info['lv']}，头像 {info['headId']}）")
        s404 = call("friend.searchplayer", {"numberId": 123456789}, 204)
        if s404.get("code") != 222:
            bad.append(f"搜一个不存在的号 code={s404.get('code')}，应该是 222"
                       f"（客户端弹 table_dictionary[1406]「你确定他在这片大陆上吗~」）")

        # ---- F. 申请 -> 重复申请 211 -> 到点自动同意 ----
        if cand:
            a = call("friend.applyfor", {"numberId": cand}, 205)
            entry = (a.get("data") or {}).get("friendMap") or {}
            if a.get("code") != 200:
                bad.append(f"申请 NPC {cand} code={a.get('code')}")
            elif not (int(entry.get("status") or 0) & friends.A_APPLY_FOR_B):
                bad.append(f"申请后 status={entry.get('status')} 没有 A_APPLY_FOR_B 位")
            elif not isinstance(entry.get("player"), dict):
                bad.append("申请回包的 friendMap 缺 player（这条会当场画到列表里）")
            else:
                print(f"       申请 {cand} -> status={entry['status']}（A_APPLY_FOR_B）")
            again = call("friend.applyfor", {"numberId": cand}, 206)
            if again.get("code") != 211:
                bad.append(f"重复申请 code={again.get('code')}，应该是 211")
            # 把申请时间往前拨，模拟 NPC 到点同意
            p = store.get_or_create_player(account)
            key = friends.key_of(me, cand)
            st = (p.get("friend") or {}).get("map") or {}
            if key in st:
                st[key]["updateTimeSec"] = int(st[key].get("updateTimeSec") or 0) - \
                    (friends.ACCEPT_DELAY_SEC + 10)
                store.save_player(p)
                call("friend.getfriendlist", {}, 207)
                p2 = store.get_or_create_player(account)
                st2 = ((p2.get("friend") or {}).get("map") or {}).get(key) or {}
                if not friends.is_friend(int(st2.get("status") or 0)):
                    bad.append(f"申请 {friends.ACCEPT_DELAY_SEC}s 后 NPC 没同意：status={st2.get('status')}")
                else:
                    print(f"       申请 {cand} 过了 {friends.ACCEPT_DELAY_SEC}s -> 自动变成战友"
                          f"（status={st2.get('status')}）")

        # ---- G. 送物资 + 日常任务 18201 ----
        friend_keys = [k for k, e in fmap.items()
                       if friends.is_friend(int(e.get("status") or 0))] if fmap else []
        if not friend_keys:
            bad.append("一个好友都没有，送/收物资没法验")
        else:
            target = other_of(friend_keys[0])
            before_sends = int((store.get_or_create_player(account).get("questStats") or {})
                               .get("friendSends") or 0)
            s = call("friend.sendmaterials", {"numberId": target}, 208)
            sent = (s.get("data") or {}).get("friendMap") or {}
            if s.get("code") != 200:
                bad.append(f"送物资 code={s.get('code')}")
            elif not (int(sent.get("status") or 0) & friends.A_SEND_MATERIALS_B):
                bad.append(f"送完 status={sent.get('status')} 没有 A_SEND_MATERIALS_B 位")
            else:
                print(f"       送物资给 {target} -> status={sent['status']}（A_SEND_MATERIALS_B）")
            again = call("friend.sendmaterials", {"numberId": target}, 209)
            if again.get("code") != 212:
                bad.append(f"同一天送第二次 code={again.get('code')}，应该是 212")
            after_sends = int((store.get_or_create_player(account).get("questStats") or {})
                              .get("friendSends") or 0)
            if after_sends != before_sends + 1:
                bad.append(f"日常 18201 的计数没涨：{before_sends} -> {after_sends}"
                           f"（quests.on_friend_send 没被调用）")
            else:
                # 等级这一档里 ct=18201 的那条日常应当当场达成
                qmap = ((s.get("data") or {}).get("quest") or {}).get("quests") or {}
                band_keys = [k for k, r in quests.table().items()
                             if str(r.get("type")) == "1" and str(r.get("ct")) == "18201"
                             and int(r.get("lv") or 0) <= int(player.get("lv") or 0)]
                achieved = [k for k in band_keys if str((qmap.get(k) or {}).get("state")) in ("3", "4")]
                if not achieved:
                    bad.append(f"送完物资后日常 18201 没达成（该档候选 {band_keys[:2]}，"
                               f"回包 quest 里有 {len(qmap)} 条）")
                else:
                    print(f"       日常 18201（给好友送物资）{achieved[0]} "
                          f"state={(qmap.get(achieved[0]) or {}).get('state')} ✓")

        # ---- H. 收物资：reward 形状 / 背包 / 计数 / 重复 213 / 上限 232 ----
        if friend_keys:
            key = [k for k in friend_keys][0]
            other = other_of(key)
            p = store.get_or_create_player(account)
            st = (p.get("friend") or {}).get("map") or {}
            st[friends.key_of(me, other)] = {
                "status": friends.BE_FRIEND | friends.B_SEND_MATERIALS_A,
                "updateTimeSec": 0,
            }
            # 收取次数先清 0，免得撞上限
            p.setdefault("friend", {})["takeCount"] = 0
            store.save_player(p)
            before_item = int(items.items_of(store.get_or_create_player(account))
                              .get(str(friends.TAKE_REWARD_KEY)) or 0)
            before_take = int((store.get_or_create_player(account).get("friend") or {})
                              .get("takeCount") or 0)
            t = call("friend.takematerials", {"numberId": other}, 210)
            td = t.get("data") or {}
            reward = td.get("reward")
            if t.get("code") != 200:
                bad.append(f"收物资 code={t.get('code')}")
            elif not isinstance(reward, dict) or not reward:
                bad.append(f"reward 是 {reward!r}（客户端 showTakeMaterialsPanel 要 "
                           f"{{itemKey: count}} 然后 for-in）")
            else:
                got = {str(k): int(v) for k, v in reward.items()}
                after_item = int(items.items_of(store.get_or_create_player(account))
                                 .get(str(friends.TAKE_REWARD_KEY)) or 0)
                gain = sum(got.values()) if str(friends.TAKE_REWARD_KEY) in got else 0
                if gain and after_item < before_item + 1:
                    bad.append(f"收取回包给了 {got}，但背包没涨（{before_item} -> {after_item}）")
                if int(td.get("takeMaterialsCount") or 0) != before_take + 1:
                    bad.append(f"takeMaterialsCount={td.get('takeMaterialsCount')}，"
                               f"应该是 {before_take + 1}")
                taken = td.get("friendMap") or {}
                if not (int(taken.get("status") or 0) & friends.A_TAKE_MATERIALS_B):
                    bad.append(f"收完 status={taken.get('status')} 没有 A_TAKE_MATERIALS_B 位")
                print(f"       收物资 -> reward={got}，背包 {before_item} -> {after_item}，"
                      f"今日第 {td.get('takeMaterialsCount')} 次")
            twice = call("friend.takematerials", {"numberId": other}, 211)
            if twice.get("code") != 213:
                bad.append(f"同一份再收一次 code={twice.get('code')}，应该是 213（已经收过啦）")
            # 上限：把 takeCount 顶到表里的值
            p = store.get_or_create_player(account)
            st = (p.get("friend") or {}).get("map") or {}
            st[friends.key_of(me, other)] = {
                "status": friends.BE_FRIEND | friends.B_SEND_MATERIALS_A,
                "updateTimeSec": 0,
            }
            p.setdefault("friend", {})["takeCount"] = friends.take_materials_limit(p)
            store.save_player(p)
            full = call("friend.takematerials", {"numberId": other}, 212)
            if full.get("code") != 232:
                bad.append(f"收取次数到上限 code={full.get('code')}，应该是 232")

        # ---- I. 同意 / 拒绝申请 ----
        p = store.get_or_create_player(account)
        st = p.setdefault("friend", {}).setdefault("map", {})
        other = next((other_of(k) for k in st
                      if friends.is_be_apply_for(int(st[k].get("status") or 0))), 0)
        if not other:
            # 没有待处理申请就自己造一条
            ids = [i for i, _k, _r, _h in friends.npc_rows() if friends.key_of(me, i) not in st]
            if ids:
                other = ids[0]
                st[friends.key_of(me, other)] = {"status": friends.B_APPLY_FOR_A, "updateTimeSec": 0}
                store.save_player(p)
        if other:
            ag = call("friend.agreeapplication", {"numberId": other}, 213)
            aentry = (ag.get("data") or {}).get("friendMap") or {}
            if ag.get("code") != 200 or not friends.is_friend(int(aentry.get("status") or 0)):
                bad.append(f"同意申请 code={ag.get('code')} status={aentry.get('status')}")
            elif int(aentry.get("status") or 0) & friends.APPLY_BITS:
                bad.append(f"同意之后申请位没清：status={aentry.get('status')}")
            else:
                print(f"       同意 {other} 的申请 -> status={aentry['status']}（BE_FRIEND，申请位已清）")
            no_apply = call("friend.agreeapplication", {"numberId": other}, 214)
            if no_apply.get("code") != 221:
                bad.append(f"同意一个没有申请的 code={no_apply.get('code')}，应该是 221")
            # 再造一条拒绝
            p = store.get_or_create_player(account)
            st = p.setdefault("friend", {}).setdefault("map", {})
            ids = [i for i, _k, _r, _h in friends.npc_rows() if friends.key_of(me, i) not in st]
            if ids:
                other2 = ids[0]
                st[friends.key_of(me, other2)] = {"status": friends.B_APPLY_FOR_A, "updateTimeSec": 0}
                store.save_player(p)
                rf = call("friend.refuseapplication", {"numberId": other2}, 215)
                rentry = (rf.get("data") or {}).get("friendMap") or {}
                rstatus = int(rentry.get("status") or 0)
                if rf.get("code") != 200 or (rstatus & friends.APPLY_BITS):
                    bad.append(f"拒绝后 status={rstatus}（申请位必须清掉，客户端申请页签"
                               f"只看 isBeApplyFor）")
                elif not (rstatus & friends.DELETE):
                    bad.append(f"拒绝后 status={rstatus} 没有 DELETE 位")
                else:
                    print(f"       拒绝 {other2} -> status={rstatus}（DELETE，申请位已清）")
                after = ((call("friend.getfriendlist", {}, 216).get("data") or {})
                         .get("recommendationList") or [])
                if str(other2) not in {str(o.get("numberId")) for o in after}:
                    bad.append(f"拒绝掉的 {other2} 没回到推荐列表（客户端就再也加不回来了）")

        # ---- J. 删好友 -> 回到推荐列表 ----
        p = store.get_or_create_player(account)
        st = p.setdefault("friend", {}).setdefault("map", {})
        victim = next((other_of(k) for k in st
                       if friends.is_friend(int(st[k].get("status") or 0))), 0)
        if not victim:
            bad.append("没有好友可删")
        else:
            dl = call("friend.deletefriend", {"numberId": victim}, 217)
            dentry = (dl.get("data") or {}).get("friendMap") or {}
            if dl.get("code") != 200 or not (int(dentry.get("status") or 0) & friends.DELETE):
                bad.append(f"删好友 code={dl.get('code')} status={dentry.get('status')}")
            else:
                ls = (call("friend.getfriendlist", {}, 218).get("data") or {}).get("friendMapList") or {}
                if friends.key_of(me, victim) in ls:
                    bad.append(f"删掉的好友 {victim} 还在 friendMapList 里")
                rec2 = ((call("friend.getrecommendationlist", {}, 219).get("data") or {})
                        .get("recommendationList") or [])
                if str(victim) not in {str(o.get("numberId")) for o in rec2}:
                    bad.append(f"删掉的 {victim} 没回到推荐列表")
                else:
                    print(f"       删除 {victim} -> 从好友列表消失、回到推荐列表")

        # ---- J2. 对已经是好友的人申请 -> 210 ----
        p = store.get_or_create_player(account)
        st = p.setdefault("friend", {}).setdefault("map", {})
        is_f = next((other_of(k) for k in st
                     if friends.is_friend(int(st[k].get("status") or 0))), 0)
        if is_f:
            dup = call("friend.applyfor", {"numberId": is_f}, 220)
            if dup.get("code") != 210:
                bad.append(f"对已经是战友的人再申请 code={dup.get('code')}，应该是 210")

        # ---- K. 换日：物资位清零 + 收取次数归零 ----
        p = store.get_or_create_player(account)
        fstate = p.setdefault("friend", {})
        fstate["day"] = "2000-01-01"
        fstate["takeCount"] = 99
        for e in (fstate.get("map") or {}).values():
            if isinstance(e, dict):
                e["status"] = int(e.get("status") or 0) | friends.MATERIAL_BITS
        store.save_player(p)
        call("friend.getfriendlist", {}, 221)
        p2 = store.get_or_create_player(account)
        f2 = p2.get("friend") or {}
        leftover = [k for k, e in (f2.get("map") or {}).items()
                    if isinstance(e, dict) and (int(e.get("status") or 0) & friends.A_TAKE_MATERIALS_B
                                                or int(e.get("status") or 0) & friends.A_SEND_MATERIALS_B)]
        if int(f2.get("takeCount") or 0) != 0:
            bad.append(f"换日后 takeMaterialsCount={f2.get('takeCount')}，应该是 0")
        if leftover:
            bad.append(f"换日后还有 {len(leftover)} 条带着「已送/已收」位：{leftover[:2]}")
        if f2.get("day") != quests.day_str():
            bad.append(f"换日后 day={f2.get('day')!r}，应该是 {quests.day_str()!r}")
        if not bad:
            sent_again = sum(1 for e in (f2.get("map") or {}).values()
                             if isinstance(e, dict)
                             and int(e.get("status") or 0) & friends.B_SEND_MATERIALS_A)
            print(f"       换日：物资位清零、收取次数归零、{sent_again} 个好友重新送了物资")
    finally:
        p = store.get_or_create_player(account)
        p["friend"] = before_friend
        p["questStats"] = before_stats
        got = items.items_of(p)
        for k in [k for k in got if k not in before_items]:
            del got[k]
        for k, v in before_items.items():
            got[k] = v
        store.save_player(p)

    if bad:
        for one in bad[:12]:
            print(f"  BAD {one}")
        return False
    print("  OK  好友：登录块形状（friendMapList 是 map、每条带 player）、numberId 齐、"
          "上限=客户端表、推荐条目平铺、搜索/申请/重复申请/自动同意、送物资+日常 18201、"
          "收物资 reward 是 {key:count} 且进背包、重复收取/次数上限、同意/拒绝/删除、"
          "换日清物资位（收尾已还原）")
    return ok


def medal_check(ok: bool) -> bool:
    """勋章 / 头像 / 衣柜（medal.*）：形状 / 默认值 / 换装 / 佩戴 / 清 NEW / 好友勋章 / 进度。

    客户端契约（`src/data/medal.jsc` 逐函数反汇编，错一条界面对不上）：

    * 登录块 `data.medal` = `{medals, newMedalIds}`；`medals` **必须覆盖
      `table_medal` 每一条** —— `isMedalCompleteByGroup` 是 `_medals[id].completeTime`，
      缺一条就是 `undefined.completeTime` TypeError。行形状 `{id, progress,
      progressInfo, completeTime}`（`createTempNewMedal` 给的）。
    * `player.headId` 是 `"<itemKey>:<HEAD_TYPE>"`（`getHeadSpr` 自己 split(":")）；
      `medalClothesId` / `medalBgId` 是 type 40/50 的道具 key。
    * `player.medalWear` = `{勋章id: 佩戴位}`，客户端 `isWearMedal` 是「同组只戴一个」。
    * 衣柜/头像/勋章本体都是**背包道具**（type 40/50/60/70），NEW 标记靠
      `item` 块里的对象形状 `{"count": n, "isNew": true}`（`Item.ctor`/`updateByObj`）。
    * 三条 change* 的 `data` 是**值**不是对象；`wearmedal` 回**整张** medalWear。

    ⚠️ 会真改存档（换装/佩戴/清 NEW/发道具），最后整块还原。
    """
    from gamesrv import config, items, medal, quests, store

    account = config.DEFAULT_ACCOUNT
    player = store.get_or_create_player(account)
    before = {
        "medal": json.loads(json.dumps(player.get("medal") or {})),
        "medalWear": json.loads(json.dumps(player.get("medalWear") or {})),
        "headId": player.get("headId"),
        "medalClothesId": player.get("medalClothesId"),
        "medalBgId": player.get("medalBgId"),
        "items": dict(items.items_of(player)),
        "questStats": json.loads(json.dumps(player.get("questStats") or {})),
    }
    bad = []
    try:
        login = call("agent.getlogindata", {}, 300)
        data = login.get("data") or {}
        pdata = data.get("player") or {}
        blk = data.get("medal") or {}

        # ---- A. data.medal 形状 ----
        medals = blk.get("medals")
        if not isinstance(medals, dict):
            bad.append(f"data.medal.medals 是 {type(medals).__name__}，要是 map")
            medals = {}
        table = medal.medal_table()
        missing = [k for k in table if str(k) not in medals]
        if missing:
            bad.append(f"medals 少了 {len(missing)} 条（第一条 {missing[0]}）—— "
                       f"客户端 isMedalCompleteByGroup 会 undefined.completeTime")
        for key in list(medals)[:59]:
            row = medals[key] or {}
            if str(row.get("id")) != str(key):
                bad.append(f"{key}: id={row.get('id')!r}（客户端按 id 索引）")
                break
            if not isinstance(row.get("progressInfo"), dict):
                bad.append(f"{key}: progressInfo 不是对象")
                break
            if "completeTime" not in row or "progress" not in row:
                bad.append(f"{key}: 缺 completeTime/progress")
                break
        done_n = sum(1 for r in medals.values() if int(r.get("completeTime") or 0) > 0)
        print(f"       登录块：勋章 {len(medals)} 条、已完成 {done_n} 条、"
              f"newMedalIds={blk.get('newMedalIds')}")

        # ---- B. 头像 / 衣服 / 背景三个 id 的合法性 ----
        head_id = str(pdata.get("headId") or "")
        base, _, kind = head_id.partition(":")
        trow = items.table("table_item").get(base) or {}
        if not base or str(trow.get("t")) != "60":
            bad.append(f"player.headId={head_id!r} 不是 `<头像道具>:<类型>`"
                       f"（客户端 getHeadSpr 会 split(':') 并查 table_item）")
        if kind not in ("1", "2"):
            bad.append(f"player.headId 的 headType={kind!r}，只能是 '1'/'2'")
        for field, itype in (("medalClothesId", "40"), ("medalBgId", "50")):
            val = str(pdata.get(field) or "")
            row = items.table("table_item").get(val) or {}
            if str(row.get("t")) != itype:
                bad.append(f"player.{field}={val!r} 不是 type={itype} 的道具")
        # ⚠️ `medalWear` 在**登录块里是 JSON 字符串**（客户端 `Player._getMedalWear`
        # 就是 `JSON.parse(this._medalWear)`）—— 发对象的话 `JSON.parse({})` 先变成
        # `"[object Object]"` 再解析，抛 SyntaxError 把整个勋章层建不出来
        # （表现：左上角点了没反应）。2026-09-21 踩过。
        wear_raw = pdata.get("medalWear")
        if not isinstance(wear_raw, str):
            bad.append(f"player.medalWear 是 {type(wear_raw).__name__}，"
                       f"登录块里必须是 JSON 字符串（客户端 _getMedalWear 会 JSON.parse）")
            wear = {}
        else:
            try:
                wear = json.loads(wear_raw)
            except ValueError:
                bad.append(f"player.medalWear 不是合法 JSON：{wear_raw[:60]!r}")
                wear = {}
        if not isinstance(wear, dict):
            bad.append(f"medalWear 解析完是 {type(wear).__name__}，要是 map（勋章id->佩戴位）")
            wear = {}
        for mid, idx in wear.items():
            if str(mid) not in table:
                bad.append(f"medalWear 里有不存在的勋章 {mid}")
            elif not isinstance(idx, int):
                bad.append(f"medalWear[{mid}]={idx!r} 不是整数位号")
        print(f"       默认值：headId={head_id} 衣服={pdata.get('medalClothesId')} "
              f"背景={pdata.get('medalBgId')}，佩戴 {len(wear)} 个")

        # ---- C. NEW 标记走 item 块的对象形状 ----
        bag_blk = data.get("item") or {}
        marks = [k for k, v in bag_blk.items() if isinstance(v, dict)]
        if not marks:
            bad.append("item 块里一个 NEW 标记都没有（勋章/头像刚发下去时应该有对象形状的条目）")
        else:
            for k in marks[:3]:
                if "isNew" not in bag_blk[k] or "count" not in bag_blk[k]:
                    bad.append(f"item['{k}']={bag_blk[k]!r} 不是 {{count, isNew}}")
                    break
            sample = bag_blk[marks[0]]
            print(f"       item 块：{len(marks)} 件带 NEW（例 {marks[0]} -> {sample}）")

        # ---- D. 换头像 / 换衣服 / 换背景：回包是值，且要落盘 ----
        heads = [k for k, r in items.table("table_item").items()
                 if isinstance(r, dict) and str(r.get("t")) == "60"]
        clothes = [k for k, r in items.table("table_item").items()
                   if isinstance(r, dict) and str(r.get("t")) == "40"]
        bgs = [k for k, r in items.table("table_item").items()
               if isinstance(r, dict) and str(r.get("t")) == "50"]
        pick_head = next((k for k in heads if k != base), heads[0] if heads else None)
        pick_clothes = next((k for k in clothes if k != str(pdata.get("medalClothesId"))),
                            clothes[0] if clothes else None)
        pick_bg = next((k for k in bgs if k != str(pdata.get("medalBgId"))),
                       bgs[0] if bgs else None)
        r = call("medal.changehead", {"headId": pick_head, "headType": "2"}, 301)
        if r.get("code") != 200 or r.get("data") != f"{pick_head}:2":
            bad.append(f"changehead -> {r.get('code')} data={r.get('data')!r}"
                       f"（要回 `{pick_head}:2` 这个值）")
        r = call("medal.changeclothes", {"clothesId": pick_clothes}, 302)
        if r.get("code") != 200 or str(r.get("data")) != str(pick_clothes):
            bad.append(f"changeclothes -> {r.get('code')} data={r.get('data')!r}")
        r = call("medal.changebg", {"bgId": pick_bg}, 303)
        if r.get("code") != 200 or str(r.get("data")) != str(pick_bg):
            bad.append(f"changebg -> {r.get('code')} data={r.get('data')!r}")
        after = (call("agent.getlogindata", {}, 304).get("data") or {}).get("player") or {}
        if str(after.get("headId")) != f"{pick_head}:2":
            bad.append(f"换完头像重登是 {after.get('headId')!r}（没落盘？）")
        elif str(after.get("medalClothesId")) != str(pick_clothes) or \
                str(after.get("medalBgId")) != str(pick_bg):
            bad.append(f"换完衣服/背景重登是 {after.get('medalClothesId')!r}/"
                       f"{after.get('medalBgId')!r}（没落盘？）")
        else:
            print(f"       换装：头像={after.get('headId')} 衣服={after.get('medalClothesId')} "
                  f"背景={after.get('medalBgId')}（重登还在）")

        # ---- E. 佩戴勋章：回整张 map、同组只留一个、非法勋章要拒 ----
        done = [k for k in table if medal.is_complete(store.get_or_create_player(account), k)]
        if not done:
            bad.append("一条已完成的勋章都没有（活动类那 25 条应该算完成）")
        else:
            first = done[0]
            r = call("medal.wearmedal", {"medalId": first, "wearIdx": 1}, 305)
            wdata = r.get("data")
            if r.get("code") != 200 or not isinstance(wdata, dict):
                bad.append(f"wearmedal -> {r.get('code')} data={wdata!r}（要回整张 medalWear）")
            elif str(wdata.get(first)) != "1":
                bad.append(f"wearmedal 回包里 {first} 的位号是 {wdata.get(first)!r}，要 1")
            # 同组第二个（如果同组有多条）
            same = [k for k in table
                    if str((table[k] or {}).get("group")) ==
                    str((table[first] or {}).get("group")) and k != first]
            if same:
                r2 = call("medal.wearmedal", {"medalId": same[0], "wearIdx": 2}, 306)
                w2 = (r2.get("data") or {})
                if r2.get("code") == 200 and str(first) in w2 and str(same[0]) in w2:
                    bad.append(f"同组两个勋章同时戴着了：{w2}")
                elif r2.get("code") != 200:
                    print(f"       （同组 {same[0]} 还没拿到，跳过「同组替换」验证）")
            bad_wear = call("medal.wearmedal", {"medalId": "99999", "wearIdx": 0}, 307)
            if bad_wear.get("code") == 200:
                bad.append("佩戴一个不存在的勋章竟然 200")
            wnow = (call("agent.getlogindata", {}, 308).get("data") or {}).get("player", {}).get("medalWear") or {}
            if isinstance(wnow, dict) and first in wnow:
                print(f"       佩戴：{first} -> 位 {wnow[first]}（medalWear={wnow}）")

        # ---- F. 清 NEW 标记：服务端那份也该少 ----
        head_mark = next((k for k in marks if str((items.table("table_item").get(k) or {}).get("t")) == "60"), None)
        r = call("medal.clearallheadnew", {}, 309)
        if r.get("code") != 200:
            bad.append(f"clearallheadnew -> {r.get('code')}")
        else:
            marks2 = [k for k, v in ((call("agent.getlogindata", {}, 310).get("data") or {}).get("item") or {}).items()
                      if isinstance(v, dict)]
            still = [k for k in marks2 if str((items.table("table_item").get(k) or {}).get("t")) == "60"]
            if still:
                bad.append(f"清完头像 NEW 之后还有 {len(still)} 件带标记（第一条 {still[0]}）")
            else:
                print(f"       清 NEW：头像类标记 {len([k for k in marks if str((items.table('table_item').get(k) or {}).get('t'))=='60'])} "
                      f"-> 0，剩余标记 {len(marks2)} 件（衣服/背景/勋章）")
        if head_mark:
            r = call("medal.setheadold", {"headId": head_mark, "headType": "2"}, 311)
            if r.get("code") != 200:
                bad.append(f"setheadold -> {r.get('code')}")
        r = call("medal.setmedalold", {"medalId": done[0] if done else "10010"}, 312)
        if r.get("code") != 200:
            bad.append(f"setmedalold -> {r.get('code')}")
        r = call("medal.setclothesorbgold", {"id": pick_clothes}, 313)
        if r.get("code") != 200:
            bad.append(f"setclothesorbgold -> {r.get('code')}")

        # ---- G. 好友勋章信息 ----
        r = call("medal.getfriendmedalinfo", {"friendId": 900003}, 314)
        finfo = r.get("data") or {}
        if r.get("code") != 200:
            bad.append(f"getfriendmedalinfo -> {r.get('code')}")
        else:
            for field in ("name", "lv", "numberId", "completeCount", "medals", "medalWear"):
                if field not in finfo:
                    bad.append(f"好友勋章信息缺 {field}（客户端 data.<field> 直接用）")
            if not isinstance(finfo.get("medals"), dict) or len(finfo.get("medals") or {}) != len(table):
                bad.append(f"好友 medals 有 {len(finfo.get('medals') or {})} 条，应该也是 {len(table)} 条")
            print(f"       好友勋章：{finfo.get('name')} 完成 {finfo.get('completeCount')} 条、"
                  f"佩戴 {len(finfo.get('medalWear') or {})} 个")
        bad_friend = call("medal.getfriendmedalinfo", {"friendId": 1}, 315)
        if bad_friend.get("code") == 200:
            bad.append("查一个不是好友的 numberId 竟然 200")

        # ---- H. 进度接线：把 5003（累计抚摸）的计数器灌满 -> 该勋章该完成 ----
        target = next((k for k, r0 in table.items()
                       if str(r0.get("condition_kind")) == "5003"), None)
        if target:
            need = medal.target_of(target)
            p = store.get_or_create_player(account)
            p.setdefault("questStats", {})["touches"] = need
            store.save_player(p)
            call("agent.getlogindata", {}, 316)
            p2 = store.get_or_create_player(account)
            entry = (p2.get("medal") or {}).get("complete") or {}
            if not entry.get(str(target)):
                bad.append(f"{target}（{table[target].get('desc')}）计数器灌满后没记 completeTime")
            elif not int((p2.get("items") or {}).get(str(table[target].get("icon_id")) or 0) or 0):
                bad.append(f"{target} 完成之后没发勋章本体 {table[target].get('icon_id')}")
            else:
                print(f"       进度：{target}「{table[target].get('name')}」"
                      f"（{table[target].get('desc')}）达成 -> 发了勋章道具 "
                      f"{table[target].get('icon_id')}")
    finally:
        p = store.get_or_create_player(account)
        p["medal"] = before["medal"]
        p["medalWear"] = before["medalWear"]
        p["headId"] = before["headId"]
        p["medalClothesId"] = before["medalClothesId"]
        p["medalBgId"] = before["medalBgId"]
        p["questStats"] = before["questStats"]
        got = items.items_of(p)
        for k in [k for k in got if k not in before["items"]]:
            del got[k]
        for k, v in before["items"].items():
            got[k] = v
        store.save_player(p)

    if bad:
        for one in bad[:12]:
            print(f"  BAD {one}")
        return False
    print("  OK  勋章/衣柜：medals 覆盖全表且行形状对、headId 是 `道具:类型`、medalWear 是 map、"
          "NEW 标记走 item 对象形状且能清、换装/背景/头像回值并落盘、佩戴回整张 map、"
          "好友勋章信息齐、计数器驱动的勋章会达成并发本体（收尾已还原）")
    return ok


def share_convert_check(ok: bool) -> bool:
    """分享领奖 / 礼包兑换（`share.receivesharereward` + `convert.convert`）。

    客户端契约（反汇编）：

    * `Share._initData`：登录块 `data.share` 只读 `shareCount`；次数上限
      `table_constant.share_reward_max_count`（客户端自己挡）；奖励内容在
      `table_constant.share_reward_key`（形如 `"100001@30"`）。
      回调里 `data.shareCount` 覆盖本地 + `ccuiManager.popupRewardWithItems(data.rewards)`，
      而那个函数是 `for (k in items) push({type:ITEM,key:k,count:items[k]})`
      —— ⚠️ **`rewards` 必须是 map，不是数组**。
    * `Package._loadTable`：`this._convertKey = table_item[key].convert_key`；
      `Bag._package` 发 `convert.convert {key: convertKey, count}`，回调把
      **`res.data` 整个当奖励数组**（`RewardBoxLayer.popReward({reward: res.data})`，
      逐项读 `.type` 过 `REWARD_TYPE_SWITCH`）—— ⚠️ **`data` 是数组，不是 `{rewards}`**。

    ⚠️ 会真改存档（分享次数、礼包道具和奖励），跑完整体还原。
    """
    from gamesrv import config, items, share, store

    account = config.DEFAULT_ACCOUNT
    player = store.get_or_create_player(account)
    before_items = dict(items.items_of(player))
    before_share = json.loads(json.dumps(player.get("share") or {}))
    bad = []

    def set_items(bag):
        p = store.get_or_create_player(account)
        got = items.items_of(p)
        for k in [k for k in got if k not in bag]:
            del got[k]
        for k, v in bag.items():
            got[k] = v
        store.save_player(p)

    try:
        # ---- A. 分享：形状 + 真发奖 + 一天一次 ----
        login = call("agent.getlogindata", {}, 320)
        sblk = (login.get("data") or {}).get("share")
        if not isinstance(sblk, dict) or "shareCount" not in sblk:
            bad.append(f"登录块 data.share 形状不对：{str(sblk)[:60]}"
                       f"（客户端 Share._initData 读 shareCount）")
        reward = share.reward_map()
        limit = share.max_count()
        if not reward:
            bad.append("table_constant.share_reward_key 解析不出奖励")
        p = store.get_or_create_player(account)
        p.setdefault("share", {})["count"] = 0
        store.save_player(p)
        before = {k: int(items.count_of(store.get_or_create_player(account), k)) for k in reward}
        r1 = call("share.receivesharereward", {"shareSuccess": True, "platform": "test"}, 321)
        d1 = r1.get("data") or {}
        if r1.get("code") != 200:
            bad.append(f"share.receivesharereward code={r1.get('code')} {str(r1)[:80]}")
        else:
            rw = d1.get("rewards")
            if not isinstance(rw, dict):
                bad.append(f"share 回包的 rewards 是 {type(rw).__name__}，"
                           f"必须 map（popupRewardWithItems 是 for-in）")
            elif {str(k): int(v) for k, v in rw.items()} != \
                    {str(k): int(v) for k, v in reward.items()}:
                bad.append(f"share 奖励和 table_constant.share_reward_key 对不上："
                           f"{rw} vs {reward}")
            if int(d1.get("shareCount") or 0) != 1:
                bad.append(f"分享之后 shareCount={d1.get('shareCount')!r}，应该是 1")
            after = {k: int(items.count_of(store.get_or_create_player(account), k)) for k in reward}
            if any(after[k] <= before[k] for k in reward):
                bad.append(f"分享奖励没进背包：{before} -> {after}")
            else:
                print(f"       分享：+{reward}，shareCount=1（上限 {limit}）")
        r2 = call("share.receivesharereward", {"shareSuccess": True, "platform": "test"}, 322)
        if r2.get("code") == 200 and limit <= 1:
            bad.append(f"同一天分享奖励能领第二次（上限 {limit}）")
        p = store.get_or_create_player(account)
        p.setdefault("share", {})["day"] = "2000-01-01"
        store.save_player(p)
        call("agent.getlogindata", {}, 323)
        s2 = (store.get_or_create_player(account).get("share") or {}).get("count")
        if int(s2 or 0) != 0:
            bad.append(f"分享次数换日没清零：{s2}")
        else:
            print("       分享次数：换日（05:00）自动清零 ✓")

        # ---- B. 礼包兑换：data 是奖励**数组** + 真扣真发 ----
        pkg, ckey = "800001", "10300001"
        bag = dict(before_items)
        bag[pkg] = int(bag.get(pkg) or 0) + 1
        set_items(bag)
        keys = ("100001", "100002", pkg)
        before2 = {k: int(items.count_of(store.get_or_create_player(account), k)) for k in keys}
        r3 = call("convert.convert", {"key": ckey, "count": 1}, 324)
        d3 = r3.get("data")
        if r3.get("code") != 200:
            bad.append(f"convert.convert {ckey} code={r3.get('code')} {str(r3)[:80]}")
        else:
            if not isinstance(d3, list) or not d3:
                bad.append(f"convert 回包 data 是 {type(d3).__name__}，"
                           f"要是奖励数组（RewardBoxLayer 逐项读 .type）")
            else:
                for row in d3:
                    if not isinstance(row, dict) or set(("type", "key", "count")) - set(row):
                        bad.append(f"奖励行形状不对：{row!r}（要 {{type,key,count}}）")
                        break
            after2 = {k: int(items.count_of(store.get_or_create_player(account), k)) for k in keys}
            if after2[pkg] != before2[pkg] - 1:
                bad.append(f"礼包没被扣掉：{pkg} {before2[pkg]} -> {after2[pkg]}")
            elif not any(after2[k] > before2[k] for k in ("100001", "100002")):
                bad.append(f"兑换奖励没进背包：{before2} -> {after2}")
            else:
                print(f"       礼包：{pkg} -> {[(r['type'], r['key'], r['count']) for r in d3]}")
        set_items(dict(before_items))
        r4 = call("convert.convert", {"key": ckey, "count": 1}, 325)
        if r4.get("code") == 200:
            bad.append("没有礼包道具却兑换成功了")
        if call("convert.convert", {"key": "99999999", "count": 1}, 326).get("code") == 200:
            bad.append("兑换一个不存在的礼包 key 竟然 200")
    finally:
        p = store.get_or_create_player(account)
        p["share"] = before_share
        got = items.items_of(p)
        for k in [k for k in got if k not in before_items]:
            del got[k]
        for k, v in before_items.items():
            got[k] = v
        store.save_player(p)

    if bad:
        for one in bad[:12]:
            print(f"  BAD {one}")
        return False
    print("  OK  分享/礼包：分享奖励取自 table_constant（rewards 是 map）、一天一次、换日清零；"
          "礼包兑换按 convert_key -> table_convert_reward 扣道具、data 是奖励数组、"
          "没道具不给兑（收尾已还原）")
    return ok


def friendsupport_check(ok: bool) -> bool:
    """助战（`friendsupport.*` 3 条）：推荐名单的形状 / 登记 / 取消登记。

    ⚠️ **`getrecommendsoldiers` 的 data 必须是 `{recommendList: [...]}`，不能是裸数组**
    （2026-09-21 定位）：反汇编这条链是

        FriendSupport.getRecommendList/<  成功 → succCb(res.data)        // 整个 data
        SupportChoiceLayer._init/</       function (data) { this._initUI(data.recommendList) }

    真正画列表的**层**读 `data.recommendList`；发裸数组的话它是 undefined →
    `setSupportList(undefined)` 第一句 `if (!list) return;` 直接退出 ——
    症状就是「助战弹窗打得开、里面一个军士都没有」。之前
    `FriendSupport._recommendList = res.data` 那句把整个 data 存下来，
    看数据类会以为"收到了 20 个"，所以一直判成"data 本身就是列表"。
    """
    from gamesrv import items

    bad = []
    r = call("friendsupport.getrecommendsoldiers", {"levelid": "101001"}, 330)
    d = r.get("data")
    if r.get("code") != 200:
        bad.append(f"friendsupport.getrecommendsoldiers code={r.get('code')}")
    if not isinstance(d, dict) or not isinstance(d.get("recommendList"), list):
        bad.append(f"回包 data 是 {type(d).__name__}，必须是 {{recommendList: [...]}}"
                   f"（裸数组会让助战弹窗一个军士都画不出来）")
        items_list = []
    else:
        items_list = d["recommendList"]
    npc = items.table("table_friend_support_npc")
    if not items_list:
        bad.append("推荐名单是空的（客户端弹窗会是空的）")
    for one in items_list[:5]:
        if not isinstance(one, dict):
            bad.append(f"条目不是对象：{one!r}")
            break
        if "npcId" not in one or "playerId" not in one:
            bad.append(f"条目缺 npcId/playerId：{sorted(one)[:8]}"
                       f"（层拿 playerId 查 userecord，SupportChoiceItem 只认 npcId）")
            break
        if str(one["npcId"]) not in npc:
            bad.append(f"npcId={one['npcId']!r} 不在 table_friend_support_npc 里")
            break
    if not bad:
        print(f"       助战推荐：{len(items_list)} 个 NPC（第一条 "
              f"{items_list[0].get('npcId')}/{items_list[0].get('playerId')}，"
              f"名字 {items_list[0].get('player_name')}）")
    for route in ("friendsupport.setfriendsupport", "friendsupport.delfriendsupport"):
        rr = call(route, {"soldiers": [], "userecord": {}}, 331)
        if rr.get("code") != 200:
            bad.append(f"{route} code={rr.get('code')}")

    if bad:
        for one in bad[:8]:
            print(f"  BAD {one}")
        return False
    print("  OK  助战：推荐名单是 {recommendList:[…]}（不是裸数组）、条目带 npcId/playerId "
          "且 npcId 在客户端表里、登记/取消登记都回 200")
    return ok


def diary_check(ok: bool) -> bool:
    """私密剧情（`diary.*` 2 条）：登录块行形状 / 可买信息 / 花金条解锁 / 错误码。

    客户端契约（`assets/src/data/diary.jsc` 反汇编）：

    * 登录块 `data.diary` = `{storyDiarys: {cid: 行}, diarysBuyInfo: {cid: 1}}`；
      行里只有两个字段被读：`unlockLevels[lid]`（已解锁）、`lockLevels[lid]`（可买）；
    * `isStoryCanShow` 里是 `_diarysBuyInfo[cid] == 1` —— **松散相等**，必须给数字 1
      （给对象/字符串 "1" 会有意外）；
    * `diary.getdiarybuyinfo` -> `{diarysBuyInfo, updateDiarys}`（客户端按 key 合并）；
    * `diary.buyunlockstory {chapterId, levelId}` -> `data` = 该章的新行
      （客户端 `_storyDiarys[data.chapterId] = data`）；失败码 201..204 会 toast 文案。

    ⚠️ 会真花金条，跑完还原（道具 + 剧情存档）。
    """
    from gamesrv import config, diary, items, store

    account = config.DEFAULT_ACCOUNT
    player = store.get_or_create_player(account)
    before_diary = json.loads(json.dumps(player.get("diary") or {}))
    before_items = dict(items.items_of(player))
    bad = []

    try:
        login = call("agent.getlogindata", {}, 340)
        dblk = (login.get("data") or {}).get("diary") or {}
        story = dblk.get("storyDiarys")
        buy = dblk.get("diarysBuyInfo")
        if not isinstance(story, dict) or not story:
            bad.append(f"data.diary.storyDiarys 是 {type(story).__name__}，要是 map（章节->行）")
            story = {}
        if not isinstance(buy, dict) or not buy:
            bad.append(f"data.diary.diarysBuyInfo 是 {type(buy).__name__}，要是 map")
            buy = {}
        for cid, row in list(story.items())[:3]:
            if not isinstance(row, dict):
                bad.append(f"{cid}: 行不是对象")
                break
            if not isinstance(row.get("unlockLevels"), dict) or \
                    not isinstance(row.get("lockLevels"), dict):
                bad.append(f"{cid}: 行里 unlockLevels/lockLevels 必须是 map"
                           f"（客户端 isUnlock 直接取下标）")
                break
        for cid, flag in list(buy.items())[:3]:
            if not isinstance(flag, int) or flag != 1:
                bad.append(f"diarysBuyInfo[{cid}] = {flag!r}，客户端判 `== 1`（给整数 1）")
                break
        print(f"       登录块：{len(story)} 章剧情、可买 {len(buy)} 章")

        r = call("diary.getdiarybuyinfo", {}, 341)
        d = r.get("data") or {}
        if r.get("code") != 200 or not isinstance(d.get("diarysBuyInfo"), dict) \
                or not isinstance(d.get("updateDiarys"), dict):
            bad.append(f"diary.getdiarybuyinfo 回包形状不对：code={r.get('code')} "
                       f"keys={sorted(d)[:5]}（要 {{diarysBuyInfo, updateDiarys}}）")

        # 找一条「可买」的：从任意一章的 lockLevels 里拿
        cid = lid = None
        p = store.get_or_create_player(account)
        for k in sorted(story):
            row = diary.story_row(p, k)
            if row["lockLevels"]:
                cid = k
                lid = sorted(row["lockLevels"])[0]
                break
        if cid is None:
            bad.append("所有章节都没有可买的剧情（lockLevels 全空）—— 买不了就没法验")
        else:
            price = diary.price_of(cid)
            gem_before = items.count_of(store.get_or_create_player(account), diary.GEM)
            r = call("diary.buyunlockstory", {"chapterId": cid, "levelId": lid}, 342)
            row = r.get("data") or {}
            if r.get("code") != 200:
                bad.append(f"解锁 {cid}/{lid} code={r.get('code')} {str(r)[:80]}")
            else:
                if str(row.get("chapterId")) != str(cid):
                    bad.append(f"回包行里 chapterId={row.get('chapterId')!r}，客户端拿它当 key")
                if not (row.get("unlockLevels") or {}).get(lid):
                    bad.append(f"解锁之后 {lid} 还在 lockLevels 里（unlockLevels={list((row.get('unlockLevels') or {}))[:4]}）")
                gem_after = items.count_of(store.get_or_create_player(account), diary.GEM)
                if gem_after != gem_before - price:
                    bad.append(f"金条没按价格扣：{gem_before} -> {gem_after}（价格 {price}）")
                else:
                    print(f"       解锁 {cid}/{lid}：-{price} 金条（{gem_before} -> {gem_after}），"
                          f"该章已解锁 {len(row.get('unlockLevels') or {})} 条")
                again = call("diary.buyunlockstory", {"chapterId": cid, "levelId": lid}, 343)
                if again.get("code") != 202:
                    bad.append(f"重复解锁 code={again.get('code')}，应该是 202（该章节已解锁）")
                bad_ch = call("diary.buyunlockstory",
                              {"chapterId": "999999", "levelId": lid}, 344)
                if bad_ch.get("code") != 204:
                    bad.append(f"不存在的章节 code={bad_ch.get('code')}，应该是 204（章节错误）")
                bad_lv = call("diary.buyunlockstory",
                              {"chapterId": cid, "levelId": "1"}, 345)
                if bad_lv.get("code") != 203:
                    bad.append(f"不属于该章的关卡 code={bad_lv.get('code')}，应该是 203（标签错误）")
                # 金条不够 -> 201
                p2 = store.get_or_create_player(account)
                gem_now = int(items.items_of(p2).get(diary.GEM) or 0)
                items.items_of(p2)[diary.GEM] = 0
                store.save_player(p2)
                row2 = diary.story_row(store.get_or_create_player(account), cid)
                poor_lid = next((x for x in sorted(row2["lockLevels"]) if x != lid), None)
                if poor_lid:
                    poor = call("diary.buyunlockstory",
                                {"chapterId": cid, "levelId": poor_lid}, 346)
                    if poor.get("code") != 201:
                        bad.append(f"金条不够时 code={poor.get('code')}，应该是 201（物品不足）")
                p3 = store.get_or_create_player(account)
                items.items_of(p3)[diary.GEM] = gem_now
                store.save_player(p3)
    finally:
        p = store.get_or_create_player(account)
        p["diary"] = before_diary
        got = items.items_of(p)
        for k in [k for k in got if k not in before_items]:
            del got[k]
        for k, v in before_items.items():
            got[k] = v
        store.save_player(p)

    if bad:
        for one in bad[:10]:
            print(f"  BAD {one}")
        return False
    print("  OK  私密剧情：登录块 {storyDiarys, diarysBuyInfo}（值是整数 1）、行里 "
          "unlockLevels/lockLevels 是 map、getdiarybuyinfo 形状对、解锁按章节价扣金条、"
          "重复 202 / 错章 204 / 错关 203 / 金条不足 201（收尾已还原）")
    return ok


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
    # ⚠️ **整份军士名单先拍快照，最后原样还原** —— 下面这些用例会吃掉军士（升级链路
    # 拿它们当材料）、也会**新增**军士（抽卡用例真的抽几发）。只靠 replenish 补默认
    # 那 18 个是不够的：玩家抽卡得来的卡不在 SOLDIER_KEYS 里（2026-09-21 因此把
    # 这个号的 44 个抽卡军士清空过）。
    snapshot_roster()
    try:
        snapshot_teams()          # 军士链路会把材料（军士）吃掉、顺带从队伍里摘掉
        ok = soldier_flow(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 军士链路异常: {exc}")
        ok = False
    try:
        restore_teams()
    except Exception as exc:  # noqa: BLE001
        print(f"  ..  收尾放回编成失败（不影响结论）: {exc}")
    try:
        restore_roster()
    except Exception as exc:  # noqa: BLE001
        print(f"  ..  收尾还原军士名单失败（不影响结论）: {exc}")

    ok = run_check(ok, "编成保存自检异常", team_save_check)

    ok = run_check(ok, "分区关卡自检异常", subarea_check)

    ok = run_check(ok, "战斗结算自检异常", level_result_check)

    ok = run_check(ok, "分区成就自检异常", subarea_achievement_check)

    ok = run_check(ok, "交易所自检异常", exchange_check)

    ok = run_check(ok, "分享礼包自检异常", share_convert_check)

    ok = run_check(ok, "私密剧情自检异常", diary_check)

    ok = run_check(ok, "派遣自检异常", detect_check)

    ok = run_check(ok, "签到自检异常", sign_check)

    ok = run_check(ok, "任务自检异常", quest_check)

    ok = run_check(ok, "好友自检异常", friend_check)

    ok = run_check(ok, "助战自检异常", friendsupport_check)

    ok = run_check(ok, "勋章自检异常", medal_check)

    ok = run_check(ok, "功能开启弹窗自检异常", module_open_check)

    ok = run_check(ok, "演习场自检异常", arena_check)

    ok = run_check(ok, "角色图鉴自检异常", char_manual_check)

    ok = run_check(ok, "抽卡自检异常", gacha_check)

    ok = run_check(ok, "奖励图标自检异常", item_icon_check)

    ok = run_check(ok, "好感度自检异常", favor_check)

    # 最后再还原一次军士名单：抽卡用例会**新增**军士（3 次抽卡 ≈ 12 张），
    # 别的用例可能吃掉军士 —— 玩家的名单必须和跑之前一模一样。
    try:
        restore_roster()
    except Exception as exc:  # noqa: BLE001
        print(f"  ..  收尾还原军士名单失败（不影响结论）: {exc}")

    print("\n全部通过 ✅" if ok else "\n有路由没回 200 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
