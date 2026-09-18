"""把 smali_classesN 合并成一个 dex。

apktool 的规则：`smali/` → classes.dex，`smali_classes2/` → classes2.dex，依此类推。
原来之所以分两个 dex，是因为带着百度/微博/推送那一堆 SDK，方法数超了 64K。

SDK 删干净之后总量只剩 1.7 MB，完全塞得进一个 dex，合并的好处：
  * 少一个 dex 文件头 / 字符串池，省一点体积
  * 启动时少验证一个 dex，快一点
  * 结构更简单

合并方式就是把 `smali_classes2/**` 原样挪进 `smali/`。
如果有同名文件（正常不会），直接报错退出，不覆盖。

用法：
    python tools/merge_dex.py --dry-run
    python tools/merge_dex.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\apk\zcsmw")
PRIMARY = "smali"
EXTRA = ("smali_classes2", "smali_classes3", "smali_classes4")


def count_smali(path: str) -> int:
    n = 0
    for _dp, _dn, fn in os.walk(path):
        n += sum(1 for f in fn if f.endswith(".smali"))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dst_root = os.path.join(APK_DIR, PRIMARY)
    if not os.path.isdir(dst_root):
        print(f"!! 找不到 {dst_root}")
        return 1

    total = 0
    for extra in EXTRA:
        src_root = os.path.join(APK_DIR, extra)
        if not os.path.isdir(src_root):
            continue

        files = []
        for dp, _dn, fn in os.walk(src_root):
            for f in fn:
                full = os.path.join(dp, f)
                rel = os.path.relpath(full, src_root)
                files.append((full, rel))

        n = sum(1 for _f, r in files if r.endswith(".smali"))
        print(f"{extra}: {len(files)} 个文件（{n} 个 smali）")

        # 先检查冲突
        conflicts = [r for _f, r in files if os.path.exists(os.path.join(dst_root, r))]
        if conflicts:
            print(f"!! 有 {len(conflicts)} 个同名文件，拒绝合并：")
            for c in conflicts[:10]:
                print("   ", c)
            return 1

        if args.dry_run:
            continue

        for full, rel in files:
            dst = os.path.join(dst_root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(full, dst)
        shutil.rmtree(src_root, ignore_errors=True)
        print(f"  -> 已并入 {PRIMARY}/，并删除 {extra}/")
        total += len(files)

    if args.dry_run:
        print("\n(dry-run，未做改动)")
    else:
        print(f"\n合并完成，共移动 {total} 个文件")
        print(f"{PRIMARY}/ 现在有 {count_smali(dst_root)} 个 smali")
        print("接下来重新打包：python tools/build_apk.py --host <IP>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
