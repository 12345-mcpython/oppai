# 引擎层补丁清单

`E:\code\zcsmw\engine\src\` 下的源码改动**不在 git 仓库里**（太大），
所以每个改动都有一个可重复执行的脚本放在 `E:\code\zcsmw\engine\build\`。
重装依赖后按顺序跑一遍即可复原。

## 本节目录

- [顺序](#顺序)
- [⑯ 触摸传播穿不过裸 Node —— 列表条目上拖不动](#⑯-触摸传播穿不过裸-node--列表条目上拖不动)
- [⑫ 打开引擎自带的远程 JS 调试器](#⑫-打开引擎自带的远程-js-调试器)
- [⑬ 修 `js_log`（`CCLOG` 在 release 里是空宏）](#⑬-修-js_logcclog-在-release-里是空宏)
- [① 最后一帧回调传动画名 —— 战斗收尾卡住的根因](#①-最后一帧回调传动画名--战斗收尾卡住的根因)
- [③b 在「最后一帧回调」里再 play 新动画 —— 宿舍换装后整个界面不响应](#③b-在最后一帧回调里再-play-新动画--宿舍换装后整个界面不响应)
- [② RotationSkewFrame 运算符优先级 —— 原生崩溃根因](#②-rotationskewframe-运算符优先级--原生崩溃根因)
- [与「为什么原引擎没这些问题」的关系](#与为什么原引擎没这些问题的关系)

---

## 顺序

```powershell
cd E:\code\zcsmw\engine\build
python fix_lastframe_engine.py      # ① 最后一帧回调传动画名（战斗收尾根因）
python fix_precedence.py            # ② RotationSkewFrame 运算符优先级（原生崩溃根因）
python fix_lastframe_replay.py      # ③b 回调里再 play 新动画时别按「播完」收尾（宿舍换装卡死根因）
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
python fix_null_texture.py          # ⑭ Sprite::draw 空贴图崩溃
python patch_scriptingcore.py       # ⑮ JS 异常内容打到 logcat
python fix_scrollview_propagate.py  # ⑯ 触摸传播穿不过裸 Node（列表条目上拖不动）
```

> ⚠️ **只要目标文件在 `engine/src/`（第三方源码、被 .gitignore 挡着）的补丁，
> 每次重编都得重跑** —— 别人的 `src/` 是干净的。`build.ps1 -Engine` 会自动重跑
> 上面这 9 个（① ② ③b ③ ⑫ ⑬ ⑭ ⑮ ⑯，都写成幂等的）。
> 改 `engine/build/oppai-engine/**` 的那些（④~⑪）结果**已经跟着仓库发布了**，不用重跑。

> ⚠️ `build.ps1` **必须是 UTF-8 带 BOM**。Windows PowerShell 5.1 会把无 BOM 的
> UTF-8 脚本按 ANSI（中文系统上就是 GBK）读，中文注释直接把脚本读崩，
> 报一堆 `Unexpected token`。另外它的 `$Move` 是 `Split-Path -Parent $PSScriptRoot`
> （一层），写成两层会指到 `E:\code\zcsmw`。
> `ndk-build` 的输出走 stderr，脚本里的 `$ErrorActionPreference = "Stop"` 会把它
> 当终止错误 —— 直接用 `ndk-build.cmd` 调更省事，见 `docs/engine-debug.md` 第 6 节。

---

## ⑯ 触摸传播穿不过裸 Node —— 列表条目上拖不动

**现象**：宿舍「RoomList」只能从条目之间的**空隙**起手才能滚动，
**在角色条目上按住拖动完全没反应**（条目自己的点击照旧好使）。

**根因**（cocos2d-x 3.6 `cocos/ui/UIWidget.cpp`）：

```cpp
Widget* Widget::getWidgetParent() { return dynamic_cast<Widget*>(getParent()); }  // 只看**直接**父节点

void Widget::propagateTouchEvent(...)
{
    Widget* widgetParent = getWidgetParent();
    if (widgetParent) widgetParent->interceptTouchEvent(event, sender, touch);
}
```

而游戏的列表条目是 `favorlistitemlayer.csb` 出来的**裸 `cc.Node`**
（实测 `items[0].constructor.name === "Node"`）。于是：

```
条目里的按钮(Widget) -> propagateTouchEvent
  -> getWidgetParent() = dynamic_cast<Widget*>(裸 Node) = nullptr
  -> 传播到此为止
```

`ScrollView::interceptTouchEvent` 永远收不到 BEGAN/MOVED，
`_isInterceptTouch` / `handleMoveLogic` 都不跑 —— 拖动被条目自己的
`_touchListener`（`Widget::addTouchEventListener` 里写死的 `setSwallowTouches(true)`）吃掉。

**改法**：`propagateTouchEvent` 改成**沿裸父链往上找第一个 Widget**。

**为什么是严格超集（改动面可控）**：
* 直接父节点就是 Widget 时，找到的还是**同一个** Widget，后面
  `Widget::interceptTouchEvent`（UIWidget.cpp:981）本来就会**逐层往上递归**，
  所以这条路径**和原来一模一样**
* 只有「中间隔了一层裸 Node」这种情况以前是断的，现在接上了
* `ScrollView::interceptTouchEvent` 在 ENDED 里**不会**再往上递归，
  所以不会出现两个 ScrollView 同时滚

**验证**：宿舍列表在条目上直接拖动就能滚；条目点击照旧。
排查手法见 `docs/pitfalls.md` 第 11 条。

---

## ⑫ 打开引擎自带的远程 JS 调试器

`ScriptingCore` 里本来就有一整套（SpiderMonkey Debugger API + Firefox 远程调试协议
+ TCP 服务），但官方模板把它放在 `#if COCOS2D_DEBUG` 里，而我们是 `NDK_DEBUG=0`，
所以从来没被调用过。`enable_js_debugger.py` 往 `AppDelegate.cpp` 里插一段无条件调用。

细节、协议、以及「调试器自己的 JS 怎么换成明文来改」见
`docs/engine-debug.md`。

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

## ③b 在「最后一帧回调」里再 play 新动画 —— 宿舍换装后整个界面不响应

**现象**（用户报的）：宿舍里给角色换衣服，换完**画面正常、音乐照放，但点哪都没反应**，
屏幕上留着「着裝中…」。不是卡顿（`cc.director` 还在跑），是输入没了。

**探针实测**（`op.touchEnabled` 上挂 setter + 包 `ActionTimeline`，见 trace）：

```
SET touchEnabled=false :: FavorLayer<._playChangeClothes@favorlayer.js:1080:13
PLAY began loop=false tl=1
REG  lastFrame tl=1 anim=began
FIRE lastFrame tl=1 anim=began          ← began 的回调响了
CB 到达
PLAY end loop=false tl=1
REG  lastFrame tl=1 anim=end            ← 注册了……
<<< 没有 FIRE lastFrame tl=1 anim=end >>>   ← 永远不响
```

换装那段 JS（`src/ui/favor/favorlayer.js`）：

```js
op.touchEnabled = false;                      // 关掉全局触摸闸
tl.play("began", false);
tl.setLastFrameCallFunc(function () {
    asstLayer.fadeIn(...);
    tl.play("end", false);                    // ← 在回调里 play 了**非循环**动画
    tl.setLastFrameCallFunc(function () {
        op.touchEnabled = true;               // 唯一开闸的地方
        tl.clearLastFrameCallFunc();
    });
    if (cb) cb();
});
```

**根因**（cocostudio `CCActionTimeline.cpp` 的 `step()` 收尾分支）：

```cpp
    else
    {
        if(_lastFrameListener != nullptr)
            _lastFrameListener();     // 回调里 play("end", false)：
                                      //   _loop=false / _currentFrame=130 / _playing=true
        _playing = _loop;             // ← 用**刚播完那段**的 loop 覆盖，新动画被按下暂停
        if(!_playing) {
            _time = _endFrame * _frameInternal;
            _currentFrame = (int)(_time / _frameInternal);   // ← 直接拽到新动画最后一帧
            stepToFrame(_currentFrame);
        }
        else
            gotoFrameAndPlay(_startFrame, _endFrame, _loop);
    }
```

新动画一帧都没播就被判定"播完了"，于是**它自己的最后一帧回调永远不会响** ——
`op.touchEnabled` 卡在 `false`。实测状态完全吻合：探针读到
`frame=236 / endFrame=236 / playing=false`（就是被拽过去的那一帧）。

> 战斗那套（`began1→loop1→end1→…`）没踩到，是因为它回调里 play 的是
> **循环**动画（`playAnimation("loop"+N, true)`），`_playing = _loop` 恰好是 true，
> 不进那个"拽到最后一帧"的分支；`end1` 是从 **frameEvent** 回调里 play 的，
> 走的是另一条路径（`stepToFrame → emitFrameEvent`），不受影响。
> 宿舍换装/换背景、`LoadingLayer.show`（began→loop）、HeroList、QuestLayer
> 等十几处都是「回调里再 play」的写法，只要那一段是非循环就会中招。

**修法**：加一个 play 代数计数器，回调返回后发现代数变了就直接 return，
把收尾交给新动画自己：

```cpp
    else
    {
        unsigned int playGen = _playGen;      // oppai
        if(_lastFrameListener != nullptr)
            _lastFrameListener();
        if (_playGen != playGen)              // 回调里又 play 了 → 别按"播完"收尾
            return;

        _playing = _loop;
        ...
    }
```

`_playGen` 在 `gotoFrameAndPlay(...)` 里自增（所有 `play` 都汇到那里）。
循环动画原路径行为不变。

**验证**：重编引擎后，宿舍换装走完 `began→end`，`touchEnabled` 能回到 true；
`out/` 里那个 trace 探针可以复现（`probe.js` 的 REPL 注入，
给 `op.touchEnabled` 挂 setter + 包 `ActionTimeline.setLastFrameCallFunc`）。

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
| ↳ 它的**遍历顺序** | ——（我们在 `patch.js` 里补的 polyfill） | **层序（BFS）**：`_ZN7cocos2d2ui6Helper14seekNodeByNameEPNS_4NodeERKSs` @ `0xaea0fd`（202 字节，层队列 + 下标递增）；`seekNodeByTag` @ `0xaea059`（164 字节）同一套 |
| `RotationSkewFrame::onApply` 优先级 | 有 bug（4 处） | 已修 |
| `setLastFrameCallFunc` 传参 | `invoke(0, ...)` 不传 | 传动画名 |

> ⚠️ **遍历顺序也是"原版语义"的一部分**：`patch.js` 第一版 polyfill 写成了深度优先，
> 结果「同名节点取到更深那个」——好友面板整页崩（`sendRedDotCase is null`）、
> 情报室返回键点不动，两个都真踩过。2026-09-21 按上面的地址反汇编原版 `.so`
> 才定成层序。判据/过程见 `docs/pitfalls.md` 第 15 条。

对比脚本见 `E:\code\zcsmw\engine\src\cocos2d-x-new`（cocos2d-x 3.17 的
ActionTimeline 目录，sparse checkout）。
