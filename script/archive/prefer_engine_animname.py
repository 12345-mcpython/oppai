"""patch.js：让 ActionTimeline 的最后一帧回调优先采用引擎传来的动画名。

引擎侧（jsb_cocos2dx_studio_auto.cpp）现在已经会把
ActionTimeline::getCurrentAnimationName() 作为第一个参数传进来，
所以 JS 这边不再需要「自己猜」—— 只保留一个兜底：

    engineName（引擎传的） > self.__oppaiAnimName（play 时记的） > "default"

这样引擎是唯一权威来源；万一绑定补丁没生效，旧逻辑仍然兜得住。
"""

import io

P = r"E:\code\zcsmw\server\client\patch.js"
s = io.open(P, encoding="utf-8").read()

OLD = '''            self.__oppaiLfFired = false;
            var wrapped = function () {
                if (self.__oppaiLfFired) { return; }
                self.__oppaiLfFired = true;'''

NEW = '''            self.__oppaiLfFired = false;
            var wrapped = function (engineName) {
                if (self.__oppaiLfFired) { return; }
                self.__oppaiLfFired = true;'''

if "function (engineName)" in s:
    print("已经改过")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    # 动画名取值：引擎优先
    old2 = '''                // 关键：原版引擎会把动画名当第一个参数传给回调
                // 游戏代码写的是 function (eventName) { if (eventName === "default") this._init(); }
                // 而 v3.6 的绑定是 invoke(0, ...) 不传参数，导致 _init() 永不调用。
                var animName = self.__oppaiAnimName || "default";'''
    new2 = '''                // 关键：引擎会把当前动画名当第一个参数传给回调（见 jsb_cocos2dx_studio_auto.cpp
                // 里 oppai 的改动），游戏代码依赖它，例如：
                //     function (eventName) { if (eventName === "default") this._init(); }
                //     function (eventName) { if (/began\\d/.test(eventName)) playAnimation("loop"+N, true); }
                // 万一引擎没传（绑定补丁没生效），退回 play 时记下的名字，再退回 "default"。
                var animName = engineName || self.__oppaiAnimName || "default";'''
    if old2 in s:
        s = s.replace(old2, new2, 1)
        io.open(P, "w", encoding="utf-8", newline="\n").write(s)
        print("已改成引擎优先")
    else:
        print("!! 没找到 animName 取值那一段")
else:
    print("!! 没找到 wrapped 定义")
