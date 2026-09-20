.class public Lorg/cocos2dx/javascript/GameShare;
.super Ljava/lang/Object;
.source "GameShare.java"


# 私服适配（2026-09-20）：微信（com.tencent.mm）、微博（com.sina.weibo）两套分享 SDK
# 已经整包删掉，这里只保留 **JS 与引擎 .so 真正会调的方法签名**，实现改成"分享失败"回调。
#
# ⚠️ 注释里包名一律写**点号**（Java 包名写法）：build_apk.py 删 SDK 时用的是
#    `smali_users_of()` 做**纯文本**匹配（把包名写成斜杠形式的那种前缀），
#    注释里写出斜杠形式会被它当成"还有人引用"，于是那些包永远删不掉。
#
# 调用面（JS 原子表 + .so 字符串表都核过）：
#   * assets/src/sdk/gameshare/gameshare.jsc
#         jsb.reflection.callStaticMethod("org/cocos2dx/javascript/GameShare",
#               "shareToWeChat" / "shareToSina", "(Ljava/lang/String;)V", path)
#   * lib/armeabi|lib/x86/libcocos2djs.so 字符串表里有
#         org/cocos2dx/javascript/GameShare、shareToWeChat、shareToSina
#     —— .so 侧也会按名字找这两个方法，**签名不能改**。
#
# 回调方式照抄原实现：Cocos2dxJavascriptJavaBridge.evalString("sharegame.shareFailed(...)")。
# JS 侧 sharegame.shareFailed(platform, errorCode) 查 GAMESHARE_ERROR_CODE 表 → toast，
# 查不到就静默返回；10 是原实现里"微信没装"那一档，所以点分享会看到失败提示，
# 不会卡在分享面板里。
#
# 原来的字段（微信 IWXAPI / 微博 IWeiboShareAPI / Oauth2AccessToken / 各种缓存路径）
# 和它们的方法（checkWechat / initWechat / bmpToByteArray / readAccessToken ...）全部删掉
# —— 那些类型所在的包已经不在包里，留着也编译不过。
#
# 要恢复原版分享：从 game/original/zcsmw-original.apk 重新解包取回本文件，
# 并把 sdk_strip 删掉的微信/微博 SDK 装回来（那两个渠道早下线，装回来也分享不出去）。

.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


.method public static shareToWeChat(Ljava/lang/String;)V
    .locals 1

    const-string v0, "sharegame.shareFailed(\"wechat\", 10)"

    invoke-static {v0}, Lorg/cocos2dx/lib/Cocos2dxJavascriptJavaBridge;->evalString(Ljava/lang/String;)I

    return-void
.end method


.method public static shareToSina(Ljava/lang/String;)V
    .locals 1

    const-string v0, "sharegame.shareFailed(\"sinaweibo\", 10)"

    invoke-static {v0}, Lorg/cocos2dx/lib/Cocos2dxJavascriptJavaBridge;->evalString(Ljava/lang/String;)I

    return-void
.end method


# 下面两个原来是给分享临时图用的；现在没有分享，返回空/零即可（调用者只有本类自己）
.method public static getContext()Lorg/cocos2dx/lib/Cocos2dxActivity;
    .locals 1

    const/4 v0, 0x0

    return-object v0
.end method


.method public static getDirPath()Ljava/lang/String;
    .locals 1

    const-string v0, ""

    return-object v0
.end method


.method public static deleteImage()V
    .locals 0

    return-void
.end method
