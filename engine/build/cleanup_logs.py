"""收尾清理：

1. patch.js 里 ActionTimeline 那块的注释已经过时（还写着"fired 标志 + 定时器兜底"），
   改成描述现状。
2. 引擎里为定位问题加的诊断 CCLOG 全部删掉：
     [oppai] play 动画名=%s loop=%d
     [oppai] lastFrame 触发 anim=%s
     [oppai] VideoPlayer 事件 %d
3. 顺手把 createTimeline 也检查一遍（不需要额外改动，只是确认）。
"""

from __future__ import annotations

import io
import os

# ------------------------------------------------------------------ 1) 注释
P = r"E:\code\zcsmw\server\client\patch.js"
s = io.open(P, encoding="utf-8").read()

OLD = """    // ActionTimeline 回调诊断 + 兜底
    //
    // 游戏 UpdateScene 的启动链：
    //     ctor() -> _logo() -> tl.setLastFrameCallFunc(cb) -> cb 调 _init()
    // 实测 Layer 节点的 action 恒为 1（永不结束），回调不触发，
    // 游戏就卡在 logo 那一屏（表现为黑屏）。
    //
    // 这里包一层：记录调用 + 加 fired 标志 + 定时器兜底。"""

NEW = """    // ActionTimeline 最后一帧回调：补动画名（引擎未传时的兜底）
    //
    // 游戏多处依赖「回调的第一个参数是动画名」，例如：
    //     UpdateScene._logo : function (eventName) { if (eventName === "default") this._init(); }
    //     BattleScene      : function (eventName) { if (/began\\d/.test(eventName)) playAnimation("loop"+N, true); }
    // 而 vanilla cocos2d-js v3.6 的自动绑定是 func->invoke(0, ...)，不传参数。
    //
    // 根治在引擎层（jsb_cocos2dx_studio_auto.cpp 已改成 invoke(1, argv, ...)，
    // argv[0] = ActionTimeline::getCurrentAnimationName()）。这里只做兜底：
    //     引擎传的 engineName  >  play 时记下的 self.__oppaiAnimName  >  "default"
    //
    // 注意：**不能**加"只触发一次"的守卫 —— 游戏复用同一个 ActionTimeline 播
    // 整段序列（began1→loop1→end1→…→end3），每段推进都靠这个回调，挡掉就卡死。"""

if "补动画名（引擎未传时的兜底）" in s:
    print("注释已更新过")
elif OLD in s:
    s = s.replace(OLD, NEW, 1)
    io.open(P, "w", encoding="utf-8", newline="\n").write(s)
    print("已更新 patch.js 注释")
else:
    print("!! patch.js 注释没匹配上（可能已经被改过）")

# ------------------------------------------------------- 2) 引擎诊断日志
EDITS = [
    (r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings\bindings\auto"
     r"\jsb_cocos2dx_studio_auto.cpp",
     '		            cocos2d::log("[oppai] lastFrame 触发 anim=%s", tlRef->getCurrentAnimationName().c_str());\n',
     ""),
    (r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings\bindings\auto"
     r"\jsb_cocos2dx_studio_auto.cpp",
     '		            cocos2d::log("[oppai] lastFrame 触发 anim=%s", cobj->getCurrentAnimationName().c_str());\n',
     ""),
    (r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings\cocos2d-x\cocos"
     r"\editor-support\cocostudio\ActionTimeline\CCActionTimeline.cpp",
     '    cocos2d::log("[oppai] play 动画名=%s loop=%d", name.c_str(), loop ? 1 : 0);\n',
     ""),
    (r"E:\code\zcsmw\engine\build\oppai-engine\Classes\utilsex\jsb_oppai_utilsex.cpp",
     '            CCLOG("[oppai] VideoPlayer 事件 %d", (int)event);\n',
     ""),
]

for path, old, new in EDITS:
    if not os.path.exists(path):
        print("!! 文件不存在: %s" % os.path.basename(path))
        continue
    t = io.open(path, encoding="utf-8").read()
    if old in t:
        t = t.replace(old, new, 1)
        io.open(path, "w", encoding="utf-8", newline="\n").write(t)
        print("已删除诊断日志: %s" % os.path.basename(path))
    else:
        print("  (已无) %s" % os.path.basename(path))

# 复查
print()
print("=== 复查残留的 oppai 诊断日志 ===")
import subprocess
for root in (r"E:\code\zcsmw\engine\src\cocos2d-js\frameworks\js-bindings",
             r"E:\code\zcsmw\engine\build\oppai-engine\Classes"):
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if not f.endswith((".cpp", ".h", ".hpp")):
                continue
            p = os.path.join(dp, f)
            try:
                t = io.open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            for line in t.split("\n"):
                if "[oppai]" in line and ("log(" in line or "CCLOG" in line):
                    print("  %s : %s" % (os.path.relpath(p, root), line.strip()[:90]))
