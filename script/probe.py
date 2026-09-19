"""在客户端里跑一串 JS 表达式。

    python tools/probe.py --restart --logs "expr1" "expr2" ...

流程：重启客户端 -> 等模块就绪 -> 阻止 UpdateScene 退出 -> 等启动流程跑完
      -> 清空 logcat -> 依次执行表达式 -> 打印这段时间里的探针日志。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADB = r"D:\Android\android-sdk\platform-tools\adb.exe"
PKG = "com.cm.zcsmw.baidu"
ACT = "org.cocos2dx.javascript.SplashActivity"

BLOCK_EXIT = "UpdateScene.prototype._exit=function(){__oppaiHook__.log('EXIT blocked')}; 'ok'"
READY = (
    "typeof op!=='undefined' && typeof server!=='undefined' && "
    "typeof ccuiManager!=='undefined' && typeof UpdateScene!=='undefined'"
)


def adb(*args):
    return subprocess.run([ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


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



    ap = argparse.ArgumentParser()
    ap.add_argument("expr", nargs="+")
    ap.add_argument("--restart", action="store_true")
    ap.add_argument("--logs", action="store_true")
    ap.add_argument("--quiet-wait", type=float, default=6.0)
    ap.add_argument("--base", default=f"http://127.0.0.1:{config.CDN_PORT}")
    args = ap.parse_args()

    if args.restart:
        adb("shell", "am", "force-stop", PKG)
        time.sleep(1.5)
        adb("logcat", "-c")
        adb("shell", "am", "start", "-n", f"{PKG}/{ACT}")

    for _ in range(40):
        time.sleep(2)
        r = ev(args.base, READY, timeout=3)
        if r.get("ok") and r.get("value") == "true":
            break
    print(">> 探针在线；阻止退出:", ev(args.base, BLOCK_EXIT))

    print(f">> 等 {args.quiet_wait:g}s 让启动流程跑完，再清空日志")
    time.sleep(args.quiet_wait)
    adb("logcat", "-c")

    for expr in args.expr:
        r = ev(args.base, expr, timeout=15)
        flag = "OK " if r.get("ok") else "ERR"
        print(f"{flag} {expr}\n    -> {r.get('value')}")

    if args.logs:
        time.sleep(3)
        print("\n===== 探针日志 =====")
        for line in hook_logs()[-60:]:
            print(line)


if __name__ == "__main__":
    main()
