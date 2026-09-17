"""跟踪客户端启动时对远程配置的读写。

在客户端里给全局 `op`（util/remoteConfig 的共享命名空间）套一层 Proxy，
只记录与版本/服务器列表相关的字段，从而看清 remoteConfig 到底怎么存怎么取。
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

PROXY = r"""
(function(){
  var o = (typeof op !== 'undefined') ? op : null;
  if (!o) return 'no op';
  if (o.__oppaiProxied) return 'already';
  o.__oppaiProxied = true;
  var WATCH = {version:1, versionConfig:1, updateConfig:1, servers:1, table_dictionary:1,
               appVersion:1, patchVersion:1, buildVersion:1, server:1};
  var p = new Proxy(o, {
    get: function(t, k){
      var v = t[k];
      if (typeof k === 'string' && WATCH[k] && typeof v !== 'function') {
        try { __oppaiHook__.log('OPGET ' + k + ' = ' + __oppaiHook__.safeJson(v).substring(0,300)); } catch(e){}
      }
      return v;
    },
    set: function(t, k, v){
      if (typeof k === 'string' && WATCH[k]) {
        try { __oppaiHook__.log('OPSET ' + k + ' = ' + __oppaiHook__.safeJson(v).substring(0,300)); } catch(e){}
      }
      t[k] = v;
      return true;
    }
  });
  window.op = p;
  return 'proxied';
})()
"""


def adb(*args):
    return subprocess.run([ADB, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


def ev(base, code, timeout=6.0):
    body = json.dumps({"code": code, "timeout": timeout}).encode()
    req = urllib.request.Request(
        base + "/control/eval", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 8) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "value": f"<{exc}>"}


def logs():
    out = adb("logcat", "-d", "-v", "brief").stdout or ""
    return [ln.split("OPPAIHOOK|", 1)[-1] for ln in out.splitlines() if "OPPAIHOOK" in ln]


def main():
    from gamesrv import config

    base = f"http://127.0.0.1:{config.CDN_PORT}"

    adb("shell", "am", "force-stop", PKG)
    time.sleep(1.5)
    adb("logcat", "-c")
    adb("shell", "am", "start", "-n", f"{PKG}/{ACT}")

    # 尽早装 Proxy
    for i in range(60):
        time.sleep(1)
        r = ev(base, PROXY, timeout=4)
        if r.get("value") == "proxied":
            print(f">> 第{i + 1}s 装上 Proxy")
            break
        if r.get("value") == "already":
            print(f">> 第{i + 1}s 已经有了")
            break
    else:
        print("!! Proxy 没装上（op 一直不存在）")

    print(">> 观察 40s ...")
    time.sleep(40)

    print("\n===== 探针日志 =====")
    for line in logs():
        print(line)


if __name__ == "__main__":
    main()
