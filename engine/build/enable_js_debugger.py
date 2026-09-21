r"""打开引擎自带的远程 JS 调试器。

     python enable_js_debugger.py

## 背景

cocos2d-js 3.6 的 `ScriptingCore` **本来就带一个完整的远程 JS 调试器**
（`ScriptingCore::enableDebugger(port)`，见
`frameworks/js-bindings/bindings/manual/ScriptingCore.cpp:1754`）：

* `JS_SetDebugMode(cx, true)` + 新建一个 debug global + `JS_DefineDebuggerObject`
  —— 用的是 **SpiderMonkey 的 Debugger API**（`OldDebugAPI.h`），
  不是自己糊的钩子，断点/单步/栈帧/作用域都是引擎级的
* 在 debug global 里跑 `script/jsb_debugger.js`（APK 里是 `jsb_debugger.jsc`）
* 起一个 TCP 服务（默认 5086），说话的是
  **Firefox 远程调试协议**（老的 actors 那一版，Firefox 24~33 用的）
* `_runLoop` 挂在 cocos 的 scheduler 上，所以断点命中时游戏是**真的停住**的
  （`_enterNestedEventLoop` / `_exitNestedEventLoop` 开嵌套事件循环）

`assets/script/debugger/README.md` 就是原版说明：配上 Firefox 的远程调试面板用。

**但它默认是关的** —— 全套 cocos2d-js 模板里这行都在
`#if defined(COCOS2D_DEBUG) && (COCOS2D_DEBUG > 0)` 里，
而我们的 `Application.mk` 是 `NDK_DEBUG=0`（`-DNDEBUG`），所以从没被调用过。
这个脚本就是把它接上。

## 做了什么

往 `build/oppai-engine/Classes/AppDelegate.cpp` 里、`sc->runScript("script/jsb_boot.js")`
之后插一段（幂等，认 `// [oppai] JS-DEBUGGER` 标记）：

```cpp
cocos2d::FileUtils* fu = cocos2d::FileUtils::getInstance();
std::string wp = fu->getWritablePath();
if (!fu->isFileExist(wp + "jsdebugger.off")) {      // 想临时关掉就建这个文件
    unsigned int jsdPort = 5086;
    std::string portFile = wp + "jsdebugger.port";  // 想换端口就写这个文件
    ...
    sc->enableDebugger(jsdPort);
}
```

**为什么放在 jsb_boot 之后**：`_prepareDebugger(global)` 第一句就是
`require = global.require;`，`require` 是 `jsb_boot.js` 建出来的。

## 跑完之后

```powershell
.\build.ps1 -Abi armeabi          # 重编 libcocos2djs.so
cd ..\game_server
python tools\build_apk.py         # 重打包 + 签名
adb install -r -d E:\code\zcsmw\out\zcsmw-mod-signed.apk
adb forward tcp:5086 tcp:5086     # MEmu 是 NAT 的，要把端口转发出来
python tools\jsd.py tabs          # 连上去看看
```
"""

from __future__ import annotations

import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APPDELEGATE = os.path.join(HERE, "oppai-engine", "Classes", "AppDelegate.cpp")

MARK = "// [oppai] JS-DEBUGGER"
ANCHOR = '    sc->runScript("script/jsb_boot.js");'

SNIPPET = r'''
    // [oppai] JS-DEBUGGER —— 打开引擎自带的远程 JS 调试器
    //
    // cocos2d-js 3.6 的 ScriptingCore 里本来就有一整套（SpiderMonkey Debugger API
    // + Firefox 远程调试协议 + TCP 服务），但官方模板把这行放在
    // `#if COCOS2D_DEBUG` 里，而我们的 Application.mk 是 NDK_DEBUG=0，
    // 所以从来没被调用过。这里无条件打开。
    //
    // 必须在 runScript("script/jsb_boot.js") **之后**：
    // jsb_debugger.js 的 _prepareDebugger() 第一句就是 `require = global.require;`。
    //
    // 想临时关掉：在可写目录建一个空文件 `jsdebugger.off`
    //   adb shell "run-as com.cm.zcsmw.baidu touch files/jsdebugger.off"   （或直接写文件）
    // 想换端口：在可写目录写 `jsdebugger.port`，内容就是端口号。
    {
        cocos2d::FileUtils* jsdFu = cocos2d::FileUtils::getInstance();
        std::string jsdWritable = jsdFu->getWritablePath();
        if (jsdFu->isFileExist(jsdWritable + "jsdebugger.off")) {
            cocos2d::log("[oppai] JS debugger 已被 jsdebugger.off 关掉");
        } else {
            unsigned int jsdPort = 5086;
            std::string jsdPortFile = jsdWritable + "jsdebugger.port";
            if (jsdFu->isFileExist(jsdPortFile)) {
                int jsdV = atoi(jsdFu->getStringFromFile(jsdPortFile).c_str());
                if (jsdV > 0 && jsdV < 65536) {
                    jsdPort = (unsigned int)jsdV;
                }
            }
            cocos2d::log("[oppai] enableDebugger port=%u  (adb forward tcp:%u tcp:%u)",
                         jsdPort, jsdPort, jsdPort);
            sc->enableDebugger(jsdPort);
        }
    }
'''


def main() -> int:
    if not os.path.isfile(APPDELEGATE):
        print(f"!! 找不到 {APPDELEGATE}", file=sys.stderr)
        return 1

    with io.open(APPDELEGATE, encoding="utf-8") as fh:
        text = fh.read()

    if MARK in text:
        print("[js-debugger] 已经打过补丁，跳过")
        return 0

    if ANCHOR not in text:
        print(f"!! 在 AppDelegate.cpp 里找不到锚点：{ANCHOR!r}", file=sys.stderr)
        print("   （cocos2d-js 换版本 / 有人改过这段之后要重新对一下）", file=sys.stderr)
        return 1

    text = text.replace(ANCHOR, ANCHOR + "\n" + SNIPPET, 1)

    # atoi 需要 <cstdlib>；FileUtils 已经在别处间接引进来了，这里显式补一个头
    if "#include <cstdlib>" not in text:
        text = text.replace('#include "AppDelegate.h"',
                            '#include "AppDelegate.h"\n\n#include <cstdlib>   // [oppai] JS-DEBUGGER: atoi',
                            1)

    with io.open(APPDELEGATE, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    print(f"[js-debugger] 已写入 {APPDELEGATE}")
    print("[js-debugger] 下一步： .\\build.ps1 -Abi armeabi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
