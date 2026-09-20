r"""量一次「登录包」在服务端要花多久 —— DES 提速前后的对照尺子。

    python script\bench_login.py            # 默认 5 次 agent.getlogindata
    python script\bench_login.py -n 3

为什么单独量这一条：登录响应里 `instance` 最大（本档 1142 个关卡行约 92 KB），
整个响应 110 KB 左右，服务端**每个请求都要 DES 加密一遍**，
自检脚本每一轮都要登录好几次 —— 所以 DES 的吞吐直接决定自检墙钟。
这里的耗时 = 服务端生成 + DES 加密 + HTTP 往返（脚本自己加密的请求体只有几十字节，可忽略）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request

_d = os.path.dirname(os.path.abspath(__file__))
if not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402
sys.path.insert(0, os.path.dirname(_d))

from gamesrv import config  # noqa: E402
from gamesrv.crypto.des import des_decode, des_encode  # noqa: E402

SECRET = bytes.fromhex("0100000000000000")  # DH 单位元


def call(route: str, msg: dict, req_id: int = 1):
    """照客户端格式打包发一次，返回 (解析结果, 响应字节数)。"""
    plain = json.dumps({"route": route, "msg": msg, "reqId": req_id},
                       ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = base64.b64encode(des_encode(SECRET, plain))
    url = f"http://127.0.0.1:{config.GAME_PORT}/"
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    text = raw.decode("utf-8", "replace")
    if '"code":' in text:
        return json.loads(text), len(raw)
    return json.loads(des_decode(SECRET, base64.b64decode(raw)).decode("utf-8")), len(raw)


def parts(account: str) -> int:
    """进程内拆开量：组包 / JSON / DES / base64 各占多少。

    走 HTTP 只能看到总数，看不出该优化谁。这里直接调 handler，
    再照 `gameproto.dumps_payload` 的写法把四步分别计时。
    """
    from gamesrv import instance, store
    from gamesrv.handlers import agent, load_all

    load_all()
    session = {"info": {"account": account}}

    t0 = time.perf_counter()
    resp = agent.get_login_data(session, {}, 1)
    t1 = time.perf_counter()
    player = store.get_or_create_player(account)
    t2 = time.perf_counter()
    block = instance.login_block(player)
    t3 = time.perf_counter()
    plain = json.dumps(resp, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    t4 = time.perf_counter()
    enc = des_encode(SECRET, plain)
    t5 = time.perf_counter()
    b64 = base64.b64encode(enc)
    t6 = time.perf_counter()

    rows = [
        ("handler 组包", t1 - t0),
        ("load_player", t2 - t1),
        ("instance.login_block（其中）", t3 - t2),
        ("json.dumps", t4 - t3),
        ("des_encode", t5 - t4),
        ("base64", t6 - t5),
    ]
    print(f"明文 {len(plain) / 1024:.1f} KB → 密文 {len(enc) / 1024:.1f} KB "
          f"→ base64 {len(b64) / 1024:.1f} KB，instance 块 {len(json.dumps(block)) / 1024:.1f} KB")
    for name, dt in rows:
        print(f"  {name:28s} {dt * 1000:8.1f} ms")
    total = t6 - t0
    print(f"  {'合计':28s} {total * 1000:8.1f} ms"
          f"（DES 占 {rows[4][1] / total * 100:.0f}%，{len(plain) / 1024 / rows[4][1]:.0f} KB/s）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--count", type=int, default=5)
    ap.add_argument("--route", default="agent.getlogindata")
    ap.add_argument("--parts", action="store_true",
                    help="进程内拆分量各步骤耗时（不需要服务端在跑）")
    ap.add_argument("--account", default=None, help="--parts 时用哪个账号")
    args = ap.parse_args()

    if args.parts:
        from gamesrv import config
        return parts(args.account or config.DEFAULT_ACCOUNT)

    times = []
    size = 0
    for i in range(args.count):
        t0 = time.perf_counter()
        data, size = call(args.route, {}, 100 + i)
        dt = time.perf_counter() - t0
        times.append(dt)
        code = (data or {}).get("code")
        print(f"  第 {i + 1} 次 {dt * 1000:7.1f} ms  {size / 1024:8.1f} KB  code={code}")
    avg = sum(times) / len(times)
    best = min(times)
    print(f"\n{args.route}：平均 {avg * 1000:.1f} ms（最快 {best * 1000:.1f} ms），"
          f"响应 {size / 1024:.1f} KB → {size / 1024 / best:.0f} KB/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
