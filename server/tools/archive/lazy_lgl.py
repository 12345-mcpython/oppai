"""LaunchGuideLayer 的补丁改成延迟安装。

问题：LaunchGuideLayer 是懒加载的（游戏 require 到战斗引导时才定义），
hook.js 执行时它还不存在 —— 所以上一轮的守卫和跟踪全都没装上，
日志里只有 "找不到 LaunchGuideLayer"。

改成轮询：每 500ms 检查一次，出现就立刻包装，然后停。
"""

import io
import re

P = r"E:\code\python\game_server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

# 1) 守卫块：把即时判断改成延迟安装
OLD_GUARD_START = '''    (function () {
        if (typeof LaunchGuideLayer === "undefined" || !LaunchGuideLayer.prototype) {
            emit("LGL-GUARD 找不到 LaunchGuideLayer");
            return;
        }
        if (LaunchGuideLayer.prototype.__oppaiGuard) { return; }
        LaunchGuideLayer.prototype.__oppaiGuard = true;'''

NEW_GUARD_START = '''    (function installGuard() {
        // LaunchGuideLayer 是懒加载的，hook 执行时可能还没有 —— 轮询等待
        if (typeof LaunchGuideLayer === "undefined" || !LaunchGuideLayer.prototype) {
            if (!window.__oppaiLglGuardTimer) {
                window.__oppaiLglGuardTimer = setInterval(function () {
                    if (typeof LaunchGuideLayer !== "undefined" && LaunchGuideLayer.prototype) {
                        clearInterval(window.__oppaiLglGuardTimer);
                        window.__oppaiLglGuardTimer = null;
                        installGuard();
                    }
                }, 500);
            }
            return;
        }
        if (window.__oppaiLglGuardTimer) {
            clearInterval(window.__oppaiLglGuardTimer);
            window.__oppaiLglGuardTimer = null;
        }
        if (LaunchGuideLayer.prototype.__oppaiGuard) { return; }
        LaunchGuideLayer.prototype.__oppaiGuard = true;'''

if OLD_GUARD_START in s:
    s = s.replace(OLD_GUARD_START, NEW_GUARD_START, 1)
    print("守卫改成延迟安装")
elif "installGuard" in s:
    print("守卫已经是延迟安装")
else:
    print("!! 没找到守卫块")

# 2) 跟踪块：同样延迟
OLD_TRACE_START = '''    (function () {
        if (typeof LaunchGuideLayer === "undefined" || !LaunchGuideLayer.prototype) {
            emit("LGL-WRAP 找不到 LaunchGuideLayer");
            return;
        }
        if (LaunchGuideLayer.prototype.__oppaiWrapped) { return; }
        LaunchGuideLayer.prototype.__oppaiWrapped = true;'''

NEW_TRACE_START = '''    (function installTrace() {
        if (typeof LaunchGuideLayer === "undefined" || !LaunchGuideLayer.prototype) {
            if (!window.__oppaiLglTraceTimer) {
                window.__oppaiLglTraceTimer = setInterval(function () {
                    if (typeof LaunchGuideLayer !== "undefined" && LaunchGuideLayer.prototype) {
                        clearInterval(window.__oppaiLglTraceTimer);
                        window.__oppaiLglTraceTimer = null;
                        installTrace();
                    }
                }, 500);
            }
            return;
        }
        if (window.__oppaiLglTraceTimer) {
            clearInterval(window.__oppaiLglTraceTimer);
            window.__oppaiLglTraceTimer = null;
        }
        if (LaunchGuideLayer.prototype.__oppaiWrapped) { return; }
        LaunchGuideLayer.prototype.__oppaiWrapped = true;'''

if OLD_TRACE_START in s:
    s = s.replace(OLD_TRACE_START, NEW_TRACE_START, 1)
    print("跟踪改成延迟安装")
elif "installTrace" in s:
    print("跟踪已经是延迟安装")
else:
    print("!! 没找到跟踪块")

# 3) LGL 跟踪日志统一降级成 vlog（默认不打）
s = re.sub(r'emit\("LGL\.', lambda m: m.group(0).replace('emit(', 'vlog(', 1), s)

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("hook.js 已更新")
