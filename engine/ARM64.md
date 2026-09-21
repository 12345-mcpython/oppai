# arm64-v8a 引擎：依赖怎么凑、怎么编

> **一句话**：cocos 官方那套第三方库在 3.6 年代**只有 armeabi / armeabi-v7a / x86**，
> arm64 得自己凑（预编译库 + 自编三个库 + 按 ABI 分头文件），这个目录下的
> [`../script/build_arm64_deps.py`](../script/build_arm64_deps.py) 把它做成了一条命令。
>
> **读完你会知道**：三步怎么编、每个依赖从哪来、**四个非踩不可的坑**（chipmunk 版本 /
> libwebsockets 结构体布局 / jpeg 的 `boolean` 宽度 / toolchain 与 `APP_PLATFORM`）、
> 以及编完怎么验证。
>
> 相关：[`README.md`](README.md)（构建脚本清单：`$fixes` 那一段）· [`ENGINE_PATCHES.md`](ENGINE_PATCHES.md)（16 个引擎补丁）·
> [`../docs/build.md`](../docs/build.md)（打包侧：`--abis` / 设备挑哪一份）· [`../docs/pitfalls.md`](../docs/pitfalls.md)（坑速查）

---

## 1. 为什么会有这份东西

`libcocos2djs.so` 是用 **NDK r10e** 把 cocos2d-x 3.6 + SpiderMonkey 33.1.1 编出来的，
中间依赖一堆**预编译库**（freetype / png / jpeg / tiff / webp / zlib / curl / openssl /
chipmunk / libwebsockets / spidermonkey）。cocos 官方的 `cocos2d-x-3rd-party-libs-bin`
在 3.6 那个年代**只提供三套 ABI**，没有 arm64。

不编 arm64 也能跑（现代 ARM 真机靠厂商的 32 位兼容层跑 v7a），代价是性能打折 +
多一层转换。arm64 编出来之后：真机 `primaryCpuAbi=arm64-v8a`，**原生 64 位跑**。

## 2. 三步（都幂等，可以反复跑）

```powershell
cd E:\code\zcsmw
python script\build_arm64_deps.py                            # ① 备依赖（一条命令，见 §3）
.\build.ps1 -Engine -Abi arm64-v8a                           # ② 编 .so（内部会自动先跑 ①）
.\build.ps1 -PackAbis arm64-v8a -Install -Serial <手机序列号>  # ③ 只带 arm64 打包并安装
```

* `python script\build_arm64_deps.py --check` —— 只报告缺什么，不动文件。
* `-Engine` 会先跑一遍 `build_arm64_deps.py`，所以 ② 单独跑也安全。
* 只打不装：`.\build.ps1 -PackAbis arm64-v8a`（产物在 `out\zcsmw-mod-signed.apk`）。

## 3. 依赖从哪来（`build_arm64_deps.py` 干的事）

| 依赖 | 从哪来 | 注 |
|---|---|---|
| freetype2 / png / jpeg / tiff / webp / zlib | 官方依赖仓库 **`v3-deps-140`**（arm64 版） | 稀疏拉取，只下要用的那几十 MB |
| curl + openssl | 同上（`v3-deps-140`）；**32 位那份头从 `v3-deps-10` 复原** | 两套头按 ABI 分开，见坑 ③ |
| SpiderMonkey | 同上：`libjs_static.a` + `js-config` 头 | 64 位要 `JS_PUNBOX64` 那份 |
| **chipmunk** | **自己编**：上游 `slembcke/Chipmunk2D` tag `Chipmunk-6.2.1` | 依赖包里是 7.0，API 不兼容，见坑 ① |
| **libwebsockets** | **自己编**：上游 tag `v1.23-chrome32-firefox24` | 依赖包里是 2.1.0（API 改名 `lws_*`），见坑 ② |

装完之后落到 `engine\src\cocos2d-js\frameworks\js-bindings\cocos2d-x\external\**`
和三个 `Android.mk` 里 —— **`engine\src` 被 `.gitignore` 挡着不进仓库，
所以这些规则只能留在脚本里**（改依赖流程时改脚本，别只改树）。

> ⚠️ **唯一一处「版本没严格对齐」**：png 1.6.16 vs 1.6.2、curl 7.52 vs 7.26、
> freetype 2.5.5 vs 2.5.0、openssl 1.1 vs 1.0（jpeg / tiff / webp / zlib 一致）。
> 实测编出来能跑（登录 + 主界面轮询 + 战斗都正常），但**要完全干净得把这几个也从源码编**。

## 4. 四个坑（都是踩出来的，顺序就是踩到的顺序）

### ① chipmunk：版本 + 约束在子目录里

依赖包里是 **7.0**（`cpSpaceAddStaticShape` 这类 6.x API 被删了），而 cocos2d-x 3.6 要 **6.2.1**
→ 用上游源码编。⚠️ **约束实现在 `src/constraints/*.c` 子目录里**：只编 `src/*.c` 会缺
26 个 `cp*JointNew` / `cp*GetClass` 符号，**链接期才炸**（日志一大片 `undefined reference`）。

### ② libwebsockets：三个坑，最后一个会让进程被 seccomp 打死

* 它要一个 **CMake 生成的 `config.h`**（仓库里没有）→ 按官方 `config.h.cmake` 写一份 Android 版
  （无 SSL + 带扩展，符号要和 cocos 那份预编译 `.a` 对得上）；
* bionic 没有 BSD 的 `getdtablesize()`（libwebsockets 直接调）→ 补一个小 shim；
* **`struct lws_context_creation_info` 里 cocos 比上游多 3 个字段**
  （`token_limits` / `http_proxy_address` / `http_proxy_port`）。引擎（`WebSocket.cpp`）按 cocos
  的头文件编译，库里若按上游布局读 `info->gid` 就会读到 **0** → 去调 `setgid(0)` →
  **Android seccomp 直接 `SIGSYS` 打死进程**：

  ```
  Fatal signal 31 (SIGSYS) ... seccomp prevented call to disallowed arm64 system call 144
    #00 libwebsocket_create_context+564   →   setgid+12
  ```

  所以编库前要把那 3 个字段补回**同一个位置**，让两边布局一致。

### ③ 头文件必须按 ABI 分（jpeg 那个最阴）

SpiderMonkey 的 `js-config`、curl 的 `curlbuild`、**jpeg 的 `boolean` 宽度**都得和链接的
那份 `.a` 对上。脚本会挂出 `spidermonkey/include/android64/js-config.h`、
`curl/include/android64/`、`jpeg/include/android64/`，并给三个 `Android.mk` 加
「arm64 用 64 位那套」的 `ifeq`。

| 对不上会怎样 | 现象 |
|---|---|
| `js-config` 错 | jsval 表示错 → **ABI 直接崩** |
| `curlbuild` 错 | `curlrules.h` 的编译期自检当场报 `size of array '__curl_rule_01__' is negative` |
| **jpeg `boolean` 宽度错** | **所有 `.jpg` 静默变黑**（主界面 `bgimage1/bgimage2`、战斗背景全黑，`.png` 一切正常），日志一个字都没有 |

jpeg 那条完整的故事（值得记，因为查了很久）：3.6 自带的
`external/jpeg/include/android/jconfig.h` 里有

```c
typedef unsigned char boolean;   /* 1 字节 */
#define HAVE_BOOLEAN
```

于是 `sizeof(struct jpeg_decompress_struct) == 632`（32 位是 452，和 deps-47 那两份 `.a`
本来就一致）；而 `v3-deps-140` 的 arm64 `libjpeg.a` 是它自己那份 CMake jconfig 编的
（`boolean` 4 字节 → 要 **664**）。`jpeg_CreateDecompress()` 进门第一件事就是查
version + structsize，对不上就 `ERREXIT`，而 cocos 的 `myErrorExit()` 只 `longjmp` 回去、
**不打印任何日志** —— 症状就是「图片悄悄变黑」。

定位手法（当时就是这么定的）：用 NDK 交叉编一个最小程序推到真机上跑，让 libjpeg 自己说：

```
JPEG parameter struct mismatch: library thinks size is 664, caller expects 632
```

现在脚本把 arm64 那份 `jconfig.h` 的这两句删掉（`boolean` 交回 `jmorecfg.h` 的 `enum`），
并且加了**编译期断言** `sizeof == 664`：对不上直接让构建失败，不会再退化成静默变黑。

### ④ `build.ps1` 侧的两处适配（已内置）

* **toolchain 4.9**：NDK r10e 的 arm64 没有 GCC 4.8；
* **`APP_PLATFORM=android-21`**：`Application.mk` 里写的是 android-9，而 arm64 最低 21。

## 5. 编完怎么验证

```powershell
# 1) 产物在不在、多大（约 21.6 MB）
Get-Item engine\build\oppai-engine\libs\arm64-v8a\libcocos2djs.so, game\lib\arm64-v8a\libcocos2djs.so

# 2) 设备真的用上了 64 位那份
adb -s <设备> shell "dumpsys package com.cm.zcsmw.baidu | grep -iE 'primaryCpuAbi'"
#    期望：primaryCpuAbi=arm64-v8a
```

装机之后（一加 PLZ110 / Android 16 实测）：冷启动正常、能登录、主界面轮询
（`boss.getbosslist` / `sync.syncupclient`）正常、无 JS 报错。

**jpeg 那个坑要专门验一次**（它不报错），在调试台的 JS 控制台里：

```js
cc.textureCache.addImage('res/ui/common/res/bgimage1.jpg').getContentSize()
// 期望 568x640；如果返回 2x2 或抛异常 = 又踩到坑 ③ 了
```

## 6. 四份 ABI 的总览

| ABI | 来源 | 未压缩 | 包里（DEFLATE） |
|---|---|---|---|
| `armeabi` | 原版包自带 | 19.0 MB | 7.2 MB |
| `armeabi-v7a` | 官方依赖包里有，直接编 | 18.1 MB | 6.9 MB |
| **`arm64-v8a`** | **本文（自建依赖）** | 21.6 MB | ≈7.5 MB |
| `x86` | 官方依赖包里有（MEmu 原生跑，不走 houdini） | 24.0 MB | 8.3 MB |

打包侧（哪几个 ABI 进包、设备会挑哪一份）见
[`../docs/build.md`](../docs/build.md) 的「ABI」一节。
