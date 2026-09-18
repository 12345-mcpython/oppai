r"""不开游戏也能自测业务协议：自己按客户端格式打包一个请求发过去。

    python tools\selftest_game.py

用的是 DH 单位元当共享密钥（和服务端一致），
所以不需要客户端参与就能验证 加解密 + 路由 + code=200。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request

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

    # 挑品质 4 的目标 + 两个材料（挑同品质的，方便按经验表核对）
    target = soldiers[0]
    mats = [s["id"] for s in soldiers[1:3]]
    res = call("char.upgradesoldierlv",
               {"id": target["id"], "key": target["key"], "materials": mats}, 101)
    code = res.get("code")
    flag = "OK " if code == 200 else "BAD"
    print(f"  {flag} char.upgradesoldierlv         code={code}  "
          f"lv {target.get('lv')} -> {((res.get('data') or {}).get('soldier') or {}).get('lv')}")
    if code != 200:
        return False

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

    print("\n全部通过 ✅" if ok else "\n有路由没回 200 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
