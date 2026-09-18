import io
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else r"E:\code\apk\move\out\nf.txt"
s = io.open(path, encoding="utf-8", errors="replace").read()

print("=== JS ERROR ===")
errs = list(re.finditer(r"JS ERROR[^\n]{0,140}", s))[:6]
for m in errs:
    print("  ", m.group(0))
if not errs:
    print("   (空=没有)")

print()
print("=== 看门狗 ===")
for m in list(re.finditer(r"(BATTLE-WD|MOVIE-WD|LGL-GUARD)[^\n]{0,80}", s))[:8]:
    print("  ", m.group(0))

print()
print("=== play / lastFrame 尾部 20 条（交错）===")
ev = []
for m in re.finditer(r"\[oppai\] (play 动画名=(\S+) loop=(\d)|lastFrame 触发 anim=(\S*))", s):
    if m.group(2):
        ev.append("play      %s loop=%s" % (m.group(2), m.group(3)))
    else:
        ev.append("lastFrame %s" % m.group(4))
for line in ev[-20:]:
    print("  ", line)

print()
print("=== 是否出现 loop3 / end3 ===")
for name in ("began1", "loop1", "end1", "began2", "loop2", "end2", "began3", "loop3", "end3"):
    n = len(re.findall(r"play 动画名=%s\b" % name, s))
    print("   %-8s 播放 %d 次" % (name, n))
