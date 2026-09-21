"""把没用的第三方 SDK 从 apktool 解包目录里剥掉。

步骤：
  1. 删掉 SDK 的 smali 目录
  2. 把 gen_stubs.py 生成的桩类装进 smali/
  3. 从 AndroidManifest.xml 里删掉指向 SDK 的组件（activity/service/receiver/provider）
     和只给 SDK 用的权限
  4. 删掉 SDK 的 res / assets / lib

用法：
    python tools/sdk_strip/strip.py --dry-run    # 只看要删什么
    python tools/sdk_strip/strip.py
"""

from __future__ import annotations

import argparse
import io
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# ⚠️ 自举必须排在**任何 `_paths.X` 使用之前**。早先 `BASE_DIR = _paths.SERVER`
# 写在这一段上面，于是本脚本从任何目录跑都是
# `NameError: name '_paths' is not defined`。
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

BASE_DIR = _paths.SERVER
APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\zcsmw\game")

sys.path.insert(0, HERE)
from analyze import STRIP_PREFIXES  # noqa: E402



SMALI_DIRS = ("smali", "smali_classes2", "smali_classes3")

# 明确要保留的例外（即使前缀命中也不能删）
KEEP_EXCEPTIONS = (
    "com/quicksdk/apiadapter/baidu/ActivityAdapter",   # R 类动态取资源 ID 要用
)

# 组件/权限：按包名判断要不要从 manifest 里删
MANIFEST_PKG_HINTS = (
    "com.baidu", "com.duoku", "com.unionpay", "com.alipay", "com.gametalkingdata",
    "com.qk.", "com.squareup", "com.ta.", "com.qq.", "com.slidingmenu",
    "com.sina", "com.tencent", "com.tendcloud", "com.talkingdata", "com.jg.",
    "com.chukong", "com.ucmobile", "com.ut.", "com.kurogame",
    "com.quicksdk",
)

# 有些组件虽然名字命中 SDK 包，但游戏要用，不能删
MANIFEST_KEEP = (
    "com.quicksdk.apiadapter.baidu.ActivityAdapter",
)


def keep(path: str) -> bool:
    return any(path.startswith(e) for e in KEEP_EXCEPTIONS)


def iter_dirs(root: str, prefix: str):
    """列出 root 下匹配 prefix 的目录（只返回最外层）"""
    parts = prefix.strip("/").split("/")
    cur = root
    for p in parts:
        cur = os.path.join(cur, p)
        if not os.path.isdir(cur):
            return
    yield cur


def strip_smali(dry: bool):
    removed = []
    for d in SMALI_DIRS:
        root = os.path.join(APK_DIR, d)
        if not os.path.isdir(root):
            continue
        for prefix in STRIP_PREFIXES:
            if keep(prefix):
                continue
            for target in list(iter_dirs(root, prefix)):
                if keep(os.path.relpath(target, root).replace("\\", "/")):
                    continue
                size = sum(
                    os.path.getsize(os.path.join(dp, f))
                    for dp, _dn, fn in os.walk(target) for f in fn
                )
                removed.append((target, size))
                if not dry:
                    shutil.rmtree(target, ignore_errors=True)
    total = sum(s for _p, s in removed)
    for p, s in removed:
        print(f"  删 {p.replace(APK_DIR + os.sep, '')}  ({s/1024/1024:.1f} MB)")
    print(f"  合计 {total/1024/1024:.1f} MB")
    return total


def strip_manifest(dry: bool):
    """删掉命中 SDK 包名的组件（activity / service / receiver / provider）。

    ⚠️ 这里原来用**文本正则**整体删标签，对「带子标签（intent-filter）的多行组件」
    只删得掉开始标签，把 `<intent-filter>…</intent-filter>` 和 `</activity>` 留在
    原地 → 清单 XML 直接非法，`build_apk.py` 的 `ET.parse` 当场报
    `ParseError: mismatched tag`（从零复刻时必踩：原版解包出来的清单里
    微博那个分享 Activity 就是这种形状）。
    改成 ElementTree 结构化删除，和 `build_apk.py` 的 `normalize_android_manifest()`
    一致 —— 顺带也就免疫标签跨行 / 属性顺序 / 子标签这些格式差异。
    """
    import xml.etree.ElementTree as ET

    path = os.path.join(APK_DIR, "AndroidManifest.xml")
    if not os.path.isfile(path):
        print("  !! 找不到 AndroidManifest.xml")
        return

    ANDROID_NS = "http://schemas.android.com/apk/res/android"
    A = "{%s}" % ANDROID_NS
    ET.register_namespace("android", ANDROID_NS)
    tree = ET.parse(path)
    root = tree.getroot()

    parent_of = {child: parent for parent in root.iter() for child in parent}

    dropped = []
    for tag in ("activity", "activity-alias", "service", "receiver", "provider"):
        for el in list(root.iter(tag)):
            name = (el.get(A + "name") or "").strip()
            if not name:
                continue
            full = name if not name.startswith(".") else "com.cm.zcsmw.baidu" + name
            if not any(full.startswith(h.rstrip(".")) for h in MANIFEST_PKG_HINTS):
                continue
            if full in MANIFEST_KEEP:
                continue
            parent = parent_of.get(el)
            if parent is None:
                continue
            parent.remove(el)
            dropped.append(full)

    if not dropped:
        print("  manifest: 没有需要删的组件")
        return

    print(f"  manifest: 删掉 {len(dropped)} 个 SDK 组件")
    for d in sorted(set(dropped))[:25]:
        print(f"      - {d}")
    if len(set(dropped)) > 25:
        print(f"      ... 还有 {len(set(dropped)) - 25} 个")
    if dry:
        return
    ET.indent(tree, space="    ")
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("<?xml version='1.0' encoding='utf-8'?>\n")
        fh.write(ET.tostring(root, encoding="unicode"))
        fh.write("\n")


def strip_assets_res_lib(dry: bool):
    targets = []
    # assets 里的 SDK 配置
    for name in ("quicksdk.xml", "bdpwxpayplugin.apk", "channelConfig", "talkingdata"):
        p = os.path.join(APK_DIR, "assets", name)
        if os.path.exists(p):
            targets.append(p)
    # lib 里已停服 SDK 的 so（保留 cocos2djs）
    lib_root = os.path.join(APK_DIR, "lib")
    drop_so = (
        "libbdpush", "libbd_wsp", "libBugly", "libby-sdk-root-jni", "libtpnsSecurity",
        "libweibosdkcore", "libxguardian", "libentryexstd", "libtnet", "libbdgame",
    )
    if os.path.isdir(lib_root):
        for abi in os.listdir(lib_root):
            d = os.path.join(lib_root, abi)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if any(f.startswith(x) for x in drop_so):
                    targets.append(os.path.join(d, f))

    total = 0
    for p in targets:
        try:
            size = os.path.getsize(p) if os.path.isfile(p) else 0
        except OSError:
            size = 0
        total += size
        print(f"  删 {p.replace(APK_DIR + os.sep, '')} ({size/1024:.0f} KB)")
        if not dry:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
    if not targets:
        print("  assets/lib：没有要删的")
    print(f"  合计 {total/1024/1024:.2f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-stubs", action="store_true")
    args = ap.parse_args()

    print("== 1. 删 SDK smali ==")
    strip_smali(args.dry_run)

    if not args.skip_stubs:
        print("== 2. 生成并安装桩类 ==")
        if not args.dry_run:
            subprocess.run(
                [sys.executable, os.path.join(HERE, "gen_stubs.py"),
                 "--out", os.path.join(APK_DIR, "smali")],
                check=True,
            )
        else:
            print("  (dry-run 跳过)")

    print("== 3. 清理 manifest ==")
    strip_manifest(args.dry_run)

    print("== 4. 删 assets/lib 里的 SDK 残留 ==")
    strip_assets_res_lib(args.dry_run)

    print("\n完成。接下来：apktool b <目录> --no-apk --no-crunch 然后 patch_apk.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
