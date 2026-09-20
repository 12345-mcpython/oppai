"""按 analyze.py 的结果生成 SDK 桩类（smali）。

    python tools/sdk_strip/gen_stubs.py            # 生成到 smali_sdkstub/
    python tools/sdk_strip/gen_stubs.py --out smali   # 直接生成到 smali/ 里

规则：
  * 被 implements 的 → 生成 interface
  * 被 extends 的  → 生成 class，父类按名字猜（Application / Activity / Object）
  * getInstance() → 单例，保证不为 null（否则 invoke-virtual 会 NPE）
  * 其它方法 → 空实现，按返回类型返回默认值
  * 返回类型是「我们自己生成的桩类」时 → 返回一个新实例（尽量别给 null）
  * com/quicksdk/apiadapter/baidu/ActivityAdapter.getResId(name, type) → **真实现**，
    用 Resources.getIdentifier 动态查资源（游戏的 R 类全靠它）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402




BASE_DIR = _paths.SERVER
APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\zcsmw\game")
NEEDED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "needed.json")

# 父类猜测
SUPER_MAP = {
    "com/quicksdk/QuickSdkApplication": "Landroid/app/Application;",
    "com/quicksdk/QuickSdkSplashActivity": "Landroid/app/Activity;",
    "com/kurogame/oppai/mt/WBShareActivity": "Landroid/app/Activity;",
}

ACTIVITY_ADAPTER = "com/quicksdk/apiadapter/baidu/ActivityAdapter"
SPLASH_ACTIVITY = "com/quicksdk/QuickSdkSplashActivity"


def smali_type(java: str) -> str:
    """把 descriptor 转成 smali 里的类型写法（其实就是原样）"""
    return java


def default_return(ret: str, locals_needed: int = 1) -> list[str]:
    if ret == "V":
        return ["    return-void"]
    if ret in ("Z", "I", "B", "S", "C"):
        return ["    const/4 v0, 0x0", "    return v0"]
    if ret in ("J", "D"):
        return ["    const-wide/16 v0, 0x0", "    return-wide v0"]
    return ["    const/4 v0, 0x0", "    return-object v0"]


def gen_activity_adapter() -> str:
    """真实现：Resources.getIdentifier(name, type, packageName)"""
    return """\
.class public Lcom/quicksdk/apiadapter/baidu/ActivityAdapter;
.super Ljava/lang/Object;
.source "ActivityAdapter.java"


# 私服适配：原生 Baidu SDK 已删除。
# 游戏的 R$*.smali 全部靠 ActivityAdapter.getResId(name, type) 动态拿资源 ID，
# 所以这里用 Resources.getIdentifier 重新实现（参数顺序：名字, 类型）。
.method public static getResId(Ljava/lang/String;Ljava/lang/String;)I
    .locals 2

    const/4 v0, 0x0

    :try_start_0
    sget-object v1, Lorg/cocos2dx/javascript/AppActivity;->instance:Lorg/cocos2dx/javascript/AppActivity;

    if-nez v1, :cond_0

    return v0

    :cond_0
    invoke-virtual {v1}, Lorg/cocos2dx/javascript/AppActivity;->getResources()Landroid/content/res/Resources;

    move-result-object v0

    invoke-virtual {v1}, Lorg/cocos2dx/javascript/AppActivity;->getPackageName()Ljava/lang/String;

    move-result-object v1

    invoke-virtual {v0, p0, p1, v1}, Landroid/content/res/Resources;->getIdentifier(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)I

    move-result v0
    :try_end_0
    .catch Ljava/lang/Throwable; {:try_start_0 .. :try_end_0} :catch_0

    return v0

    :catch_0
    const/4 v0, 0x0

    return v0
.end method
"""


def gen_splash_activity() -> str:
    """QuickSDK 的闪屏基类。

    游戏的 `SplashActivity extends QuickSdkSplashActivity` 并重写 `onSplashStop()`
    来拉起 AppActivity —— 原本是这个基类在闪屏结束后回调的。
    桩里如果只继承 Activity 而不回调，App 会**永远卡在闪屏**（实测踩过）。
    所以这里在 onCreate 里直接回调一次。
    """
    return """\
.class public Lcom/quicksdk/QuickSdkSplashActivity;
.super Landroid/app/Activity;
.source "QuickSdkSplashActivity.java"


# 私服适配：原生 QuickSDK 闪屏已删除。
# 子类(SplashActivity)靠 onSplashStop() 回调去启动 AppActivity，
# 所以这里 onCreate 里直接回调一次。
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Landroid/app/Activity;-><init>()V

    return-void
.end method


.method protected onCreate(Landroid/os/Bundle;)V
    .locals 0

    invoke-super {p0, p1}, Landroid/app/Activity;->onCreate(Landroid/os/Bundle;)V

    invoke-virtual {p0}, Lcom/quicksdk/QuickSdkSplashActivity;->onSplashStop()V

    return-void
.end method


.method public onSplashStop()V
    .locals 0

    return-void
.end method
"""


def gen_class(cls: str, info: dict, all_classes: set) -> str:
    if cls == ACTIVITY_ADAPTER:
        return gen_activity_adapter()
    if cls == SPLASH_ACTIVITY:
        return gen_splash_activity()

    is_iface = info.get("as_impl", 0) > 0 and info.get("as_super", 0) == 0
    super_cls = SUPER_MAP.get(cls, "Ljava/lang/Object;")
    methods = sorted(set(tuple(m) for m in info.get("methods", [])))
    # 同一个方法可能被 static / virtual 两种方式调用，static 优先（否则 ICCE）
    static_names = {m[0] for m in methods if len(m) > 3 and str(m[3]).replace("invoke-", "") in ("static", "static-range")}

    lines = []
    simple = cls.rsplit("/", 1)[-1]
    if is_iface:
        lines.append(f".class public interface abstract L{cls};")
        lines.append(f".super {super_cls}")
    else:
        lines.append(f".class public L{cls};")
        lines.append(f".super {super_cls}")
    lines.append('.source "%s.java"' % simple)
    lines.append("")

    has_get_instance = any(m[0] == "getInstance" for m in methods)
    if has_get_instance:
        static_names.add("getInstance")
    if has_get_instance:
        lines.append(f".field private static inst:L{cls};")
        lines.append("")

    lines.append("# direct methods")
    lines.append("")

    if not is_iface:
        # 构造函数
        ctors = [m for m in methods if m[0] == "<init>"]
        if ctors or info.get("as_super", 0) > 0 or not methods:
            lines += [
                ".method public constructor <init>()V",
                "    .locals 0",
                "",
                f"    invoke-direct {{p0}}, {super_cls}-><init>()V",
                "",
                "    return-void",
                ".end method",
                "",
            ]

    if has_get_instance:
        lines += [
            f".method public static getInstance()L{cls};",
            "    .locals 1",
            "",
            f"    sget-object v0, L{cls};->inst:L{cls};",
            "",
            "    if-nez v0, :cond_0",
            "",
            f"    new-instance v0, L{cls};",
            "",
            f"    invoke-direct {{v0}}, L{cls};-><init>()V",
            "",
            f"    sput-object v0, L{cls};->inst:L{cls};",
            "",
            "    :cond_0",
            "    return-object v0",
            ".end method",
            "",
        ]

    for entry in methods:
        name, args, ret = entry[0], entry[1], entry[2]
        if name in ("<init>", "getInstance"):
            continue
        arg_list = "".join(a for a in args.split(",") if a) if args else ""
        is_static = name in static_names
        if is_iface:
            lines.append(f".method public abstract {name}({arg_list}){ret}")
            lines.append(".end method")
            lines.append("")
            continue

        n_locals = 2 if ret in ("J", "D") else 1
        mod = "public static" if is_static else "public"
        lines.append(f".method {mod} {name}({arg_list}){ret}")
        lines.append(f"    .locals {n_locals}")
        lines.append("")

        if ret == "L" + cls + ";" and name.startswith("set") and not is_static:
            lines += ["    return-object p0", ".end method", ""]
            continue

        # 返回类型是别的桩类 → new 一个，尽量别给 null
        m = re.match(r"^L([A-Za-z0-9_/$]+);$", ret)
        if m and m.group(1) in all_classes and not name.startswith("get"):
            inner = m.group(1)
            lines += [
                f"    new-instance v0, L{inner};",
                "",
                f"    invoke-direct {{v0}}, L{inner};-><init>()V",
                "",
                "    return-object v0",
                ".end method",
                "",
            ]
            continue

        lines += default_return(ret)
        lines.append(".end method")
        lines.append("")

    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(APK_DIR, "smali_sdkstub"))
    args = ap.parse_args()

    needed = json.load(io.open(NEEDED, encoding="utf-8"))
    all_classes = set(needed.keys())

    n = 0
    for cls, info in sorted(needed.items()):
        src = gen_class(cls, info, all_classes)
        path = os.path.join(args.out, cls + ".smali")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        io.open(path, "w", encoding="utf-8", newline="\n").write(src)
        n += 1
    print(f"生成 {n} 个桩类 -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
