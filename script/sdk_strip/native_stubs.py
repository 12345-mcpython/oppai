"""libcocos2djs.so 原生层硬依赖的类，必须存在。

cocos2d-x 的 jsb 绑定在 .so 里写死了这些类名/方法名/签名：

    com/tencent/bugly/cocos/Cocos2dxAgent     Bugly 崩溃上报
    com/tendcloud/tenddata/TalkingDataGA      TalkingData 统计
    com/tendcloud/tenddata/TDGAAccount
    com/tendcloud/tenddata/TDGAItem
    com/tendcloud/tenddata/TDGAMission
    com/tendcloud/tenddata/TDGAVirtualCurrency
    com/chukong/cocosplay/client/CocosPlayClient

删掉它们后，原生层 FindClass 返回 null，紧接着 GetStaticMethodID(clazz=null)
会让 ART 直接 `JNI DETECTED ERROR: java_class == null` → SIGABRT。

所以这里按「方法名 × 所有出现过的签名」生成**全部重载**：
JNI 只按 (名字, 签名) 查，多定义几个变体不会有副作用
（参数列表不同的重载在 dex 里是合法的）。

方法名/签名是从 .so 的 .rodata 里按相邻字符串挖出来的（见 so_pairs.py）。
"""

from __future__ import annotations

from typing import Dict, List, Tuple
import os
# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402




# (方法名, 参数, 返回类型, 是否 static)
Method = Tuple[str, str, str, bool]

_CTX = "Landroid/content/Context;"
_STR = "Ljava/lang/String;"
_MAP = "Ljava/util/Map;"
_ACT = "Landroid/app/Activity;"
_ACCT = "Lcom/tendcloud/tenddata/TDGAAccount;"
_ATYPE = "Lcom/tendcloud/tenddata/TDGAAccount$AccountType;"
_AGEN = "Lcom/tendcloud/tenddata/TDGAAccount$Gender;"


def _m(name: str, params: str, ret: str, static: bool = True) -> Method:
    return (name, params, ret, static)


NATIVE_STUBS: Dict[str, List[Method]] = {
    # ---------------- Bugly ----------------
    "com/tencent/bugly/cocos/Cocos2dxAgent": [
        _m("getContext", "", _CTX),
        _m("initCrashReport", _CTX + _STR + "Z", "V"),
        _m("initCrashReport", _CTX + _STR + _STR, "V"),
        _m("setUserSceneTag", _CTX + "I", "V"),
        _m("putUserData", _CTX + _STR + _STR, "V"),
        _m("removeUserData", _CTX + _STR, "V"),
        _m("setUserId", _STR, "V"),
        _m("setUserId", "I" + _STR + _STR + _STR + "Z", "V"),
        _m("postException", "I" + _STR + _STR + _STR + "Z", "V"),
        _m("setAppVersion", _STR, "V"),
        _m("setAppVersion", "I" + _STR + _STR, "V"),
        _m("setAppChannel", _STR, "V"),
        _m("setLog", "I" + _STR + _STR, "V"),
        _m("JSReport", _STR + _STR, "Z"),
        _m("setSDKPackagePrefixName", _CTX + _STR, "V"),
    ],
    # ---------------- TalkingData ----------------
    "com/tendcloud/tenddata/TalkingDataGA": [
        _m("init", _CTX + _STR + _STR, "V"),
        _m("onResume", _ACT, "V"),
        _m("onPause", _ACT, "V"),
        _m("onEvent", _STR + _MAP, "V"),
        _m("onEvent", _CTX + _STR + _MAP, "V"),
        _m("onKill", _ACT, "V"),
        _m("setVerboseLogDisable", "", "V"),
        _m("getDeviceId", _CTX, _STR),
    ],
    "com/tendcloud/tenddata/TDGAAccount": [
        _m("setAccount", _STR, _ACCT),
        _m("setAccountName", _STR, _ACCT),
        _m("setAccountName", _ATYPE, "V"),
        _m("setAccountType", _ATYPE, "V"),
        _m("setLevel", "I", "V"),
        _m("setLevel", _AGEN, "V"),
        _m("setGender", _AGEN, "V"),
        _m("setGender", _STR, _AGEN),
        _m("setAge", "I", "V"),
        _m("setGameServer", _STR, "V"),
        _m("setGameServer", _STR + _STR, "V"),
        _m("valueOf", _STR, _ATYPE),
    ],
    "com/tendcloud/tenddata/TDGAItem": [
        _m("onPurchase", _STR + "ID", "V"),
        _m("onUse", _STR + "I", "V"),
    ],
    "com/tendcloud/tenddata/TDGAMission": [
        _m("onBegin", _STR, "V"),
        _m("onBegin", _STR + _STR, "V"),
        _m("onCompleted", _STR, "V"),
        _m("onCompleted", _STR + _STR, "V"),
        _m("onFailed", _STR + _STR, "V"),
    ],
    "com/tendcloud/tenddata/TDGAVirtualCurrency": [
        _m("onChargeRequest", _STR + _STR + "D" + _STR + "D" + _STR, "V"),
        _m("onChargeSuccess", _STR, "V"),
        _m("onChargeSuccess", "D" + _STR, "V"),
        _m("onReward", "D" + _STR, "V"),
        _m("onReward", _STR + "D", "V"),
    ],
    # ---------------- cocosplay ----------------
    "com/chukong/cocosplay/client/CocosPlayClient": [
        _m("init", _ACT + "Z", "Z"),
        _m("isEnabled", "", "Z"),
        _m("isDemo", "", "Z"),
        _m("isNotifyFileLoadedEnabled", "", "Z"),
        _m("notifyFileLoaded", _STR, "V"),
        _m("notifyDemoEnded", "", "V"),
        _m("getGameRoot", "", _STR),
        _m("updateAssets", _STR, "V"),
        _m("fileExists", _STR, "Z"),
        _m("getContext", "", _CTX),
        _m("getClassLoader", "", "Ljava/lang/ClassLoader;"),
        _m("loadClass", _STR, "Ljava/lang/Class;"),
    ],
}

# 上面用到的枚举也得存在
EXTRA_ENUMS = {
    "com/tendcloud/tenddata/TDGAAccount$AccountType": [
        ("valueOf", _STR, _ATYPE, True),
        ("values", "", "[Lcom/tendcloud/tenddata/TDGAAccount$AccountType;", True),
    ],
    "com/tendcloud/tenddata/TDGAAccount$Gender": [
        ("valueOf", _STR, _AGEN, True),
        ("values", "", "[Lcom/tendcloud/tenddata/TDGAAccount$Gender;", True),
    ],
}
