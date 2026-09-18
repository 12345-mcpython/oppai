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
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(os.path.dirname(HERE))
APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\apk\zcsmw")

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
    path = os.path.join(APK_DIR, "AndroidManifest.xml")
    if not os.path.isfile(path):
        print("  !! 找不到 AndroidManifest.xml")
        return
    src = io.open(path, encoding="utf-8").read()
    orig = src

    tag_re = re.compile(
        r"<(?P<tag>activity|activity-alias|service|receiver|provider)\b(?P<body>[^>]*?)"
        r"android:name=\"(?P<name>[^\"]+)\"(?P<rest>[^>]*?)(?P<selfclose>/?)>",
        re.S,
    )

    dropped = []

    def repl(m):
        name = m.group("name")
        full = name.lstrip(".")
        if name.startswith("."):
            full = "com.cm.zcsmw.baidu" + name
        if any(full.startswith(h) or full.startswith(h.rstrip(".")) for h in MANIFEST_PKG_HINTS) \
                and full not in MANIFEST_KEEP:
            dropped.append(full)
            # 整个标签（含子标签）都要删
            start = m.start()
            end = src.find(f"</{m.group('tag')}>", m.end())
            if m.group("selfclose") == "/" or end < 0:
                return ""  # 自闭合
            return ""  # 交给下面统一处理
        return m.group(0)

    # 简单起见：先找出所有要删的组件名，再按标签整体删除
    to_drop = set()
    for m in tag_re.finditer(src):
        name = m.group("name").lstrip(".")
        full = ("com.cm.zcsmw.baidu" + "." + name) if m.group("name").startswith(".") else name
        if any(full.startswith(h.rstrip(".")) for h in MANIFEST_PKG_HINTS) and full not in MANIFEST_KEEP:
            to_drop.add(full)

    for full in sorted(to_drop):
        short = full[len("com.cm.zcsmw.baidu") + 1:] if full.startswith("com.cm.zcsmw.baidu.") else full
        # 匹配 <tag ... android:name="full" ... /> 或 ...>...</tag>
        pat = re.compile(
            r"\s*<(?P<tag>activity|activity-alias|service|receiver|provider)\b[^>]*?"
            r'android:name="(?P<n>' + re.escape(full) + r'|' + re.escape(short) + r')"[^>]*?(?P<sc>/?)>'
            r"(?:(?!</(?P=tag)>).)*?(?:</(?P=tag)>)?",
            re.S,
        )
        new = pat.sub("", src)
        if new != src:
            src = new
            dropped.append(full)

    if src != orig:
        print(f"  manifest: 删掉 {len(set(dropped))} 个 SDK 组件")
        for d in sorted(set(dropped))[:25]:
            print(f"      - {d}")
        if len(set(dropped)) > 25:
            print(f"      ... 还有 {len(set(dropped)) - 25} 个")
        if not dry:
            io.open(path, "w", encoding="utf-8", newline="\n").write(src)
    else:
        print("  manifest: 没有需要删的组件")


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
