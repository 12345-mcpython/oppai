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

用法：
    1. 先把 client/ServerLoginRunnable.smali 复制到解包目录的
       smali/org/cocos2dx/javascript/ 下（脚本会尝试自动复制）
    2. python client/patch_smali.py
    3. 用 apktool 重编译 smali（见 README）

路径可用环境变量覆盖：
    GS_APK_DIR   apktool 解包目录（默认 E:\\code\\apk\\zcsmw）
"""

from __future__ import annotations

import os
import shutil
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\zcsmw\game")

SMALI_DIR = os.path.join(APK_DIR, "smali", "org", "cocos2dx", "javascript")
SMALI = os.path.join(SMALI_DIR, "QuickAdapter.smali")
RUNNABLE_SRC = os.path.join(BASE_DIR, "client", "ServerLoginRunnable.smali")
RUNNABLE_DST = os.path.join(SMALI_DIR, "ServerLoginRunnable.smali")

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

    # 1) 投放 ServerLoginRunnable
    if os.path.isfile(RUNNABLE_DST):
        print("ServerLoginRunnable.smali 已存在，跳过复制")
    else:
        shutil.copyfile(RUNNABLE_SRC, RUNNABLE_DST)
        print(f"已写入 {RUNNABLE_DST}")

    # 2) 打 QuickAdapter.login()
    with open(SMALI, "r", encoding="utf-8") as fh:
        src = fh.read()
    if NEW.splitlines()[1] in src:
        print("QuickAdapter.login() 已经打过补丁了")
        return 0
    if OLD not in src:
        print("!! 没找到原始 login() 方法，检查 smali 是否被改过", file=sys.stderr)
        return 1
    with open(SMALI, "w", encoding="utf-8") as fh:
        fh.write(src.replace(OLD, NEW, 1))
    print("QuickAdapter.login() 已改为直接回调私服登录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
