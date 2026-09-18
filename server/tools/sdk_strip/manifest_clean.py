"""用 ElementTree 正经地删 AndroidManifest.xml 里的 SDK 组件。

正则删 XML 一定会出标签不匹配的问题（已经踩过），所以这里老老实实解析。

用法：
    python tools/sdk_strip/manifest_clean.py            # 就地清理
    python tools/sdk_strip/manifest_clean.py --dry-run
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import xml.etree.ElementTree as ET

APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\apk\zcsmw")
MANIFEST = os.path.join(APK_DIR, "AndroidManifest.xml")

ANDROID_NS = "http://schemas.android.com/apk/res/android"
ET.register_namespace("android", ANDROID_NS)
A = "{%s}" % ANDROID_NS

# 命中这些包名的组件会被删掉
PKG_HINTS = (
    "com.baidu", "com.duoku", "com.unionpay", "com.alipay", "com.gametalkingdata",
    "com.qk.", "com.squareup", "com.ta.", "com.qq.", "com.slidingmenu",
    "com.sina", "com.tencent", "com.tendcloud", "com.talkingdata", "com.jg.",
    "com.chukong", "com.ucmobile", "com.ut.", "com.kurogame",
    "com.quicksdk",
)

# 这些即使命中也要留
KEEP = {
    "com.quicksdk.apiadapter.baidu.ActivityAdapter",
}

# 只给 SDK 用的权限（删掉能少一堆隐私声明）
PERM_HINTS = (
    "android.permission.READ_SMS", "android.permission.RECEIVE_SMS",
    "android.permission.SEND_SMS", "android.permission.READ_CONTACTS",
    "android.permission.CALL_PHONE", "android.permission.READ_LOGS",
    "android.permission.BATTERY_STATS", "android.permission.CAMERA",
    "android.permission.ACCESS_COARSE_LOCATION", "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.GET_TASKS", "android.permission.RESTART_PACKAGES",
    "android.permission.KILL_BACKGROUND_PROCESSES", "android.permission.BROADCAST_STICKY",
    "android.permission.MOUNT_UNMOUNT_FILESYSTEMS", "android.permission.DISABLE_KEYGUARD",
    "android.permission.FLASHLIGHT", "android.permission.BLUETOOTH",
    "android.permission.BLUETOOTH_ADMIN", "android.permission.RECEIVE_USER_PRESENT",
    "android.permission.ACCESS_DOWNLOAD_MANAGER", "android.permission.DOWNLOAD_WITHOUT_NOTIFICATION",
    "android.permission.WRITE_SETTINGS", "android.permission.SYSTEM_ALERT_WINDOW",
    "android.permission.RECEIVE_BOOT_COMPLETED", "android.permission.VIBRATE",
    "android.permission.WAKE_LOCK", "android.permission.CHANGE_WIFI_STATE",
    "android.permission.ACCESS_WIFI_STATE", "android.permission.CHANGE_NETWORK_STATE",
    "android.permission.ACCESS_NETWORK_STATE",
)

COMPONENT_TAGS = ("activity", "activity-alias", "service", "receiver", "provider")

# 这些权限游戏自己要用，必须留
PERM_KEEP = {
    "android.permission.INTERNET",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.ACCESS_WIFI_STATE",
    "android.permission.WRITE_EXTERNAL_STORAGE",
    "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.READ_PHONE_STATE",
    "android.permission.WAKE_LOCK",
    "android.permission.VIBRATE",
    "android.permission.MODIFY_AUDIO_SETTINGS",
    "android.permission.RECORD_AUDIO",
}


def matches(name: str) -> bool:
    n = name.lstrip(".")
    if n.startswith("."):
        n = "com.cm.zcsmw.baidu" + n
    if not n.startswith(".") and "." not in n:
        n = "com.cm.zcsmw.baidu." + n
    return any(n == h.rstrip(".") or n.startswith(h) for h in PKG_HINTS) and n not in KEEP


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    tree = ET.parse(MANIFEST)
    root = tree.getroot()
    app = root.find("application")
    if app is None:
        print("!! manifest 里没有 <application>")
        return 1

    dropped = []
    for tag in COMPONENT_TAGS:
        for el in list(app.findall(tag)):
            name = el.get(A + "name") or ""
            if name and matches(name):
                app.remove(el)
                dropped.append(f"{tag}:{name}")

    # 权限
    dropped_perm = []
    for el in list(root.findall("uses-permission")):
        perm = el.get(A + "name") or ""
        if perm and perm not in PERM_KEEP and perm in PERM_HINTS:
            root.remove(el)
            dropped_perm.append(perm)

    print(f"删除组件 {len(dropped)} 个 / 权限 {len(dropped_perm)} 个")
    for d in dropped[:20]:
        print("   - ", d)
    if len(dropped) > 20:
        print(f"   ... 还有 {len(dropped)-20} 个组件")
    for p in dropped_perm:
        print("   - ", p)

    if args.dry_run:
        return 0

    tree.write(MANIFEST, encoding="utf-8", xml_declaration=True)
    # ElementTree 会写成单引号、没有换行，aapt2 能接受；但补个换行更保险
    src = io.open(MANIFEST, encoding="utf-8").read()
    if not src.endswith("\n"):
        io.open(MANIFEST, "w", encoding="utf-8", newline="\n").write(src + "\n")
    print(f"已写回 {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
