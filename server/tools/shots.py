r"""重启客户端并连续截图，方便肉眼确认走到哪一步。

    python tools\shots.py 12 5         # 每 5 秒一张，共 12 张
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADB = r"D:\Android\android-sdk\platform-tools\adb.exe"
PKG = "com.cm.zcsmw.baidu"
ACT = "org.cocos2dx.javascript.SplashActivity"
OUT = r"E:\code\apk\shots"


def adb(*args):
    return subprocess.run([ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    gap = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    os.makedirs(OUT, exist_ok=True)

    adb("shell", "am", "force-stop", PKG)
    time.sleep(2)
    adb("logcat", "-c")
    adb("shell", "am", "start", "-n", f"{PKG}/{ACT}")
    print("已启动，开始截图 ...")

    for i in range(n):
        time.sleep(gap)
        remote = f"/sdcard/shot{i:02d}.png"
        adb("shell", "screencap", "-p", remote)
        local = os.path.join(OUT, f"shot{i:02d}.png")
        adb("pull", remote, local)
        alive = PKG in (adb("shell", "ps", "-A").stdout or "")
        print(f"  shot{i:02d}  alive={alive}")

    logs = (adb("logcat", "-d", "-v", "brief").stdout or "").splitlines()
    print("\n===== 探针日志 =====")
    for line in logs:
        if "OPPAIHOOK" in line:
            print("   ", line.split("OPPAIHOOK|", 1)[-1])


if __name__ == "__main__":
    main()
