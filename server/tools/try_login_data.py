"""反复调用 cb4AfterLogin 看还缺什么字段。

    python tools\try_login_data.py

每次都会重新拉 agent.getlogindata 并喂给 dataManager.cb4AfterLogin，
然后把探针日志里的错误打出来，方便逐个补齐模块字段。
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


def adb(*args):
    return subprocess.run([ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def ev(base, code, timeout=20):
    body = json.dumps({"code": code, "timeout": timeout}).encode()
    req = urllib.request.Request(
        base + "/control/eval", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 8) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "value": f"<{exc}>"}


CALL = """
(function(){
  server.request('agent.getlogindata', {}, function(err, res){
    if (err || !res || !res.data) { __oppaiHook__.log('TRY no data ' + __oppaiHook__.safeJson(err)); return; }
    try {
      dataManager.cb4AfterLogin(null, res.data);
      __oppaiHook__.log('TRY OK player=' + (dataManager.player ? 'yes' : 'null') +
                        ' isLogin=' + dataManager.isLogin);
    } catch (e) {
      __oppaiHook__.log('TRY ERR ' + e + ' | ' + ((e && e.stack) || '').split('\\n').slice(0,3).join('|'));
    }
  });
  return 'sent';
})()
"""


def main():
    from gamesrv import config

    base = f"http://127.0.0.1:{config.CDN_PORT}"
    adb("logcat", "-c")
    print(">>", ev(base, CALL))
    time.sleep(3)
    out = adb("logcat", "-d", "-v", "brief").stdout or ""
    for line in out.splitlines():
        if "OPPAIHOOK" in line and ("TRY" in line or "CTOR" in line):
            print("   ", line.split("OPPAIHOOK|", 1)[-1])


if __name__ == "__main__":
    main()
