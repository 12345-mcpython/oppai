"""带守护的游戏服务端启动器。

为什么需要它：直接 `Start-Process python run.py` 启动的是**分离进程**，
父 PowerShell 会话结束时会被回收（表现为"服务端好像宕机了"）。
这个脚本常驻，负责拉起 run.py 并在它退出时重启。

用法（在 DSH 里作为后台任务跑，就能一直活着）：

    python tools/serve.py

也可以直接本地跑：

    python tools/serve.py --once     # 只跑一次，不退不重启
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "run.py")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只跑一次，退出后不重启")
    ap.add_argument("--delay", type=float, default=3.0, help="重启前等待秒数")
    args = ap.parse_args()

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")

    round_no = 0
    while True:
        round_no += 1
        print("[serve] 第 %d 次启动 run.py" % round_no, flush=True)
        try:
            # stdio 继承：日志直接进当前任务输出
            code = subprocess.call([sys.executable, RUN], cwd=ROOT, env=env)
        except KeyboardInterrupt:
            print("[serve] 收到中断，退出", flush=True)
            return 0

        print("[serve] run.py 退出，code=%s" % code, flush=True)
        if args.once:
            return code or 0

        time.sleep(args.delay)


if __name__ == "__main__":
    raise SystemExit(main())
