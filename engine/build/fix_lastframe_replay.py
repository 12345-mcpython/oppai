"""引擎层根治：在「最后一帧回调」里再 play() 新动画时，别把新动画按播完收尾。

根因（2026-09-20 用运行时探针在模拟器上钉死）
===========================================

`ActionTimeline::step()` 的收尾分支：

```cpp
    else
    {
        if(_lastFrameListener != nullptr)
            _lastFrameListener();     // ← 游戏常在这里 play() 下一段动画

        _playing = _loop;             // ← 用的是「刚播完那一段」的 loop
        if(!_playing) {
            _time = _endFrame * _frameInternal;
            _currentFrame = (int)(_time / _frameInternal);   // ← 拽到最后一帧
            stepToFrame(_currentFrame);
        }
        else
            gotoFrameAndPlay(_startFrame, _endFrame, _loop);
    }
```

回调里 `play("end", false)` 会把 `_loop=false / _currentFrame=startIndex / _playing=true`
设好，但**回到 step() 之后**这三行立刻把它覆盖：`_playing = _loop`（false）→
`_currentFrame = _endFrame`（直接拽到新动画的最后一帧）→ 新动画一帧都没播，
**它自己的最后一帧回调永远不会响**。

游戏里踩到的具体链路（宿舍换装）：

```js
// src/ui/favor/favorlayer.js  FavorLayer._playChangeClothes
op.touchEnabled = false;                       // 关掉全局触摸闸
tl.play("began", false);
tl.setLastFrameCallFunc(function () {          // began 的最后一帧 ✓ 会响
    asstLayer.fadeIn(...);
    tl.play("end", false);                     // ← 在回调里 play 非循环动画
    tl.setLastFrameCallFunc(function () {      // ✗ 永远不响
        op.touchEnabled = true;                //   （唯一开闸的地方）
        tl.clearLastFrameCallFunc();
    });
    if (cb) cb();
});
```

症状完全吻合实测：`op.touchEnabled` 一直是 `false`、画面照常渲染（所以"没卡顿"）、
时间轴停在新动画的 `_endFrame`（探针读到 `frame=236 / endFrame=236 / playing=false`）、
而 `end` 的最后一帧回调一次都没触发（trace 里只有 `FIRE ... anim=began`）。
换背景（`_replaceBg`）、`LoadingLayer.show`（began→loop）、HeroList、QuestLayer 等
十几处都是同一个写法；只要回调里 play 的是**非循环**动画就会中招。

修法
====

加一个「play 代数」计数器，`step()` 在调回调前记下代数，回调返回后一旦发现
代数变了（说明回调里 play 了新动画），就**直接 return**，让新动画按 `resume()`
留下的状态自己继续播：

```cpp
    else
    {
        unsigned int playGen = _playGen;      // oppai
        if(_lastFrameListener != nullptr)
            _lastFrameListener();
        if (_playGen != playGen)              // oppai: 回调里又 play 了 → 交给新动画
            return;

        _playing = _loop;
        ...
    }
```

这样 `end` 会正常从头播到尾，它的最后一帧回调也就会响，`op.touchEnabled` 得以恢复。
循环动画（战斗 began→loop）原来就走 `_playing = _loop`（true）分支，行为不变。
"""

from __future__ import annotations

import io
import re

ROOT = r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings"
H = ROOT + r"\cocos2d-x\cocos\editor-support\cocostudio\ActionTimeline\CCActionTimeline.h"
C = ROOT + r"\cocos2d-x\cocos\editor-support\cocostudio\ActionTimeline\CCActionTimeline.cpp"

MARK = "_playGen"
ok = True


def save(path, text):
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)
    print("已写入", path)


# ---------------------------------------------------------------- 1) 头文件：加成员
s = io.open(H, encoding="utf-8").read()
if MARK in s:
    print("头文件已处理")
else:
    anchor = "    std::string _currentAnimationName;   // oppai"
    if anchor not in s:
        anchor = "    std::function<void()> _lastFrameListener;"
    if anchor not in s:
        raise SystemExit("!! 头文件里找不到锚点（%s）" % H)
    add = ("\n    /** oppai: play() 代数 —— 最后一帧回调里又 play 了新动画时用来识别 */\n"
           "    unsigned int _playGen;")
    s = s.replace(anchor, anchor + add, 1)
    save(H, s)
    ok = ok and MARK in s

# ---------------------------------------------------------------- 2) cpp：ctor 初始化
s = io.open(C, encoding="utf-8").read()
if "oppai: play 代数" in s:
    print("cpp 已处理")
else:
    # 2a) 构造函数初始化列表
    anchor = "    , _lastFrameListener(nullptr)"
    if anchor in s:
        s = s.replace(anchor, anchor + "\n    , _playGen(0)          // oppai: play 代数", 1)
    else:
        m = re.search(r"(ActionTimeline::ActionTimeline\(\)\s*\n\s*:\s*\n)", s)
        if not m:
            raise SystemExit("!! cpp 里找不到构造函数初始化列表")
        s = s[:m.end()] + "    _playGen(0)\n" + s[m.end():]

    # 2b) gotoFrameAndPlay 里自增（所有 play 都汇到它）
    g_anchor = ("void ActionTimeline::gotoFrameAndPlay(int startIndex, int endIndex, "
                "int currentFrameIndex, bool loop)\n{\n")
    if g_anchor not in s:
        raise SystemExit("!! 找不到 gotoFrameAndPlay(start,end,cur,loop)")
    s = s.replace(
        g_anchor,
        g_anchor + "    ++_playGen;                   // oppai: play 代数\n",
        1,
    )

    # 2c) step() 的收尾分支：回调里 play 了新动画就别收尾
    old = ("        if(_lastFrameListener != nullptr)\n"
           "            _lastFrameListener();\n"
           "        \n"
           "        _playing = _loop;")
    if old not in s:
        raise SystemExit("!! 找不到 step() 的 else 分支原文（引擎源码变了？）")
    new = ("        unsigned int playGen = _playGen;   // oppai: play 代数\n"
           "        if(_lastFrameListener != nullptr)\n"
           "            _lastFrameListener();\n"
           "        // oppai: 回调里可能又 play() 了新动画（宿舍换装/换背景、LoadingLayer\n"
           "        // 的 began->loop 都是这么写的）。若还按「这一段播完了」收尾，就会用\n"
           "        // 旧那段的 _loop 覆盖 _playing 并把新动画拽到它的最后一帧 —— 它的\n"
           "        // 最后一帧回调永远不会响（症状：op.touchEnabled 卡在 false，界面\n"
           "        // 不响应但画面正常）。代数变了就交给新动画自己继续。\n"
           "        if (_playGen != playGen)\n"
           "            return;\n"
           "        \n"
           "        _playing = _loop;")
    s = s.replace(old, new, 1)
    save(C, s)
    ok = ok and MARK in s

# ---------------------------------------------------------------- 3) 自检
h = io.open(H, encoding="utf-8").read()
c = io.open(C, encoding="utf-8").read()
checks = [
    ("头文件有 _playGen 成员", "unsigned int _playGen;" in h),
    ("ctor 初始化 _playGen(0)", "_playGen(0)" in c),
    ("gotoFrameAndPlay 自增", "++_playGen;" in c),
    ("step() 比较代数", "if (_playGen != playGen)" in c),
]
for name, good in checks:
    print(("  OK  " if good else "  BAD ") + name)
    ok = ok and good

print("补丁完成" if ok else "补丁有问题，别编")
raise SystemExit(0 if ok else 1)
