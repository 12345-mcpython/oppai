.class public Lorg/cocos2dx/javascript/ServerLoginRunnable;
.super Ljava/lang/Object;
.source "ServerLoginRunnable.java"

# interfaces
.implements Ljava/lang/Runnable;


# 私服适配：QuickSDK / 百度渠道的登录服务器早已下线，
# 原生登录弹窗（com.baidu.platformsdk.LoginActivity）永远登不进去。
# 这个 Runnable 直接以「登录成功」的姿态回调 JS：
#     quicksdk.sdkLoginCallback(1, "<account>", "<token>")
# 与 AppActivity$6$1（QuickSDK 真正登录成功时的回调）格式完全一致。

# instance fields
.field private final account:Ljava/lang/String;

.field private final token:Ljava/lang/String;


# direct methods
.method public constructor <init>(Ljava/lang/String;Ljava/lang/String;)V
    .locals 0
    .param p1, "account"    # Ljava/lang/String;
    .param p2, "token"    # Ljava/lang/String;

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    iput-object p1, p0, Lorg/cocos2dx/javascript/ServerLoginRunnable;->account:Ljava/lang/String;

    iput-object p2, p0, Lorg/cocos2dx/javascript/ServerLoginRunnable;->token:Ljava/lang/String;

    return-void
.end method


# virtual methods
.method public run()V
    .locals 2

    new-instance v0, Ljava/lang/StringBuilder;

    invoke-direct {v0}, Ljava/lang/StringBuilder;-><init>()V

    const-string v1, "quicksdk.sdkLoginCallback(1, \""

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    iget-object v1, p0, Lorg/cocos2dx/javascript/ServerLoginRunnable;->account:Ljava/lang/String;

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v1, "\", \""

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    iget-object v1, p0, Lorg/cocos2dx/javascript/ServerLoginRunnable;->token:Ljava/lang/String;

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string v1, "\")"

    invoke-virtual {v0, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v0

    invoke-static {v0}, Lorg/cocos2dx/lib/Cocos2dxJavascriptJavaBridge;->evalString(Ljava/lang/String;)I

    return-void
.end method
