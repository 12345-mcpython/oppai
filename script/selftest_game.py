r"""不开游戏也能自测业务协议：自己按客户端格式打包一个请求发过去。

    python tools\selftest_game.py

用的是 DH 单位元当共享密钥（和服务端一致），
所以不需要客户端参与就能验证 加解密 + 路由 + code=200。

⚠️ **这个脚本会改真存档**（它跑的就是默认账号）：
   * 军士链路会**吃掉 2 个军士当材料**，而且不会还回来 —— 每跑一次少 2 个。
     初始名单只有 18 个，跑几轮就没了。要恢复：把 `store.py` 的
     `ROSTER_VERSION` +1（`_migrate` 会整个重发名单，**代价是军士等级重置**）。
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
        ok = soldier_flow(ok)
    except Exception as exc:  # noqa: BLE001
        print(f"  BAD 军士链路异常: {exc}")
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
