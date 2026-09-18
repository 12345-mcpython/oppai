"""把 native_stubs.py 里的定义生成 smali（覆盖掉 gen_stubs.py 的通用版本）。"""

from __future__ import annotations

import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from native_stubs import EXTRA_ENUMS, NATIVE_STUBS  # noqa: E402

APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\apk\zcsmw")


def default_ret(ret: str) -> list:
    if ret == "V":
        return ["    return-void"]
    if ret in ("Z", "I", "B", "S", "C"):
        return ["    const/4 v0, 0x0", "    return v0"]
    if ret in ("J", "D"):
        return ["    const-wide/16 v0, 0x0", "    return-wide v0"]
    return ["    const/4 v0, 0x0", "    return-object v0"]


def gen(cls: str, methods) -> str:
    simple = cls.rsplit("/", 1)[-1]
    out = [f".class public L{cls};", ".super Ljava/lang/Object;", f'.source "{simple}.java"', ""]
    seen = set()
    out += ["# direct methods", ""]
    # 构造函数
    out += [
        ".method public constructor <init>()V",
        "    .locals 0",
        "",
        "    invoke-direct {p0}, Ljava/lang/Object;-><init>()V",
        "",
        "    return-void",
        ".end method",
        "",
    ]
    for name, params, ret, static in methods:
        key = (name, params, ret, static)
        if key in seen:
            continue
        seen.add(key)
        mod = "public static" if static else "public"
        n = 2 if ret in ("J", "D") else 1
        out.append(f".method {mod} {name}({params}){ret}")
        out.append(f"    .locals {n}")
        out.append("")
        out += default_ret(ret)
        out.append(".end method")
        out.append("")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(APK_DIR, "smali"))
    args = ap.parse_args()

    all_defs = dict(NATIVE_STUBS)
    all_defs.update(EXTRA_ENUMS)

    n = 0
    for cls, methods in all_defs.items():
        path = os.path.join(args.out, cls + ".smali")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        io.open(path, "w", encoding="utf-8", newline="\n").write(gen(cls, methods))
        n += 1
    print(f"生成 {n} 个原生依赖桩 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
