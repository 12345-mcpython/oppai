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
    r = call("favor.touchcharasst", {"charKey": acquired[0]}, 112)
    data = r.get("data") or {}
    if r.get("code") != 200:
        # 次数用完是正常业务拒绝，不算失败
        print(f"  ..  favor.touchcharasst 被拒（多半是互动次数用完了）：{r.get('msg')}")
    elif "favorInteractChance" not in data or "favorAdd" not in data or "favor" not in data:
        print(f"  BAD favor.touchcharasst 响应缺字段：{sorted(data)}")
        return False
    print(f"  OK  好感度登录块：{len(rows)} 个角色（{len(acquired)} 个已获得），"
          f"互动次数 {block['favorInteractChance']}")
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
    print(f"  OK  分区关卡：登录块与 getsubarealevel 一致，{len(data)} 关，"
          f"每日上限={limits[0]}")
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
        ok = soldier_flow(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 军士链路异常: {exc}")
        ok = False
    try:
        restore_roster()
    except Exception as exc:  # noqa: BLE001
        print(f"  ..  收尾补军士失败（不影响结论）: {exc}")

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
        ok = favor_check(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 好感度自检异常: {exc}")
        ok = False

    print("\n全部通过 ✅" if ok else "\n有路由没回 200 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
