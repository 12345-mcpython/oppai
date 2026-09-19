"""smali 可达性分析 —— 找出「谁都不引用」的类。

    python script\\smali_reach.py              # 报表
    python script\\smali_reach.py --unused     # 只列没人引用的类

做法是从真正的入口往下做闭包：

    根 = AndroidManifest 里声明的组件
       ∪ lib/*.so 里出现的类名（JNI 的 FindClass / jsb.reflection 的目标）
       ∪ assets 里的 js/jsc 里出现的类名（jsb.reflection.callStaticMethod）

三类根都算上，宁可多算（多算的后果是「少删几个」，不会误删）。

⚠️ 结论只用来**决定删什么**，删之前还是要过 build_apk.py 里那道
`smali_users_of()` 复查。另外注意：dex 在 APK 里是 DEFLATE 的，压缩比约 3:1，
删 1 KB smali 大概只换回 0.1 KB 的包 —— 别拿 smali 的字节数估收益。

哪些是**框架类**（boot classpath 里就有，放进 app dex 纯属白占地方）：
     android/**  org/json/**  org/apache/**  org/xml/**  javax/**
这些即使「没人引用」也直接可删；反过来，app 自己的类要是没人引用，
删之前得想清楚是不是反射用的。
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys

# --- 路径自举 ---
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

GAME = _paths.GAME

# Lfoo/bar/Baz;  形态的类描述符
RE_DESC = re.compile(rb"L([A-Za-z][A-Za-z0-9_$]*(?:/[A-Za-z0-9_$]+)+);")
# 散落的类名（反射用的字符串）：至少两段，用 / 或 . 分隔
RE_NAME = re.compile(rb"[A-Za-z][A-Za-z0-9_$]*(?:[./][A-Za-z0-9_$]+)+")

FRAMEWORK_PREFIXES = (
    "android/", "java/", "javax/", "org/json/", "org/apache/", "org/w3c/",
    "org/xml/", "org/xmlpull/", "dalvik/", "com/android/",
)


def norm(s: str) -> str:
    """统一成 smali 的斜杠写法。"""
    return s.replace(".", "/")


def smali_classes() -> dict:
    """返回 {类路径: 文件路径}。"""
    out = {}
    root = os.path.join(GAME, "smali")
    for r, _, fs in os.walk(root):
        for f in fs:
            if f.endswith(".smali"):
                p = os.path.join(r, f)
                out[os.path.relpath(p, root)[:-len(".smali")].replace(os.sep, "/")] = p
    return out


def refs_in(path: str) -> set:
    """一个文件里出现的所有类名（描述符 + 字符串两种写法都收）。"""
    out = set()
    try:
        with open(path, "rb") as fh:
            b = fh.read()
    except OSError:
        return out
    for m in RE_DESC.finditer(b):
        out.add(m.group(1).decode("ascii", "replace"))
    for m in RE_NAME.finditer(b):
        out.add(norm(m.group().decode("ascii", "replace")))
    return out


def roots(classes: dict) -> dict:
    """{根类: 来源说明}"""
    out = {}

    man = os.path.join(GAME, "AndroidManifest.xml")
    if os.path.isfile(man):
        with open(man, encoding="utf-8") as fh:
            t = fh.read()
        for n in re.findall(r'android:name="([^"@][^"]*)"', t):
            n = norm(n)
            if n in classes:
                out[n] = "AndroidManifest"
        # meta-data 的 value 也常常是类名
        for n in re.findall(r'<meta-data[^>]*android:value="([^"]+)"', t):
            n = norm(n)
            if n in classes:
                out[n] = "AndroidManifest(meta-data)"

    so_dir = os.path.join(GAME, "lib")
    for r, _, fs in os.walk(so_dir):
        for f in fs:
            if f.endswith(".so"):
                for c in refs_in(os.path.join(r, f)):
                    if c in classes and c not in out:
                        out[c] = f"lib/{os.path.basename(r)}/{f}"

    for sub in ("assets/src", "assets/script"):
        d = os.path.join(GAME, sub)
        for r, _, fs in os.walk(d):
            for f in fs:
                if f.endswith((".js", ".jsc")):
                    for c in refs_in(os.path.join(r, f)):
                        if c in classes and c not in out:
                            out[c] = f"{sub}/{f}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="smali 可达性分析")
    ap.add_argument("--unused", action="store_true", help="只列没人引用的类")
    ap.add_argument("--edges", action="store_true", help="打印引用图统计")
    args = ap.parse_args()

    classes = smali_classes()
    if not classes:
        print("!! 没找到 smali", file=sys.stderr)
        return 1

    edges = {c: refs_in(p) for c, p in classes.items()}
    rt = roots(classes)

    seen = set(rt)
    queue = collections.deque(rt)
    while queue:
        c = queue.popleft()
        for nxt in edges.get(c, ()):
            if nxt in classes and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)

    unused = sorted(set(classes) - seen)
    fw = [c for c in unused if c.startswith(FRAMEWORK_PREFIXES)]
    app = [c for c in unused if c not in set(fw)]

    total_bytes = sum(os.path.getsize(classes[c]) for c in classes)
    unused_bytes = sum(os.path.getsize(classes[c]) for c in unused)
    fw_bytes = sum(os.path.getsize(classes[c]) for c in fw)
    app_bytes = sum(os.path.getsize(classes[c]) for c in app)

    print(f"smali 共 {len(classes)} 个类 / {total_bytes/1024:.0f} KB")
    print(f"根: {len(rt)} 个")
    for c, why in sorted(rt.items())[:20]:
        print(f"    {c:<48} <- {why}")
    if len(rt) > 20:
        print(f"    ... 还有 {len(rt)-20} 个")
    print(f"\n可达 {len(seen)} 个   不可达 {len(unused)} 个 / {unused_bytes/1024:.0f} KB")
    print(f"  其中框架类（boot classpath 就有，纯粹白占地方）: "
          f"{len(fw)} 个 / {fw_bytes/1024:.0f} KB")
    print(f"  其中 app 自己的类（删前确认没被反射）: "
          f"{len(app)} 个 / {app_bytes/1024:.0f} KB")

    if args.edges:
        print("\n=== 引用最多的类 ===")
        cnt = collections.Counter()
        for c, refs in edges.items():
            for n in refs:
                if n in classes:
                    cnt[n] += 1
        for c, n in cnt.most_common(15):
            print(f"    {n:>4}  {c}")

    if unused or args.unused:
        print("\n=== 框架类（可直接删）===")
        for c in fw:
            print(f"    {c}")
        print("\n=== app 自己的不可达类（先确认不是反射目标）===")
        for c in app:
            print(f"    {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
