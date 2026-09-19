"""引擎层根治：让 setLastFrameCallFunc 的回调收到「动画名」。

根因（从 battlebeganui.csb + battlescene.jsc 反汇编交叉确认）：

    tl.setLastFrameCallFunc(function (eventName) {
        if (/began\\d/.test(eventName))      scene.playAnimation("loop" + N, true);
        else if (/end\\d/.test(eventName))   scene._battleBeganUi.visible = false;
    });

    _show1(cb)  -> _nextCb = cb; playAnimation("began3")
    began3 播完 -> 最后一帧回调("began3") -> playAnimation("loop3", true)
    playAnimation("loop3") -> /loop\\d/ 命中 -> _nextCb()          <- 战斗继续

也就是说游戏**依赖最后一帧回调收到动画名**。

但 vanilla cocos2d-js v3.6 自动生成的绑定是：
    bool ok = func->invoke(0, nullptr, &rval);      // 0 个参数！
游戏那版引擎显然传了动画名。

修法（两层）：
  1. ActionTimeline 记录最近 play 的动画名，并提供 getter
  2. jsb_cocos2dx_studio_auto.cpp 里 setLastFrameCallFunc 的 lambda
     改成 invoke(1, argv, &rval)，argv[0] = 动画名
  3. 顺带把 getCurrentAnimationName 也注册成 JS 方法

改完 patch.js 里那个「猜动画名」的补丁就可以去掉了（它只在 play 时记录，
如果游戏在别处播放就失效；引擎层记录才是唯一权威）。
"""

from __future__ import annotations

import io
import re
import os

ROOT = r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings"
H = ROOT + r"\cocos2d-x\cocos\editor-support\cocostudio\ActionTimeline\CCActionTimeline.h"
C = ROOT + r"\cocos2d-x\cocos\editor-support\cocostudio\ActionTimeline\CCActionTimeline.cpp"
AUTO = ROOT + r"\bindings\auto\jsb_cocos2dx_studio_auto.cpp"

# ---------------------------------------------------------------- 1) 头文件
s = io.open(H, encoding="utf-8").read()
if "_currentAnimationName" in s:
    print("头文件已处理")
else:
    s = s.replace(
        "    void setLastFrameCallFunc(std::function<void()> listener);",
        "    void setLastFrameCallFunc(std::function<void()> listener);\n"
        "    /** oppai: 最近一次 play() 的动画名 —— 供最后一帧回调使用 */\n"
        "    const std::string& getCurrentAnimationName() const { return _currentAnimationName; }",
        1,
    )
    # 成员
    m = re.search(r"(\n\s*std::function<void\(\)>\s*_lastFrameListener;)", s)
    if m:
        s = s[:m.end()] + "\n    std::string _currentAnimationName;   // oppai" + s[m.end():]
    else:
        # 退而求其次：塞到 protected/private 段开头
        s = s.replace("protected:", "protected:\n    std::string _currentAnimationName;   // oppai", 1)
    io.open(H, "w", encoding="utf-8", newline="\n").write(s)
    print("头文件已加 getCurrentAnimationName")

# ---------------------------------------------------------------- 2) 实现
s = io.open(C, encoding="utf-8").read()
if "_currentAnimationName = name" in s:
    print("实现已处理")
else:
    old = """void ActionTimeline::play(std::string name, bool loop)
{
    if (_animationInfos.find(name) == _animationInfos.end())"""
    new = """void ActionTimeline::play(std::string name, bool loop)
{
    _currentAnimationName = name;      // oppai: 记录，供最后一帧回调使用
    if (_animationInfos.find(name) == _animationInfos.end())"""
    if old in s:
        s = s.replace(old, new, 1)
        io.open(C, "w", encoding="utf-8", newline="\n").write(s)
        print("实现已记录动画名")
    else:
        print("!! 没找到 ActionTimeline::play 的定义")

# ------------------------------------------- 3) 修自动绑定 setLastFrameCallFunc
s = io.open(AUTO, encoding="utf-8").read()
if "oppai: 把动画名传给最后一帧回调" in s:
    print("绑定已处理")
else:
    # 精确定位 setLastFrameCallFunc 里的 lambda
    i = s.find("js_cocos2dx_studio_ActionTimeline_setLastFrameCallFunc")
    if i < 0:
        raise SystemExit("找不到 setLastFrameCallFunc 绑定")
    seg = s[i:i + 2000]
    old_lambda = """		        auto lambda = [=]() -> void {
		            JSB_AUTOCOMPARTMENT_WITH_GLOBAL_OBJCET
		            JS::RootedValue rval(cx);
		            bool ok = func->invoke(0, nullptr, &rval);"""
    new_lambda = """		        cocostudio::timeline::ActionTimeline* tlRef = cobj;   // oppai
		        auto lambda = [=]() -> void {
		            JSB_AUTOCOMPARTMENT_WITH_GLOBAL_OBJCET
		            JS::RootedValue rval(cx);
		            // oppai: 把动画名传给最后一帧回调。
		            // 游戏里是 function (eventName) { if (/began\\d/.test(eventName)) ... }，
		            // 原生成代码 invoke(0, ...) 不传参数，导致这类回调永远走不进分支。
		            jsval argv[1];
		            argv[0] = std_string_to_jsval(cx, tlRef->getCurrentAnimationName());
		            bool ok = func->invoke(1, argv, &rval);"""
    if old_lambda in seg:
        s = s[:i] + seg.replace(old_lambda, new_lambda, 1) + s[i + 2000:]
        io.open(AUTO, "w", encoding="utf-8", newline="\n").write(s)
        print("绑定已改成传动画名")
    else:
        print("!! 没匹配到 lambda（可能是制表符差异），尝试宽松替换")
        j = s.find("bool ok = func->invoke(0, nullptr, &rval);", i)
        if j > 0 and j - i < 2500:
            s = s[:j] + ("jsval argv[1];\n"
                         "		            argv[0] = std_string_to_jsval(cx, cobj->getCurrentAnimationName());\n"
                         "		            bool ok = func->invoke(1, argv, &rval);") + s[j + len("bool ok = func->invoke(0, nullptr, &rval);"):]
            io.open(AUTO, "w", encoding="utf-8", newline="\n").write(s)
            print("绑定已改成传动画名（宽松）")
        else:
            print("!! 放弃")
