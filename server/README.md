# 战场双马尾（Oppai / zcsmw）私服模拟器

Kuro Game《战场双马尾》v2.2.0 已经停服。这个项目用 **纯 Python（仅标准库）** 重新实现了
一份服务端，并配套一套客户端补丁，让 Android 模拟器上的**原版客户端**能重新跑起来。

> 仅供个人研究 / 存档 / 客户端逆向学习，请勿用于任何商业用途。
> 本项目不包含任何游戏素材或反编译出来的游戏代码。

---

## 目录

- [1. 成果总览](#1-成果总览)
- [2. 项目结构](#2-项目结构)
- [3. 快速开始](#3-快速开始)
- [4. 客户端补丁](#4-客户端补丁)
- [5. 协议全貌](#5-协议全貌)
- [6. jsc 反汇编器](#6-jsc-反汇编器)
- [7. 踩过的坑（重点）](#7-踩过的坑重点)
- [8. 当前状态与 TODO](#8-当前状态与-todo)

> **想先看全貌**：直接读 [`docs/overview.md`](docs/overview.md) ——
> 成果清单、系统分层、逆向结论、按症状查原因的坑表、待办、工具工作流都在那一份里。
> README 这份偏「怎么上手 + 细节索引」。

---

## 1. 成果总览

```
[客户端] 启动 → 公告 → 远程配置 → 版本校验 → 服务器列表 → 登录界面      ✅
[客户端] 点「开始游戏」→ 原生 SDK 登录（Java 层补丁，秒成功，无弹窗）    ✅
[客户端] oauth 换 token                                                ✅
[客户端] WebSocket 握手（DH + DES + HMAC）→ 登录成功                    ✅
[客户端] agent.getlogindata → 新号自动 agent.createplayer              ✅
[客户端] cb4AfterLogin → 31 个数据模块初始化                            ✅
[客户端] 切主场景 MainScene → 开场动画 → 主界面                          ✅
[玩法]   编成 / 上阵队伍（18 个初始士兵）                                ✅
[玩法]   任务 → 主线任务（12 条窗口，可领奖、会推进）                     ✅
[玩法]   战斗（自己重建的引擎 + 11 个补丁）                              ✅
```

整条链路全自动，装上补丁版 APK 启动即可，不需要任何手动操作。
副本关卡 / 扭蛋 / 培养 / 排行榜这些还没做，详见
[`docs/overview.md` §1 与 §7](docs/overview.md)。

**技术栈**：cocos2d-js v3.6 + SpiderMonkey 33.1.1（`libcocos2djs.so` 里能看到
`JavaScript-C33.1.1`），客户端逻辑编译成 `.jsc`（XDR 字节码，不加密但不留源码）。

---

## 2. 项目结构

```
game_server/
├── run.py                     服务端启动入口
├── gamesrv/
│   ├── config.py              端口 / 地址 / 版本号
│   ├── logx.py                日志 + 原始报文落盘（var/capture/*.jsonl）
│   ├── httpd.py               迷你 HTTP 框架（多端口、路由、全量记录）
│   ├── wsserver.py            迷你 WebSocket 服务端（RFC6455）
│   ├── session.py             WS 登录握手（DH 单位元 + DES）
│   ├── gameproto.py           业务请求 / 响应打包解包
│   ├── accounts.py            账号（登录即注册）
│   ├── store.py               玩家 / 主角 / 队伍 / 士兵 / 模块开启状态（含字段迁移）
│   ├── quests.py              主线任务（窗口推进 + 领奖状态机）
│   ├── repl.py                下发给客户端探针的命令队列
│   ├── crypto/des.py          标准 DES（已用客户端真实密文对拍验证）
│   ├── handlers/              业务路由（agent.* / player.* / quest.*，共 16 条）
│   ├── data/table_quest.json  从客户端抽出来的任务表
│   └── apps.py                cdn / gate / login / game 四个端口的实现
├── client/                    客户端补丁（全部在这里）
│   ├── patch.js               ★ 必须的适配：polyfill / 引导跳过 / 响应派发 / 各种兜底
│   ├── probe.js               诊断探针（--no-probe 打包时不含）：REPL / 日志 / 调用追踪
│   ├── modernize.py           现代化：minSdk/targetSdk、明文 HTTP、运行时权限
│   ├── PermissionHelper.smali 运行时权限申请
│   ├── ServerLoginRunnable.smali   原生登录回调（绕开 SDK 弹窗）
│   └── patch_smali.py         打 Java 层登录补丁
├── tools/                      ★ 工具索引见 tools/README.md
│   ├── jsc_disasm.py          ★ jsc 反汇编器（SM33.1.1 XDR 字节码）
│   ├── disasm_func.py         按函数名反汇编
│   ├── jsc_strings.py         ★ 只扒 atom（标识符）表，定位函数逻辑最快
│   ├── extract_client_tables.py  ★ 把客户端 table_* 抽成服务端 JSON
│   ├── csb_dump.py            解析 cocostudio .csb（动画区间 / 帧事件）
│   ├── gen_opcodes.py         从 Opcodes.h 生成操作码表（产物是 _opcodes_gen.py）
│   ├── build_apk.py           改 assets + apktool 打包 + 对齐 + 签名
│   ├── serve.py               服务端守护（run.py 挂了自动拉起）
│   ├── merge_dex.py           把 smali_classesN 并成单 dex
│   ├── repl.py                在游戏进程里执行任意 JS
│   ├── probe.py               重启客户端 + 批量执行 JS
│   ├── selftest_game.py       不开游戏也能自测业务协议
│   ├── bisect_init.py         逐模块二分，找把 JS 主线程卡死的那个
│   ├── shots.py               连续截图
│   ├── sdk_strip/             删掉没用的第三方 SDK（详见 4.0）
│   └── archive/               一次性脚本的历史存档（别再跑，但注释里全是当时的证据链）
│       ├── analyze.py             扫出游戏代码引用了哪些 SDK 类/方法
│       ├── gen_stubs.py           据此生成桩类
│       ├── native_stubs.py        .so 硬依赖的类的桩定义
│       ├── gen_native_stubs.py    生成原生依赖桩
│       ├── strip.py               删 smali + 装桩 + 清 manifest + 清 assets/lib
│       ├── manifest_clean.py      用 ElementTree 删 manifest 组件
│       ├── so_pairs.py            从 .so 挖 JNI 名字+签名
│       ├── js_class_refs.py       扫 JS 里 jsb.reflection 调的 Java 类名
│       └── find_orphans.py        找出宿主 SDK 删掉后变成孤儿的包
└── docs/
    ├── overview.md            ★ 全景总览（先看这份）
    ├── build.md               打包逻辑（为什么这么做）
    ├── protocol.md            协议逐项细节
    └── reverse-engineering.md 反汇编器原理 + 运行时探测手法
```

---

## 3. 快速开始

### 3.1 起服务端

```powershell
cd game_server
python tools\serve.py     # 守护进程，run.py 挂了会自动拉起（推荐）
# 或者前台跑：
python run.py -v
```

> ⚠️ 别用 `Start-Process python run.py` 直接拉 —— 这样起的子进程会被回收，
> 表现就是「服务器莫名其妙宕机了」。要么前台跑，要么用 `tools/serve.py`。

| 端口  | 作用 | 原来对应 |
|-------|------|----------|
| 18080 | CDN：远程配置、热更新清单、公告、探针控制接口 | `cdn.shuangmawei.net` |
| 10001 | 网关：服务器在线状态 | `114.55.66.97:16840` 一类 |
| 8080  | 登录：`/oauth/*` + WebSocket | 客户端里硬编码的 `OAUTH_HOST` |
| 10003 | 游戏业务：加密 POST | login 返回的 `gameServUrl` |

> **端口不能随便改**：客户端 `urlconfig.jsc` / `server.jsc` 里的 URL 是**原地字节替换**
> （jsc 里字符串带长度前缀，长度必须一致）：
> `cdn.shuangmawei.net`（19 字节）→ `<host>:<port>` 必须 19 字节，
> `http://114.55.66.97:16840`（25 字节）→ `http://<host>:<port>` 必须 25 字节。
> 换机器时改 `gamesrv/config.py` 的 `PUBLIC_HOST`，并用同样的端口位数重新打包。

### 3.2 打客户端补丁

> 每一步「为什么这么做」见 [docs/build.md](docs/build.md)。

```powershell
# 0) 准备：原版 APK、apktool 解包目录、JDK、Android build-tools
#    路径可用 GS_APK_SRC / GS_APK_DIR / GS_JAVA_HOME / GS_BUILD_TOOLS 覆盖

# 1) Java 层补丁（只做一次）
python client\patch_smali.py

# 2) 重新编译 dex（只重打 dex，不动资源）
cd <apktool 解包目录>
apktool.bat b . --no-apk --no-crunch        # 产物在 <解包目录>\build\apk\classes*.dex

# 3) 打 URL 补丁 + 注入 patch.js/probe.js + 注入 dex + 重新签名
cd game_server
python tools\build_apk.py --host <主机IP>          # 带探针（开发和排障用）
python tools\build_apk.py --host <主机IP> --no-probe   # 正式包：只带 patch.js

# 4) 安装
adb install -r -d E:\code\apk\work\zcsmw-mod-signed.apk
```

### 3.3 不开游戏自测服务端

```powershell
python tools\selftest_game.py
```

自己按客户端格式打包加密请求直接打服务端，验证「加解密 + 路由 + code=200」。
改服务端时不用每次都装 APK 起游戏。

---

## 4. 客户端补丁

分 **Java(smali) 层**、**注入的明文 JS（patch.js / probe.js）**、**现代化/精简** 三部分。
哪些补丁是「必须的适配」、哪些只是诊断，见
[`docs/overview.md` §5](docs/overview.md)。

### 4.0 现代化 / 精简 / 删 SDK

原版 APK 是 2015 年的东西：**`minSdkVersion: 9`（Android 2.3）而且根本没有
`targetSdkVersion`**（默认取 minSdk），68 个组件里绝大多数是已经停服的
百度 / 微博 / 推送 / Bugly / TalkingData SDK。

```powershell
# 0) 先备份！
copy zcsmw.apk backup\zcsmw-original.apk

# 1) 现代化（manifest / apktool.yml / 运行时权限）
python client\modernize.py

# 2) 删掉没用的第三方 SDK（会自动生成桩类 + 清理 manifest）
python tools\sdk_strip\analyze.py --json tools\sdk_strip\needed.json
python tools\sdk_strip\strip.py
python tools\sdk_strip\gen_native_stubs.py

# 3) 改 assets + apktool 打包 + 签名（一步搞定）
python tools\build_apk.py --host <主机IP>
```

#### 删掉了什么

`tools/sdk_strip/analyze.py` 的 `STRIP_PREFIXES` 里列了全部要删的包，
**46.6 MB smali**：

```
com/baidu(22.0) com/duoku(5.6) com/tencent(6.2) com/squareup(3.0) com/unionpay(2.4)
com/sina(2.1) com/alipay(1.4) com/tendcloud(1.4) com/quicksdk(0.9) com/ta(0.5)
com/slidingmenu(0.3) com/qq(0.3) com/gametalkingdata(0.2) com/qk(0.1)
com/talkingdata com/jg com/chukong com/kurogame com/UCMobile com/ut
```

manifest 里删掉 **66 个组件 + 26 个权限**（`ReadSms` / `SendSms` / `ReadContacts` /
`CallPhone` / `Camera` 之类全是 SDK 要的）。dex 从 **7.45 MB 降到 1.7 MB**。

#### 删 SDK 的三个坑（都是 JNI 的锅）

删 SDK 不只是删文件 —— 有三类代码会**按名字引用**被删的类，缺一个就崩：

**坑 1：游戏自己的 Java 代码直接调 SDK API**

`AppActivity` / `GameApplication` / `QuickAdapter` / `GameShare` / `XGAdapter` 一共
引用了 34 个 SDK 类。`analyze.py` 把这些引用（类型 / 方法签名 / 父类 / 接口 /
static 还是 virtual）全扫出来，`gen_stubs.py` 据此生成**空实现桩类**。

* `getInstance()` → 生成单例，返回非 null（否则 `invoke-virtual` 直接 NPE）
* 被 `implements` 的 → 生成 interface
* `QuickSdkApplication` / `QuickSdkSplashActivity` → 父类换成 Application / Activity
* **必须按 `invoke-static` / `invoke-virtual` 区分**，否则会
  `IncompatibleClassChangeError: expected static but found virtual`

**坑 2：`SplashActivity` 靠 SDK 基类回调才启动**

```java
SplashActivity extends QuickSdkSplashActivity
  onSplashStop() { startActivity(AppActivity); finish(); }
```

原来由 SDK 闪屏播完后回调 `onSplashStop()`。桩里如果只继承 Activity 不回调，
App 会**永远卡在闪屏**（界面全白、logcat 什么都没有）。桩里要在 `onCreate` 里直接回调一次。

**坑 3：`libcocos2djs.so` 里硬编码了 SDK 类名**

原生层 cocos2d-x 的 jsb 绑定会 `FindClass` 这些类，找不到就
`JNI DETECTED ERROR: java_class == null` → SIGABRT（连 Java 异常都看不到）：

```
com/tencent/bugly/cocos/Cocos2dxAgent     Bugly
com/tendcloud/tenddata/TalkingDataGA      TalkingData
com/tendcloud/tenddata/TDGAAccount / TDGAItem / TDGAMission / TDGAVirtualCurrency
com/chukong/cocosplay/client/CocosPlayClient
```

从 `.so` 的 `.rodata` 里按**相邻字符串**把「方法名 + JNI 签名」挖出来
（`so_pairs.py`），然后**把所有出现过的重载都定义上** ——
JNI 只按 (名字, 签名) 查，多定义几个参数列表不同的重载没有副作用。
见 `tools/sdk_strip/native_stubs.py` + `gen_native_stubs.py`。

**坑 4：游戏的 R 类是动态解析资源 ID 的**

`com/cm/zcsmw/baidu/R$*.smali` 的 `<clinit>` 全部调
`com.quicksdk.apiadapter.baidu.ActivityAdapter.getResId(name, type)`
（共 3392 处）。所以这个类不能删，要用标准 API 重新实现：

```java
public static int getResId(String name, String type) {
    return AppActivity.instance.getResources()
        .getIdentifier(name, type, AppActivity.instance.getPackageName());
}
```

#### 效果

| 项 | 原版 | 现在 |
|---|---|---|
| smali（删掉） | — | **-46.6 MB** |
| dex | 7.45 MB | **1.7 MB** |
| manifest 组件 | 68 | **2** |
| 权限 | 34 | **8** |
| APK | 551.6 MB | **545.3 MB** |

APK 只小了 6.33 MB —— 因为 540 MB 是游戏资源（png/mp3），SDK 那点代码本来就不占体积。
**删 SDK 的真正意义是**：没有死代码、没有向已停服服务器发请求的后台服务
（原来还有个 `com.cm.zcsmw.baidu:bdservice_v1` 百度推送进程）、少了 26 个用不上的隐私权限。

| 项 | 改动 |
|---|---|
| `minSdkVersion` | `9` → `21` |
| `targetSdkVersion` | 无 → **`23`**（见下面的警告） |
| `usesCleartextTraffic` | `true`（模拟服是明文 HTTP） |
| `extractNativeLibs` | `true` |
| `requestLegacyExternalStorage` | `true` |
| 运行时权限 | 新增 `client/PermissionHelper.smali`，在 `AppActivity.onCreate` 注入一次申请 |

> #### ⚠️ targetSdk 必须停在 23，不能再往上提
>
> 这个 2016 年的 QuickSDK / 百度 SDK 有两个硬性上限，都踩过：
>
> | targetSdk | 结果 |
> |---|---|
> | 9（原版） | 一切正常，但现代 Android 会拦安装 / 弹「为旧版 Android 打造」 |
> | **23** | ✅ **能用**：装得上、没有旧版警告、SDK 也正常 |
> | 27 | ❌ 能启动，但百度 SDK 初始化失败并不断重试 → **屏幕一直闪** |
> | 28+ | ❌ 起不来：隐藏 API 限制 |
>
> **24 起：`MODE_WORLD_READABLE` 会抛异常**
>
> ```
> E/channel.baidu: at com.quicksdk.apiadapter.baidu.SdkAdapter.init
> E/channel.baidu: at com.quicksdk.Sdk.init
> D/BaseLib.BIN  : =>BIN onFailed message = MODE_WORLD_READABLE no longer supported
> ```
>
> 百度 SDK 用 `MODE_WORLD_READABLE` 写文件，Android 7.0（API 24）起
> targetSdk >= 24 的应用调用它会直接抛 SecurityException。
> 初始化失败 → 不断重试 → 每次重试 show/dismiss 一次 QuickSDK 的
> 小 Loading Dialog（`com.quicksdk.utility.g`，居中 69×69 的 APPLICATION 窗口），
> 表现出来就是**屏幕一直在闪 + 中间一个转圈**。
>
> **怎么定位的**：`screencap` 连抓 10 帧，发现内容帧和空白帧交替（约各一半）；
> 再 `dumpsys window windows` 看到一个 **69×69 居中的 APPLICATION 窗口**
> （另一个同款窗口处于 `EXITING`）—— 这就是 Java 层的那个转圈。
>
> **28 起：隐藏 API 限制**
>
> QuickSDK 靠反射调隐藏 API 初始化，异常被它自己的 try/catch 吞掉：
>
> ```
> QuickSdkApplication.onCreate
>   -> com.quicksdk.utility.a.a() 返回 null
>   -> IAdapterFactory.adtActivity() 空指针
>   -> FATAL: Unable to create application ...GameApplication
> ```
>
> **为什么 23 是甜点**：`< 24` 不受 MODE_WORLD_READABLE 限制；`< 28` 不受隐藏 API 限制；
> `>= 23` 就能装在现代 Android 上、且不再弹「为旧版 Android 打造」。
> 代价是危险权限变成运行时申请 —— 已经用 `PermissionHelper.smali` 补上了。
>
> 想再往上提，只能先把 QuickSDK / 百度 SDK 整套删掉。

`tools/build_apk.py` 的 `DROP_ASSETS` 默认剔除：

```
assets/res/adimage/        广告图（广告服务早已下线）
assets/res/adcolumn/
assets/bdpwxpayplugin.apk  百度支付插件
```

**精简效果**：551.6 MB → 547.6 MB（-3.95 MB）。实话实说，**能安全砍的只有这么多** ——
`assets/res` 占 540 MB，里面是 `.png` 289.6 MB + `.mp3` 178.6 MB，
都是游戏必需资源。想大幅瘦身只能上有损压缩
（pngquant 量化 + mp3 降码率，预估能到 ~280 MB，但立绘会有色带、语音音质下降）。

### 4.1 Java 层：让 SDK 登录直接成功（关键）

原来的调用链：

```
JS jsb.reflection.callStaticMethod(QuickAdapter, "login")
  -> QuickAdapter.login()                       (smali)
  -> AppActivity.login() -> runOnUiThread(AppActivity$8)
  -> QuickSDK.User.login(activity)
  -> 渠道 SDK(百度) -> com.baidu.platformsdk.LoginActivity 弹窗
  -> 登录成功后原生 evalString('quicksdk.sdkLoginCallback(1, "uid", "token")')
```

QuickSDK / 百度服务器早就下线，弹窗永远登不进去。`client/patch_smali.py` 把
`QuickAdapter.login()` 换成：

```smali
.method public static login()V
    .locals 4
    sget-object v0, Lorg/cocos2dx/javascript/AppActivity;->instance:Lorg/cocos2dx/javascript/AppActivity;
    new-instance v1, Lorg/cocos2dx/javascript/ServerLoginRunnable;
    const-string v2, "emulator"
    const-string v3, "emulator-token"
    invoke-direct {v1, v2, v3}, Lorg/cocos2dx/javascript/ServerLoginRunnable;-><init>(Ljava/lang/String;Ljava/lang/String;)V
    invoke-virtual {v0, v1}, Lorg/cocos2dx/lib/Cocos2dxActivity;->runOnGLThread(Ljava/lang/Runnable;)V
    return-void
.end method
```

配套新增 `client/ServerLoginRunnable.smali`，仿照 `AppActivity$6$1`
（QuickSDK 真正登录成功时的回调）拼出并 eval：

```js
quicksdk.sdkLoginCallback(1, "emulator", "emulator-token")
```

这样点「开始游戏」走的还是游戏自己的原始流程，只是原生登录瞬间成功、弹窗不再出现。

### 4.2 注入的明文 JS：`client/patch.js` + `client/probe.js`

明文 JS，通过改 `project.json` 的 `jsList` 注入（jsc 不存在时会回退到明文，实测可行）。
拆成两个文件：

* **`patch.js` —— 必须的适配**，正式包也要带（`tools/build_apk.py` 默认打包）
* **`probe.js` —— 诊断探针**，`--no-probe` 时不打包

`patch.js` 做的事：

| 补丁 | 说明 |
|------|------|
| `ccui.helper.seekNodeByName/ByTag` | 游戏那版引擎有、v3.6 没有，纯 JS polyfill |
| `ActionTimeline.setLastFrameCallFunc` 包装 | 同上（引擎层已根治，这层是保险） |
| `ccui.WebView` polyfill | v3.6 没这个绑定；公告是私服自己的 HTML，抓下来剥标签用 `ccui.Text` 渲染 |
| 新手引导跳过 | 引导层会吃掉**所有**菜单点击，私服数据下它会永远卡住 |
| `Gacha.getGachaFullInfo` 兜底 | 缺扭蛋配置时抛异常会把 `initUserData` 后半段全打断 |
| 数据模块构造容错 | 某个模块数据没对齐时不连累整体 |
| `initUserData` 兜底 | 无论如何保证 `player._moduleState` 建出来（否则主界面黑屏） |
| `RESP-DISPATCH` 响应派发 | 客户端 `responseConfig` 在这套引擎上根本没被派发，自己补一层 |
| LGL-GUARD | 视频层清理 + 最后一帧回调幂等 |

`probe.js` 做的事：

| 功能 | 说明 |
|------|------|
| `REQ / PACK / UNPACK / WS / GAMELOG` 日志 | 网络层调用、加密函数十六进制输入输出、WebSocket 生命周期、游戏自己的 `cc.log` 全转发到 logcat |
| `SCHEMA` 探测 | 用 `Proxy` 记录客户端读了响应里的哪些字段 |
| **REPL** | 轮询 `<cdn>/hook/poll` 执行 JS，结果 POST 回 `/hook/result` —— 等于在游戏进程里开了个控制台 |
| 调用序列追踪 | 登录期把 `Player` / `Gacha` / `guideManager` 的调用逐个打出来，出错直接给 stack |
| 两个开关 | `AUTO_AFTER_LOGIN` / `AUTO_CLICK_START`（默认都关，手动点） |

> ⚠️ **探针的第一原则：只包一层，不要重写。**
> 早先的版本把 `server.request` 整个重写了（因为 `reqPack` 依赖登录时写入的闭包变量），
> 结果把客户端原生的响应派发整条路绕掉了，害得「服务端推数据」全部失效 ——
> 这类错误非常难查，因为它表现为「服务端怎么改都没用」。

### 4.3 其它重定向

`tools/build_apk.py` 会做（全部是**原地等长字节替换**）：

| 文件 | 替换 |
|------|------|
| `assets/srcex/urlconfig.jsc` | `cdn.shuangmawei.net` → `<host>:18080` |
| `assets/src/util/server.jsc` | `http://114.55.66.97:16840` → `http://<host>:8080` |
| `assets/src/data/share.jsc` | 分享服务地址 → 同上 |
| `assets/src/patch/project.manifest` | 热更新地址 |

---

## 5. 协议全貌

细节（含反汇编证据）见 [`docs/protocol.md`](docs/protocol.md)，这里只列骨架。

### 5.1 远程配置（CDN）

`GET /android/v2.2.0/config/config.txt`，客户端 `getRemoteConfig(key)` 读**顶层字段**：

```jsonc
{
  "version": { "2.2.0": { /* 键必须是 op.appVersion */ } },
  "update":  { "code": 0, "msg": "" },     // code != 0 会弹窗拦下
  "servers": [ /* 服务器列表 */ ],
  "table_dictionary": { /* 覆盖本地文案 */ }
}
```

### 5.2 登录链路

```
GET  http://<gate>/get_login?key=<logindKey>      → gamedStatus / loginPort / port
GET  <OAUTH_HOST>/oauth/request_token?account=..&password=..
                                                  → {code, msg, data:{token, ...}}
WS   ws://<host>:<loginPort>                      握手（见下）
POST http://<gameServUrl>/                        业务（见下）
```

WebSocket 握手：

```
S -> C   base64(欢迎包)                      触发客户端 onmessage 开始握手
C -> S   base64(clientKey)                   dhExchange(randomKey())
S -> C   base64(serverKey)                   服务端 DH 公钥
C -> S   base64(hmac64(hashKey(seed#clientKey), secret))
C -> S   base64(desEncode(secret, utf8(登录信息 JSON)))
S -> C   base64(desEncode(secret, utf8({code,session,gameServUrl,userId,data})))
```

真实抓包：

```
CRYPT base64Decode(e30=)                            => 7b7d              "{}"
CRYPT randomKey()                                   => 7f813b99976732bd
CRYPT dhExchange(7f813b99976732bd)                  => 6e58ce797a29f3f4
CRYPT hashKey(7b7d23 6e58ce797a29f3f4)              => d784c7c8e1d79a2c
CRYPT base64Decode(AQAAAAAAAAA=)                     => 0100000000000000
CRYPT dhSecret(0100000000000000, 7f813b99976732bd)  => 0100000000000000
CRYPT hmac64(d784c7c8e1d79a2c, 0100000000000000)    => d3684ec0fa8d0f4a
CRYPT desEncode(0100000000000000, <319字节登录JSON>) => <320字节密文>
```

#### DH 的「单位元」技巧

客户端的 `crypt.dhExchange` / `dhSecret` 是自研实现（`biPowMod` + `dhTransTo64`）。
实测它满足正常 DH 群性质，并且存在单位元：

```
X = dhExchange(00 00 00 00 00 00 00 00) = 01 00 00 00 00 00 00 00
dhSecret(X, *) == dhSecret(*, 00…00) == X
```

服务端把自己的公钥设成 `X`，**双方共享密钥必然等于 `X`**，完全不需要知道 p / g。
经典 small-subgroup 思路，对单机模拟服够用且省事。

#### DES

`crypt.desEncode(key, msg)` **就是标准 DES-ECB**，补位是 ISO/IEC 9797-1 method 2
（先补 `0x80` 再补 `0x00`）。`gamesrv/crypto/des.py` 已用标准测试向量
（`133457799BBCDFF1` / `0123456789ABCDEF` → `85E813540F0AB405`）
和真实客户端密文往返双向验证。

### 5.3 业务协议（HTTP POST）

```js
// reqPack(route, msg, reqId)
data = crypt.desEncode(key, crypt.utf16To8(JSON.stringify({route, msg, reqId})))
body = crypt.base64Encode(data)

// resUnpack(data, key)
if (data.indexOf('"code":') >= 0) return JSON.parse(data)   // 明文错误包
return JSON.parse(crypt.utf8To16(crypt.desDecode(key, crypt.base64Decode(data))))
```

**⚠️ 业务成功码是 `200`，不是 `0`。** 反汇编 `dataManager.playerLogin/</<`：

```js
var code = data.code;
if (code === 200) cb4AfterLogin(null, data.data);
else              cb4AfterLogin(data, null);   // ← 把响应当错误 → 弹「温馨提示」显示 JSON
```

`Player.updateGuideMark` 的回调同样判断 `data.code == 200`，不是 200 就会**无限重发**。

### 5.4 `agent.getlogindata` 的模块 key

**key 名不是模块名**！从 `dataManager.initUserData` 反汇编读出来的映射：

| data 里的 key | 构造的类 | dataManager 属性 |
|---|---|---|
| `player` | `Player` | `player` |
| `instance` | `Instance` | `instance` |
| `item` | `Bag` | `bag` |
| `char` | `CharCenter` | `character` |
| `gacha` | `Gacha` | `gacha` |
| `mail` | `Mailbox` | `mailbox` |
| `actquest` | `ActQuestCenter` | `actQuestCenter` |
| `quest` | `QuestCenter` | `questCenter` |
| `favor` | `FavorCenter` | `favorCenter` |
| `favorevent` | `FavorEventCenter` | `favorEventCenter` |
| `friend` | `Friend` | `friend` |
| `exchange` | `ExchangeCenter` | `exchangeCenter` |
| `talents` | `TalentCenter` | `talentCenter` |
| `sign` | `SignCenter` | `signCenter` |
| `shop` | `Shop` | `shop` |
| `arena` | `ArenaCenter` | `arenaCenter` |
| `rank` / `score` | `Rank` / `Score` | `rank` / `score` |
| `society` / `societyclg` | `Society` / `SocietyClg` | 同名 |
| `boss` | `BossCenter` | `bossCenter` |
| `chat` / `detect` / `medal` | `Chat` / `Detect` / `Medal` | 同名 |
| `equipment` | `EquipmentCenter` | `equipmentCenter` |
| `share` | `Share` | `share` |
| `subareaachievement` | `SubareaAchievement` | `subareaachievement` |
| `consumeactivity` | `ConsumeActivity` | `consumeActivity` |
| `diary` | `Diary` | `diary` |
| `friendsupport` | `FriendSupport` | `friendSupport` |
| `novicequest` | `NoviceQuestCenter` | `noviceQuestCenter` |

另外 `data.agent` **必须直接带 `timeSec` / `timezoneOffset`**（`initUserData` 第一句是
`syncTime(data.agent, ...)`，读的是 `data.agent.timeSec` 而不是 `data.agent.timeObj.timeSec`），
还要带 `msgCenterAddr` / `worldChatLimit`（给 `chat.init` 用）。

---

## 6. jsc 反汇编器

`.jsc` 是 SpiderMonkey 33.1.1 的 XDR 字节码，不加密、但不保留源码
（`Function.prototype.toString()` 只给 `[sourceless code]`）。
既然格式固定，就自己写了一个解析器：

```powershell
python tools\gen_opcodes.py                        # 从 Opcodes.h 生成操作码表
python tools\jsc_disasm.py <file.jsc> [起] [止]     # 反汇编
python tools\disasm_func.py <file.jsc> --list      # 列出所有函数
python tools\disasm_func.py <file.jsc> cb4AfterLogin
```

反汇编出来的 `dataManager.cb4AfterLogin`：

```
 0  this / 1 getarg 0 (err) / 4 not / 5 setprop "isLogin"      // this.isLogin = !err
11  getarg 0 / 14 not / 15 ifeq -> 67                          // if (err) 跳过初始化
20  name "server"    / 26 callprop "syncLastServer"
36  this / 38 callprop "initUserData" / 44 getarg 1 / 47 call 1
51  name "gameEvent" / 57 callprop "onLogin"
67  getarg 2 (cb) / 70 and -> 89                               // if (cb)
76  getarg 2 / 79 undefined / 80 getarg 0 / 83 getarg 1 / 86 call 2
90  retrval
```

原理和踩的坑写在 [`docs/reverse-engineering.md`](docs/reverse-engineering.md)。
几个要点：

1. `magic = 0xb973c02c = 0xb973c0de - 178`，和 `FIREFOX_33_1_1_RELEASE` 的
   `vm/Xdr.h` 完全一致，源码可从
   `https://hg-edge.mozilla.org/releases/mozilla-release/raw-file/FIREFOX_33_1_1_RELEASE/js/src/...` 拉。
2. 字段顺序：`nargs nblocklocals nvars length prologLength version natoms nsrcnotes
   nconsts nobjects nregexps ntrynotes nblockscopes nTypeSets funLength scriptBits`
   → `bindings → ScriptSource → sourceStart/End → lineno/column/nslots/staticLevel
   → code[length] → notes → atoms → consts → objects → ...`
3. `scriptBits & (1<<12)`（OwnSource）时中间会插一段 `ScriptSource::performXDR`。
4. **真正的代码在嵌套 lambda 里**：`data/*.jsc` 外层脚本只有 26 字节
   （`var X = (function(){...}).call()`），全部逻辑在 `objects[0]` 那个函数里，要递归解析。
5. **字节码立即数是大端序**（最坑的一点）。
6. 操作码表从 `Opcodes.h` 自动生成（229 个），注意 `JOF_TMPSLOT2/3` 里带数字。

---

## 7. 踩过的坑（重点）

### 7.1 服务端 / 协议

| 坑 | 现象 | 原因 |
|---|---|---|
| **成功码是 200 不是 0** | 引导进度无限重发、登录弹 JSON 提示框 | 客户端判断 `data.code === 200` |
| **`data.agent` 要直接带 `timeSec`** | 进登录后 JS 主线程死循环（点哪都没反应） | `syncTime(data.agent)` 读 `timeSec`，拿不到就是 `NaN` |
| **模块 key 不是模块名** | `new Bag(undefined)` → 后面全崩 | `new Bag(data.item)`、`new Mailbox(data.mail)`…… |
| **时间字段必须是字符串** | `Player.ctor` 崩在 `getTimeStr`/`getDateStr` | `actionPointTime` / `createTime` 要 `"YYYY-MM-DD HH:mm:ss"` |
| **URL 原地等长替换** | APK 打包报"长度不一致" | jsc 字符串带长度前缀，`cdn.shuangmawei.net` 必须换 19 字节 |
| **WebSocket 要回显子协议** | 客户端 `onopen` 不触发 | 请求里带 `Sec-WebSocket-Protocol`，不选一个客户端就会主动断开 |
| **`onSuccCb` 靠第一条消息触发** | 连上了但什么都不发生 | 服务端先发一条 base64 欢迎包，客户端 `onmessage` 才开始握手 |
| **请求体前面还有一段 session** | 点开始游戏弹 `温馨提示 {"code":1,"msg":"bad request"}` | 真实请求体是 `base64(" " + sessionId)` + `base64(des(payload))` 两段拼的，不摘掉就解不出密文 |
| **`get_or_create_player()` 返回的是临时副本** | 任务领奖「累计 1 条」永远不涨，反复刷新顶上还是同两条 | 改完必须 `store.save_player(player)` 写回，否则请求一结束改动就没了 |

### 7.2 客户端 / 逆向

| 坑 | 现象 | 解决 |
|---|---|---|
| **原生 `new XMLHttpRequest()` 发不出去** | 探针的 XHR 永远不回调 | 用 `cc.loader.getXMLHttpRequest()` |
| **GET 的 body 传数字会静默卡住** | 轮询请求根本没发出 | body 必须 `String(...)` |
| **`jsb.fileUtils.writeStringToFile` 不存在** | 探针写文件失败 | 改用 logcat |
| **游戏自己的 `cc.log` 不进 logcat** | 看不到客户端内部报错 | 探针包一层 `console.log`/`cc.log` 转发（`GAMELOG`） |
| ⚠️ **探针不要重写 `server.request`** | 「服务端推数据」全部失效（任务领奖成功但不刷新），改服务端怎么改都没用 | 只能**包一层**打日志，行为交给原实现。原生实现里带着 `responseConfig` 派发 |
| **`responseConfig` 压根没被派发** | 响应里出现 `quest`/`player` 也不会 `updateByServer` | 客户端 `patch.js` 里自己补了一层 `RESP-DISPATCH` |
| **一个士兵构造失败会丢整份列表** | 编成里一个兵都没有 | `CharCenter` 是整体 try；只能用实测 `new` 得出来的 key（`lfcz01` 会抛 `row is undefined`） |
| **`window.op` 是共享命名空间** | 猜不出远程配置的 key | 给 `op` 套 `Proxy` 记录属性访问 |
| **返回值不是简单类型** | `version` 不能直接比 | 用 `Proxy` + `Error().stack` 定位到 `version[appVersion]` |
| **jsc 立即数是大端序** | 反汇编全乱 | 所有字节码立即数按大端读 |
| **`initUserData` 中途抛异常会连累一大片** | 黑屏 / 引导乱走 / 某个模块是半成品 | 先把异常和调用序列打出来（探针的 `hookInitUserData` + `hookInterfaceTrace`），别猜 |

> 完整版按症状查原因的表在 [`docs/overview.md` §6](docs/overview.md)。

---

## 8. 当前状态与 TODO

### 已完成

- [x] 服务端：CDN / 网关 / oauth / WebSocket 登录 / 加密业务路由（16 条）
- [x] DES 用标准实现复刻并双向验证；DH 用单位元技巧绕过
- [x] Java 层去掉百度登录弹窗；删掉 46.6MB 没用的第三方 SDK
- [x] 全自动登录 → 主场景 → 新手开场动画 → 主界面
- [x] **引擎源码重建**（cocos2d-js v3.6 + 11 个补丁），战斗可完整跑完
- [x] jsc 反汇编器（含递归解析嵌套函数）+ atom 表提取器 + 运行时 REPL 探针
- [x] **编成 / 上阵队伍**（18 个初始士兵，前锋/中卫/后卫各 6）
- [x] **主线任务**（12 条窗口 + 领奖 + 窗口推进 + 刷新）
- [x] 文档：`docs/overview.md`（全景）/ `protocol.md` / `reverse-engineering.md` / `build.md`

### 待办

按「卡不卡住玩法」排序：

1. [ ] **副本 / 关卡（`instance`）** —— 主线任务的条件全是「通关 N 次」，
       没有关卡就没法自然完成。关卡表同样可以抽（`table_level` / `table_chapter`），
       做法见 `tools/extract_client_tables.py`。
2. [ ] **扭蛋 / 抽卡** —— 缺 `gachaMasterList` 这类运营配置（客户端表里也没有），
       现在只靠空壳兜底保证不崩。`GUIDE_GACHA_KEY = 1002`（`GACHA_KEYS.GEM`）。
3. [ ] **培养（天赋）** —— `TalentCenter` 构造抛 `this._talentTypes[v.type] is undefined`。
       已试过 7~8 种数据形状都没在 REPL 里复现（手动 `new TalentCenter(data.talents)` 是好的），
       怀疑 `initUserData` 传进去的不是 `data.talents`；下一步是在探针里把构造参数打出来再登一次。
4. [ ] **日常 / 成就任务** —— 只做了主线（`QUEST_TYPE.NORMAL = "2"`），
       日常（`1`，246 条）/ 成就（`3`，91 条）还没接。
5. [ ] **其余 stub 路由** —— `rank.*` / `exchange.*` / `boss.*` / `shop.*` / `mail.*` 等只回空 data。
   补法：看对应模块的 `updateByServer` / `*Cb` 读哪些 key：

   ```powershell
   python tools\disasm_func.py <assets>\src\data\rank.jsc updateByServer
   python tools\jsc_strings.py <assets>\src\data\rank.jsc | findstr /i update
   python tools\disasm_func.py <assets>\src\manager\datamanager.jsc --list
   ```

6. [ ] `hashKey` / `hmac64` 还没复刻（登录靠单位元绕过；要校验 `etoken` 才需要）
7. [ ] 自研 DH (`dhExchange`/`dhSecret`) 的完整算法也可以直接反汇编还原
- [ ] 自研 DH (`dhExchange`/`dhSecret`) 的完整算法也可以直接反汇编还原
- [ ] `player.teams` 目前只给 5 支空队伍 + 主角 `hadf` + 机甲 `madflj`
      （key 取自 `table_hero` / `table_mecha`），战斗相关玩法需要继续补

---

## 9. 免责声明

- 本项目**不包含**任何游戏素材、`.jsc` 字节码、反编译产物或 APK。
- 所有代码均为自行编写，仅通过公开的 SpiderMonkey 源码与运行时观察还原协议。
- 请仅用于个人学习研究，不要传播游戏素材或用于商业用途。
