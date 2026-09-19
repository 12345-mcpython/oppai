"""精简日志。

三类噪音：
  1) hook.js 里每帧/每次轮询都打的诊断日志（AUTO 提示、TICK、AT.*、US.*、LGL.*、WEBVIEW）
  2) JniHelper::getJavaVM() —— cocos2d-x 每次 getStaticMethodInfo 都会 LOGD 一行
  3) 我自己的 [oppai] CCLOG

做法：
  - hook.js 分成 emit()（保留）和 vlog()（只在 __OPPAI_VERBOSE__ 打开时输出）
    冗长的诊断日志全部改成 vlog；错误、守卫命中、REPL 结果仍用 emit
  - AUTO 提示只打一次
"""

import io
import re

P = r"E:\code\zcsmw\server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

# ---------------------------------------------------------------- 1) 加 vlog
if "function vlog(" not in s:
    OLD_EMIT = '''    function emit(line) {
        lines.push(line);
        try {
            console.log(TAG + "|" + line);
        } catch (e) {
        }
    }'''
    NEW_EMIT = '''    // 冗长日志（每帧/每次轮询的那种）走 vlog，默认不输出。
    // 想看就在 REPL 里执行： __OPPAI_VERBOSE__ = true
    function vlog(line) {
        if (typeof __OPPAI_VERBOSE__ !== "undefined" && __OPPAI_VERBOSE__) {
            emit(line);
        }
    }

    function emit(line) {
        lines.push(line);
        try {
            console.log(TAG + "|" + line);
        } catch (e) {
        }
    }'''
    assert OLD_EMIT in s, "找不到 emit 定义"
    s = s.replace(OLD_EMIT, NEW_EMIT, 1)
    print("已加入 vlog()")

# ------------------------------------------------- 2) 冗长日志改成 vlog
# 精确替换（只改 emit("...") 的调用，不动函数定义）
VLOG_PATTERNS = [
    r'emit\("AUTO 自动点击',
    r'emit\("TICK "',
    r'emit\("HEARTBEAT',
    r'emit\("REPL poll#',
    r'emit\("REPL watchdog',
    r'emit\("AT\.',
    r'emit\("US\.',
    r'emit\("LGL\.',
    r'emit\("LGL-WRAP',
    r'emit\("WEBVIEW\.loadURL',
    r'emit\("WEBVIEW 内容',
    r'emit\("TRANSPORT ',
    r'emit\("SELFTEST ',
    r'emit\("HOOK ERROR',
]
n = 0
for pat in VLOG_PATTERNS:
    s2 = re.sub(pat, lambda m: m.group(0).replace('emit(', 'vlog(', 1), s)
    if s2 != s:
        n += 1
    s = s2
print("已把 %d 类冗长日志改成 vlog" % n)

# ------------------------------------------- 3) AUTO 提示只打一次
if "__oppaiAutoLogged" not in s:
    OLD_AUTO = '''    if (!AUTO_CLICK_START) {
        emit("AUTO 自动点击「开始游戏」已关闭，请手动点");
        return;
    }'''
    NEW_AUTO = '''    if (!AUTO_CLICK_START) {
        if (!window.__oppaiAutoLogged) {
            window.__oppaiAutoLogged = true;
            emit("AUTO 自动点击已关闭（只提示一次）");
        }
        return;
    }'''
    if OLD_AUTO in s:
        s = s.replace(OLD_AUTO, NEW_AUTO, 1)
        print("AUTO 提示改成只打一次")
    else:
        # 中文可能被转义，退化成只替换那一行的 emit
        s = re.sub(r'emit\("AUTO [^"]*"\);',
                   'if (!window.__oppaiAutoLogged) { window.__oppaiAutoLogged = true; emit("AUTO 自动点击已关闭"); }',
                   s, count=1)
        print("AUTO 提示改成只打一次（宽松匹配）")

io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("hook.js 已更新")
