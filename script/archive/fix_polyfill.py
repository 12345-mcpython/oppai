"""把 polyfill 从 emit("HOOK LOADED ...") 语句中间移到它后面。"""

import io
import re

P = r"E:\code\zcsmw\server\client\hook.js"
s = io.open(P, encoding="utf-8").read()

START = "    // ------------------------------------------------------------------\n    // ccui.helper.seekNodeByName"
END = "    })();\n"

i = s.find(START)
if i < 0:
    raise SystemExit("找不到 polyfill 起点")
j = s.find(END, i)
if j < 0:
    raise SystemExit("找不到 polyfill 终点")
j += len(END)

block = s[i:j]
s = s[:i] + s[j:]
print("已摘出 polyfill（%d 字符）" % len(block))

# 找 emit("HOOK LOADED ...) 语句的结尾：从 emit( 开始配平括号
k = s.find('emit("HOOK LOADED')
if k < 0:
    raise SystemExit("找不到 HOOK LOADED")
d = 0
end = -1
for idx in range(k, len(s)):
    c = s[idx]
    if c == '(':
        d += 1
    elif c == ')':
        d -= 1
        if d == 0:
            end = idx
            break
assert end > 0, "括号没配平"
# 移动到分号之后
while s[end] != ';':
    end += 1
end += 1
# 吃掉后面的换行
while s[end] == '\n':
    end += 1

s = s[:end] + "\n" + block + s[end:]
io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("已把 polyfill 移到 HOOK LOADED 语句之后")
