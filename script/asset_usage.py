"""查 `assets/` 里某个文件到底有没有人用。

    python script\\asset_usage.py                    # 查内置的一批可疑文件
    python script\\asset_usage.py pinyinindex data.bin
    python script\\asset_usage.py --hex pinyinindex  # 顺便看看文件头

判据：把「能读到它的地方」全扫一遍，看名字（或名字片段）出现过没有。

    assets/src/**            游戏逻辑（.jsc 是二进制字节码，字符串原子是 ASCII）
    assets/script/**         调试器 + patch/probe
    assets/srcex/**          扩展逻辑
    assets/main.jsc, config.json, project.json
    smali/**                 Java 层（R 字段名、类名都在这儿）
    lib/*/libcocos2djs.so    native（JNI 类名 / cocos2d FileUtils 的路径字面量）
    res/**                   Android 资源

**ASCII / UTF-16LE / UTF-16BE 三种编码都试**（实测 `.jsc` 的字符串原子是 ASCII 的，
但别的文件不保证）。

一个名字如果哪儿都没出现，就没有任何代码能加载它 —— 除非它是**拼出来的**
（`"pinyin" + "index"`），所以这里还会额外报「子串」的命中情况。
真要下手之前，还是留个备份、打完包跑一遍（`check_devtools.py`）。

⚠️ 别忘了 apktool 的 `build/apk/` 增量缓存（见 build.md 2a-2 坑 3）：
从 `assets/` 删了文件，缓存里那份照样进包。`build_apk.py` 的 `DROP_ASSETS`
已经带了这个处理；手工删的话得自己清。
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GAME = os.path.join(ROOT, "game")

AREAS = [
    ("assets/src", ("jsc", "js")),
    ("assets/script", ("js", "jsc")),
    ("assets/srcex", ("jsc", "js", "json")),
    ("assets", ("json", "jsc")),
    ("smali", ("smali",)),
    ("lib", ("so",)),
    ("res", ("xml", "txt", "json")),
]

# 内置的「可疑清单」：都是 SDK 残留或来路不明的文件
DEFAULT = {
    "assets/drawable*": ["drawable-hdpi", "drawable-xhdpi", "weibosdk", "ic_com_sina"],
    "countryCode*.txt": ["countryCode"],
    "data.bin": ["data.bin"],
    "pinyinindex": ["pinyinindex", "pinyin"],
    "service.cfg": ["service.cfg"],
}


def scan(path, needles):
    try:
        with open(path, "rb") as fh:
            b = fh.read()
    except OSError:
        return {}
    out = {}
    for n in needles:
        c = 0
        for enc in ("ascii", "utf-16-le", "utf-16-be"):
            try:
                c += b.count(n.encode(enc))
            except UnicodeEncodeError:
                pass
        if c:
            out[n] = c
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="查 assets 里的文件有没有人用")
    ap.add_argument("names", nargs="*", help="要查的文件名/片段；不给就用内置清单")
    ap.add_argument("--hex", action="store_true", help="再打印文件头的十六进制")
    args = ap.parse_args()

    groups = {n: [n] for n in args.names} if args.names else DEFAULT

    # 先收集待扫文件
    files = []
    for area, exts in AREAS:
        d = os.path.join(GAME, area)
        if not os.path.isdir(d):
            continue
        for r, _, fs in os.walk(d):
            files += [os.path.join(r, f) for f in fs
                      if not exts or f.lower().endswith(exts)]
    print(f"扫 {len(files)} 个文件（assets/src|script|srcex、main.jsc、smali/**、"
          f"lib/*.so、res/**）")

    for label, needles in groups.items():
        hits = {n: [] for n in needles}
        for p in files:
            for n, c in scan(p, needles).items():
                hits[n].append(os.path.relpath(p, GAME))
        print(f"\n=== {label} ===")
        for n in needles:
            if hits[n]:
                print(f"  {n:<24} 命中 {len(hits[n])} 个文件")
                for h in hits[n][:5]:
                    print(f"        {h}")
            else:
                print(f"  {n:<24} 0 处 —— 没有任何代码能加载它")

    if args.hex:
        print("\n=== 文件头 ===")
        for n in args.names:
            p = os.path.join(GAME, "assets", n)
            if not os.path.isfile(p):
                continue
            with open(p, "rb") as fh:
                head = fh.read(48)
            print(f"  {n:<16} " + " ".join(f"{c:02x}" for c in head[:20]))
            print(f"  {'':<16} " + "".join(chr(c) if 32 <= c < 127 else "." for c in head))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
