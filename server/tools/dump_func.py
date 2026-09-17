"""重启客户端 -> 阻止退出 -> 把关键函数的源码/结构 dump 出来。

用 REPL 在客户端里执行任意 JS，输出走 logcat 的 OPPAIHOOK 探针。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADB = r"D:\Android\android-sdk\platform-tools\adb.exe"
PKG = "com.cm.zcsmw.baidu"
ACT = "org.cocos2dx.javascript.SplashActivity"


def adb(*args):
    return subprocess.run(
        [ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )


def ev(base, code, timeout=8.0):
    body = json.dumps({"code": code, "timeout": timeout}).encode()
    req = urllib.request.Request(
        base + "/control/eval", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 8) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "value": f"<{exc}>"}


def hook_logs():
    out = adb("logcat", "-d", "-v", "brief").stdout or ""
    return [ln.split("OPPAIHOOK|", 1)[-1] for ln in out.splitlines() if "OPPAIHOOK" in ln]


def main():
    from gamesrv import config

    base = f"http://127.0.0.1:{config.CDN_PORT}"

    if "--no-restart" not in sys.argv:
        adb("shell", "am", "force-stop", PKG)
        time.sleep(1.5)
        adb("logcat", "-c")
        adb("shell", "am", "start", "-n", f"{PKG}/{ACT}")

    # 等探针上线
    for _ in range(30):
        time.sleep(2)
        if ev(base, "'x'", timeout=3).get("ok"):
            break
    print(">> 探针在线")

    # 1) 阻止退出
    print("阻止退出:", ev(base, "UpdateScene.prototype._exit=function(){__oppaiHook__.log('EXIT blocked')}; 'ok'"))

    # 2) 关键函数源码（如果字节码保留了 source）
    print("源码长度:", ev(base, "String(UpdateScene.prototype._startUpdate).length"))
    print("源码:", ev(base, "String(UpdateScene.prototype._startUpdate)"))

    time.sleep(2)
    print("\n===== 探针日志 =====")
    for line in hook_logs()[-40:]:
        print(line)


if __name__ == "__main__":
    main()
