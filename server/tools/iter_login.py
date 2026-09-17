r"""重启服务 + 跑一次登录数据探测的循环小工具。

    python tools\iter_login.py            # 重启服务端并跑一次
    python tools\iter_login.py --apk      # 顺便重启客户端
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    # 重启服务端
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force"],
        capture_output=True,
    )
    time.sleep(2)
    subprocess.Popen(
        ["python", "run.py", "-v"],
        cwd=HERE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(5)

    if "--apk" in sys.argv:
        subprocess.run([sys.executable, os.path.join(HERE, "tools", "probe.py"), "--restart", "1+1"])

    subprocess.run([sys.executable, os.path.join(HERE, "tools", "try_login_data.py")])


if __name__ == "__main__":
    main()
