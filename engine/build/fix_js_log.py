r"""修 `js_log` —— 让引擎自己的 JS `log()` 在 release 包里也能打出来。

     python fix_js_log.py

## 现象

引擎给 debug global 装了一个 `log()`（`ScriptingCore.cpp:1770`），
调试器自己的 `dumpn()` / 我们的诊断都走它：

```cpp
void js_log(const char *format, ...) {
    ...
    if (len > 0) {
        CCLOG("JS: %s", _js_log_buf);        // ← 问题在这
    }
}
```

而 `CCLOG` 在 **`COCOS2D_DEBUG == 0` 时是个空宏**（`CCPlatformMacros.h:228`）：

```cpp
#if !defined(COCOS2D_DEBUG) || COCOS2D_DEBUG == 0
#define CCLOG(...)       do {} while (0)
```

我们的 `Application.mk` 是 `NDK_DEBUG=0` → `-DNDEBUG` → `COCOS2D_DEBUG` 没定义 →
**`log()` 一个字都打不出来**。

查这个问题的时候绕了一圈：调试器明明跑起来了（`listTabs`/`attach` 都正常），
但在 JS 里加的 `log('[oppai] ...')` 在 logcat 里死活找不到 ——
不是 JS 没执行，是日志被宏吃了。

## 改法

直接调 `cocos2d::log`（= `LOGD` = `__android_log_print`），它不受 `COCOS2D_DEBUG` 影响。
顺带把 `ScriptingCore::reportError` 里那几处 `js_log` 也一并受益。
"""

from __future__ import annotations

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOVE = os.path.dirname(HERE)
TARGET = os.path.join(MOVE, "src", "cocos2d-js", "frameworks", "js-bindings",
                      "bindings", "manual", "ScriptingCore.cpp")

OLD = """    if (len > 0)
    {
        CCLOG("JS: %s", _js_log_buf);
    }"""

NEW = """    if (len > 0)
    {
        // [oppai] 原来这里是 CCLOG，而 CCLOG 在 COCOS2D_DEBUG == 0（我们 release 包的情况）
        // 是个空宏（CCPlatformMacros.h:228），于是引擎自己的 JS `log()` ——
        // 调试器的 dumpn() 也走它 —— 在 logcat 里一个字都看不到。
        // 这里改调 cocos2d::log（= LOGD），它不受 COCOS2D_DEBUG 影响。
        cocos2d::log("JS: %s", _js_log_buf);
    }"""


def main() -> int:
    if not os.path.isfile(TARGET):
        print(f"!! 找不到 {TARGET}", file=sys.stderr)
        return 1
    with io.open(TARGET, encoding="utf-8") as fh:
        text = fh.read()
    if "[oppai] 原来这里是 CCLOG" in text:
        print("[js-log] 已经打过补丁，跳过")
        return 0
    if OLD not in text:
        print("!! 找不到 js_log 里那段（cocos2d-js 换版本了？）", file=sys.stderr)
        return 1
    with io.open(TARGET, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace(OLD, NEW, 1))
    print(f"[js-log] 已写入 {TARGET}")
    print("[js-log] 下一步：关掉 build.ps1 的 $ErrorActionPreference，然后重编")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
