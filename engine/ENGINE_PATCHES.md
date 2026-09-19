# 引擎层补丁清单

`E:\code\zcsmw\engine\src\` 下的源码改动**不在 git 仓库里**（太大），
所以每个改动都有一个可重复执行的脚本放在 `E:\code\zcsmw\engine\build\`。
重装依赖后按顺序跑一遍即可复原。

## 顺序

```powershell
cd E:\code\zcsmw\engine\build
python fix_lastframe_engine.py      # ① 最后一帧回调传动画名（战斗收尾根因）
python fix_precedence.py            # ② RotationSkewFrame 运算符优先级（原生崩溃根因）
python quiet_engine.py              # ③ JniHelper 日志降噪
python add_bugly.py                 # ④ Bugly 全局函数
python add_touch.py                 # ⑤ cc.Touch 力度方法（战斗摇杆）
python add_native_videoplayer.py    # ⑥ ccui.VideoPlayer 原生绑定
python add_vp_create.py             # ⑦ 上一项的静态 create()
python add_uihelper.py              # ⑧ ccui.helper（注：被 jsb_boot 覆盖，实际靠 patch.js）
python add_csloader.py              # ⑨ ccs.CSLoader
python move_ccs.py                  # ⑩ ActionTimelineCache（并入 utilsex.cpp）
python add_ccs.py                   # ⑪ 注册进 Android.mk / AppDelegate
python enable_js_debugger.py        # ⑫ 打开引擎自带的远程 JS 调试器
python fix_js_log.py                # ⑬ 让引擎自己的 JS log() 在 release 包里也能打出来
```

> ⚠️ `build.ps1` **必须是 UTF-8 带 BOM**。Windows PowerShell 5.1 会把无 BOM 的
> UTF-8 脚本按 ANSI（中文系统上就是 GBK）读，中文注释直接把脚本读崩，
> 报一堆 `Unexpected token`。另外它的 `$Move` 是 `Split-Path -Parent $PSScriptRoot`
> （一层），写成两层会指到 `E:\code\zcsmw`。
> `ndk-build` 的输出走 stderr，脚本里的 `$ErrorActionPreference = "Stop"` 会把它
> 当终止错误 —— 直接用 `ndk-build.cmd` 调更省事，见 `docs/engine-debug.md` 第 6 节。

---

## ⑫ 打开引擎自带的远程 JS 调试器

`ScriptingCore` 里本来就有一整套（SpiderMonkey Debugger API + Firefox 远程调试协议
+ TCP 服务），但官方模板把它放在 `#if COCOS2D_DEBUG` 里，而我们是 `NDK_DEBUG=0`，
所以从来没被调用过。`enable_js_debugger.py` 往 `AppDelegate.cpp` 里插一段无条件调用。

细节、协议、以及「调试器自己的 JS 怎么换成明文来改」见
`server/docs/engine-debug.md`。

## ⑬ 修 `js_log`（`CCLOG` 在 release 里是空宏）

```cpp
void js_log(const char *format, ...) {
    if (len > 0) { CCLOG("JS: %s", _js_log_buf); }    // ← COCOS2D_DEBUG==0 时 CCLOG 是 do{}while(0)
}
```

引擎给 debug global 装的 `log()` 走这里，调试器的 `dumpn()` 也走这里 ——
release 包里一个字都打不出来，排查时极易误判成「JS 没执行」。改成 `cocos2d::log`。

---

## ① 最后一帧回调传动画名 —— 战斗收尾卡住的根因

**证据链**（三份材料交叉确认）：

`battlebeganui.csb` 解析出来的动画序列：

```
began1 [   0,  28]   loop1 [  30, 450]   end1 [ 455, 471]
began2 [ 475, 503]   loop2 [ 505, 925]   end2 [ 930, 946]
began3 [ 950, 978]   loop3 [ 980,1400]   end3 [1405,1421]
```

帧事件只有 3 个，全是音效，**没有任何 `loop\d` 帧事件**：

```
frame   1  sound_battlebegansound   [began1]
frame 476  sound_battlebegansound   [began2]
frame 951  sound_battlebegansound   [began3]
```

`battlescene.jsc` 的 `BattleScene.ctor`：

```js
var tl = ccs.csLoader.createTimeline("res/battlebeganui");
battleBeganUi.runAction(tl);
battleBeganUi.playAnimation = (lambda0).bind(this);      // 绑到场景
tl.setFrameEventCallFunc(op.onSoundFrameEvent);          // 只处理音效
tl.setLastFrameCallFunc(lambda1);                        // ← 关键
```

`lambda1`（`BattleScene<.ctor/<`，92 字节）：

```js
function (eventName) {                                   // ← 取第一个参数！
    if (/began\d/.test(eventName))      scene.playAnimation("loop" + N, true);
    else if (/end\d/.test(eventName))   scene._battleBeganUi.visible = false;
}
```

`battleBeganUi.playAnimation`（227 字节）：

```js
playAnimation(eventName, loop) {
    if (/loop\d/.test(eventName)) { if (this._nextCb) { this._nextCb(); this._nextCb = null; } }
    else if (/began\d/.test(eventName)) { ...进度条复位... }
    player.play(eventName, loop);
}
```

**完整链条**：

```
_show1(cb) → _nextCb = cb; playAnimation("began3")
began3 播完 → 最后一帧回调("began3") → /began\d/ 命中 → playAnimation("loop3", true)
playAnimation("loop3") → /loop\d/ 命中 → _nextCb()      ← 战斗继续
```

**根因**：vanilla cocos2d-js v3.6 的自动绑定是

```cpp
bool ok = func->invoke(0, nullptr, &rval);      // 0 个参数
```

游戏代码依赖第一个参数是**动画名**，所以整个链条断在第一步。

**修法**：

1. `CCActionTimeline.h` 加 `std::string _currentAnimationName;` +
   `getCurrentAnimationName()`
2. `CCActionTimeline.cpp` 的 `play()` 里记录 `_currentAnimationName = name;`
3. `jsb_cocos2dx_studio_auto.cpp` 的 `setLastFrameCallFunc` lambda 改成
   `func->invoke(1, argv, &rval)`，`argv[0]` = 动画名

**顺带**：`UpdateScene._logo` 的黑屏也是同一个根因 ——

```js
tl.setLastFrameCallFunc(function (eventName) {
    if (eventName === "default") { this._init(); }      // ← 同样依赖参数
});
```

一个引擎 bug 同时造成两处症状。

---

## ② RotationSkewFrame 运算符优先级 —— 原生崩溃根因

tombstone 里的完整符号堆栈：

```
cocostudio::timeline::RotationSkewFrame::onApply(float)
cocostudio::timeline::Frame::apply(float)
cocostudio::timeline::Timeline::apply(int)
cocostudio::timeline::Timeline::gotoFrame(int)
→ SIGSEGV (null pointer dereference)
```

`CCFrame.cpp`：

```cpp
if (nullptr != _node && _betweenSkewX != 0 || _betweenSkewY != 0)   // && 优先级高于 ||
```

实际解析成 `(A && B) || C` —— 只要 `_betweenSkewY != 0` 而 `_node == nullptr`
就空指针解引用。

同文件 403 行是**已经修好的写法**（`nullptr != _node && (A || B)`），
说明上游后来正是这么改的 —— 游戏那版引擎已包含修复。

共 4 处：285 / 345 / 463 / 697。

---

## 与「为什么原引擎没这些问题」的关系

游戏当年的 cocos2d-x **不是** vanilla 3.6，而是打过补丁的更新版本。
已证实的三处差异：

| 差异 | vanilla 3.6 | 游戏那版 |
|---|---|---|
| `ui::Helper::seekNodeByName(Node*,name)` | 不存在（只有收 `Widget*` 的） | 存在（3.7+ 才加） |
| `RotationSkewFrame::onApply` 优先级 | 有 bug（4 处） | 已修 |
| `setLastFrameCallFunc` 传参 | `invoke(0, ...)` 不传 | 传动画名 |

对比脚本见 `E:\code\zcsmw\engine\src\cocos2d-x-new`（cocos2d-x 3.17 的
ActionTimeline 目录，sparse checkout）。
