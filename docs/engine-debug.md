# 引擎层调试支持

**结论：引擎本来就带一个完整的 JS 调试器，只是默认关着。**

cocos2d-js 3.6 的 `ScriptingCore` 里有 `enableDebugger(port)`：
用 **SpiderMonkey 的 Debugger API**（不是糊的钩子）挂进 JS 引擎，
外面套一层 **Firefox 远程调试协议**，在 TCP 上说话。
断点、单步、调用栈、作用域、**暂停时求值**全都有，而且游戏主循环是真的停住。

官方模板把它放在 `#if defined(COCOS2D_DEBUG) && (COCOS2D_DEBUG > 0)` 里，
我们的 `Application.mk` 是 `NDK_DEBUG=0`（`-DNDEBUG`），所以它从来没被调用过。

本文记录：它是什么、怎么打开、踩了哪四个坑、怎么用。

## 本节目录

- [1. 引擎里到底有什么](#1-引擎里到底有什么)
- [2. 怎么打开](#2-怎么打开)
- [3. 四个坑（都是实测踩出来的）](#3-四个坑都是实测踩出来的)
- [4. 实测长什么样](#4-实测长什么样)
- [5. 和另外两块调试能力的关系](#5-和另外两块调试能力的关系)
- [6. 复现清单](#6-复现清单)

---

## 1. 引擎里到底有什么

`bindings/manual/ScriptingCore.cpp:1754`：

```cpp
void ScriptingCore::enableDebugger(unsigned int port)
{
    if (_debugGlobal.empty())
    {
        JSAutoCompartment ac0(_cx, _global.ref().get());
        JS_SetDebugMode(_cx, true);                       // 打开 debug mode（禁 JIT 优化）
        _debugGlobal.construct(_cx);
        _debugGlobal.ref() = NewGlobalObject(_cx, true);  // 单独一个 debug global
        ...
        JS_DefineFunction(_cx, rootedDebugObj, "log", ScriptingCore::log, 0, ...);
        JS_DefineFunction(_cx, rootedDebugObj, "_bufferWrite", JSBDebug_BufferWrite, 1, ...);
        JS_DefineFunction(_cx, rootedDebugObj, "_enterNestedEventLoop", ..., 0, ...);
        JS_DefineFunction(_cx, rootedDebugObj, "_exitNestedEventLoop", ..., 0, ...);

        runScript("script/jsb_debugger.js", rootedDebugObj);   // ← 调试器本体（JS）

        jsval argv = OBJECT_TO_JSVAL(globalObj);
        JS_CallFunctionName(_cx, rootedDebugObj, "_prepareDebugger", ..., &outval);

        auto t = std::thread(&serverEntryPoint, port);         // ← TCP 服务，后台线程
        t.detach();

        Scheduler* scheduler = Director::getInstance()->getScheduler();
        scheduler->scheduleUpdate(this->_runLoop, 0, false);   // ← 游戏循环里泵命令
    }
}
```

`NewGlobalObject(cx, debug=true)` 里就一句关键的：

```cpp
if (ok && debug)
    ok = JS_DefineDebuggerObject(cx, glob);     // SpiderMonkey 的 Debugger 对象
```

`JS_DefineDebuggerObject` / `JS_SetDebugMode` 声明在 **`OldDebugAPI.h`**（不是 `jsapi.h`），
实现和 `libjs_static.a` 一起静态链进 `libcocos2djs.so` —— 所以**当前这个包其实已经带着调试器了**，
只是没人调 `enableDebugger`。

### 1.1 断点为什么能真的停住

暂停走的是 **嵌套事件循环**（`ScriptingCore.cpp:1574`）：

```cpp
bool JSBDebug_enterNestedEventLoop(...) {
    uint32_t nestLevel = ++s_nestedLoopLevel;
    while (NS_SUCCEEDED(rv) && s_nestedLoopLevel >= nestLevel) {
        if (!NS_ProcessNextEvent()) rv = NS_ERROR_UNEXPECTED;   // 泵调试器命令队列
    }
}
```

`NS_ProcessNextEvent` 每次从 C++ 队列取一条客户端命令、交给 debug global 的 `processInput`。
所以在断点上暂停时：**游戏主线程卡在这个 while 里，cocos 的 scheduler 不再跑**，
但调试器仍然是活的（能收 `resume` / `frames` / `clientEvaluate`）。

`script/jsb_debugger.js` 里那句 `DebuggerServer.openListener(5086)` 是个**假监听**
（`main.js` 里的 `ServerSocket` 是桩），真正的 socket 是 C++ 的 `serverEntryPoint`。

### 1.2 协议

**Firefox 远程调试协议**（老的那套 actors，Firefox 24~33 用的）。
帧格式 `<utf8 字节长度>:<json>`。完整速查表在 `gamesrv/jsdlink.py` 的模块 docstring 里。

```
{"to":"root",   "type":"listTabs"}                        -> {tabs:[{actor,title,url}]}
{"to":<tab>,    "type":"attach"}                          -> {type:"tabAttached", threadActor}
{"to":<thread>, "type":"attach"}                          -> {type:"paused"}   ⚠️ 游戏从这里冻住
{"to":<thread>, "type":"sources"}                         -> {sources:[...]}
{"to":<thread>, "type":"setBreakpoint","location":{...}}  -> {actor}
{"to":<thread>, "type":"resume"}                          -> {type:"resumed"}
{"to":<thread>, "type":"resume","resumeLimit":{"type":"step|next|finish"}}
{"to":<thread>, "type":"frames"}                          -> {frames:[...]}
{"to":<thread>, "type":"clientEvaluate","frame":<f>,"expression":"..."}
{"to":<thread>, "type":"detach"}                          -> {type:"detached"}
```

原版配合 **Firefox 24+ 的远程调试面板**用（`assets/script/debugger/README.md` 就是这份说明）。
我们没走 Firefox，而是自己写了个客户端（协议就上面这些，自己发 JSON 更省事）。

---

## 2. 怎么打开

### 2.1 引擎侧：一句调用

```powershell
cd E:\code\zcsmw\engine\build
python enable_js_debugger.py      # 往 AppDelegate.cpp 插 enableDebugger()，幂等
.\build.ps1 -Abi armeabi          # 重编 libcocos2djs.so
cd E:\code\zcsmw
python script\build_apk.py && adb install -r -d E:\code\zcsmw\out\zcsmw-mod-signed.apk
adb forward tcp:5086 tcp:5086     # MuMu 是 NAT 的，要把端口转出来
```

插进去的是这段（在 `sc->runScript("script/jsb_boot.js")` **之后**：

```cpp
cocos2d::FileUtils* jsdFu = cocos2d::FileUtils::getInstance();
std::string jsdWritable = jsdFu->getWritablePath();
if (jsdFu->isFileExist(jsdWritable + "jsdebugger.off")) {
    cocos2d::log("[oppai] JS debugger 已被 jsdebugger.off 关掉");
} else {
    unsigned int jsdPort = 5086;
    std::string jsdPortFile = jsdWritable + "jsdebugger.port";
    if (jsdFu->isFileExist(jsdPortFile)) { ...从文件读端口... }
    cocos2d::log("[oppai] enableDebugger port=%u", jsdPort);
    sc->enableDebugger(jsdPort);
}
```

* **为什么放在 `jsb_boot` 之后**：`_prepareDebugger(global)` 第一句是
  `require = global.require;`，而 `require` 是 `jsb_boot.js` 建出来的。
* **想临时关掉**：可写目录放一个 `jsdebugger.off`（`/data/data/<pkg>/files/`）。
* 起来之后 logcat 里能看到 `[oppai] enableDebugger port=5086`。

### 2.2 调试器自己的 JS：**不用重编引擎也能改**

`ScriptingCore::compileScript` 找脚本的顺序是 `.jsc` 优先、**没有才读 `.js`**：

```cpp
std::string byteCodePath = RemoveFileExt(path) + ".jsc";
if (futil->isFileExist(byteCodePath)) { script = JS_DecodeScript(...); }   // a)
if (!script) { ...JS::Compile(cx, obj, op, jsFileContent.c_str(), ...); }  // b)
```

而 `require` 就是 `ScriptingCore::executeScript` → `runScript`，走同一条路。
所以把 `assets/script/jsb_debugger.js` 和 `assets/script/debugger/**` 换成明文、
删掉对应的 `.jsc`，**调试器本身就成了可改的源码**。

`script\patch_js_debugger.py` 干的就是这件事（顺便打下面几个补丁），幂等。

> 这条对别的脚本也成立：任何 `.jsc` 旁边放一份同名 `.js`、把 `.jsc` 删掉，引擎就改读明文。
> 这是「不重编引擎就能改客户端逻辑」的口子 —— 项目里的 `patch.js` / `probe.js` 本来就是靠它生效的。

### 2.3 客户端

```powershell
python script\jsd.py tabs                  # 连不连得上
python script\jsd.py sources charcenter    # 列脚本
python script\jsd.py repl                  # 交互式
python script\jsd.py demo probe.js 78      # 一条龙演示
```

浏览器里则是调试台的 **「调试器」** 页签（`http://127.0.0.1:18080/devtools`）。

---

## 3. 四个坑（都是实测踩出来的）

### 坑 1：引擎自己的 `log()` 在 release 包里打不出来

`enableDebugger` 给 debug global 装了 `log`，调试器的 `dumpn()` 和我们的诊断都走它。
但实现是：

```cpp
void js_log(const char *format, ...) {
    if (len > 0) { CCLOG("JS: %s", _js_log_buf); }     // ← 问题在这
}
```

而 `CCLOG` 在 **`COCOS2D_DEBUG == 0` 时是空宏**（`CCPlatformMacros.h:228`）：

```cpp
#if !defined(COCOS2D_DEBUG) || COCOS2D_DEBUG == 0
#define CCLOG(...)       do {} while (0)
```

我们 `NDK_DEBUG=0` → 一个字都出不来。
**排查时在这上面绕了一圈**：调试器明明跑起来了（`listTabs`/`attach` 都正常），
但在 JS 里加的 `log('[oppai] ...')` 在 logcat 里死活找不到 —— 不是 JS 没执行，是日志被宏吃了。

→ `build\fix_js_log.py`：改成 `cocos2d::log`（= `LOGD`），不受 `COCOS2D_DEBUG` 影响。

### 坑 2：`sources` 请求的回复被通知淹了

协议里**没有请求 id**，回复只能靠形状认。`sources` 请求会先收到 **几百条 `newSource`**
通知（引擎每发现一个脚本推一条），真正的回复在最后。
客户端如果「发一个请求读一个包」，拿到的是 `newSource` —— 表现就是
「`sources` 永远返回空、不知道有哪些脚本、也就无从下断点」。

→ `gamesrv/jsdlink.py` 的 `request(to, type, expect=...)`：跳过
`newSource`/`newGlobal`/`tabListChanged` 这些通知，按形状等真正的回复。

### 坑 3：`script.js` 比 SpiderMonkey 33 新，`isExtensible` 不存在

`assets/script/debugger/` 是从比 SM 33 更新的 Firefox 里搬过来的。
`Debugger.Object.prototype.isExtensible / isFrozen / isSealed` 在 SM 33 上**还不存在**，
而 `ObjectActor.grip()` 里直接调：

```js
"extensible": this.obj.isExtensible(),
```

`grip()` 是 `frame.form()` 里给 `this` 做 grip 时的必经一步 ——
所以 **`frames` 请求整个失败**，报：

```
TypeError: this.obj.isExtensible is not a function
Stack: OA_grip@assets/script/debugger/actors/script.js:2714:21
       ... FA_form@... TA_onFrames@...
```

也就是「断点能命中，但拿不到调用栈」。

→ `script\patch_js_debugger.py` 里兜一下（函数不存在就给保守值）。

### 坑 4：`interrupt` 停下来是**没有栈帧**的

`{"type":"interrupt"}` 处理命令的地方是**调试器自己的 `processInput`**，
不是在某个 debuggee 帧里，所以 `_updateFrames()` 拿不到东西 —— `frames` 返回 `[]`。

想要调用栈，只能让**断点命中**（或单步停下）：那时暂停发生在 debuggee 的帧里。

→ 调试台面板上「暂停」按钮的提示就写了这一条；`jsd.py repl` 的 `pause` 也一样。

---

## 4. 实测长什么样

`python script\jsd.py demo probe.js 78`：

```
1) attach（这一步会把游戏冻住）
   线程 = conn2.context1   pauseActor = conn2.pause3
   暂停原因 = {"type": "attached"}

2) 列脚本
   共 550 个（jsc 的 url 是**构建机上的绝对路径**）
   选中 assets/src/patch/probe.js

3) 在 assets/src/patch/probe.js:78 下断点（只能趁游戏暂停的时候下）
   bp actor = conn2.breakpoint554

5) 恢复运行，等断点
   ★ 命中！{"type": "breakpoint", "actors": ["conn2.breakpoint554"]}

6) 调用栈
  → #0  emit              assets/src/patch/probe.js:78
    #1  (匿名)            assets/src/patch/probe.js:720
    #2  (匿名)            assets/src/patch/patch.js:581
    #3  (匿名)            F:\oppai\v2.2.0\...\assets\src\data\bosscenter.js:350

7) 在栈顶帧求值（等于在断点处开了一个 JS 控制台）
   typeof res                                           => {"return": "object"}
   dataManager && dataManager.player && dataManager.player.lv => {"return": 30}
   Object.keys(dataManager.character.soldiers).length   => {"return": 18}
```

断在游戏自己的 `.jsc` 里也一样（断点 `patch.js:538` → `applyResponse`）：

```
  → #0  applyResponse     assets/src/patch/patch.js:538
    #1  (匿名)            assets/src/patch/patch.js:576
    #2  (匿名)            assets/src/patch/probe.js:729
    #3  (匿名)            assets/src/patch/probe.js:392
    #4  (匿名)            F:\oppai\...\assets\src\util\server.js:432
    #5  (匿名)            assets/src/patch/probe.js:475
    #6  (匿名)            F:\oppai\...\assets\src\util\httpc.js:22
```

**顺带拿到一个很有用的东西**：`sources` 列表里 550 个脚本的 URL 就是
**构建机上的原始路径**（`F:\oppai\v2.2.0\client\oppai\frameworks\runtime-src\proj.android_cn_quick\assets\src\...`），
也就是「APK 里每个 `.jsc` 对应哪个源码文件、什么目录结构」一目了然。

### 拿不到的东西

* **源码正文取不到**。`{"to":<source>,"type":"source"}` 会去 fetch `file://F:\...`，
  失败返回 `loadSourceError`。`.jsc` 里没留源码（所以还是得靠
  `script/jsc_disasm.py` / `script/jsc_strings.py` 反汇编）。
  例外是我们自己发的明文 `.js`（`patch.js` / `probe.js` / 调试器自己）—— 那些是有的，
  所以行号可以直接对着源文件写。
* **`.jsc` 的行号要猜**。断点位置是 `{url, line}`，而游戏脚本的源码我们看不到。
  两个办法：
  1. 在**明文脚本**（`patch.js` / `probe.js` / 调试器自己）里下断点，行号直接对着文件写；
  2. 想在游戏脚本里断，就先在明文脚本里断下来，从**调用栈**里读出游戏脚本的
     `url:line`，再用那个行号去下断点。

---

## 5. 和另外两块调试能力的关系

| 层 | 工具 | 能干什么 | 局限 |
|---|---|---|---|
| **引擎层**（本文） | `/devtools` 的「调试器」页签、`script/jsd.py` | **断点 / 单步 / 调用栈 / 暂停时求值**，游戏真的停住 | 一次只能接一个客户端；接上就会把游戏冻住 |
| 客户端 JS 层 | `/devtools` 的「控制台」页签、`script/repl.py` | 在游戏进程里跑任意 JS，**不暂停** | 只能求值，不能停、不能单步、没有栈 |
| 服务端 | `/devtools` 的「流量」「玩家」「日志」「数据」 | 请求/回包、存档、作弊、日志 | 看不到客户端内部 |

三者共用同一个 `/devtools` 页面：**控制台**用来「顺手试一下」，
**调试器**用来「停下来看清楚」。控制台跑不了的东西（比如要看某个函数被谁调的）
就去调试器下断点。

---

## 6. 复现清单

```powershell
# 1. 引擎：打开调试器 + 修 js_log（各一次，幂等）
cd E:\code\zcsmw\engine\build
python enable_js_debugger.py
python fix_js_log.py
.\build.ps1 -Abi armeabi          # 注意 build.ps1 必须是 UTF-8 **带 BOM**
                                   #（Windows PowerShell 5.1 会把无 BOM 的 UTF-8 当 ANSI，中文注释直接读崩）
cd E:\code\zcsmw
# 引擎产物 .so 拷进解包目录（build.ps1 -Engine 会自动做这一步）
copy engine\build\oppai-engine\libs\armeabi\libcocos2djs.so game\lib\armeabi\libcocos2djs.so

# 2. 调试器自己的 JS 换成明文 + 打四个补丁
python script\patch_js_debugger.py

# 3. 打包安装 + 端口转发
python script\build_apk.py
adb install -r -d E:\code\zcsmw\out\zcsmw-mod-signed.apk
adb shell am start -n com.cm.zcsmw.baidu/org.cocos2dx.javascript.SplashActivity
adb forward tcp:5086 tcp:5086

# 4. 验证
adb logcat -d -v brief | findstr "oppai.*Debugger"     # 应该有 enableDebugger port=5086
python script\jsd.py tabs
python script\jsd.py demo probe.js 78
```

四个坑对应的脚本：

| 坑 | 脚本 |
|---|---|
| 调试器没打开 | `move\build\enable_js_debugger.py` |
| `log()` 被 `CCLOG` 吃掉 | `move\build\fix_js_log.py` |
| `sources` 空（通知淹没回复） | `script\patch_js_debugger.py`（客户端改法在 `gamesrv/jsdlink.py`） |
| `frames` 报 `isExtensible` | `script\patch_js_debugger.py` |
