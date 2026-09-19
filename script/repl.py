"""在游戏客户端里执行 JS 表达式。

前提：游戏进程里已经加载了 assets/src/patch/hook.js 探针，
      并且游戏服务器（run.py）正在运行。

    python tools/repl.py "crypt.base64Encode('abc')"
    python tools/repl.py                 # 交互模式
"""

from __future__ import annotations

import argparse
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


def eval_remote(base: str, code: str, timeout: float = 20.0):
    body = json.dumps({"code": code, "timeout": timeout}).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/control/eval",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    from gamesrv import config



    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="*", help="要执行的 JS 表达式")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--base", default=f"http://127.0.0.1:{config.CDN_PORT}")
    args = ap.parse_args()

    def run(code: str) -> int:
        try:
            result = eval_remote(args.base, code, args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"!! 控制接口不可用: {exc}", file=sys.stderr)
            return 2
        flag = "OK " if result.get("ok") else "ERR"
        print(f"{flag} {result.get('value')}")
        return 0 if result.get("ok") else 1

    if args.code:
        return run(" ".join(args.code))

    print("输入 JS 表达式，空行/EOF 退出。")
    while True:
        try:
            line = input("js> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            continue
        run(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
