# 战场双马尾（Oppai / zcsmw）私服

Kuro Game《战场双马尾》v2.2.0 已停服。这个项目用**纯 Python（仅标准库）**重新实现了一份服务端，
配套一套客户端补丁和一个从源码重建的引擎，让 Android 模拟器上的**原版客户端**重新跑起来。

> 仅供个人研究 / 存档 / 客户端逆向学习，请勿用于任何商业用途。
> 本项目**不包含**任何游戏素材或反编译出来的游戏代码。

> ## 🚀 想直接跑起来 → **[`REPRODUCE.md`](REPRODUCE.md)（从零复刻，逐步带验证点）**
>
> 那份是**照着做就能跑起来**的操作手册：环境 → 准备外部资源 → 解包 →
> （可选）重编引擎 → 起服务端 → 打包装机 → 抽客户端表 → 验证。
> 本文只讲"仓库里有什么"，分工不同。

---

## 这个仓库里有什么 / 没有什么

**只放我们自己写的东西**（约 220 个文件，3 MB 出头）：

| 路径 | 内容 |
|---|---|
| `server/` | Python 服务端（纯标准库，无依赖），72 条业务路由 + 浏览器调试台 + 引擎调试桥 |
| `script/` | 实用脚本：jsc 反编译 / 反汇编 / 按函数切原子表、抽客户端表、apktool 打包、APK 体积体检、smali 可达性分析、路由缺口分析… |
| `engine/build/` | 引擎补丁脚本（30 个，每个都幂等可重跑）+ `oppai-engine/{Classes,jni}`：我们自己写的 `jsb_oppai_*` 绑定和 `Android.mk` |
| `build.ps1` | 一键构建 |

### 不在仓库里 —— 这些要自己准备

`.gitignore` 里逐条写了原因，简单说就是**版权**和**体积**（GitHub 单文件硬上限 100 MB）：

| 缺什么 | 体积 | 怎么来 |
|---|---|---|
| `game/` | 1.16 GB | **自备《战场双马尾》v2.2.0 的 APK**（见下方占位地址），`script\apktool.bat d` 解包。**游戏素材和反编译代码的版权不属于本项目，请勿再分发** |
| `engine/src/` | 985 MB | cocos2d-js **v3.6** 源码（含 cocos2d-x 3.6 + SpiderMonkey 33.1.1） |
| `engine/src/.../cocos2d-x/external/` | 70 MB | cocos2d-x **`v3-deps-47`** 第三方预编译库（缺了报 `Cannot find module with tag 'freetype2/prebuilt/android'`） |
| `engine/ndk/` | 4.4 GB | Android NDK **r10e**（版本必须对，别的版本编不过） |
| `script/apktool_3.0.3.jar` | 15 MB | apktool 3.0.3（Apache-2.0），放到 `script\` 下即可 |
| `engine/build/oppai-engine/Classes/{protobuf-lite,runtime}/` | 2.2 MB | 从 cocos2d-js 的 `js-template-runtime`（`frameworks/runtime-src/proj.android`）抄回来 |
| `engine/ref/libcocos2djs-original.so` | 18 MB | 从原版 APK 的 `lib/armeabi/` 里取出来（只作参考） |

### 游戏 APK 从哪来（占位）

原版 v2.2.0 APK **不在本仓库里，也不由本项目提供** —— 请自行合法获取：

```
        ┌────────────────────────────────────────────┐
        │   游戏 APK 地址（占位，替换成你自己的来源）  │
        │                                            │
        │            <GAME_APK_URL>                  │
        │                                            │
        └────────────────────────────────────────────┘

  要求：包名 com.cm.zcsmw.baidu / 版本 2.2.0
        APK 内 lib/armeabi/ 下必须有 libcocos2djs.so
```

拿到后**先留一份只读备份**（后面的步骤会反复改解包目录）：

```powershell
mkdir E:\code\zcsmw\game\original -Force
copy <你的APK> E:\code\zcsmw\game\original\zcsmw.apk
```

逐步骤的操作流程见 [`REPRODUCE.md`](REPRODUCE.md)。

凑齐之后：

```powershell
.\build.ps1 -Install -Launch            # 用现成的引擎 .so，约 1.5 分钟
.\build.ps1 -Engine -Install -Launch    # 先重编引擎再打（要 NDK r10e，几分钟）
```

---

## 目录结构

```
E:\code\zcsmw\
├── build.ps1            ★ 一键构建（见下）
├── README.md            本文
├── game\                游戏包
│   ├── assets\ smali\ lib\ res\ AndroidManifest.xml\ apktool.yml   apktool 解包目录（工作副本）
│   └── original\        原版 APK 备份（唯一一份，别删）
├── server\              服务端（python 包）
│   ├── run.py           入口
│   ├── gamesrv\         config / store / quests / instance / devtools / handlers …
│   ├── client\          客户端补丁源：patch.js / probe.js / *.smali / *.py
│   └── docs\            协议、逆向、调试台、引擎调试、反编译的细节文档
├── script\              实用脚本（见下）
│   ├── apktool.bat / apktool_3.0.3.jar
│   ├── _paths.py        路径自举（每个脚本开头都会 import 它）
│   └── sdk_strip\ archive\
├── engine\              引擎源码 + NDK（重编 libcocos2djs.so 用）
│   ├── src\             cocos2d-js v3.6 + cocos2d-x 3.6 + SpiderMonkey 33.1.1
│   ├── build\           oppai-engine 工程 + 每个补丁一个可重复执行的脚本
│   ├── ndk\             android-ndk-r10e
│   └── ref\             参考源码 / 原始 .so
└── out\                 构建产物
    ├── zcsmw-mod-signed.apk   最终可安装的包
    ├── shots\                 截图
    └── *.log
```

---

## 一键构建

```powershell
cd E:\code\zcsmw
.\build.ps1                      # 打 APK（用现成的引擎 .so，约 1.5 分钟）
.\build.ps1 -Engine              # 先重编引擎 libcocos2djs.so，再打 APK
.\build.ps1 -Install -Launch     # 打完装到模拟器并启动
.\build.ps1 -NoProbe             # 出正式包（不带探针，不能 REPL / 调试）
.\build.ps1 -Engine -Abi x86     # 只重编 x86 引擎（-Abi 默认 armeabi,x86）
```

产物固定在 `out\`：

| 文件 | 说明 |
|---|---|
| `out\zcsmw-mod-signed.apk` | 最终可安装的包（zipalign + 签名过了） |
| `out\zcsmw-mod.apk` | apktool 打出来的未签名包 |
| `out\<abi>_libcocos2djs.so` | 引擎产物，`abi` = `armeabi` / `x86`（`-Engine` 时更新） |
| `out\engine-build-<abi>.log` | 引擎编译日志 |

### 为什么包里同时放 armeabi 和 x86

MuMu 是 x86 模拟器，`ro.product.cpu.abilist32 = x86,armeabi-v7a,armeabi`。
包里有 `lib/x86/` 时 Android 会**优先选 x86 原生跑**；只有 armeabi 的话就得交给
houdini（ARM→x86 二进制翻译层）接管，实测会在新手引导那段代码上被 houdini
自己 trap 掉 —— tombstone 里唯一一帧永远是 `/system/lib/libhoudini.so`，
`fault addr 0xdead0000` / `signal 4 (SIGILL)`，且每次寄存器状态完全一致。
同名文件同时放两个 ABI，真机（ARM）和模拟器（x86）都能跑。

> ⚠️ **所有 `.ps1` 都必须是 UTF-8 带 BOM**（不只是 `build.ps1`）。Windows PowerShell
> 会把无 BOM 的 UTF-8 脚本按 ANSI（中文系统上就是 GBK）读，中文注释和字符串被误解析、
> 顺手把引号吃掉，报一大片 `Unexpected token` / `Missing closing '}'`。
> 最坑的是**解析失败 = 脚本一行都没执行** —— 别把它当成"跑过了但没效果"。
> 用编辑器改完存成「UTF-8 with BOM」；临时的验证脚本干脆只写 ASCII 最省心。

---

## 起服务端

```powershell
python script\serve.py           # 守护进程，run.py 挂了自动拉起（推荐）
```

四个端口（客户端里的 URL 是**原地等长字节替换**进去的，不能随便改）：

| 端口 | 作用 |
|---|---|
| 18080 | CDN：远程配置 / 热更新清单 / 公告 / 探针接口 / **调试台** |
| 10001 | 网关：服务器在线状态 |
| 8080 | 登录：`/oauth/*` + WebSocket 握手 |
| 10003 | 游戏业务：加密 POST |

---

## 脚本速查（`script\`）

只列最常用的；**全量索引**（六类、每个脚本一句话）见 [`script/README.md`](script/README.md)。

**日常**

| 脚本 | 干什么 |
|---|---|
| `serve.py` | 起服务端（守护） |
| `build_apk.py` | 打包 APK（`build.ps1` 第 4 步调的就是它） |
| `patch_js_debugger.py` | 调试器自己的 JS 换成明文（不用重编引擎就能改调试器） |
| `selftest_game.py` | 不开游戏自测：加解密 + 路由 + code=200 + 军士培养链路 |
| `selftest_favor.py` | 好感度 / 宿舍事件 / 守护灵 / 助战公式自测（进程内，172 条断言，不需要模拟器也不需要服务端） |
| `check_devtools.py` | 调试台自测（静态一致性 + 接口全打一遍） |

**逆向**

| 脚本 | 干什么 |
|---|---|
| `jsc_strings.py` | ★ 只扒 `.jsc` 的 atom 表 —— 没源码也能看懂一个函数在干什么，**最快的一把刀** |
| `jsc_disasm.py` / `disasm_func.py` | SM33.1.1 字节码反汇编 |
| **`jsc_decompile.py`** | ★ **jsc → js 反编译器**，`assets/src/**` 572/575 能过 `node --check` |
| `jsc_find.py` | ★ 按原子反查：这个 key / route / 方法名在哪个 `.jsc` 的哪个函数里用过 |
| `extract_client_tables.py` | 把客户端 `table_*` 抽成服务端 JSON（补新表加 `--only`，别整轮全抽） |
| `csb_dump.py` | 解析 cocostudio 的 `.csb`（动画区间 / 帧事件） |

**运行时调试**

| 脚本 | 干什么 |
|---|---|
| **`/devtools`** | ★ 浏览器调试台 `http://127.0.0.1:18080/devtools`：流量 / 控制台 / 存档作弊 / 日志 / 表查询 / **引擎调试器** |
| **`jsd.py`** | ★ 引擎自带的远程 JS 调试器客户端：断点 / 单步 / 调用栈 / 暂停时求值 |
| `repl.py` / `probe.py` | 在游戏进程里跑任意 JS（不暂停） |
| `shots.py` / `bisect_init.py` | 截图 / 逐模块二分找卡死点 |

> 脚本从 `script/` 里往上找 `_paths.py` 来自举 `sys.path`，
> 所以随便从哪个目录调都能 `from gamesrv import ...`。

---

## 现在能跑到哪

```
[客户端] 启动 → 公告 → 远程配置 → 版本校验 → 服务器列表 → 登录界面      ✅
[客户端] 点「开始游戏」→ 原生 SDK 登录（Java 层补丁，秒成功，无弹窗）    ✅
[客户端] oauth 换 token → WebSocket 握手（DH + DES + HMAC）             ✅
[客户端] agent.getlogindata → 新号自动建号 → 31 个数据模块初始化          ✅
[客户端] 切主场景 MainScene → 开场动画 → 主界面                          ✅
[玩法]   编成 / 上阵队伍（18 个初始士兵，都是 card_type==1 的自军卡）     ✅
[玩法]   军士培养 / 突破 / 技能（升级公式和客户端逐字段对齐）             ✅
[玩法]   任务 → 主线任务（20 条窗口，进度接真实战斗）                     ✅
[玩法]   战斗（自己重建的引擎 + 16 个补丁）                              ✅
[调试]   浏览器调试台 + 引擎级断点调试 + jsc 反编译                       ✅
```

扭蛋 / 日常成就任务 / 黑市交易所 / 侦查这些还没做（路由回空 stub），清单见
[`docs/overview.md`](docs/overview.md) §7。

---

## 文档地图

| 文档 | 内容 |
|---|---|
| **[`REPRODUCE.md`](REPRODUCE.md)** | ★ **从零复刻**：环境、外部资源、逐步操作 + 验证点、排查顺序 |
| [`docs/overview.md`](docs/overview.md) | ★ **先看这份**：全景、系统分层、关键逆向成果、现状与待办 |
| [`docs/pitfalls.md`](docs/pitfalls.md) | ★ **坑速查**：症状 → 真正原因 → 在哪个文件（14 条，排查必翻） |
| [`docs/differences.md`](docs/differences.md) | ★ **与原版的差异总账**：A 不得不改 / B 私服取舍 / C 还没做 / D **数值是猜的** |
| [`docs/protocol.md`](docs/protocol.md) | 协议逐项细节 + 反汇编证据 |
| [`docs/reverse-engineering.md`](docs/reverse-engineering.md) | jsc 反汇编器原理、运行时探测手法 |
| [`docs/decompile.md`](docs/decompile.md) | jsc → js 反编译器：怎么做、三个关键字节码形状、已知问题 |
| [`docs/devtools.md`](docs/devtools.md) | 浏览器调试台：六个面板、架构取舍、怎么加面板 |
| [`docs/engine-debug.md`](docs/engine-debug.md) | 引擎层调试：自带远程 JS 调试器怎么打开、协议、4 个坑 |
| [`docs/build.md`](docs/build.md) | 打包逻辑（为什么这么做）、ABI、完整重建命令 |
| [`engine/ENGINE_PATCHES.md`](engine/ENGINE_PATCHES.md) | 16 个引擎补丁的证据链与复现脚本 |
| [`engine/README.md`](engine/README.md) | 引擎移植过程、版本确认、踩过的坑 |
| [`script/README.md`](script/README.md) | 脚本索引 + 加新玩法模块的推荐流程 |

---

## 环境依赖

| 东西 | 路径（可用环境变量覆盖） |
|---|---|
| Python | 3.10+（只用标准库） |
| JDK 17 | `GS_JAVA_HOME`，默认 `D:\java\zulu17...` |
| build-tools | `GS_BUILD_TOOLS`，默认 `D:\Android\android-sdk\build-tools\36.0.0` |
| adb | `GS_ADB` / `GS_ADB_SERIAL`，默认 `127.0.0.1:21503` |
| NDK r10e | 只有 `-Engine` 才用，在 `engine\ndk\` 里 |

模拟器：MEmu（Android 9 x86_64，已 root）。**默认不依赖宿主机 IP**：对外地址取自
`server/gamesrv/config.py` 的 `PUBLIC_HOST`（默认 `127.0.0.1`），打包时写进
`patch.js`，由它在**运行时**改写客户端里的 CDN / oauth 地址；配合
`adb reverse tcp:{18080,8080,10001,10003}` 连局域网都不需要（真机插 USB 同理）。
要局域网直连就设 `$env:GS_PUBLIC_HOST='<宿主机 IP>'`。

---

## 免责声明

- 本项目不包含任何游戏素材、`.jsc` 字节码、反编译产物或 APK。
- 所有代码均为自行编写，仅通过公开的 SpiderMonkey / cocos2d-x 源码与运行时观察还原协议。
- 请仅用于个人学习研究，不要传播游戏素材或用于商业用途。
