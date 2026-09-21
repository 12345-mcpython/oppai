# 从零复刻 —— 完整步骤

> 这份是**照着做就能跑起来**的操作手册。每一步都给了「验证点」，
> 卡住了先看那一步的验证点，再翻对应文档。
>
> 想先理解原理再动手：本仓库一共只做三件事 ——
> **① 给原版客户端打补丁  ② 用源码重建引擎 `.so`  ③ 自己写一个服务端**。
> 这三件事分别对应 §4 的 Step 2 / Step 4 / Step 3。

---

## 0. 复刻完之后你会得到什么

* 一台**模拟器**上跑起来的原版《战场双马尾》v2.2.0 客户端
* 一个自己写的服务端（纯 Python 标准库），**72 条业务路由**：
  登录 / 编成 / 军士培养 / 关卡战斗 / 主线任务 / 商店 / 邮件 /
  天赋 / 装备 / 好感度（宿舍）/ 宿舍事件 / 守护灵 / 设置助战
* 一个浏览器调试台（`http://127.0.0.1:18080/devtools`）：流量重放、
  在游戏进程里跑 JS、改存档、看日志、查客户端表
* 一整套逆向工具：`.jsc` 反编译 / 反汇编 / 原子表提取 / 运行时探针

游戏内**能玩到**的部分和**没做**的部分，见
[`server/docs/differences.md`](server/docs/differences.md) §C。

---

## 1. 前置环境

| 需要 | 版本 | 说明 |
|---|---|---|
| **Windows** | 10 / 11 | 构建脚本是 PowerShell（`.ps1`）。**Linux 没试过**，理论上除了打 APK 那几步都能跑 |
| **PowerShell** | 5.1+ | 系统自带 |
| **Python** | **3.10+** | 服务端**纯标准库**，不需要 pip 装任何东西 |
| **JDK** | **17** | apktool / zipalign / apksigner 都用它 |
| **Android SDK build-tools** | **36.0.0**（别的版本大概率也行） | 提供 `zipalign` / `apksigner` |
| **adb** | 任意 | 装包、截图、看 logcat |
| **Android NDK** | **r10e**（**版本必须对**） | **只有 `-Engine` 重编引擎才需要**。别的 NDK 版本编不过（工具链/头文件差异） |
| **模拟器** | **MuMu，Android 9，x86_64，已 root** | 见下面「为什么是 MuMu」 |

环境变量（都给默认值，不改也能跑，放在别的盘就设一下）：

```powershell
$env:GS_JAVA_HOME   = "D:\java\zulu17.68.203-ca-jdk17.0.20.1-win_x64"
$env:GS_BUILD_TOOLS = "D:\Android\android-sdk\build-tools\36.0.0"
$env:GS_ADB         = "D:\Android\android-sdk\platform-tools\adb.exe"
$env:GS_ADB_SERIAL  = "127.0.0.1:21503"
```

> **为什么是 MuMu / x86**：包里有 `lib/x86/` 时 Android 会原生跑；
> 只有 `armeabi` 的话会被 **houdini**（ARM→x86 翻译层）接管，实测会在
> 新手引导那段代码上被 houdini 自己 trap 掉，tombstone 里永远只有
> `/system/lib/libhoudini.so` 一帧 —— 排查时完全是噪音。
> 真机（ARM）不受影响，两种 ABI 同时打进包里就行。

---

## 2. 准备外部资源（**都不在仓库里**）

`.gitignore` 里逐条写了原因，简单说就是**版权**和**体积**。
请按下面的表准备，放到指定路径：

| # | 放哪 | 体积 | 从哪来 |
|---|---|---|---|
| 1 | `game/` | ~1.2 GB | **自备《战场双马尾》v2.2.0 的 Android APK**（见 §2.1），按 §4-Step 1 解包 |
| 2 | `engine/src/cocos2d-js/` | ~985 MB | **cocos2d-js v3.6** 源码（含 cocos2d-x 3.6 + js-bindings）。从 cocos2d 官方仓库 `cocos2d/cocos2d-js` 的 **v3.6** tag 取 |
| 3 | `engine/src/cocos2d-js/frameworks/js-bindings/cocos2d-x/external/` | ~70 MB | cocos2d-x **`v3-deps-47`** 第三方预编译库（官方 `cocos2d-x-3rd-party-libs-bin` 仓库）。缺它会报 `Cannot find module with tag 'freetype2/prebuilt/android'` |
| 4 | `engine/ndk/android-ndk-r10e/` | ~4.4 GB（解压后 ~1 GB 有效） | **Android NDK r10e**，Google 的 NDK 归档里找 `android-ndk-r10e-windows-x86_64.zip` |
| 5 | `script/apktool_3.0.3.jar` | 15 MB | **apktool 3.0.3**（Apache-2.0）。官方 releases 下载后改名成这个，`script\apktool.bat` 会自动找 `apktool_*.jar` |
| 6 | `engine/build/oppai-engine/Classes/protobuf-lite/`<br>`engine/build/oppai-engine/Classes/runtime/` | 2.2 MB | 从 cocos2d-js 的 **`js-template-runtime`** 抄：`templates/js-template-runtime/frameworks/runtime-src/proj.android/` 下的 `Classes/` |
| 7 | `engine/ref/libcocos2djs-original.so` | 18 MB | 从自备 APK 的 `lib/armeabi/libcocos2djs.so` 取出来（**只作参考**，用来对比符号/API） |
| 8 | `engine/ref/spidermonkey/` | — | 可选。SpiderMonkey 33.1.1 源码（Mozilla，MPL），需要查字节码格式时用 |

> ⚠️ **第 2/3/4/6 项的版本必须和上表一致** —— 引擎是靠版本号对上才能编的
> （`cocos2d-x 3.6` + `SpiderMonkey 33.1.1`，证据见
> [`engine/README.md`](engine/README.md)「版本确认」）。
>
> ⚠️ 第 1 项的**游戏素材和反编译产物的版权不属于本项目**，
> 只放在本地跑，**不要再分发**。

### 2.1 关于游戏 APK（占位）

原版 v2.2.0 APK **不在本仓库里，也不由本项目提供**。
自己找一份，然后把路径记下来 —— 后面 Step 1 会用到。

```
┌──────────────────────────────────────────────────────────────┐
│  游戏 APK 下载地址（占位，请自行替换成你手上的合法来源）：      │
│                                                              │
│      <GAME_APK_URL>                                          │
│                                                              │
│  要求：包名 com.cm.zcsmw.baidu，版本 2.2.0，                  │
│        APK 内 lib/ 下必须有 armeabi/libcocos2djs.so          │
└──────────────────────────────────────────────────────────────┘
```

拿到之后建议**留一份只读备份**（项目里对应的是 `game/original/`）：

```powershell
mkdir E:\code\zcsmw\game\original -Force
copy <你的APK> E:\code\zcsmw\game\original\zcsmw-original.apk
```

> ⚠️ **文件名必须是 `zcsmw-original.apk`**：`script\build_apk.py` 每次打包都要从它里面
> 恢复 4 个「带地址的文件」（默认的运行时改写路子靠这个保证每次打包都是干净的），
> 找不到会**直接报错中止**。要放别处就用环境变量指定：
> `$env:GS_ORIGINAL_APK = "D:\apk\oppai-2.2.0.apk"`。
>
> 解包目录是**工作副本**，后面几步会反复改它（删 SDK、改 assets、替换 `.so`）。
> 备份丢了就得重新找一份原版包。

---

## 3. 目录布局

跑完 §2 之后，仓库应该长这样（★ = 要自己准备的）：

```
E:\code\zcsmw\
├── build.ps1                    一键构建
├── README.md  REPRODUCE.md      本文
├── game\                        ★ apktool 解包目录（工作副本）
│   └── original\zcsmw-original.apk  ★ 原版 APK 备份（文件名别改，见 §2.1）
├── server\                      Python 服务端
├── script\                      工具脚本
│   └── apktool_3.0.3.jar        ★
├── engine\
│   ├── src\cocos2d-js\          ★ cocos2d-js v3.6
│   ├── ndk\android-ndk-r10e\    ★ NDK r10e
│   ├── ref\libcocos2djs-original.so  ★
│   └── build\                   引擎补丁脚本 + oppai-engine 工程
└── out\                         构建产物（自动创建）
```

---

## 4. 步骤

### Step 1 · 解包游戏 APK

```powershell
cd E:\code\zcsmw
script\apktool.bat d game\original\zcsmw-original.apk -o game -f
```

**验证点**：`game\` 下出现 `AndroidManifest.xml` / `apktool.yml` / `assets\` /
`smali*\` / `lib\armeabi\libcocos2djs.so` / `res\`。

```powershell
Test-Path game\assets\src\main.jsc          # 应该是 True
Get-ChildItem game\lib -Recurse -Filter *.so | Select-Object Name   # 有 libcocos2djs.so
```

> 顺手把第 7 项外部资源取出来：
> `copy game\lib\armeabi\libcocos2djs.so engine\ref\libcocos2djs-original.so`

---

### Step 1b · 客户端树的一次性处理（**只有重新解包过才需要跑**）

⚠️ **`build.ps1` 不含这几步**，但少了它们，从零解包出来的包**装不上 / 连不上服务端**：

```powershell
cd E:\code\zcsmw
# 顺序不能变。2026-09-20 从原版包逐步验证过：跑完得到的树和仓库里 game\ 一致
# （只差 ABI 选择和几个 apktool 中间产物；smali 文件数 134 = 134）。
python server\client\modernize.py            # 1) 补 <uses-sdk> / 明文 HTTP / 运行时权限申请
python script\sdk_strip\analyze.py --json script\sdk_strip\needed.json   # SDK 没动过可跳过
python script\sdk_strip\strip.py             # 2) 删渠道 SDK（内部会调 gen_stubs.py 重建桩类）
python script\sdk_strip\gen_native_stubs.py  # 3) 补 .so 硬依赖的桩类
python script\merge_dex.py                   # 4) smali_classes2 -> smali/（DROP 路径按合并后写的）
python server\client\patch_smali.py          # 5) ⚠️ 必须排在 strip.py 之后
python script\patch_js_debugger.py           # 6) 调试器 JS 换成明文（并删掉同名 .jsc）
```

不跑的后果（都验证过）：

| 少了哪步 | 后果 |
|---|---|
| `modernize.py` | 原版清单里**没有 `<uses-sdk>`**（`apktool.yml` 也只有 `minSdkVersion: 9`）→ 打出来的包没有 targetSdk、Android 14+ 直接拒装；也少 `usesCleartextTraffic`（targetSdk 33 下明文 HTTP 被禁，连不上服务端）。**兜底**：`build_apk.py` 的 `normalize_android_manifest()` 现在自己会补 `<uses-sdk>` 和这两个属性，但它不补 `PermissionHelper` |
| `strip.py` | 2016 年那套渠道 SDK 还留在包里，它们的 Java 类会跟 targetSdk 33 打架（历史上就是它们逼得 targetSdk 只能停在 23） |
| `merge_dex.py` | `smali_classes2/**` 里的孤儿包（Apache HttpClient / Okio / 银联残留…共 461 个类）**一个都删不掉** —— `build_apk.py` 的 `DROP_SMALI` 路径都是按合并后写的 |
| `patch_js_debugger.py` | 引擎优先读同名 `.jsc`，没删掉的那份会把我们改过的明文调试器 JS 盖掉 |

> 顺序不能颠倒：`strip.py` 会把 `smali\com\quicksdk` 整个删掉再按 `needed.json` 重建桩类，
> **`patch_smali.py` 必须排在它后面**；`merge_dex.py` 必须排在 `patch_smali.py` / `build_apk.py`
> 之前。细节与完整命令见 [`server/docs/build.md`](server/docs/build.md) §「完整重建命令」。
>
> 想在不碰 `game\` 的情况下演练整条链：给每一步都带上 `GS_APK_DIR=<别的解包目录>`
> （打包时再加 `GS_ORIGINAL_APK=<原版包路径>`）。

---

### Step 2 · （可选）引擎：拿源码 → 打补丁 → 编译

**只有想改引擎 / 排查原生崩溃才需要。** 跳过的话要自己准备一份
`libcocos2djs.so` 放进 `game\lib\<abi>\`（用原版的也能跑，但少掉 16 个补丁）。

```powershell
# 2.1 先把外部资源按 §2 摆好，确认这几条都是 True
Test-Path engine\src\cocos2d-js\frameworks\js-bindings      # cocos2d-js v3.6
Test-Path engine\src\cocos2d-js\frameworks\js-bindings\cocos2d-x\external   # v3-deps-47
Test-Path engine\ndk\android-ndk-r10e\ndk-build.cmd           # NDK r10e
Test-Path engine\build\oppai-engine\jni\Android.mk          # 仓库自带
Test-Path engine\build\oppai-engine\Classes\runtime         # §2 第 6 项，要自己抄回来

# 2.2 编（补丁会自动打，见下）
cd E:\code\zcsmw
.\build.ps1 -Engine -Abi x86        # 只编 x86；不带 -Abi 则 armeabi + x86 都编
```

**补丁不用你手动跑。** `engine\build\oppai-engine\` 这个工程**整个在仓库里**
（含 `jni/Android.mk`、4 个自研绑定、`AppDelegate.cpp`），那些改它的补丁
（`add_*.py` / `fix_attach*.py` / `fix_vp_*.py` / `move_ccs.py` / …）
**结果已经跟着仓库一起发布了**。

需要重跑的只有一类：**目标在 `engine\src\` 里的补丁** —— 那是 cocos2d-js
第三方源码，被 `.gitignore` 挡在外面，别人的 `src/` 是干净的。
`build.ps1 -Engine` 会自动重跑这 7 个（都写成幂等的，已打过就打印「已处理」）：

| 补丁 | 干什么 | 不跑的后果 |
|---|---|---|
| `fix_lastframe_engine.py` | 最后一帧回调补上**动画名** | **战斗收尾卡住**（`began3` 播完不接 `loop3`） |
| `fix_precedence.py` | `RotationSkewFrame` 运算符优先级 | **原生崩溃** |
| `patch_scriptingcore.py` | JS 异常内容打到 logcat | 只看到 `(evaluatedOK == JS_FALSE)`，不知道错在哪 |
| `fix_js_log.py` | 让引擎自己的 JS `log()` 在 release 包里也能打 | 调试器/诊断日志全是空的 |
| `fix_null_texture.py` | `Sprite::draw` 空贴图崩溃 | 新手引导那段必崩（`Texture2D::getName()+4`） |
| `quiet_engine.py` | `JniHelper` 日志降噪 | 日志被 JNI 刷屏 |
| `enable_js_debugger.py` | 打开引擎自带的远程 JS 调试器 | 用不了引擎级断点（`script\jsd.py`） |

> 每个补丁的**现象 / 根因 / 验证方法**见
> [`engine/ENGINE_PATCHES.md`](engine/ENGINE_PATCHES.md)；
> 移植过程踩的坑（NDK 版本、`APP_PLATFORM`、runtime vs default 模板、
> 为什么只有 `libhoudini` 一帧的 tombstone 不可信）见
> [`engine/README.md`](engine/README.md)。

**验证点**：`out\x86_libcocos2djs.so` 存在（~19 MB），
日志里没有 `error:`；`game\lib\x86\libcocos2djs.so` 被更新。

```powershell
Get-Item out\x86_libcocos2djs.so | Select-Object Length
Get-Content out\engine-build-x86.log -Tail 5
# 补丁那一段应该长这样（都是「已处理」/「跳过」，不该出现 !! ）
#   头文件已处理 / 已经打过补丁，跳过 / 共修 0 处 / [js-debugger] 已经打过补丁，跳过
```

> **编不过先看这三条**：
> ① 缺 `v3-deps-47` → `Cannot find module with tag 'freetype2/prebuilt/android'`
> ② `APP_PLATFORM` 没设成 `android-9` → `GLES2/gl2platform.h: No such file`
> ③ `ndk-build` 的输出走 stderr，而 `engine\build\build.ps1` 里有
> `$ErrorActionPreference = "Stop"` → 警告被当成终止错误中断构建。
> **直接调 `ndk-build.cmd` 更省事**，`build.ps1`（仓库根那个）就是这么做的。
>
> 深入：[`engine/ENGINE_PATCHES.md`](engine/ENGINE_PATCHES.md)、
> [`server/docs/engine-debug.md`](server/docs/engine-debug.md)。

---

### Step 3 · 起服务端（**和 Step 4 可以并行**）

```powershell
cd E:\code\zcsmw
python script\serve.py
```

**验证点**：四个端口都在 LISTENING。

```powershell
Get-NetTCPConnection -State Listen |
    Where-Object { $_.LocalPort -in 18080,10001,8080,10003 } |
    Select-Object LocalPort
```

| 端口 | 作用 |
|---|---|
| 18080 | CDN：远程配置 / 热更新清单 / 公告 / 探针接口 / **调试台** |
| 10001 | 网关：服务器在线状态 |
| 8080 | 登录：`/oauth/*` + WebSocket 握手 |
| 10003 | 游戏业务：加密 POST |

> ⚠️ 用 `serve.py`（守护进程），**别用 `Start-Process python run.py`** ——
> 那起的是分离进程，父会话结束就被回收，表现为"服务端莫名宕机"。
>
> **对外地址**（服务器列表 / `gameServUrl` / 打包时写进客户端的地址）来自
> `gamesrv/config.py` 的 `PUBLIC_HOST`，**默认 `127.0.0.1`** = adb reverse 工作流
> （手机/模拟器的回环转发到本机，不需要局域网/防火墙/root，而且和 PC 的 IP 无关）。
> 要局域网直连就覆盖它：
>
> ```powershell
> $env:GS_PUBLIC_HOST = '192.168.1.100'      # 打包会自动用同一个值
> python script\serve.py
> ```
>
> ⚠️ 四个端口**不能改**：客户端里原来的地址长度是固定的
> （`cdn.shuangmawei.net` = 19 字节 / `http://114.55.66.97:16840` = 25 字节），
> 老路子（`-JscUrlPatch`）下必须等长替换；默认的运行时改写没有这个约束，
> 但端口仍然贯穿服务端配置与客户端，别动。
> 详见 [`server/docs/build.md`](server/docs/build.md)。
>
> 深入：[`server/docs/protocol.md`](server/docs/protocol.md)。

---

### Step 4 · 打客户端补丁 + 打包 + 安装

```powershell
cd E:\code\zcsmw

# 4.1 客户端 JS 补丁（patch.js / probe.js）由 build.ps1 自动写进 assets，
#     不用手动拷；先跑一遍 Java 层补丁确认它认得出工作副本
python server\client\patch_smali.py

# 4.2 打包（改 assets → apktool b → zipalign → 签名）
#     对外地址默认取自服务端配置（默认 127.0.0.1 = adb reverse 工作流，
#     build.ps1 会自动把四个端口 adb reverse 掉）
.\build.ps1 -Install -Launch
```

**验证点**：

```powershell
Get-Item out\zcsmw-mod-signed.apk | Select-Object Length   # ~550 MB
adb -s 127.0.0.1:21503 shell pm path com.cm.zcsmw.baidu    # 装上了
```

游戏启动后应该：**公告弹窗 → 登录界面 → 点开始 → 主界面**，零 JS 报错。

```powershell
adb -s 127.0.0.1:21503 logcat -d -v brief | Select-String "OPPAIPATCH|JS ERROR"
```

**这一步在做什么**（细节见 [`server/docs/build.md`](server/docs/build.md)）：

| 子步 | 内容 |
|---|---|
| 3 | 调试器自己的 JS 换成明文（`.jsc` 只是缓存，删掉引擎就改读同名 `.js`） |
| 3b | **Java 层补丁** —— 见下 |
| 4 | 改 assets（URL 等长替换）→ 删没用的资源/SDK → `apktool b` → `zipalign` → `apksigner` |

**为什么要打 Java 层补丁**：原版点「开始游戏」走
`QuickSDK → 百度 SDK → 登录弹窗`，而那两个服务器**早就下线了**，
弹窗永远登不进去。`patch_smali.py` 把 `QuickAdapter.login()` 换成
「直接回调登录成功」，游戏自己那条链路（注册 SDK 回调 → `op.login` → 回调）
一点没动。

> ⚠️ **所有 `.ps1` 都必须是 UTF-8 带 BOM**。PowerShell 5.1 会把无 BOM 的
> UTF-8 脚本按 GBK 读，中文注释吃掉引号 → 一大片 `Unexpected token`。
> 最坑的是**解析失败 = 一行都没执行**，别把它当成"跑过了但没效果"。
>
> 深入：[`server/docs/build.md`](server/docs/build.md)、
> [`server/client/patch.js`](server/client/patch.js) 头部（11 条适配各自的原因，其中第 6/9 条是体验改动，可删）。

---

### Step 4b · **默认工作流**（模拟器 / 真机通用）：`adb reverse` + 运行时地址改写

> **开发就用这条**（也是默认值）：地址固定 `127.0.0.1`，四个端口用 `adb reverse`
> 转发到本机。好处是**和 PC 的 IP 无关** —— DHCP 换了地址也不会让包作废
> （踩过：地址一变，包里烘死的 IP 失效，连调试台的下发通道都一起断），
> 而且不需要局域网、防火墙、root、hosts。模拟器和 USB 真机走的是同一条路。
>
> 要**脱离 USB**（手机自己连 Wi-Fi 玩）就用本节末尾的局域网模式 —— 也能用，
> 代价是 PC 的 IP 一变就要重打包。

真机上有三条硬约束，按「装得上 → 跑得起来 → 连得上」确认：

| 卡点 | 结论 | 怎么办 |
|---|---|---|
| **装得上** | 原版 `targetSdkVersion=23`。Android 14 起禁装 `<23`、**Android 15 起禁装 `<24`**，所以以前装真机得带 `--bypass-low-target-sdk-block` | **已修**：`script/build_apk.py` 的 `normalize_android_manifest()` 每次打包把 targetSdk 提到 **33**，并给带 intent-filter 的组件补显式 `android:exported`（31+ 不写会报 `android:exported needs to be explicitly specified`）。现在 `.\build.ps1 -Install` 直接装 |
| **跑得起来** | 原版包只有 `armeabi` + `x86`，而引擎的预编译依赖（curl/websockets/png/freetype…）在 cocos 官方那套里也只有 armeabi / armeabi-v7a / x86 —— **arm64 是后来自己凑依赖编出来的**（见 §4b 末尾和 [`build.md`](server/docs/build.md) §ABI，现在四份 ABI 都能打） | **别只看属性，直接装一个试** —— `ro.product.cpu.abilist` / `ro.zygote` 说只有 64 位，不代表跑不了：一加 PLZ110（Android 16，`abilist32` 为空、`ro.zygote=zygote64`）**带厂商 32 位兼容层**（有 `app_process32`、32 位 `linker`/bionic、`init.svc.zygote_tango`），装 v7a 包能跑；没这层的机器（Pixel 7 以后）才会 `UnsatisfiedLinkError` —— 那种机器就打 arm64 包（`-PackAbis arm64-v8a`） |
| **连得上** | 地址烘在包里 → 换 IP 就要重打包 | 见下面，**改成运行时改写**，连局域网都不需要 |

```powershell
# 手机插 USB、开 USB 调试，拿到序列号
adb devices

# 1) 【默认就是这个工作流】对外地址 = 127.0.0.1，四个端口反向转发到本机
#    build.ps1 到 [4b] 会自动重设；手工做就是这四行
foreach ($p in 18080,8080,10001,10003) { adb -s <手机序列号> reverse "tcp:$p" "tcp:$p" }
adb -s <手机序列号> reverse --list

# 2) 起服务端（默认就宣告 127.0.0.1，不用设环境变量）
python script\serve.py

# 3) 打包 + 装到真机（-Serial 指到手机；不带任何地址参数，读服务端配置）
#    真机建议顺手只带 arm 的 .so（省 8.3 MB，不用带模拟器那份 x86）：
.\build.ps1 -Install -Serial <手机序列号> -PackAbis armeabi-v7a,armeabi

# 4) 现在直接装就行（targetSdk 已经是 33，见上面「装得上」那行）
#    万一你手上的包还是旧的 targetSdk=23，才需要这条官方开关（不需要 root）：
adb -s <手机序列号> install -r --bypass-low-target-sdk-block out\zcsmw-mod-signed.apk
```

> **ABI**：包里现在有 `armeabi`（原版）/ `armeabi-v7a` / **`arm64-v8a`**（后两个 2026-09-20 自己编）
> / `x86`（模拟器）四份可选，系统按设备 `abilist` 自己挑。实测一加 PLZ110 会挑
> **`primaryCpuAbi=arm64-v8a`**（原生 64 位，不走厂商 32 位兼容层），MEmu 挑 `x86`。
> 真机建议：`-PackAbis arm64-v8a -Install -Serial <手机>`（只带 64 位那一份，543 MB）。
> arm64 依赖怎么凑（chipmunk 6.2.1 / libwebsockets 1.23 / 按 ABI 分头文件）见
> [`build.md`](server/docs/build.md) 的「ABI」一节 + `script/build_arm64_deps.py`。

**实测结论（一加 PLZ110，Android 16 / SDK 36）**：

```
ro.product.cpu.abilist    arm64-v8a        ← 只有 64 位
ro.product.cpu.abilist32  （空）
ro.zygote                 zygote64
```

看着像"跑不了"，但**实际能跑**：装上后 cocos 层正常起来、`isLogin:true`、`lv:30`，
`__oppaiFixUrl()` 把 CDN 域名改到 `127.0.0.1:18080`，WS 握手四个帧齐全。
原因是这条 ROM 带**厂商 32 位兼容层**（`/system/bin/app_process32`、32 位
`linker`/bionic、`init.svc.zygote_tango` 在跑），32 位 `libcocos2djs.so` 能加载。
没有这层兼容层的机器（如 Pixel 7 以后）才会 `UnsatisfiedLinkError` —— 那种情况
才需要为 arm64 重编引擎依赖。

> 屏幕适配：该机日志里 `OplusDisplayCompatUtils: maxAspectRatio 1.86 >>> 1.7778`
> —— 系统按 16:9 给这个老包**加黑边**（不拉伸），属于预期行为。

**为什么不用 root / 不用改 hosts**：

* jsc 里的官方地址**不再被打包时改写**（这是现在的**默认**行为；老路子要显式加
  `-JscUrlPatch`），`patch.js` 在运行时把 `cdn.shuangmawei.net` / `114.55.66.97:16840`
  改写到对外地址。拦截点是 `cc.loader.getXMLHttpRequest()` 和 `window.WebSocket`
  两个 JS 单点。
* 热更新那份 `project.manifest` 走**原生 curl**，拦不到 → 由 `build_apk.py`
  按 **JSON** 重写（纯文本，不受等长约束）。
* 于是地址可以随便填（`127.0.0.1` 也行），`adb reverse` 把手机的回环转发到 PC ——
  局域网、防火墙、`hosts`、root **全都不需要**，插 USB 就能跑。

> ⚠️ **包装原生构造函数要抄静态常量**。`wsFactory` 是在模块加载时**捕获**
> `window.WebSocket` 的，而 `wsHandle.send` 判的是
> `socket.readyState === WebSocket.OPEN`。包出来的函数不抄 `OPEN` 就等于
> `undefined` → 判定恒假 → **登录握手一个字节都发不出去**（症状：WS 连上了、
> 密钥也算完了，服务端发完欢迎包就一直阻塞在 recv）。详见
> [`server/docs/overview.md`](server/docs/overview.md) §6.13。
>
> ⚠️ 老路子（`-JscUrlPatch`）的坑，知道一下就行：它走**等长**替换
> （`<host>:18080` 必须 19 字节 → **host 必须 13 个字符**），而且那几个文件是
> **就地改写**的 —— 换地址时替换逻辑「找不到旧串」会**静默跳过**，整包作废
> （实测踩过：DHCP 换了 IP）。默认路子不存在这个问题（jsc 永远保持原始地址，
> 每次打包都先从 `game/original/zcsmw-original.apk` 恢复一遍）。

#### 局域网模式（不用 USB / 不用 adb reverse）

模拟器实测：**能直接连 PC 的局域网 IP**（MEmu 的 NAT 网关 `192.168.232.1` 反而不通），
所以只要设备与 PC 在同一网络：

```powershell
$env:GS_PUBLIC_HOST = '10.210.22.230'   # 换成 PC 当前的局域网 IP（ipconfig 看 WLAN）
python script\serve.py                   # 服务端和打包共用这一处配置
.\build.ps1 -Install                     # 地址自动跟随；[4b] 不会执行
```

* 防火墙要放行入站 TCP `18080 / 8080 / 10001 / 10003`
* ⚠️ **IP 一变就得重打包**（约 1 分钟）。想省事就给 PC 设 DHCP 保留/静态 IP
* 手机不插 USB 就用这条；插着 USB 的话还是推荐上面默认那条（IP 无关，最省心）

---

### Step 5 · 抽客户端表

服务端的数值**全部来自客户端自带的 `table_*`**（那些表是编译进 `.jsc` 的静态配置，
不是服务端下发的），所以要从**运行中的游戏**里把它们导出来。

```powershell
# 前提：游戏已经跑起来、探针在线（Step 4 打完的包里带 probe.js）
cd E:\code\zcsmw
python script\extract_client_tables.py
```

**验证点**：`server\gamesrv\data\` 下出现 26 个 `table_*.json`，总计约 1.7 MB。

```powershell
Get-ChildItem server\gamesrv\data\table_*.json | Measure-Object -Property Length -Sum
```

> ⚠️ **只补新表时加 `--only <子串>`**：每张表都是一次 `/control/eval`，
> eval 跑在**游戏主线程**上、返回值还要 base64 + DES 走一遍 HTTP。
> 整轮全抽（含 `table_soldier` / `table_equipment` 那种几十万字的）
> **会把模拟器压到卡死**，实测过一次。
>
> 这个仓库里**已经带了这 26 张表**，所以正常复刻可以跳过这一步；
> 只有客户端换版本、或者你要验一遍流程时才需要跑。

---

### Step 6 · 验证

```powershell
cd E:\code\zcsmw

# 不需要游戏、不需要服务端（进程内自测）
python script\selftest_favor.py          # 好感度/宿舍/守护灵/助战公式：172 条断言
python script\check_soldier_calc.py      # 军士升级公式 vs 客户端（要游戏在跑）

# 需要服务端在跑
python script\selftest_game.py           # 走真协议：加解密+路由+落盘
python script\check_devtools.py          # 调试台：静态一致性 + 接口全打一遍

# 缺口盘点
python script\route_gap.py --static      # 客户端候选 161 / 已实现 72 / 缺 97
```

**游戏内该看到的**：

| 入口 | 期望 |
|---|---|
| 主界面 | 指挥部 30 级，18 个军士（三站位各 6），钻石 10 万 / 萌钞 1000 万 |
| 编成 → 培养 | 能选材料升级、材料真被吃掉、重登不回退 |
| 关卡 | 能打完、有星级、章节星数奖励能领 |
| 任务 | 主线能推进能领奖 |
| 商店 | 能买（真扣钱、真发货） |
| 邮件 | 能收/读/领附件 |
| 天赋（培养） | 三条课题线，升级真扣材料 |
| 装备 | 穿/脱/升级/分解/锁定 |
| **宿舍** | 63 个角色里 19 个已获得；能送礼、**按住来回搓**角色（搓约 1.7 秒）加好感度；换装 42 件、换背景 54 张可换 |
| 调试台 | `http://127.0.0.1:18080/devtools` 六个面板都有数据 |

> ⚠️ **宿舍「互动（抚摸）」是按住来回搓，不是单击** ——
> 而且判定框原版只有 100×100、不可见、还在角色右边。
> 本项目在 [`server/client/patch.js`](server/client/patch.js) 末尾把它放大到覆盖角色了
> （私下体验改动，见 [`server/docs/differences.md`](server/docs/differences.md) §B）。
> 排查这类"点了没反应"，先看 [`server/docs/overview.md`](server/docs/overview.md) §6.10。

---

## 5. 排查顺序（按复用性）

1. **先看服务端日志**：请求到没到、服务端说了什么
   `server\var\logs\server.log`（或调试台的**日志**面板）
2. **再看调试台的流量面板**：「客户端调了但服务端没实现」会**直接标黄**，
   请求 msg 和回包都是原文，还能一键重放
3. **分清是 Java 层 / 引擎层 / JS 层**：
   `adb shell dumpsys activity top`、tombstone、`adb logcat`
4. **JS 异常先看 stack**：`initUserData` 抛异常会连累一大片
5. **拿不准的数据形状，直接在调试台控制台里试** —— 几秒钟一个
6. **要停住游戏看调用栈**：调试台的**调试器**页签（或 `script\jsd.py repl`），
   那是引擎级断点，游戏会真的停住

**症状 → 原因**的速查表在
[`server/docs/overview.md`](server/docs/overview.md) **§6**（10 条坑，
每条都有现象、根因、定位方法）。

---

## 6. 各步骤的深入文档

| 想看什么 | 文档 |
|---|---|
| 全景、分层、成果、坑速查 | [`server/docs/overview.md`](server/docs/overview.md) |
| **和原版哪里不一样**（含"哪些数值是猜的"） | [`server/docs/differences.md`](server/docs/differences.md) |
| 协议逐项 + 反汇编证据 | [`server/docs/protocol.md`](server/docs/protocol.md) |
| 没有源码怎么逆向（`.jsc` 格式、探针手法） | [`server/docs/reverse-engineering.md`](server/docs/reverse-engineering.md) |
| jsc → js 反编译器怎么做 | [`server/docs/decompile.md`](server/docs/decompile.md) |
| 打包逻辑（为什么要删那些东西） | [`server/docs/build.md`](server/docs/build.md) |
| 浏览器调试台 | [`server/docs/devtools.md`](server/docs/devtools.md) |
| 引擎层调试（自带远程 JS 调试器） | [`server/docs/engine-debug.md`](server/docs/engine-debug.md) |
| 13 个引擎补丁的证据链 | [`engine/ENGINE_PATCHES.md`](engine/ENGINE_PATCHES.md) |
| 引擎移植过程与踩过的坑 | [`engine/README.md`](engine/README.md) |
| 脚本索引 + 加新玩法模块的流程 | [`script/README.md`](script/README.md) |

---

## 7. 免责声明

* 本仓库**只包含自己写的代码**，不含任何游戏素材、`.jsc` 字节码、
  反编译产物或 APK。§2 里那些资源请自行合法获取，**不要再分发**。
* 仅供个人研究 / 存档 / 客户端逆向学习，**请勿用于任何商业用途**。
