"""客户端「现代化」补丁（在 apktool 解包目录上原地修改）。

做四件事：

1. AndroidManifest.xml
   * 补上 `<uses-sdk>`：`minSdkVersion 9 -> 21`、`targetSdkVersion -> 27`
     （原版根本没有 targetSdk，默认取 minSdk=9，现代 Android 会拦安装/弹旧版警告）
   * `<application>` 上补 `usesCleartextTraffic="true"`
     （模拟服是明文 HTTP）
   * `extractNativeLibs="true"`（lib 是压缩存放的，显式声明更稳）
   * `requestLegacyExternalStorage="true"`（万一以后提到 29 也能继续用老存储模型）

2. apktool.yml 里的 sdkInfo 同步改掉（apktool 重编译时会用它）

3. 投放 PermissionHelper.smali —— 运行时权限申请

4. 在 AppActivity.onCreate 里注入一行调用 PermissionHelper.request(this)

⚠️ **targetSdk 为什么停在 27、不能上 28？**

实测在 Android 9（API 28）上，一旦 targetSdk >= 28，系统就启用
**隐藏 API（non-SDK interface）限制**，而 2016 年的 QuickSDK / 百度 SDK 大量依赖
反射调隐藏 API 做初始化。异常被它自己的 try/catch 吞掉，表现为：

    QuickSdkApplication.onCreate
      -> com.quicksdk.utility.a.a() 返回 null
      -> IAdapterFactory.adtActivity() 空指针
      -> FATAL: Unable to create application ...GameApplication

所以 targetSdk 必须 <= 27。27 已经能满足现代安装要求
（Android 14 只拦 targetSdk < 23；Android 12+ 的「为旧版 Android 打造」提示
只在 targetSdk < 23 时出现）。除非把 QuickSDK 整套删掉，否则不要再往上提。

用法：
    python client/modernize.py                    # 就地修改（targetSdk 27）
    python client/modernize.py --target-sdk 27
    python client/modernize.py --revert           # 撤销 smali 注入
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\apk\zcsmw")

MANIFEST = os.path.join(APK_DIR, "AndroidManifest.xml")
APKTOOL_YML = os.path.join(APK_DIR, "apktool.yml")
SMALI_DIR = os.path.join(APK_DIR, "smali", "org", "cocos2dx", "javascript")
APP_ACTIVITY = os.path.join(SMALI_DIR, "AppActivity.smali")
PERM_SRC = os.path.join(BASE_DIR, "client", "PermissionHelper.smali")
PERM_DST = os.path.join(SMALI_DIR, "PermissionHelper.smali")

MIN_SDK = 21
TARGET_SDK = 28

USES_SDK = f'    <uses-sdk android:minSdkVersion="{MIN_SDK}" android:targetSdkVersion="{TARGET_SDK}"/>'

APP_ATTRS = (
    ' android:usesCleartextTraffic="true"'
    ' android:extractNativeLibs="true"'
    ' android:requestLegacyExternalStorage="true"'
)

INJECT_CALL = (
    "\n    # 私服适配：targetSdk 提到 28 后危险权限要运行时申请\n"
    "    invoke-static {p0}, Lorg/cocos2dx/javascript/PermissionHelper;->request(Landroid/app/Activity;)V\n"
)

SPUT = "sput-object p0, Lorg/cocos2dx/javascript/AppActivity;->instance:Lorg/cocos2dx/javascript/AppActivity;"


def patch_manifest() -> bool:
    src = open(MANIFEST, "r", encoding="utf-8").read()
    orig = src
    changed = []

    if "<uses-sdk" in src:
        # 已经有就替换掉
        src = re.sub(r"<uses-sdk\b[^>]*/>", USES_SDK.strip(), src, count=1)
        changed.append("uses-sdk(替换)")
    else:
        m = re.search(r"<manifest\b[^>]*>", src)
        if not m:
            print("!! 找不到 <manifest> 标签", file=sys.stderr)
            return False
        src = src[: m.end()] + "\n" + USES_SDK + src[m.end():]
        changed.append(f"uses-sdk(min={MIN_SDK},target={TARGET_SDK})")

    for attr in ("usesCleartextTraffic", "extractNativeLibs", "requestLegacyExternalStorage"):
        if attr in src:
            changed.append(f"{attr}(已有)")

    need = [a for a in APP_ATTRS.split(' android:')[1:] if not re.search(r'android:' + a.split('=')[0] + r'=', src)]
    if need:
        m = re.search(r"<application\b", src)
        if not m:
            print("!! 找不到 <application> 标签", file=sys.stderr)
            return False
        ins = "".join(" android:" + a for a in need)
        src = src[: m.end()] + ins + src[m.end():]
        changed.append("application属性: " + ",".join(a.split("=")[0] for a in need))

    if src != orig:
        open(MANIFEST, "w", encoding="utf-8", newline="\n").write(src)
    print("AndroidManifest.xml:", "; ".join(changed) if changed else "无需修改")
    return True


def patch_apktool_yml() -> None:
    if not os.path.isfile(APKTOOL_YML):
        print("!! 找不到 apktool.yml，跳过")
        return
    src = open(APKTOOL_YML, "r", encoding="utf-8").read()
    if "sdkInfo:" not in src:
        src = f"sdkInfo:\n  minSdkVersion: {MIN_SDK}\n  targetSdkVersion: {TARGET_SDK}\n" + src
    else:
        src = re.sub(r"(sdkInfo:\s*\n)", r"\1", src)
        if re.search(r"^\s*minSdkVersion:", src, re.M):
            src = re.sub(r"^(\s*)minSdkVersion:.*$", rf"\g<1>minSdkVersion: {MIN_SDK}", src, count=1, flags=re.M)
        else:
            src = re.sub(r"(sdkInfo:\s*\n)", rf"\g<1>  minSdkVersion: {MIN_SDK}\n", src, count=1)
        if re.search(r"^\s*targetSdkVersion:", src, re.M):
            src = re.sub(r"^(\s*)targetSdkVersion:.*$", rf"\g<1>targetSdkVersion: {TARGET_SDK}", src, count=1, flags=re.M)
        else:
            src = re.sub(r"(sdkInfo:\s*\n(\s*minSdkVersion:.*\n))", rf"\g<1>  targetSdkVersion: {TARGET_SDK}\n", src, count=1)
    open(APKTOOL_YML, "w", encoding="utf-8", newline="\n").write(src)
    print(f"apktool.yml: minSdkVersion={MIN_SDK} targetSdkVersion={TARGET_SDK}")


def install_permission_helper() -> None:
    if os.path.isfile(PERM_DST):
        print("PermissionHelper.smali 已存在，覆盖")
    shutil.copyfile(PERM_SRC, PERM_DST)
    print(f"已写入 {PERM_DST}")


def patch_app_activity() -> bool:
    src = open(APP_ACTIVITY, "r", encoding="utf-8").read()
    if "PermissionHelper;->request" in src:
        print("AppActivity.onCreate 已经注入过了")
        return True
    if SPUT not in src:
        print("!! AppActivity 里没找到 instance 赋值那一行", file=sys.stderr)
        return False
    src = src.replace(SPUT, SPUT + INJECT_CALL, 1)
    open(APP_ACTIVITY, "w", encoding="utf-8", newline="\n").write(src)
    print("AppActivity.onCreate: 已注入 PermissionHelper.request(this)")
    return True


def revert_smali() -> None:
    """只撤 smali 注入（manifest 请从原始 apk 重新解包）"""
    if not os.path.isfile(APP_ACTIVITY):
        return
    src = open(APP_ACTIVITY, "r", encoding="utf-8").read()
    if INJECT_CALL in src:
        open(APP_ACTIVITY, "w", encoding="utf-8", newline="\n").write(src.replace(INJECT_CALL, "", 1))
        print("已撤销 AppActivity 注入")
    if os.path.isfile(PERM_DST):
        os.remove(PERM_DST)
        print("已删除 PermissionHelper.smali")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(MANIFEST):
        print(f"!! 找不到 {MANIFEST}（apktool 解包目录不对？设 GS_APK_DIR）", file=sys.stderr)
        return 1

    if args.revert:
        revert_smali()
        return 0

    ok = patch_manifest()
    patch_apktool_yml()
    install_permission_helper()
    ok = patch_app_activity() and ok
    print("\nOK" if ok else "\n有步骤失败")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
