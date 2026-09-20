"""Java(smali) 层补丁：让 SDK 登录直接成功，不再弹百度登录框。

原生侧原本的调用链：

    JS jsb.reflection.callStaticMethod(QuickAdapter, "login")
      -> QuickAdapter.login()            (smali)
      -> AppActivity.login() -> runOnUiThread(AppActivity$8)
      -> QuickSDK.User.login(activity) -> 渠道 SDK(百度) -> LoginActivity 弹窗
      -> 登录成功后原生 evalString('quicksdk.sdkLoginCallback(1, "uid", "token")')

QuickSDK / 百度的服务器早就下线了，这个弹窗永远登不进去。
本脚本把 `QuickAdapter.login()` 换成「直接回调 SDK 登录成功」：

    QuickAdapter.login()
      -> runOnGLThread(new ServerLoginRunnable("emulator", "emulator-token"))
      -> evalString('quicksdk.sdkLoginCallback(1, "emulator", "emulator-token")')

这样点「开始游戏」走的还是游戏自己的原始流程（注册 SDK 回调 → op.login → SDK 回调），
只是原生登录瞬间成功。

这个脚本一共管 5 件事（都幂等，可以反复跑）：

| # | 干什么 | 和 `sdk_strip/strip.py` 的关系 |
|---|---|---|
| 1 | 投放我们自己的 smali：`ServerLoginRunnable` / `PermissionHelper` / `GameShare` / `XGAdapter` | 必须**在 strip.py 之后**跑 |
| 2 | `QuickAdapter.login()` 改成直接回调私服登录 | 同理 |
| 3 | 修 `AppActivity$12`（退出弹窗）的乱码文案 | 无关 |
| 4 | `com.quicksdk.Sdk.exit()` 从空桩改成 finish + killProcess | 依赖 strip.py 先重建桩类 |
| 5 | `AppActivity`：删 3 处 TalkingData 调用 + 注入 `PermissionHelper.request(this)` | 同上 |

⚠️ **第 1、5 条为什么在这里**（2026-09-20 第二轮 SDK 剥离）：

`GameShare` / `XGAdapter` / `AppActivity` 里还吊着微信（com.tencent.mm）、微博
（com.sina.weibo）、信鸽（com.tencent.android.tpush）、TalkingData（com.tendcloud）
几个包。得先把这三个类处理成「保留签名、实现清空 / 去掉调用点」，那几个包在可达性上
才变成死代码，`script/build_apk.py` 的 `DROP_SMALI` / `DROP_SMALI_GROUPS` 才肯删它们。

早先这三处是**手工改**的，原件只备份在 `out/removed-smali-20260920/`（不进 git）——
重新 `apktool d` 之后没人重做，闸门就会判「还有人引用」：那几个 SDK 包**删不掉**
（包变大，且运行时 `NoClassDefFoundError`）。所以现在按下面这样脚本化：

* `GameShare.smali` / `XGAdapter.smali` 是**我们自己写的代码**（87 / 91 行，只有签名和
  空实现）→ 和 `ServerLoginRunnable.smali` 一样整文件放在 `client/` 下，内容不一致就覆盖；
* `AppActivity.smali` 是**游戏自己的代码**（1100+ 行）→ 本仓库只放自己写的代码，
  所以不整文件覆盖，只按下面 `TALKINGDATA_SITES` 做定点字符串手术 + 注入一行调用。

用法：
    1. 先把 client/ServerLoginRunnable.smali 复制到解包目录的
       smali/org/cocos2dx/javascript/ 下（脚本会尝试自动复制）
    2. python client/patch_smali.py
    3. 用 apktool 重编译 smali（见 README）

路径可用环境变量覆盖：
    GS_APK_DIR   apktool 解包目录（默认 E:\\code\\zcsmw\\game）
"""

from __future__ import annotations

import os
import re
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\zcsmw\game")

SMALI_DIR = os.path.join(APK_DIR, "smali", "org", "cocos2dx", "javascript")
SMALI = os.path.join(SMALI_DIR, "QuickAdapter.smali")
RUNNABLE_SRC = os.path.join(BASE_DIR, "client", "ServerLoginRunnable.smali")
RUNNABLE_DST = os.path.join(SMALI_DIR, "ServerLoginRunnable.smali")

# 我们自己写的 smali（整文件投放；内容不一致就覆盖，见 install_own_smali）：
#   * PermissionHelper       —— 运行时权限申请，由 AppActivity.onCreate 调一次
#   * GameShare / XGAdapter  —— 微信/微博/信鸽 SDK 整包删掉后剩下的空实现桩
OWN_SMALI = (
    ("PermissionHelper.smali",
     "运行时权限申请（targetSdk 28+ 必须），AppActivity.onCreate 里注入一次调用"),
    ("GameShare.smali",
     "微信(com.tencent.mm)/微博(com.sina.weibo)分享 SDK 已删 —— 只留 JS 与 .so 会调的签名"),
    ("XGAdapter.smali",
     "信鸽推送(com.tencent.android.tpush)已删 —— 只留签名，实现全空"),
)

# AppActivity：TalkingData(com.tendcloud) 统计后台早已下线，删掉 3 处调用点之后
# 整包就没人引用了（build_apk.py 的闸门才肯删）。文本是**逐字节**从原始包
# → 现在这份的差异里取的，所以脚本跑完和手工改的结果完全一致。
APP_ACTIVITY = os.path.join(SMALI_DIR, "AppActivity.smali")

TALKINGDATA_SITES = (
    (
        '    const-string v2, "2A0BD65E4889DEA3D6621BCC4F81BAD4"\n'
        '\n'
        '    sget-object v3, Lorg/cocos2dx/javascript/AppActivity;->packageName:Ljava/lang/String;\n'
        '\n'
        '    invoke-static {v1, v2, v3}, Lcom/tendcloud/tenddata/TalkingDataGA;->init(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;)V\n',
        '    # 私服：TalkingData 统计 SDK 已删（原来这里三行是\n'
        '    #   const-string v2, "2A0BD65E4889DEA3D6621BCC4F81BAD4"\n'
        '    #   sget-object v3, ...AppActivity;->packageName:Ljava/lang/String;\n'
        '    #   invoke-static {v1, v2, v3}, com.tendcloud.tenddata.TalkingDataGA.init(...)V\n'
        '    # 统计后台早下线；删掉调用点之后 com.tendcloud 整包就没人引用了。\n'
        '    # ⚠️ 包名写点号：build_apk.py 的 smali_users_of() 是纯文本匹配斜杠前缀，\n'
        '    #    注释里写斜杠形式会被当成"还有人引用" → 那包删不掉。\n',
    ),
    (
        '    invoke-static {p0}, Lcom/tendcloud/tenddata/TalkingDataGA;->onPause(Landroid/app/Activity;)V\n',
        '    # 私服：TalkingData 统计已删（原来这一行是 com.tendcloud.tenddata.TalkingDataGA.onPause）\n',
    ),
    (
        '    invoke-static {p0}, Lcom/tendcloud/tenddata/TalkingDataGA;->onResume(Landroid/app/Activity;)V\n',
        '    # 私服：TalkingData 统计已删（原来这一行是 com.tendcloud.tenddata.TalkingDataGA.onResume）\n',
    ),
)

# 和 server/client/modernize.py 里那份**完全一致**（两边都幂等，谁先跑都行）：
# 注入点选在 instance 赋值那一行之后，和 modernize.py 的锚点相同 →
# 两边产出的文本逐字节一致。
PERM_INJECT = (
    "\n    # 私服适配：targetSdk 提到 28 后危险权限要运行时申请\n"
    "    invoke-static {p0}, Lorg/cocos2dx/javascript/PermissionHelper;->request(Landroid/app/Activity;)V\n"
)
PERM_ANCHOR = "sput-object p0, Lorg/cocos2dx/javascript/AppActivity;->instance:Lorg/cocos2dx/javascript/AppActivity;"


# ---------------------------------------------------------------------------
# AppActivity.exit() 的退出确认弹窗：官方包里的 4 个字符串本来就是乱码。
#
# 证据：原版 APK 的 classes2.dex 里就有 26 个 U+FFFD（= 这 4 个串的替换符个数
# 6+14+2+4），而同一个 dex 里别的「确定」「取消」是正常 UTF-8。也就是说厂商当年
# 发布时这个弹窗就是乱码的，不是本项目工具链搞坏的，也没有干净副本可抄。
#
# 那些 U+FFFD 已经把原始字节吃掉了，原文不可精确还原，所以这里换成规范文案。
# 注意：**写成 \uXXXX 转义**（见下方 _escape_ascii），因为根因就是编码问题 ——
# 转义串是纯 ASCII，对 apktool / javac 的默认字符集完全免疫。apktool 自己写
# 这个文件时用的也是转义（\u70d8\u7236 就是原乱码里幸存的「烘父」）。
EXIT_SMALI = os.path.join(SMALI_DIR, "AppActivity$12.smali")

EXIT_DIALOG_FIX = [
    # (官方包里的乱码 const-string 字面量, 替换成的正确文案)
    (r'"\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd"',
     "温馨提示"),
    (r'"\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\u70d8\u7236\ufffd\ufffd\ufffd?"',
     "确定要退出游戏吗?"),
    (r'"\u7ead\ufffd\u7039\ufffd"',
     "确定"),
    (r'"\ufffd\ufffd\ufffd\u5a11\ufffd"',
     "取消"),
]


def _escape_ascii(s):
    """把中文转成 \\uXXXX 转义，保证写进 smali 的是纯 ASCII。"""
    out = []
    for ch in s:
        if ord(ch) < 0x80:
            out.append(ch)
        else:
            out.append("\\u%04x" % ord(ch))
    return "".join(out)


def patch_exit_dialog():
    """把退出弹窗的乱码文案换成正常中文（幂等）。"""
    if not os.path.isfile(EXIT_SMALI):
        print(f"!! 找不到 {EXIT_SMALI}，跳过退出弹窗修补", file=sys.stderr)
        return 1

    with open(EXIT_SMALI, "r", encoding="utf-8") as fh:
        src = fh.read()

    changed = 0
    already = 0
    for old, new in EXIT_DIALOG_FIX:
        fixed = '"%s"' % _escape_ascii(new)
        if old in src:
            src = src.replace(old, fixed, 1)
            changed += 1
            print(f"  退出弹窗文案: {old}  ->  {new}")
        elif fixed in src:
            already += 1

    if changed:
        with open(EXIT_SMALI, "w", encoding="utf-8") as fh:
            fh.write(src)
        print(f"退出弹窗已修：{changed} 处乱码文案 -> 正常中文")
    elif already == len(EXIT_DIALOG_FIX):
        print("退出弹窗文案已经修过了，跳过")
    else:
        print(f"!! 退出弹窗只匹配到 {already}/{len(EXIT_DIALOG_FIX)} 处，"
              f"smali 可能被改过，请人工核对 {EXIT_SMALI}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# 「退出」按钮点不动：SDK 剥离时 gen_stubs.py 给 com.quicksdk.Sdk.exit() 生成的是
# **空桩**（直接 return-void），而退出弹窗的「确定」按钮正是调它：
#
#     AppActivity$12$1.onClick -> Sdk.getInstance().exit(activity)   # 空桩，啥也不干
#
# 所以弹窗文字正常、按钮却毫无反应。真 SDK 在这里走渠道退出流程（并再弹它自己的
# 确认框）；私服没有渠道 —— **finish 之后必须把进程也结束掉**。
#
# ⚠️ 只 finish 不杀进程 = 埋了一个"第二次进游戏闪退"（2026-09-20 实测）：
#     finish() 之后进程留在 cached，玩家再点「进入游戏」时**复用同一个进程**，
#     而引擎（GL 线程 / native AppDelegate / JS VM）在 Activity 销毁时已经拆过一半，
#     重新初始化就崩在 libcocos2djs.so 的 GL 线程里：
#
#       11:25:23 Start proc 22041             ← 冷启动
#       12:07:29 AppActivity 图层销毁          ← 退出（finish），没有 "Killing 22041"
#       12:07:31 START SplashActivity          ← 复用同一进程再进
#       12:07:31 Fatal signal 11 (SIGSEGV) fault addr 0x14  GLThread  libcocos2djs.so
#       12:07:32 Process 22041 has died        ← 崩溃把进程杀了
#       12:07:34 Start proc 5878               ← 所以"再进一次反而正常"
#
#     加上 killProcess 之后，下一次启动必然是全新冷启动，这条路径就不存在了。
#     （早先不杀进程是怕 logcat 里留 signal 9 看着像崩溃 —— 那个顾虑是错的：
#      killProcess 是自杀，不会进 tombstone，日志里只有一句 "has died"。）
SDK_SMALI = os.path.join(APK_DIR, "smali", "com", "quicksdk", "Sdk.smali")

SDK_EXIT_OLD = """.method public exit(Landroid/app/Activity;)V
    .locals 1

    return-void
.end method"""

# 目标实现：finish + 结束自己这个进程
SDK_EXIT_HARD = """.method public exit(Landroid/app/Activity;)V
    .locals 1

    # 私服适配：原桩是空实现，退出弹窗点「确定」没反应。
    # 真 SDK 走渠道退出流程；私服 = finish() + 结束进程。
    #
    # ⚠️ killProcess 不能省：只 finish 的话进程留在 cached，玩家下次进游戏会
    # 复用同一进程，而引擎已经拆过一半 → GL 线程 SIGSEGV（实测：主界面一闪而过闪退，
    # 再进一次才好——因为那次是被崩掉的进程，等于冷启动）。
    invoke-virtual {p1}, Landroid/app/Activity;->finish()V

    invoke-static {}, Landroid/os/Process;->myPid()I

    move-result v0

    invoke-static {v0}, Landroid/os/Process;->killProcess(I)V

    return-void
.end method"""

# 历史形态：只 finish（会踩上面那个坑），保留用于识别
SDK_EXIT_SOFT = """.method public exit(Landroid/app/Activity;)V
    .locals 1

    invoke-virtual {p1}, Landroid/app/Activity;->finish()V

    return-void
.end method"""

# 整个方法块（含注释）一把换掉。
# ⚠️ 原来按"整段文本 + 注释"精确匹配，结果一改注释就匹配不上（实测踩过：
#    game/smali 里是旧注释、常量是新注释 → 报"形状不认识"）。注释不是接口，
#    所以这里只认方法签名，正文按特征判断。
SDK_EXIT_RE = re.compile(
    r"\.method public exit\(Landroid/app/Activity;\)V.*?\.end method", re.S)


def patch_sdk_exit():
    """让退出弹窗的「确定」真的退出（finish + 杀进程，幂等）。

    按方法签名匹配、按特征迁移，认三种形态：生成器产的空桩（`return-void`）、
    只 finish 的软退（会崩，见上面那段）、以及目标实现（含 killProcess）。
    不认识就报错让人看一眼 —— 宁可不动，也别把不认识的代码覆盖掉。
    """
    if not os.path.isfile(SDK_SMALI):
        print(f"!! 找不到 {SDK_SMALI}，跳过 Sdk.exit 修补", file=sys.stderr)
        return 1

    with open(SDK_SMALI, "r", encoding="utf-8") as fh:
        src = fh.read()

    m = SDK_EXIT_RE.search(src)
    if not m:
        print(f"!! Sdk.exit(Activity) 方法块找不到，请人工核对 {SDK_SMALI}",
              file=sys.stderr)
        return 1

    body = m.group(0)
    # ⚠️ 认**指令**而不是关键词：旧实现的注释里就写着 "不用 killProcess 硬杀…"，
    #    按关键词判断会误判成"已经打过补丁"（实测踩过）。
    if "Landroid/os/Process;->killProcess" in body:
        print("Sdk.exit() 已经是 finish + killProcess，跳过")
        return 0
    if "finish" not in body and "return-void" not in body:
        print(f"!! Sdk.exit() 里有不认识的逻辑，没敢动，请人工核对 {SDK_SMALI}",
              file=sys.stderr)
        return 1

    with open(SDK_SMALI, "w", encoding="utf-8") as fh:
        fh.write(src[:m.start()] + SDK_EXIT_HARD + src[m.end():])
    print("Sdk.exit(): -> finish + killProcess（原来只 finish 会崩在下次进游戏）")
    return 0

def install_own_smali():
    """把 client/ 下我们自己写的 smali 投放到解包目录（内容不一致才覆盖，幂等）。"""
    rc = 0
    for name, why in OWN_SMALI:
        src = os.path.join(BASE_DIR, "client", name)
        dst = os.path.join(SMALI_DIR, name)
        if not os.path.isfile(src):
            print(f"!! 缺 {src}（脚本自己写在 client/ 下，仓库里应该有）", file=sys.stderr)
            rc = 1
            continue
        new = open(src, "rb").read()
        old = open(dst, "rb").read() if os.path.isfile(dst) else None
        if old == new:
            print(f"{name} 已是最新，跳过")
        else:
            shutil.copyfile(src, dst)
            print(f"{'覆盖' if old is not None else '写入'} {name}（{why}）")
    return rc


def patch_app_activity():
    """AppActivity：删 3 处 TalkingData 调用 + 注入 PermissionHelper（幂等）。

    只做定点替换，不整文件覆盖 —— AppActivity 是游戏自己的代码（1100+ 行），
    本仓库的规矩是只放自己写的代码（见 README §免责声明）。
    """
    if not os.path.isfile(APP_ACTIVITY):
        print(f"!! 找不到 {APP_ACTIVITY}，跳过 AppActivity 修补", file=sys.stderr)
        return 1
    with open(APP_ACTIVITY, "r", encoding="utf-8") as fh:
        src = fh.read()

    changed = []
    rc = 0
    for i, (old, new) in enumerate(TALKINGDATA_SITES, 1):
        if old in src:
            src = src.replace(old, new, 1)
            changed.append(f"TalkingData 调用点 {i}")
        elif new not in src:
            print(f"!! AppActivity 里第 {i} 处 TalkingData 调用既不认识原文、"
                  f"也没有改过的痕迹，请人工核对", file=sys.stderr)
            rc = 1

    if "PermissionHelper;->request" in src:
        pass
    elif PERM_ANCHOR not in src:
        print("!! AppActivity 里没找到 instance 赋值那一行，没法注入 PermissionHelper",
              file=sys.stderr)
        rc = 1
    else:
        src = src.replace(PERM_ANCHOR, PERM_ANCHOR + PERM_INJECT, 1)
        changed.append("注入 PermissionHelper.request(this)")

    if changed:
        with open(APP_ACTIVITY, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src)
        print("AppActivity：" + "、".join(changed))
    else:
        print("AppActivity 已经处理过（没有 TalkingData 调用、权限申请也注入过）")
    return rc


OLD = """.method public static login()V
    .locals 1

    .prologue
    .line 47
    sget-object v0, Lorg/cocos2dx/javascript/AppActivity;->instance:Lorg/cocos2dx/javascript/AppActivity;

    invoke-virtual {v0}, Lorg/cocos2dx/javascript/AppActivity;->login()V

    .line 48
    return-void
.end method"""

NEW = """.method public static login()V
    .locals 4

    # 私服适配：不再走 QuickSDK / 百度登录（其服务器已下线，弹窗登不进去），
    # 直接在 GL 线程回调 quicksdk.sdkLoginCallback(1, "emulator", "emulator-token")
    sget-object v0, Lorg/cocos2dx/javascript/AppActivity;->instance:Lorg/cocos2dx/javascript/AppActivity;

    new-instance v1, Lorg/cocos2dx/javascript/ServerLoginRunnable;

    const-string v2, "emulator"

    const-string v3, "emulator-token"

    invoke-direct {v1, v2, v3}, Lorg/cocos2dx/javascript/ServerLoginRunnable;-><init>(Ljava/lang/String;Ljava/lang/String;)V

    invoke-virtual {v0, v1}, Lorg/cocos2dx/lib/Cocos2dxActivity;->runOnGLThread(Ljava/lang/Runnable;)V

    return-void
.end method"""


def main():
    if not os.path.isfile(SMALI):
        print(f"!! 找不到 {SMALI}（apktool 解包目录不对？设 GS_APK_DIR）", file=sys.stderr)
        return 1

    rc = 0

    # 1) 投放我们自己的 smali（ServerLoginRunnable + PermissionHelper + 两个 SDK 桩）
    if os.path.isfile(RUNNABLE_DST):
        print("ServerLoginRunnable.smali 已存在，跳过复制")
    else:
        shutil.copyfile(RUNNABLE_SRC, RUNNABLE_DST)
        print(f"已写入 {RUNNABLE_DST}")
    if install_own_smali() != 0:
        rc = 1

    # 2) 打 QuickAdapter.login()
    with open(SMALI, "r", encoding="utf-8") as fh:
        src = fh.read()
    if NEW.splitlines()[1] in src:
        print("QuickAdapter.login() 已经打过补丁了")
    elif OLD not in src:
        print("!! 没找到原始 login() 方法，检查 smali 是否被改过", file=sys.stderr)
        rc = 1
    else:
        with open(SMALI, "w", encoding="utf-8") as fh:
            fh.write(src.replace(OLD, NEW, 1))
        print("QuickAdapter.login() 已改为直接回调私服登录")

    # 3) 修 AppActivity.exit() 退出确认弹窗的乱码文案
    #    这一步必须无条件跑到：上面 QuickAdapter 已打过补丁时会「跳过」，
    #    早先写成 return 0 会让退出弹窗的修补永远轮不到。
    if patch_exit_dialog() != 0:
        rc = 1

    # 4) 让退出弹窗的「确定」真的结束游戏（Sdk.exit 是空桩）
    if patch_sdk_exit() != 0:
        rc = 1

    # 5) AppActivity：删 TalkingData 调用点 + 注入运行时权限申请
    if patch_app_activity() != 0:
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
