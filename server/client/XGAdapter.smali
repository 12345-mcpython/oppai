.class public Lorg/cocos2dx/javascript/XGAdapter;
.super Ljava/lang/Object;
.source "XGAdapter.java"


# 私服适配（2026-09-20）：信鸽推送（com.tencent.android.tpush）整包删掉，这里保留
# JS / AppActivity 会调的方法签名，实现全空。
# ⚠️ 注释里包名写点号：build_apk.py 的 `smali_users_of()` 是纯文本匹配（把包名写成
#    斜杠形式的那种前缀），注释里写出斜杠形式会被当成"还有人引用" → 那包删不掉。
#
# 调用面（JS 原子表 + 全量 smali 都核过）：
#   * assets/src/sdk/xg/xg.jsc → XGAdapter.init / getDeviceToken / setTag / delTag /
#                                addNotification / clearNotifications / XGServiceEnabled
#   * org/cocos2dx/javascript/AppActivity → XGAdapter.init(Context)
#
# `XGServiceEnabled()` 返回 false：客户端那套 XG 红点/角标就不会去等推送 token。
# `getDeviceToken()` 返回空串：登录信息里的 deviceId 本来就不靠它（ws 握手那段自己取，
# 服务端日志里看到的是 "emulator"）。
#
# 要恢复原版推送：从 game/original/zcsmw-original.apk 重新解包取回本文件 +
# 装回 tpush SDK（信鸽后台也早停了）。

.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


.method public static init(Landroid/content/Context;)V
    .locals 0

    return-void
.end method


.method public static XGServiceEnabled()Z
    .locals 1

    const/4 v0, 0x0

    return v0
.end method


.method public static getDeviceToken()Ljava/lang/String;
    .locals 1

    const-string v0, ""

    return-object v0
.end method


.method public static setTag(Ljava/lang/String;)V
    .locals 0

    return-void
.end method


.method public static delTag(Ljava/lang/String;)V
    .locals 0

    return-void
.end method


.method public static addNotification(JLjava/lang/String;Ljava/lang/String;)V
    .locals 0

    return-void
.end method


.method public static clearNotifications()V
    .locals 0

    return-void
.end method


.method public static getContext()Lorg/cocos2dx/lib/Cocos2dxActivity;
    .locals 1

    const/4 v0, 0x0

    return-object v0
.end method
