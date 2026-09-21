"""打包 APK —— 走完整的 apktool 流程。

设计原则：**所有改动都落在解包目录里，然后让 apktool 打完整包**。
不再手动拼 zip —— 那样虽然快一点，但绕过了 aapt2 的资源组装，
`--strip` 也只能靠字符串前缀匹配，不够可控。

    python tools/build_apk.py --host 10.110.29.230

流程：
    1. 改解包目录里的资源
         assets/srcex/urlconfig.jsc        CDN 地址（原地等长替换）
         assets/src/util/server.jsc        登录服务地址
         assets/src/data/share.jsc         分享服务地址
         assets/src/patch/project.manifest 热更地址
         assets/project.json               jsList 里加 patch.js（+ 可选 probe.js）
         assets/src/patch/patch.js         必须的客户端适配
         assets/src/patch/probe.js         诊断探针（--no-probe 时不写）
       并删掉用不到的：
         assets/res/adimage, adcolumn      广告图
         assets/bdpwxpayplugin.apk         百度支付插件
         assets/quicksdk.xml / lib/*.so    已由 strip.py 删过
         smali/android/support, android/net  死代码，dex 里白占地方（见 DROP_SMALI）
    2. apktool b <解包目录> -o <work>/zcsmw-mod.apk --no-crunch
    3. zipalign -f -p 4
    4. apksigner sign（v1 + v2）

所有步骤都是**幂等**的：已经替换过的地址会被识别出来直接跳过。

路径可用环境变量覆盖：AndroidManifest.xml 的规范化见 `normalize_android_manifest()`：targetSdk 23 -> 33、
带 intent-filter 的组件补 android:exported（31+ 不写直接装不上）、删死掉的渠道
meta-data。它和别的清理一样**每次打包都跑** —— `game/` 不进 git，手改留不住，
而「装不上」要等真机安装才暴露。

GS_APK_DIR / GS_WORK_DIR / GS_JAVA_HOME / GS_BUILD_TOOLS
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402




BASE_DIR = _paths.SERVER          # client/patch.js 在服务端那边

APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\zcsmw\game")
WORK = os.environ.get("GS_WORK_DIR", r"E:\code\zcsmw\out")
DEFAULT_PATCH = os.path.join(BASE_DIR, "client", "patch.js")
DEFAULT_PROBE = os.path.join(BASE_DIR, "client", "probe.js")
APKTOOL = os.environ.get("GS_APKTOOL", r"E:\code\zcsmw\script\apktool.bat")

BUILD_TOOLS = os.environ.get("GS_BUILD_TOOLS", r"D:\Android\android-sdk\build-tools\36.0.0")
JAVA_HOME = os.environ.get("GS_JAVA_HOME", r"D:\java\zulu17.68.203-ca-jdk17.0.20.1-win_x64")
KEYSTORE = os.path.join(WORK, "debug.keystore")

OLD_HOST = b"cdn.shuangmawei.net"          # 19 字节
OLD_WWW = b"www.shuangmawei.net"           # 19 字节
OLD_OAUTH = b"http://114.55.66.97:16840"   # 25 字节
OLD_SHARE = b"http://114.55.66.97:14589"   # 25 字节

# 原始包（默认路子要拿它恢复「带地址的那几个文件」）
ORIGINAL_APK = os.environ.get(
    "GS_ORIGINAL_APK", os.path.join(APK_DIR, "original", "zcsmw-original.apk"))

# 打包时会改地址的文件（相对 APK_DIR）。前三个是**编译过的 jsc**，只能等长替换；
# 最后一个是纯文本 JSON（热更新读的，走原生 curl，运行时拦不到）。
URL_FILES = (
    "assets/srcex/urlconfig.jsc",
    "assets/src/util/server.jsc",
    "assets/src/data/share.jsc",
    "assets/src/patch/project.manifest",
)

# 直接删掉的资源（已经停服/用不到）
DROP_ASSETS = (
    "assets/res/adimage",                     # 广告原图（广告服务早停）
    "assets/res/adcolumn",
    "assets/bdpwxpayplugin.apk",              # 百度支付插件
    "assets/quicksdk.xml",                    # QuickSDK 配置
    "assets/com.qk.plugin.qkfx.Manager",      # QuickSDK 插件管理器
    "assets/open_sdk_file.dat",               # QQ 互联 SDK
    # apktool 把「不认识的散装文件」放在 unknown/ 下，打包时会原样导回去。
    # 这里只剩 SDK 残留：微博的 CA 证书 x2 + 百度渠道号，整目录删掉。
    "unknown",
    # --- 2026-09 复核过的一批：把能加载它们的地方全扫了，一个名字都没出现 ---
    # 扫描范围：assets/src、assets/script、assets/srcex、main.jsc、config.json、
    # project.json、smali/**、lib/*/libcocos2djs.so、res/**（共 1085 个文件），
    # ASCII 和 UTF-16LE/BE 三种编码都试过。一个都没命中 = 没有任何代码能加载它们。
    "assets/drawable",                        # 微博 SDK 的图（weibosdk_* /
    "assets/drawable-hdpi",                   #   ic_com_sina_weibo_sdk_* /
    "assets/drawable-ldpi",                   #   login_country_background …）
    "assets/drawable-mdpi",                   # 注意是 assets/ 下，不是 res/drawable-*；
    "assets/drawable-xhdpi",                  # 微博 SDK 类早就被 strip.py 删了
    "assets/drawable-xxhdpi",
    "assets/service.cfg",                     # 百度钱包 SDK 配置（baifubao.com，日期还停在 2014）
    "assets/countryCode.txt",                 # 手机号登录的国家码表（mcc 规则）
    "assets/countryCodeEn.txt",
    "assets/countryCodeTw.txt",
    "assets/pinyinindex",                     # 全工程连 "pinyin" 这个子串都没有
    "assets/data.bin",                        # 同上，没有任何地方引用
)

# `res/` 下 SDK 资源的文件名前缀 —— 命中就整文件删（见 drop_sdk_res()）。
#
#     bdp_     百度支付（Baidu Pay）
#     dk_      多酷（Duoku），百度系渠道
#     wallet_  百度钱包
#     ebpay_   易宝支付
#     bd_      百度 SDK 的杂项
#     qk_      QuickSDK
#
# 这 6 个前缀覆盖了 res/ 下 942 个文件里的 906 个（1613 KB），剩下的 26 个是
# 游戏自己的：各密度 icon.png、splash_img_0.png、slidingmenumain.xml、
# nfc_tech_filter.xml、values-*/dimens.xml。
# 每条都对着实际文件核过，没有游戏资源被误伤。
DROP_RES_PREFIXES = ("bdp_", "dk_", "wallet_", "ebpay_", "bd_", "qk_")

# XML 里的资源引用：`@anim/foo`、`@+id/foo`、`@android:color/foo`
RE_RES_REF = re.compile(r"@(?:\+)?(?:android:)?([a-z]+)/([A-Za-z0-9_.]+)")

# 直接删掉的 smali —— 死代码，dex 里白占地方。
#
# android/support/**  1124 个文件 / 7.5 MB smali。整个工程（smali/com、smali/org、
#   AndroidManifest.xml、assets 里的 js/jsc）**一处引用都没有**。唯一的引用方是
#   res/layout 下百度钱包 / 多酷的三个布局（bd_wallet_sign_channel_list.xml、
#   dk_dialog_back.xml、dk_downloadmanager_activity.xml），而那几个 SDK 的类
#   早就被 script/sdk_strip/strip.py 删干净了，这些布局永远 inflate 不到。
#   MultiDex 也没人用：Application 链是
#     org.cocos2dx.javascript.GameApplication
#       -> com.quicksdk.QuickSdkApplication -> android.app.Application
#   没有 MultiDexApplication，而且只有一个 classes.dex，本来就不需要 multidex。
#
# android/net/**  android.net.http.* + android.net.compatibility.WebAddress，
#   都是 **framework 类**。app dex 里的同名类永远被 boot classpath 挡住，
#   放进来纯属白占地方（工程真正用到的是框架里的 android.net.Uri /
#   android.net.wifi.WifiManager，那些在 /system/framework 里）。
#
# 删错了也不要紧：原版包在 game/original/zcsmw-original.apk，重新 apktool d
# 就能拿回来；out/removed-smali/ 里也留了一份现成的。
#
# 每条是 (要删的目录, 判定「还有人引用吗」用的类名前缀)。
# ⚠️ 前缀不能随便拿目录名去拼：`smali/android/net` 如果拿 "android/net" 当前缀，
# 会把满地的 `Landroid/net/Uri;`、`Landroid/net/wifi/WifiManager;`（框架类，
# 在 /system/framework 里）全算成命中，于是永远不敢删。只能查它实际带的子包。
DROP_SMALI = (
    ("smali/android/support", ("android/support",)),
    ("smali/android/net", ("android/net/http", "android/net/compatibility")),
    # --- 2026-09-20 第二轮：把"游戏自己的代码还吊着"的那几个 SDK 包也清了 ---
    #
    # 这一轮不是"没人引用的类"（那种早清完了），而是**反过来**：这些 SDK 类之所以
    # 一直留着，是因为 `GameShare` / `XGAdapter` / `AppActivity` 里还留着对它们的
    # 调用。先把那三个类改写成"保留接口、实现清空"的桩（见各文件头部注释 +
    # `out/removed-smali-20260920/` 里的原件），这些包才变成可达性上的死代码。
    #
    # 接口为什么必须留：`GameShare.shareToWeChat/shareToSina` 在
    # `assets/src/sdk/gameshare/gameshare.jsc` 里被 jsb.reflection 点名，
    # 而且两个 `.so` 的字符串表里也有 `shareToWeChat` / `shareToSina`
    # （引擎侧也会按名字找）；`XGAdapter` 那几个方法是 `assets/src/sdk/xg/xg.jsc`
    # 用的，`init(Context)` 由 `AppActivity` 调。签名一改就是运行时
    # "method not found"，所以只删包、不删方法。
    #
    # ⚠️ 注释里千万别写斜杠形式的包名：`smali_users_of()` 是**纯文本**匹配斜杠前缀，
    #    注释里出现那个串就会被当成"还有人引用"，这几个包会永远删不掉（真踩过）。
    ("smali/com/tencent/mm", ("com/tencent/mm",)),                    # 微信分享 SDK
    ("smali/com/tencent/android/tpush", ("com/tencent/android/tpush",)),  # 信鸽推送
    ("smali/com/sina", ("com/sina",)),                                # 微博分享 SDK
    ("smali/com/kurogame", ("com/kurogame",)),                        # 微博分享 Activity
    ("smali/com/tendcloud", ("com/tendcloud",)),                      # TalkingData 统计
    # --- 2026-09-20 第三轮：`script/merge_dex.py` 把 smali_classes2 并进 smali/ 之后，
    #     底下这批「孤儿包」才出现在 smali/ 下（上面那条 android/net 按 smali/ 写是对的，
    #     但它只覆盖 http / compatibility 两个子目录）。判定依据：
    #       * `script/smali_reach.py` 的闭包（清单组件 ∪ .so 里的类名 ∪ js/jsc 里的类名）；
    #       * 两个 .so + 全部 asset 的字符串表里这些包名 0 命中；
    #       * 前三个按 smali_reach.py 自己的说明是**框架类**（boot classpath 里就有，
    #         打进 app dex 纯粹白占地方）→ 没人引用就该删。
    #     闸门（`drop_dead_smali()`）每次打包都会复验一遍，将来真有人引用会自动保留。
    ("smali/org/apache", ("org/apache",)),                            # Apache HttpClient
    ("smali/okio", ("okio",)),                                        # OkHttp 的 IO 库，只剩自引用
    ("smali/cn/gov", ("cn/gov",)),                                    # 银联 / 移动支付 SDK 残留
    ("smali/com/android/internal/http", ("com/android/internal/http",)),  # AOSP HTTP legacy 的 multipart
)

# 单类级别的死代码，一组一组删。每组是 ((glob…), 说明)。
#
# 判定「还有人引用吗」用的是**组里所有类的名字**，扫描时把整组文件排除掉 ——
# 必须按组而不是按文件，因为 R$anim 的注解里就写着 `value = Lcom/cm/zcsmw/baidu/R;`，
# 按文件判会自己把自己当成引用方，于是永远不敢删。
# 只要组外还有一处引用，整组保留。
#
# 每一组都是 script/smali_reach.py 从「清单组件 ∪ .so 里的类名 ∪ js/jsc 里的类名」
# 做闭包算出来的不可达类，另外单独确认过没被反射：
# 两个 .so 和 13861 个 asset 里 `zcsmw` 只出现在编译器塞进去的源码路径
# （`E:/code/zcsmw/engine/src/...`），拼不出 `com.cm.zcsmw.baidu.R$*` 这种名字。
DROP_SMALI_GROUPS = (
    (
        ("com/cm/zcsmw/baidu/R.smali", "com/cm/zcsmw/baidu/R$*.smali"),
        "R 资源表：14 个类 / 1.27 MB smali，占了当时剩余 smali 的 60%",
    ),
    (
        ("org/json/alipay/*.smali",),
        "支付宝 SDK 自己带的一份 org.json（和框架的 org.json 不是一回事）",
    ),
    (
        ("org/cocos2dx/lib/GameController*.smali",),
        "手柄支持：Cocos2dxActivity 并没 implements GameControllerDelegate，8 个文件只自引用",
    ),
    (
        ("org/cocos2dx/lib/Cocos2dxLuaJavaBridge.smali",),
        "Lua 桥 —— 这游戏是 cocos2d-js，根本不走 Lua",
    ),
    (
        ("com/tendcloud/tenddata/TDGA*.smali",),
        "TalkingData 的数据类（主类留着，这几个没人调）",
    ),
    (
        ("com/cm/zcsmw/baidu/wxapi/WXEntryActivity.smali",
         "com/tencent/mm/sdk/openapi/BaseResp.smali"),
        "微信回调 Activity + 它的父类：清单里压根没声明这个 Activity",
    ),
)

# R 为什么会变成「死代码」——顺手记一下，免得以后有人以为是误删：
#
#   R$*.smali 不是普通的常量表。它的字段全是**只声明不给值**
#   （`.field public static final dk_float_big_bubble_in:I`），真正的值在 <clinit>
#   里调 `Lcom/quicksdk/apiadapter/baidu/ActivityAdapter;->getResId(名字,类型)I`
#   现查现填 —— 百度/多酷那套加固手法。
#   而 ActivityAdapter 是**我们自己写的桩**（用 Resources.getIdentifier 顶替），
#   它存在的唯一理由就是让这套 R 能用。
#
#   所以：R 是「给已经被 strip.py 删掉的百度渠道 SDK 用的资源表」。SDK 没了，
#   就没人读 R 的字段了（三个清单组件、所有 quicksdk 桩、两个 .so、全部 asset
#   都搜过，零引用），整组可删。
#   **但 ActivityAdapter 故意留着**：就 1.2 KB，而且是那个渠道适配的说明性代码；
#   哪天把 R 从原版包解回来，它还得接着用。



# ---------------------------------------------------------------------------
# Android 清单：targetSdk 与死掉的渠道残留（见 normalize_android_manifest()）
# ---------------------------------------------------------------------------
ANDROID_NS = "http://schemas.android.com/apk/res/android"
A = "{%s}" % ANDROID_NS

# 原包是 minSdk 21 / **targetSdk 23**（Android 6）。后果：
#   * Android 14 起**拒绝安装** targetSdk < 23 的包，Android 15 起 < 24 ——
#     以前往新设备/新模拟器装都得 `adb install -r -d --bypass-low-target-sdk-block`；
#   * 系统按 2015 年的行为给你放行（存储、后台、通知全是老的宽松规则）。
# 停在 33（Android 13）是"够新 + 坑最少"的一档：
#   * 31+ 要求带 intent-filter 的组件显式写 android:exported（下面会自动补）；
#   * 30+ 起分区存储：WRITE/READ_EXTERNAL_STORAGE 失效 —— 引擎写的是应用内目录，
#     实测没影响（`GameShare` 里那处 `Environment.getExternalStorageDirectory()`
#     是分享截图用的，分享链路本来就没接）；
#   * 34 会要求前台服务声明类型、35 会强制 edge-to-edge（全屏横版游戏容易画面出问题），
#     所以不往上抬。要抬就改这一个常量。
TARGET_SDK = 33
MIN_SDK = 21

# 死掉的渠道 SDK meta-data。判定依据（都扫过一遍）：
#   * `YESDK_*` / `BDPlatformType` / `BDGameVersion` 是 YE SDK / 百度 SDK 用的，
#     那些 Java 类早被 `script/sdk_strip/strip.py` 删光 —— smali / 两个 .so /
#     assets 里对这些**键名** 0 引用；
#   * 剩下的 smali 里唯一读 meta-data 的是 `AppActivity`，它读的键是 `CPS`
#     （`AppActivity.CPS = "CPS"`），manifest 里本来就没有 → 走 null 分支。
DEAD_META_DATA = (
    "YESDK_APP_ID", "YESDK_APPKEY", "YESDK_CHANNEL_ID", "YESDK_EXTRA",
    "BDPlatformType", "BDGameVersion",
)

# ⚠️ 别照抄 `script/sdk_strip/manifest_clean.py` 那份 PERM_HINTS 去删权限：
# 它是"看着像只给 SDK 用"的粗筛，而现在这几个**确实有人在用** ——
# `Utilsex.smali` 调 `PowerManager.newWakeLock`（要 WAKE_LOCK）、
# 客户端读 `WifiManager`（3 处，要 ACCESS_WIFI_STATE）、
# `ACCESS_NETWORK_STATE` 是联网判定用的。删了不是"少一条隐私声明"，
# 是运行时 SecurityException。要删权限得先按 smali/.so/assets 全量扫引用。


def log(*a):
    print("[build]", *a, flush=True)

def Warn(*a):
    print("[build][warn]", *a, flush=True)


def _excluded(path: str, exclude) -> bool:
    """`path` 是否落在某个被排除的目录 / 文件里（「它反正要删，别算引用方」）。"""
    ap = os.path.abspath(path)
    for e in exclude:
        if ap == e or ap.startswith(e + os.sep):
            return True
    return False


def smali_users_of(rel: str, prefixes, exclude=frozenset()) -> list:
    """在 smali 里找谁还引用着 `rel` 这个包。

    `prefixes` 是类名前缀（斜杠写法，如 "android/support"）——smali 引用一个类
    永远是 `Landroid/support/v4/view/ViewPager;` 这种描述符，斜杠写法是准的。
    点号写法（反射用的 `Class.forName("android.support...")`）单独审过：全工程 0 处。

    `exclude` 里的路径不算引用方（用于「这些也一并在删除清单上」的迭代判定，
    见 `drop_dead_smali()`）。
    """
    root = os.path.join(APK_DIR, "smali")
    skip = os.path.abspath(os.path.join(APK_DIR, rel))
    needles = [p.encode() for p in prefixes]
    hits = []
    for r, _, fs in os.walk(root):
        if os.path.abspath(r) == skip or os.path.abspath(r).startswith(skip + os.sep):
            continue
        for f in fs:
            if not f.endswith(".smali"):
                continue
            p = os.path.join(r, f)
            if exclude and _excluded(p, exclude):
                continue
            try:
                with open(p, "rb") as fh:
                    b = fh.read()
            except OSError:
                continue
            if any(n in b for n in needles):
                hits.append(os.path.relpath(p, APK_DIR))
    return hits


def expand_smali_globs(patterns) -> list:
    """把 `game/smali` 下的相对 glob 展开成 [绝对路径]。"""
    root = os.path.join(APK_DIR, "smali")
    out = []
    for pat in patterns:
        out.extend(glob.glob(os.path.join(root, pat.replace("/", os.sep))))
    return sorted(set(os.path.abspath(p) for p in out if os.path.isfile(p)))


def smali_users_of_classes(members, exclude=frozenset()) -> list:
    """整组一起看：组外还有谁引用组里的类？

    `members` 是绝对路径列表。判定用的「类名」直接从文件路径推出来，
    所以调用方不用手写前缀，也不会写错。`exclude` 同 `smali_users_of()`。
    """
    root = os.path.join(APK_DIR, "smali")
    members = {os.path.abspath(m) for m in members}
    needles = []
    for m in members:
        cls = os.path.relpath(m, root)[: -len(".smali")].replace(os.sep, "/")
        needles.append(cls.encode())
    hits = []
    for r, _, fs in os.walk(root):
        for f in fs:
            if not f.endswith(".smali"):
                continue
            p = os.path.join(r, f)
            if os.path.abspath(p) in members:
                continue
            if exclude and _excluded(p, exclude):
                continue
            try:
                with open(p, "rb") as fh:
                    b = fh.read()
            except OSError:
                continue
            if any(n in b for n in needles):
                hits.append(os.path.relpath(p, APK_DIR))
    return hits


def drop_dead_smali() -> None:
    """删死代码：整包（`DROP_SMALI`）+ 单类 / 小组（`DROP_SMALI_GROUPS`）。

    判定标准是「**除它自己之外**，还有没有别的 smali 引用它」，但有两个坑
    （2026-09-20 从零复刻时实测到）：

    1. **待删项之间会互相保**。`smali/com/tencent/mm`（微信 SDK 包）唯一的引用方是
       `com/cm/zcsmw/baidu/wxapi/WXEntryActivity`，而它自己在 `DROP_SMALI_GROUPS`
       的「微信回调 Activity」那一组里、也等着被删；反过来那组又被 com/tencent/mm
       里的类引用着 —— 互相保的结果是**两个都删不掉**，包里白留一堆微信 SDK 类
       （而且和仓库里 `game/` 的现状不一致）。
    2. 所以不能"一轮定生死"：要**把所有待删项都当成"将来不存在"**再扫引用，
       一轮轮迭代到不动点。最后仍被"存活文件"引用的才保留。

    安全性方向不变：**只有候选集之外没人引用的才会被删** —— 绝不会删掉一个还被
    存活文件引用着的包（那才是跑起来才崩的 NoClassDefFoundError）。
    """
    cands = []
    for rel, prefixes in DROP_SMALI:
        p = os.path.join(APK_DIR, rel)
        if os.path.exists(p):
            cands.append({"kind": "pkg", "label": rel, "rel": rel, "prefixes": prefixes,
                          "paths": {os.path.abspath(p)}})
    for pats, why in DROP_SMALI_GROUPS:
        members = expand_smali_globs(pats)
        if members:
            cands.append({"kind": "group", "label": pats[0], "why": why,
                          "members": members,
                          "paths": {os.path.abspath(m) for m in members}})
    if not cands:
        return

    kept = []
    for _round in range(8):                     # 一般 1~2 轮就收敛
        cand_paths = set().union(*(c["paths"] for c in cands))
        losers = []
        for c in cands:
            if c["kind"] == "pkg":
                users = smali_users_of(c["rel"], c["prefixes"], exclude=cand_paths)
            else:
                users = smali_users_of_classes(c["members"], exclude=cand_paths)
            if users:
                losers.append(c)
        if not losers:
            break
        for c in losers:
            cands.remove(c)
            kept.append(c)
    else:
        # 8 轮还没收敛（理论上不该发生）：剩下的也一律保留，宁可包大
        kept.extend(cands)
        cands = []

    for c in cands:
        if c["kind"] == "pkg":
            shutil.rmtree(os.path.join(APK_DIR, c["rel"]), ignore_errors=True)
            log(f"  删除 {c['rel']}")
        else:
            # ⚠️ 组里的文件可能已经被上面的**包级**删除带走了（比如
            #    com/tendcloud 那几个类），所以这里要按"还在不在"过滤一遍，
            #    否则 getsize/remove 会 FileNotFoundError（实测踩过）。
            members = [m for m in c["members"] if os.path.exists(m)]
            if not members:
                continue
            kb = sum(os.path.getsize(m) for m in members) / 1024
            for m in members:
                os.remove(m)
            log(f"  删除 {len(members)} 个类 ({kb:.0f} KB)  {c['why']}")

    # 保留的：删完之后再扫一遍，报的引用方才是准的
    for c in kept:
        if c["kind"] == "pkg":
            users = smali_users_of(c["rel"], c["prefixes"])
            head = f"{c['rel']} 现在还有 {len(users)} 处引用，**保留不删**："
            hint = "    这是新加回来的 SDK 依赖？那就把 DROP_SMALI 里对应那条去掉。"
        else:
            members = [m for m in c["members"] if os.path.exists(m)]
            users = smali_users_of_classes(members)
            head = f"{c['label']} 等 {len(members)} 个类还有 {len(users)} 处引用，**保留不删**："
            hint = "    这些类变成「可达」了？那就把 DROP_SMALI_GROUPS 里对应那组去掉。"
        # 宁可留着一个大的包，也不要出一个跑起来才崩的包。
        Warn(head)
        for u in users[:5]:
            Warn(f"    {u}")
        Warn(hint)



# ---------------------------------------------------------------------------
# 地址替换（必须等长：jsc 里字符串是长度前缀存的）
# ---------------------------------------------------------------------------
def make_host_token(host: str, port: int) -> bytes:
    token = f"{host}:{port}".encode()
    if len(token) != len(OLD_HOST):
        raise SystemExit(
            f"host:port 必须是 {len(OLD_HOST)} 字节，'{token.decode()}' 是 {len(token)} 字节"
        )
    return token


def make_login_base(host: str, port: int) -> bytes:
    token = f"http://{host}:{port}".encode()
    if len(token) != len(OLD_OAUTH):
        raise SystemExit(
            f"登录服务地址必须是 {len(OLD_OAUTH)} 字节，'{token.decode()}' 是 {len(token)} 字节 "
            f"—— 端口请用 4 位数"
        )
    return token


def _replace_in_file(path: str, pairs, what: str) -> int:
    """把 (旧, 新) 逐个做原地等长替换；已经换过的直接跳过（幂等）。"""
    with open(path, "rb") as fh:
        data = fh.read()
    orig = data
    done = 0
    for old, new in pairs:
        if new in data:
            continue                      # 已经替换过
        if old not in data:
            log(f"  ! {os.path.relpath(path, APK_DIR)}: 找不到 {old.decode()}，跳过")
            continue
        data = data.replace(old, new)
        done += 1
    if data != orig:
        with open(path, "wb") as fh:
            fh.write(data)
    log(f"  {what}: 替换 {done} 处")
    return done


def restore_pristine_urls() -> None:
    """把带地址的那几个文件从**原始包**恢复回来。

    默认路子用。为什么必须恢复：这几个文件是**就地改写**的，
    换地址时 `_replace_in_file` 找不到旧串会**静默跳过** —— 实测踩过：
    DHCP 换了 PC 的 IP 之后，`game/` 里那三个 jsc + manifest 卡在旧 IP 上，
    重打包出来的包还是旧地址，整包作废。恢复成原始串之后，运行时改写
    （`patch.js` 的 URL-REWRITE）才有东西可改。
    """
    if not os.path.exists(ORIGINAL_APK):
        raise SystemExit(
            f"!! 找不到原始包 {ORIGINAL_APK}\n"
            f"   默认路子需要它来恢复地址（可用 GS_ORIGINAL_APK 指定）")
    with zipfile.ZipFile(ORIGINAL_APK) as z:
        for rel in URL_FILES:
            data = z.read(rel)
            dst = os.path.join(APK_DIR, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(data)
    log(f"  已从原始包恢复 {len(URL_FILES)} 个带地址的文件（交给运行时改写）")


def rewrite_manifest(base: str) -> None:
    """`project.manifest` 按 **JSON** 重写（只换 host，路径原样保留）。

    ⚠️ 它是**热更新**读的，走的是原生 curl 而不是 JS —— `patch.js` 的
    URL-REWRITE 拦不到，所以必须在这里改对。
    好处：纯文本 JSON 不受「等长替换」约束，所以域名/IP/端口随便填，
    换服务器也不会出现「找不到旧串就跳过」那类静默失败。
    """
    path = os.path.join(APK_DIR, "assets/src/patch/project.manifest")
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    try:
        cfg = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"!! project.manifest 不是合法 JSON：{exc}") from exc
    n = 0
    for key in ("packageUrl", "remoteManifestUrl", "remoteVersionUrl"):
        value = cfg.get(key)
        if isinstance(value, str) and value:
            new = re.sub(r"^[a-z]+://[^/]+", base, value, count=1)
            if new != value:
                cfg[key] = new
                n += 1
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(cfg, indent=4))
    log(f"  project.manifest: 改写 {n} 个地址 -> {base}")


# res/<dir> -> 资源类型。只列「能按文件删」的；values*/ 是定义处，不参与。
RES_DIR_TYPE = {
    "drawable": "drawable", "layout": "layout", "anim": "anim", "color": "color",
    "xml": "xml", "raw": "raw", "menu": "menu", "mipmap": "mipmap",
}


def _res_type(dirname: str):
    """`drawable-hdpi-v4` -> `drawable`；`values*` -> None（不参与删除）。"""
    if dirname.startswith("values"):
        return None
    return RES_DIR_TYPE.get(dirname.split("-")[0])


def _res_name(fname: str) -> str:
    """文件名 -> 资源名。

    ⚠️ 九图（nine-patch）要多剥一层：`bd_wallet_single_item_bg.9.png` 的资源名是
    `bd_wallet_single_item_bg`，而 `os.path.splitext()` 只会给出
    `bd_wallet_single_item_bg.9` —— 拿它去比 public.xml / XML 引用永远对不上，
    于是「文件删了、public.xml 条目留着」，aapt 报
    `no definition for declared symbol 'drawable/bd_wallet_single_item_bg'`。
    第一版就是栽在这上面。
    """
    n = fname
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".xml"):
        if n.lower().endswith(ext):
            n = n[: -len(ext)]
            break
    if n.endswith(".9"):
        n = n[:-2]
    return n


def drop_sdk_res() -> None:
    """删掉 `res/` 下**没人引用**的 SDK 资源（百度钱包 / 多酷支付 / ebpay / QuickSDK）。

    这些渠道的服务器早没了，Java 类也被 `sdk_strip/strip.py` 删光了，
    `res/` 里剩下的就是一堆永远 inflate 不到的布局和图。

    判据不是「文件名带 SDK 前缀就删」—— 那样会翻车：`res/values/styles.xml`
    里有 46 处引用着 `@anim/wallet_base_slide_from_right` 这类 SDK 资源，
    删了 aapt 直接报 `resource anim/... not found`。（第一版就是这么挂的。）

    所以走引用闭包：
        根   = 非 SDK 前缀的文件 + `res/values*/**` + AndroidManifest.xml
        边   = XML 里的 `@type/name`
        保留 = 闭包里的全部；删除 = 带 SDK 前缀且不在闭包里的
    这样「values 里还被 style 引用的 anim」会连同它的依赖一起留下。

    **绝不动 `res/values/*.xml` 文件本身** —— 那里面游戏和 SDK 的条目是混在一起
    的，整文件删会把 `app_name`、`xg_service_enabled` 一起带走。

    最后仍有 aapt 兜底：只要还有保留文件引用被删资源，打包会直接报错，
    不会出静默失效的包。报错就从 `out/removed-res/` 把那个文件拿回来。
    """
    res_root = os.path.join(APK_DIR, "res")
    if not os.path.isdir(res_root):
        return

    # 1) 建索引：(类型, 名字) -> [文件]，以及全部可删候选
    index = {}
    files = []
    for r, _, fs in os.walk(res_root):
        typ = _res_type(os.path.basename(r))
        if typ is None:
            continue
        for f in fs:
            p = os.path.join(r, f)
            files.append(p)
            index.setdefault((typ, _res_name(f)), []).append(p)
    if not files:
        return

    def is_sdk(p):
        return os.path.basename(p).startswith(DROP_RES_PREFIXES)

    # 2) 从「根」出发做闭包
    roots = [p for p in files if not is_sdk(p)]
    keep = set(roots)
    queue = list(roots)
    # values*/ 只是定义处，但它们引用谁谁就得留
    for r, _, fs in os.walk(res_root):
        if os.path.basename(r).startswith("values"):
            queue.extend(os.path.join(r, f) for f in fs if f.endswith(".xml"))
    man = os.path.join(APK_DIR, "AndroidManifest.xml")
    if os.path.isfile(man):
        queue.append(man)

    while queue:
        cur = queue.pop()
        try:
            with open(cur, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for typ, name in RE_RES_REF.findall(text):
            for p in index.get((typ, name), ()):
                if p not in keep:
                    keep.add(p)
                    queue.append(p)

    # 3) 删
    doomed = [p for p in files if is_sdk(p) and p not in keep]
    if not doomed:
        return
    kb = sum(os.path.getsize(p) for p in doomed) / 1024
    for p in doomed:
        os.remove(p)
    log(f"  删除 {len(doomed)} 个没人引用的 SDK 资源 ({kb:.0f} KB)")
    # values 里还被引用的那些 SDK 资源（连同依赖）留在原地
    kept_sdk = [p for p in files if is_sdk(p) and p in keep]
    if kept_sdk:
        log(f"    （另有 {len(kept_sdk)} 个 SDK 资源被 values/ 引用，保留）")
    prune_public_xml({(os.path.relpath(p, res_root).split(os.sep)[0], _res_name(os.path.basename(p)))
                      for p in doomed})


def prune_public_xml(doomed) -> None:
    """把 `public.xml` 里指向已删资源的条目剪掉。

    public.xml 的作用是把每个资源的 ID 钉死；删了文件却留着条目，apktool 会报
    「public.xml 指向不存在的资源」。只剪 `doomed` 里那些 (类型, 名字) ——
    其余一律保留，这样**还在的资源 ID 不会变**（smali 里有按数字取资源的，
    例如 `XGAdapter` 取 `0x7f060000` = `xg_service_enabled`）。
    """
    path = os.path.join(APK_DIR, "res", "values", "public.xml")
    if not os.path.isfile(path) or not doomed:
        return
    names = {n for _, n in doomed}
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    out, removed = [], 0
    for ln in lines:
        m = re.search(r'<public\s+type="([^"]+)"\s+name="([^"]+)"', ln)
        if m and m.group(2) in names:
            removed += 1
            continue
        out.append(ln)
    if not removed:
        return
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out))
    log(f"  public.xml: 剪掉 {removed} 条已删资源的 ID")


def prune_stale_build_res() -> None:
    """清掉 apktool 缓存 `build/apk/res/` 里源文件已经不存在的条目。

    `build/apk/` 是 apktool 的增量缓存，**打包时会原样塞进 APK**。删掉 `game/res`
    下某个资源后，缓存里那份编译好的还在，于是照样进包 —— 删了等于没删。

    这个坑很隐蔽：`resources.arsc` 和 `classes.dex` 都会重新生成（看着一切正常），
    只有 `res/` 是增量的。实测删了 873 个资源（1594 KB），APK 只小了 90 KB，
    一查包内还有 906 个 SDK 资源原封不动。

    dex 那边早有 `prune_stale_dex()`，res 一直没人管 —— 这里补上。

    做法是最稳的那种：**整个删掉 `build/apk/res/`**，让 apktool 下次从 `game/res`
    全量重编。现在 source 只剩几十个文件，重编的开销可以忽略；
    按文件比对「源里还在不在」也可以，但只要有 21 个源文件还没进过缓存
    （apktool 本来就是懒编译），逐文件比对就得赌它会不会补编译。
    """
    build_res = os.path.join(APK_DIR, "build", "apk", "res")
    if not os.path.isdir(build_res):
        return
    n = sum(len(fs) for _, _, fs in os.walk(build_res))
    shutil.rmtree(build_res, ignore_errors=True)
    log(f"  清掉 apktool 的 res 增量缓存（{n} 个，防止已删资源照进包）")


def prune_do_not_compress() -> None:
    """剪掉 apktool.yml 里 doNotCompress 指向已删文件的条目。

    这些残留条目会让 apktool 把已经不存在的路径也当成「不压缩」，
    有时还会把原始 APK 里的 unknown file 一起带进产物。
    注意不要动 `arsc` / `png` / `mp3` 这种**扩展名**条目（它们不是路径）。
    """
    path = os.path.join(APK_DIR, "apktool.yml")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    out, removed = [], []
    in_dnc = False
    for line in lines:
        if line.startswith("doNotCompress:"):
            in_dnc = True
            out.append(line)
            continue
        if in_dnc:
            if line.startswith("- "):
                rel = line[2:].strip()
                if "/" in rel and not os.path.exists(os.path.join(APK_DIR, rel)):
                    removed.append(rel)
                    continue
            elif line.strip():
                in_dnc = False
        out.append(line)

    if removed:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out))
        log(f"  apktool.yml: 剪掉 {len(removed)} 条已失效的 doNotCompress")
        for r in removed[:8]:
            log(f"      - {r}")


def normalize_android_manifest() -> None:
    """把 `AndroidManifest.xml` 规范成我们要的样子（**幂等**，每次打包都跑）。

    为什么必须放在这里而不是手改 manifest：`game/` 是 **gitignore 的解包树**
    （`git ls-files game` = 0 个文件）。手改的 targetSdk 一旦重新 `apktool d`
    就没了，而"装不上"这种问题要等真机安装才暴露 —— 所以规则得留在构建脚本里。

    三件事：

    1. **`uses-sdk` 的 targetSdkVersion 提到 `TARGET_SDK`**（apktool.yml 的 `sdkInfo`
       一起改，apktool 打包以它为准）。
    2. **给带 `<intent-filter>` 的组件补显式 `android:exported`**。
       targetSdk 31+ 不写就 **装不上**：`android:exported needs to be explicitly
       specified. Apps targeting Android 12 and higher are required to specify an
       explicit value`。值按老语义取 —— 31 之前"有 intent-filter = 默认导出"，
       所以补 `true`（原样保留行为），不是 `false`。
    3. **删掉死掉的渠道 meta-data**、合并重复的 `<supports-screens>`。
    4. **缺 `<uses-sdk>` 就自己造一个**，并补上 `usesCleartextTraffic` /
       `extractNativeLibs`（见下面那段注释）—— 这两个兜底是为了"漏跑
       `server/client/modernize.py` 也打不出装不上 / 连不上的包"。
    """
    import xml.etree.ElementTree as ET

    path = os.path.join(APK_DIR, "AndroidManifest.xml")
    if not os.path.isfile(path):
        return
    ET.register_namespace("android", ANDROID_NS)
    tree = ET.parse(path)
    root = tree.getroot()
    changes = []

    # 1) uses-sdk：**没有就创建**。
    #    原版包的清单里压根没有这个节点（apktool.yml 的 sdkInfo 只写了 minSdkVersion 9），
    #    而现代 Android 把"没写 targetSdkVersion"当成 targetSdk = minSdk = 9 →
    #    Android 14+ 直接拒装。历史上前提是"必须跑一次 server/client/modernize.py"，
    #    但那一步不在 build.ps1 里，漏跑就打出一个装不上的包（很难查）。
    #    既然这里每次打包都会规范化清单，就顺手把它做成自给自足。
    sdk_els = root.findall("uses-sdk")
    if not sdk_els:
        el = ET.Element("uses-sdk")
        el.set(A + "minSdkVersion", str(MIN_SDK))
        el.set(A + "targetSdkVersion", str(TARGET_SDK))
        root.insert(0, el)
        sdk_els = [el]
        changes.append(f"补 <uses-sdk min={MIN_SDK} target={TARGET_SDK}>（原清单里没有）")
    else:
        for el in sdk_els:
            if el.get(A + "targetSdkVersion") != str(TARGET_SDK):
                changes.append(f"targetSdkVersion {el.get(A + 'targetSdkVersion')} -> {TARGET_SDK}")
                el.set(A + "targetSdkVersion", str(TARGET_SDK))
            if el.get(A + "minSdkVersion") != str(MIN_SDK):
                el.set(A + "minSdkVersion", str(MIN_SDK))

    # 1b) application 上的三个属性（同样由 modernize.py 负责，这里兜底）：
    #     * usesCleartextTraffic —— targetSdk 28+ 默认禁止明文 HTTP，而我们的
    #       CDN / 登录服务是 http://127.0.0.1:18080 这种明文地址 → 不写就连不上；
    #     * extractNativeLibs —— 引擎 .so 在包里是压缩存的，得让系统解压出来；
    #     * requestLegacyExternalStorage —— 老存储模型（targetSdk 29 以下才有意义，
    #       留着是为了和 modernize.py 的产物一致）。
    APP_ATTRS = (
        ("usesCleartextTraffic", "true"),
        ("extractNativeLibs", "true"),
        ("requestLegacyExternalStorage", "true"),
    )
    for app in root.iter("application"):
        for attr, val in APP_ATTRS:
            if app.get(A + attr) != val:
                app.set(A + attr, val)
                changes.append(f"application: {attr}={val}")


    # 2) exported
    for tag in ("activity", "activity-alias", "service", "receiver", "provider"):
        for el in root.iter(tag):
            if el.find("intent-filter") is None:
                continue
            if el.get(A + "exported") is None:
                name = (el.get(A + "name") or "?").rsplit(".", 1)[-1]
                el.set(A + "exported", "true")
                changes.append(f"{tag} {name}: 补 android:exported=true")

    # 3) 死 meta-data + 重复 supports-screens
    for el in list(root.iter("meta-data")):
        name = el.get(A + "name")
        if name in DEAD_META_DATA:
            parent = None
            for cand in root.iter():
                if el in list(cand):
                    parent = cand
                    break
            if parent is not None:
                parent.remove(el)
                changes.append(f"删 meta-data {name}")

    screens = list(root.iter("supports-screens"))
    for el in screens[1:]:
        root.remove(el)
        changes.append("删重复的 <supports-screens>")

    if not changes:
        return
    ET.indent(tree, space="    ")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("<?xml version='1.0' encoding='utf-8'?>\n")
        fh.write(ET.tostring(root, encoding="unicode"))
        fh.write("\n")

    # apktool.yml 的 sdkInfo 也一起改（apktool 重编译时会用它）：
    # 老包这份里只有 `minSdkVersion: 9`、**没有** targetSdkVersion，所以要能补上。
    yml = os.path.join(APK_DIR, "apktool.yml")
    if os.path.isfile(yml):
        with open(yml, "r", encoding="utf-8") as fh:
            text = fh.read()
        new = re.sub(r"(?m)^(\s*minSdkVersion:\s*)\d+", rf"\g<1>{MIN_SDK}", text)
        if re.search(r"(?m)^\s*targetSdkVersion:\s*\d+", new):
            new = re.sub(r"(?m)^(\s*targetSdkVersion:\s*)\d+", rf"\g<1>{TARGET_SDK}", new)
        else:
            new = re.sub(r"(?m)^(\s*minSdkVersion:[^\n]*\n)",
                         rf"\g<1>  targetSdkVersion: {TARGET_SDK}\n", new, count=1)
        if new != text:
            with open(yml, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new)

    log(f"  AndroidManifest: 规范化 {len(changes)} 处 -> targetSdk {TARGET_SDK}")
    for c in changes:
        log(f"      - {c}")


# ---------------------------------------------------------------------------
# 打包哪几个 ABI 的 .so（`--abis`）
# ---------------------------------------------------------------------------
# `game/lib/<abi>/libcocos2djs.so` 是引擎产物：原版包只有 armeabi / x86，
# 我们后来自己编了 armeabi-v7a（`build.ps1 -Engine -Abi armeabi-v7a` 会把产物
# 直接拷进 `game/lib/armeabi-v7a/`）。
#
# 真机只用得上 arm 那套；x86 那 24 MB 纯粹是给模拟器/Nox 这类 x86 环境用的。
# 所以打包支持只带指定 ABI：
#
#     python script\build_apk.py --abis armeabi-v7a,armeabi
#
# 不选的 ABI **不删**，挪到 `out\lib-abi-cache\<abi>\` 存着，下次选上再挪回来
# —— `game/lib` 是 gitignore 的解包树，删了只能重新 apktool d，所以绝不真删。
LIB_ABI_CACHE = os.path.join(WORK, "lib-abi-cache")
KNOWN_ABIS = ("armeabi", "armeabi-v7a", "arm64-v8a", "x86", "x86_64")


def _abi_dirs_in(root: str) -> dict:
    """`{abi: [该目录下的 .so 文件]}`。"""
    out = {}
    if not os.path.isdir(root):
        return out
    for name in os.listdir(root):
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        sos = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".so")]
        if sos:
            out[name] = sos
    return out


def select_lib_abis(want) -> None:
    """只保留 `want` 里的 ABI（空 = 不动，保持现在的样子）。

    没选中的挪进 `LIB_ABI_CACHE`，选中的（哪怕之前被挪走过）挪回来 —— 幂等，可来回切。
    """
    libdir = os.path.join(APK_DIR, "lib")
    in_tree = _abi_dirs_in(libdir)
    in_cache = _abi_dirs_in(LIB_ABI_CACHE)

    if not want:
        if in_tree:
            detail = "、".join(f"{a} {sum(os.path.getsize(f) for f in fs)/1048576:.1f}MB"
                               for a, fs in sorted(in_tree.items()))
            log(f"  .so ABI 全部打包（{detail}）；只带一部分用 --abis armeabi-v7a,armeabi")
        return

    want = [w.strip() for w in want if w.strip()]
    bad = [w for w in want if w not in KNOWN_ABIS]
    if bad:
        raise SystemExit(f"!! 不认识的 ABI：{bad}（可用：{'、'.join(KNOWN_ABIS)}）")
    miss = [w for w in want if w not in in_tree and w not in in_cache]
    if miss:
        hint = f"   要自己编：.\\build.ps1 -Engine -Abi {' '.join(miss)}"
        if "arm64-v8a" in miss:
            # ⚠️ 这条提示原来写的是「arm64-v8a 现在编不了」—— 2026-09-20 已经能编了
            #    （依赖由 script\build_arm64_deps.py 凑齐）。别再把它写回去。
            hint += (
                "\n   ℹ️ arm64-v8a 要**先备依赖**：python script\\build_arm64_deps.py"
                "（cocos 官方那套预编译库只有 armeabi / armeabi-v7a / x86，"
                "arm64 是仓库脚本自己凑的：自编 chipmunk 6.2.1 / libwebsockets 1.23 + 按 ABI 分头文件），"
                "然后再 -Engine -Abi arm64-v8a。详见 engine\\ARM64.md")
        raise SystemExit(
            f"!! 要的 ABI 在 game/lib 和 {os.path.relpath(LIB_ABI_CACHE)} 里都没有：{miss}\n"
            f"   现有：{sorted(set(in_tree) | set(in_cache))}\n{hint}")

    os.makedirs(LIB_ABI_CACHE, exist_ok=True)
    restored, parked = [], []
    for abi in sorted(set(in_tree) | set(in_cache)):
        src_tree = os.path.join(libdir, abi)
        src_cache = os.path.join(LIB_ABI_CACHE, abi)
        if abi in want:
            if not os.path.isdir(src_tree) and os.path.isdir(src_cache):
                shutil.move(src_cache, src_tree)
                restored.append(abi)
        else:
            if os.path.isdir(src_tree):
                if os.path.isdir(src_cache):
                    shutil.rmtree(src_cache, ignore_errors=True)
                shutil.move(src_tree, src_cache)
                parked.append(abi)

    kept = _abi_dirs_in(libdir)
    size = sum(os.path.getsize(f) for fs in kept.values() for f in fs) / 1048576
    log(f"  .so ABI 只打包 {sorted(kept)}（{size:.1f} MB）")
    if restored:
        log(f"      从缓存挪回：{restored}")
    if parked:
        log(f"      挪出（留在 {os.path.relpath(LIB_ABI_CACHE)}）：{parked}")


def prune_stale_dex() -> None:
    """清理 apktool 增量缓存里已经不存在的 dex。

    `build/apk/` 是 apktool 的缓存，打包时会把它里面的东西原样塞进 APK。
    合并/删掉 smali 目录之后（比如 smali_classes2 并进 smali、smali_sdkstub 删掉），
    缓存里的 classes2.dex / sdkstub.dex 还在，会被一起打进包。

    这里按「当前存在哪些 smali 目录」算出期望的 dex 名，多余的删掉。
    """
    build = os.path.join(APK_DIR, "build", "apk")
    if not os.path.isdir(build):
        return

    expected = set()
    for d in os.listdir(APK_DIR):
        if not os.path.isdir(os.path.join(APK_DIR, d)) or not d.startswith("smali"):
            continue
        if d == "smali":
            expected.add("classes.dex")
        else:
            # smali_classes2 -> classes2.dex； smali_sdkstub -> sdkstub.dex
            expected.add(d[len("smali_"):] + ".dex" if d.startswith("smali_") else "classes.dex")

    for f in sorted(os.listdir(build)):
        if f.endswith(".dex") and f not in expected:
            os.remove(os.path.join(build, f))
            log(f"  清理陈旧 dex: build/apk/{f}")


def default_host() -> str:
    """打包用的对外地址 —— **和服务端同一处配置**（`gamesrv/config.py`）。

    以前这里是硬编码的 `10.110.29.230`，而服务端那份默认值也在自己文件里，
    两边一旦不一致，客户端就会收到「客户端连 A、服务器列表说 B」的组合。
    现在默认读 `config.PUBLIC_HOST`（可用 `GS_PUBLIC_HOST` 覆盖），只有一处要设。
    """
    try:
        sys.path.insert(0, BASE_DIR)
        from gamesrv import config as gsconfig
        return gsconfig.PUBLIC_HOST
    except Exception as exc:  # noqa: BLE001
        log(f"  ! 读不到 gamesrv.config（{exc}），--host 回退到 127.0.0.1")
        return "127.0.0.1"


def prepare_assets(host: str, port: int, login_port: int, patch_path: str,
                   probe_path: str, with_probe: bool,
                   patch_urls: bool = False) -> None:
    cdn_base = f"http://{host}:{port}"
    login_url = f"http://{host}:{login_port}"

    if patch_urls:
        # 老路子（`--patch-jsc-urls`）：把 jsc 里的地址**等长**替换掉
        # （<host>:<port> 必须 19 字节 → host 必须 13 个字符）。
        # 只在「包里不留官方地址」这类正式分发场景才需要。
        token = make_host_token(host, port)
        login_base = make_login_base(host, login_port)
        log(f"CDN      -> {token.decode()}（等长替换 jsc）")
        log(f"登录服务 -> {login_base.decode()}（等长替换 jsc）")
        _replace_in_file(os.path.join(APK_DIR, "assets/srcex/urlconfig.jsc"),
                         [(OLD_HOST, token)], "urlconfig.jsc")
        _replace_in_file(os.path.join(APK_DIR, "assets/src/util/server.jsc"),
                         [(OLD_OAUTH, login_base)], "server.jsc")
        _replace_in_file(os.path.join(APK_DIR, "assets/src/data/share.jsc"),
                         [(OLD_SHARE, login_base)], "share.jsc")
    else:
        # 默认路子：jsc 保持官方原始地址，运行时由 patch.js 的 URL-REWRITE 改写。
        # 好处是没有长度约束（127.0.0.1 也行），换服务器只要重打包 assets，
        # 而且打包是幂等的（每次先从原始包恢复，不会「找不到旧串就静默跳过」）。
        restore_pristine_urls()
        log(f"CDN      -> {cdn_base}（运行时改写，jsc 保持原始地址）")
        log(f"登录服务 -> {login_url}（运行时改写）")

    # manifest 走原生 curl，运行时拦不到 → 一律在这里按 JSON 改对
    rewrite_manifest(cdn_base)

    # project.json：jsList 里加入 patch.js（必需）和 probe.js（可选）
    #
    # 拆分说明：patch.js 是「少了游戏就跑不对」的适配层，必须打包；
    #           probe.js 是研究用探针（加密/协议挂钩、REPL、字段探测），
    #           release 可以用 GS_WITH_PROBE=0 排除掉。
    pj = os.path.join(APK_DIR, "assets/project.json")
    cfg = json.loads(open(pj, "r", encoding="utf-8").read())
    js_list = cfg.setdefault("jsList", [])
    # 老版本注入过 hook.js，清掉
    if "src/patch/hook.js" in js_list:
        js_list.remove("src/patch/hook.js")
    wanted = ["src/patch/patch.js"]
    if with_probe:
        wanted.append("src/patch/probe.js")
    # 先清掉这次不打包的（让 --no-probe 的增量构建也正确）
    changed = False
    for name in ("src/patch/patch.js", "src/patch/probe.js"):
        if name not in wanted and name in js_list:
            js_list.remove(name)
            changed = True
    for name in wanted:
        if name not in js_list:
            js_list.append(name)
            changed = True
    if changed:
        with open(pj, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(cfg, indent=4, ensure_ascii=False))
        log(f"  project.json: jsList = {js_list}")
    else:
        log(f"  project.json: jsList 已是最新 {js_list}")

    # 不打包的文件要从 assets 里删掉，否则会残留在 APK 里
    keep = {w.rsplit("/", 1)[-1] for w in wanted}
    for name in ("patch.js", "probe.js"):
        if name not in keep:
            stale = os.path.join(APK_DIR, "assets/src/patch", name)
            if os.path.exists(stale):
                os.remove(stale)
                log(f"  已删除 assets/src/patch/{name}")

    # 写 patch.js / probe.js，把占位符换成真实地址
    #
    #   __CDN_BASE__          probe.js 的 REPL/hook 地址
    #   __OPPAI_CDN_BASE__    patch.js 的 URL-REWRITE：CDN 目标
    #   __OPPAI_LOGIN_BASE__  patch.js 的 URL-REWRITE：登录/oauth 目标
    # （默认路子里后两个才是真正的地址来源，长度随便）
    sources = [("patch.js", patch_path)]
    if with_probe:
        sources.append(("probe.js", probe_path))
    for name, src in sources:
        with open(src, "rb") as fh:
            body = fh.read()
        reps = [(b"__CDN_BASE__", cdn_base.encode()),
                (b"__OPPAI_CDN_BASE__", cdn_base.encode()),
                (b"__OPPAI_LOGIN_BASE__", login_url.encode())]
        for old_b, new_b in reps:
            if old_b in body:
                body = body.replace(old_b, new_b)
        dst = os.path.join(APK_DIR, "assets/src/patch", name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(body)
        log(f"  已写入 assets/src/patch/{name} ({len(body)} B)")

    # 老文件清理
    old = os.path.join(APK_DIR, "assets/src/patch/hook.js")
    if os.path.exists(old):
        os.remove(old)
        log("  已删除旧的 assets/src/patch/hook.js")

    # 删无用资源
    for rel in DROP_ASSETS:
        p = os.path.join(APK_DIR, rel)
        if os.path.exists(p):
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            log(f"  删除 {rel}")

    # 删死掉的 smali（见 DROP_SMALI / DROP_SMALI_GROUPS）
    drop_dead_smali()

    # 删 res/ 下的 SDK 资源（百度钱包 / 多酷 / ebpay / QuickSDK）
    drop_sdk_res()

    # apktool 的 res 增量缓存里会留着已删资源的编译产物，必须一起清掉
    prune_stale_build_res()

    # 剪掉 apktool.yml 里指向已删文件的 doNotCompress 条目
    prune_do_not_compress()

    # Android 清单规范化：targetSdk + exported + 死掉的渠道 meta-data
    normalize_android_manifest()


# ---------------------------------------------------------------------------
# apktool / zipalign / apksigner
# ---------------------------------------------------------------------------
def run(cmd, **kw):
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    env["PATH"] = os.path.join(JAVA_HOME, "bin") + os.pathsep + env.get("PATH", "")
    subprocess.run(cmd, check=True, env=env, **kw)


def apktool_build(out_apk: str) -> None:
    log("apktool b（完整打包，可能需要 1 分钟左右）...")
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    env["PATH"] = os.path.join(JAVA_HOME, "bin") + os.pathsep + env.get("PATH", "")
    subprocess.run(
        [APKTOOL, "b", APK_DIR, "-o", out_apk, "--no-crunch"],
        check=True, env=env, input=b"\n",
    )


def align(path: str) -> str:
    out = path.replace(".apk", "-aligned.apk")
    if os.path.exists(out):
        os.remove(out)
    run([os.path.join(BUILD_TOOLS, "zipalign.exe"), "-f", "-p", "4", path, out])
    log("zipalign 完成")
    return out


def ensure_keystore() -> None:
    if os.path.exists(KEYSTORE):
        return
    log("生成调试签名证书 ...")
    keytool = os.path.join(JAVA_HOME, "bin", "keytool.exe")
    run([
        keytool, "-genkeypair", "-v", "-keystore", KEYSTORE, "-alias", "oppai",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-storepass", "android", "-keypass", "android",
        "-dname", "CN=Oppai Emulator, OU=Dev, O=Emu, L=CN, S=CN, C=CN",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sign(path: str) -> str:
    ensure_keystore()
    out = path.replace("-aligned.apk", "-signed.apk")
    if os.path.exists(out):
        os.remove(out)
    run([
        os.path.join(BUILD_TOOLS, "apksigner.bat"), "sign",
        "--ks", KEYSTORE, "--ks-pass", "pass:android", "--key-pass", "pass:android",
        "--ks-key-alias", "oppai",
        "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
        "--out", out, path,
    ])
    log("签名完成")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None,
                    help="客户端要连的地址。默认读服务端配置 gamesrv/config.py 的 "
                         "PUBLIC_HOST（= GS_PUBLIC_HOST，默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=18080, help="CDN 端口")
    ap.add_argument("--login-port", type=int, default=8080, help="登录端口")
    ap.add_argument("--patch", default=DEFAULT_PATCH)
    ap.add_argument("--probe", default=DEFAULT_PROBE)
    ap.add_argument("--no-probe", action="store_true",
                    help="不打包 probe.js（release 构建）")
    ap.add_argument("--patch-jsc-urls", action="store_true",
                    help="【老路子，默认关】把地址**等长**替换进 jsc，而不是运行时改写。"
                         "代价：host 必须 13 个字符、端口位数固定，而且换地址时"
                         "「找不到旧串」会静默跳过（踩过）；好处是包里不留官方地址")
    ap.add_argument("--no-url-patch", action="store_true",
                    help=argparse.SUPPRESS)   # 已默认，保留只为兼容老命令
    ap.add_argument("--out", default=os.path.join(WORK, "zcsmw-mod.apk"))
    ap.add_argument("--abis", default="",
                    help="只打包这几个 ABI 的 .so，逗号分隔（如 armeabi-v7a,armeabi）。"
                         "留空 = game/lib 里现有的全都带上；不选的挪到 out\\lib-abi-cache\\ 不删")
    ap.add_argument("--skip-prepare", action="store_true", help="只打包，不重新改资源")
    ap.add_argument("--keep-intermediate", action="store_true", help="保留 aligned 中间产物")
    ap.add_argument("--print-host", action="store_true",
                    help="只打印默认对外地址（读服务端配置）然后退出，给 build.ps1 用")
    args = ap.parse_args()

    if args.print_host:
        print(default_host())
        return 0

    os.makedirs(WORK, exist_ok=True)
    host = args.host or default_host()

    if not args.skip_prepare:
        prepare_assets(host, args.port, args.login_port, args.patch,
                       args.probe, not args.no_probe,
                       patch_urls=args.patch_jsc_urls)
        prune_stale_dex()

    # 打包哪几个 ABI 的 .so（--abis；留空 = 全带）
    select_lib_abis([a for a in args.abis.split(",") if a.strip()])

    apktool_build(args.out)
    aligned = align(args.out)
    signed = sign(aligned)
    if not args.keep_intermediate:
        os.remove(args.out)
        os.remove(aligned)
        log("已清理中间产物（--keep-intermediate 可保留）")
    log("最终产物:", signed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
