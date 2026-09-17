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
    print("\n全部通过 ✅" if ok else "\n有路由没回 200 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
