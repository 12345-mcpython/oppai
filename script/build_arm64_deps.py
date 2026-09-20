r"""给 **arm64-v8a** 准备引擎依赖（幂等，可反复跑）。

    python script\build_arm64_deps.py            # 需要什么补什么
    python script\build_arm64_deps.py --check     # 只报告缺什么，不动文件

## 为什么需要这个脚本

cocos 官方的依赖包（`cocos2d-x-3rd-party-libs-bin`）在 3.6 那个年代**只有
armeabi / armeabi-v7a / x86 三套**，没有 arm64 —— 所以想编 64 位 .so，
这些依赖得自己凑。脚本做五件事（`engine\src\**` 被 .gitignore 挡着，不进仓库，
所以规则必须留在这里，不能只改文件）：

1. **大部分库**：从依赖仓库的 arm64 版本里取（tag `v3-deps-140`，那份里 10 个库
   + openssl 都有 arm64-v8a）。按需稀疏拉取，只下要用的那几十 MB。
2. **chipmunk**：依赖包里是 **7.0**（`cpSpaceAddStaticShape` 等 6.x API 被删了），
   而 cocos2d-x 3.6 要 **6.2.1** → 从上游 `slembcke/Chipmunk2D` tag `Chipmunk-6.2.1`
   拿源码，用 NDK 编一份。
   ⚠️ 约束在 `src/constraints/*.c` 子目录里，只编 `src/*.c` 会缺 26 个
   `cp*JointNew` / `cp*GetClass` 符号（踩过）。
3. **libwebsockets**：依赖包里是 **2.1.0**（API 改名成 `lws_*`），3.6 要
   **`v1.23-chrome32-firefox24`** → 从上游 `warmcat/libwebsockets` 拿那个 tag 的源码自己编。
   ⚠️ 两个坑：
     * 它要一个 CMake 生成的 `config.h`（仓库里没有）→ 这里按官方 `config.h.cmake`
       写一份 Android 版（无 SSL + 带扩展，和 cocos 那份预编译的符号一致）。
     * `struct lws_context_creation_info` 里 **cocos 比上游多 3 个字段**
       （`token_limits` / `http_proxy_address` / `http_proxy_port`）。引擎按 cocos
       的头文件编译，库若按上游布局读 `info->gid` 就读到 0 → 去调 `setgid(0)` →
       **Android seccomp 直接 SIGSYS 打死**（真机上就是这么崩的）。所以编库前要把
       这 3 个字段补回同一个位置，让两边布局一致。
     * bionic 没有 BSD 的 `getdtablesize()`（libwebsockets 直接调它）→ 补一个小 shim。
4. **头文件按 ABI 分**：SpiderMonkey 的 `js-config`（32 位 `JS_NUNBOX32` /
   64 位 `JS_PUNBOX64`）、curl 的 `curlbuild`、以及 **jpeg 的 `boolean` 宽度**
   都必须和链接的那份 .a 对得上：
     * SM 对不上 ABI 直接错；curl 对不上是 curlrules.h 的编译期自检报错；
     * **jpeg 对不上最阴**（踩过，查了很久）：3.6 自带的
       `external/jpeg/include/android/jconfig.h` 里有
       `typedef unsigned char boolean;` + `#define HAVE_BOOLEAN`，
       而 v3-deps-140 的 arm64 `libjpeg.a` 是它自己 CMake 那份 jconfig 编的
       （`boolean` = jmorecfg.h 的 `enum {FALSE,TRUE}`，4 字节）。
       `jpeg_CreateDecompress()` 进门第一件事就是查 version + structsize，
       对不上直接 `ERREXIT` → cocos 的 `myErrorExit()` 只 longjmp 回去、
       **一个字都不打印**：表现是主界面那种 `.jpg` 大背景
       （`bgimage1`/`bgimage2`）全黑、`.png` 一切正常。
       实测尺寸：arm64 要 **664**，我们编出来 **632**；32 位（x86 452 / 1 字节）
       和 deps-47 那两份 .a 是对得上的，所以只有 arm64 要单开
       `include/android64`，32 位那份原样保留。
5. 把上面这些落到 `engine\src\...\external\**` 和两个 `Android.mk` 里。

弄完就可以 `.\build.ps1 -Engine -Abi arm64-v8a` 编 64 位 .so 了
（`build.ps1` 里 arm64 会自动换 toolchain 4.9 + `APP_PLATFORM=android-21`）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

# --- 路径自举（项目已重排：脚本在 script/、引擎在 engine/）---
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

ROOT = _paths.ROOT
ENGINE = os.path.join(ROOT, "engine")
WORK = os.path.join(ROOT, "out")
VERBOSE = False
NDK = os.path.join(ENGINE, "ndk", "android-ndk-r10e")
JSB = os.path.join(ENGINE, "src", "cocos2d-js", "frameworks", "js-bindings")
EXT = os.path.join(JSB, "cocos2d-x", "external")
SM = os.path.join(JSB, "external", "spidermonkey")

# 和当前 cocos2d-x 3.6 配套的依赖 tag（3.6 时代的版本号，见仓库里的 version.json）
DEPS_TAG = "v3-deps-140"          # 有 arm64 的最近一份
DEPS_ERA_TAG = "v3-deps-10"       # 3.6 时代（只有三套 ABI，但版本和源码头文件对得上）
DEPS_REPO = "https://github.com/cocos2d/cocos2d-x-3rd-party-libs-bin.git"
CHIPMUNK_TAG = "Chipmunk-6.2.1"
LWS_TAG = "v1.23-chrome32-firefox24"

# 预编译库：库名 -> 目录里要拷的 .a（chipmunk / websockets 是自己编的，见下面）
PREBUILT_LIBS = {
    "freetype2": ["libfreetype.a"],
    "jpeg": ["libjpeg.a"],
    "png": ["libpng.a"],
    "tiff": ["libtiff.a"],
    "webp": ["libwebp.a"],
    "zlib": ["libz.a"],
}
# curl 目录里还要放 openssl 那两份（cocos 的 Android.mk 是这么找的）
CURL_LIBS = ["libcurl.a"]
OPENSSL_LIBS = ["libcrypto.a", "libssl.a"]

# —— chipmunk 源码（上游 tag）——
CHIPMUNK_REPO = "https://github.com/slembcke/Chipmunk2D.git"
# —— libwebsockets 源码（上游 tag）——
LWS_REPO = "https://github.com/warmcat/libwebsockets.git"
LWS_SRCS = (
    "base64-decode", "client", "client-handshake", "client-parser", "daemonize",
    "extension", "extension-deflate-frame", "extension-deflate-stream", "getifaddrs",
    "handshake", "libwebsockets", "output", "parsers", "sha-1",
    "server", "server-handshake", "cocos_android_compat",
)

LWS_CONFIG_H = """\
/* libwebsockets 1.23 的 config.h —— 官方那份由 CMake 生成，这里手写 Android/NDK 版。
 * 依据：仓库里的 config.h.cmake + 官方 Android.mk（-DLWS_BUILTIN_GETIFADDRS）+
 * cocos 预编译 .a 的符号（无 SSL、带扩展）。由 script\\build_arm64_deps.py 生成。
 */
#ifndef LWS_CONFIG_H
#define LWS_CONFIG_H

#define LWS_LIBRARY_VERSION "1.23"
#define LWS_BUILTIN_GETIFADDRS 1

#define HAVE_BZERO 0
#define HAVE_DLFCN_H 1
#define HAVE_FCNTL_H 1
#define HAVE_FORK 1
#define HAVE_INTTYPES_H 1
#define HAVE_MALLOC 1
#define HAVE_MEMORY_H 1
#define HAVE_MEMSET 1
#define HAVE_NETINET_IN_H 1
#define HAVE_REALLOC 1
#define HAVE_SOCKET 1
#define HAVE_STDINT_H 1
#define HAVE_STDLIB_H 1
#define HAVE_STRERROR 1
#define HAVE_STRINGS_H 1
#define HAVE_STRING_H 1
#define HAVE_SYS_PRCTL_H 1
#define HAVE_SYS_SOCKET_H 1
#define HAVE_SYS_STAT_H 1
#define HAVE_SYS_TYPES_H 1
#define HAVE_UNISTD_H 1
#define HAVE_ZLIB_H 1
#define STDC_HEADERS 1

#endif /* LWS_CONFIG_H */
"""

LWS_COMPAT_C = """\
/* Android(bionic) 缺的 BSD 函数：libwebsockets 1.23 直接调 getdtablesize()。
 * sysconf(_SC_OPEN_MAX) 在 Android 上常返回 1048576（内核上限），
 * 直接返回会让 libwebsockets 按它去 malloc fd 查找表（~8MB），所以封顶。
 * 由 script\\build_arm64_deps.py 生成。
 */
#include <unistd.h>

int getdtablesize(void) {
    long n = sysconf(_SC_OPEN_MAX);
    if (n <= 0 || n > 65536) {
        return 1024;
    }
    return (int) n;
}
"""


def log(msg: str) -> None:
    print("[arm64]", msg, flush=True)


def run(cmd, cwd=None) -> int:
    """跑子进程并**吞掉它的 stdout/stderr**。

    ⚠️ 不是为了好看：`build.ps1` 里有 `$ErrorActionPreference = "Stop"`，而
    `git clone` 会把 "Cloning into ..." 写到 **stderr** —— PowerShell 5.1 把原生命令的
    stderr 当终止错误（NativeCommandError）当场抛出来，于是 clone 还在跑脚本就被掐死，
    报的还是一条和真正错误无关的消息（和 ndk-build 那个坑同一类，见 build.md）。
    所以这里统一捕获，失败时由调用方自己打日志。`--verbose` 可以看到详细输出。
    """
    if VERBOSE:
        return subprocess.call(cmd, cwd=cwd)
    return subprocess.call(cmd, cwd=cwd,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def toolchain(abi: str = "arm64-v8a") -> tuple:
    """返回 (gcc, ar, sysroot)。"""
    tc = os.path.join(
        NDK, "toolchains",
        "aarch64-linux-android-4.9" if abi == "arm64-v8a" else "arm-linux-androideabi-4.8",
        "prebuilt", "windows-x86_64", "bin",
    )
    prefix = "aarch64-linux-android-" if abi == "arm64-v8a" else "arm-linux-androideabi-"
    arch = "arch-arm64" if abi == "arm64-v8a" else "arch-arm"
    api = "android-21" if abi == "arm64-v8a" else "android-9"
    return (os.path.join(tc, prefix + "gcc.exe"),
            os.path.join(tc, prefix + "ar.exe"),
            os.path.join(NDK, "platforms", api, arch))


def clone_sparse(repo: str, tag: str, dest: str, paths: list) -> bool:
    """浅克隆 + 稀疏检出（blobless，只下要用的那部分）。已存在则只补路径。"""
    if not os.path.isdir(os.path.join(dest, ".git")):
        log(f"克隆 {repo} @ {tag} -> {os.path.relpath(dest, ROOT)}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if run(["git", "clone", "--filter=blob:none", "--no-checkout", "--depth", "1",
                "--branch", tag, repo, dest]) != 0:
            return False
        run(["git", "-C", dest, "sparse-checkout", "init", "--cone"])
    run(["git", "-C", dest, "sparse-checkout", "add"] + paths)
    if run(["git", "-C", dest, "checkout"]) != 0:
        return False
    return True


def clone_full(repo: str, tag: str, dest: str) -> bool:
    if os.path.isdir(os.path.join(dest, ".git")):
        return True
    log(f"克隆 {repo} @ {tag} -> {os.path.relpath(dest, ROOT)}")
    return run(["git", "clone", "--depth", "1", "--branch", tag, repo, dest]) == 0


def compile_dir(srcs: list, outdir: str, cc: str, sysroot: str, flags: list) -> list:
    """编译一组 .c，返回失败的 basename 列表。"""
    os.makedirs(outdir, exist_ok=True)
    failed = []
    for src in srcs:
        name = os.path.splitext(os.path.basename(src))[0]
        cmd = [cc, "-c", "-O2", "-fPIC", "-std=gnu99", "-DNDEBUG",
               f"--sysroot={sysroot}"] + flags + ["-o", os.path.join(outdir, name + ".o"), src]
        if subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
            failed.append(name)
    return failed


def archive(ar: str, outdir: str, out_a: str, dst: str) -> None:
    objs = [os.path.join(outdir, f) for f in sorted(os.listdir(outdir)) if f.endswith(".o")]
    if os.path.exists(out_a):
        os.remove(out_a)
    run([ar, "rcs", out_a] + objs)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(out_a, dst)
    log(f"  {os.path.basename(dst)} <- {len(objs)} 个 .o，{os.path.getsize(dst)/1024:.0f} KB")


def ensure_sm_headers(deps: str) -> None:
    """SpiderMonkey：补 js-config-32/64.h，并给 arm64 单开一个 include 目录。"""
    inc = os.path.join(SM, "include")
    src = os.path.join(deps, "spidermonkey", "include", "android")
    for f in ("js-config-32.h", "js-config-64.h"):
        if os.path.isfile(os.path.join(src, f)):
            shutil.copy2(os.path.join(src, f), os.path.join(inc, "android", f))
    a64 = os.path.join(inc, "android64")
    os.makedirs(a64, exist_ok=True)
    shutil.copy2(os.path.join(inc, "android", "js-config-64.h"),
                 os.path.join(a64, "js-config.h"))
    log("  spidermonkey: include/android(js-config-32/64) + include/android64/js-config.h")


# v3-deps-140 的 arm64 libjpeg.a 期望的结构体大小（boolean 4 字节）。
# 32 位那份是 452（boolean 1 字节），和 3.6 原版头文件一致，不用动。
JPEG_STRUCT_SIZE = 664

# 编译期断言：尺寸不是 664 就编译不过 → 构建当场失败，不会又变成「图片静默变黑」。
JPEG_SIZE_CHECK_C = """\
#include <stdio.h>
#include <stddef.h>
#include "jpeglib.h"
typedef char jpeg_decompress_struct_size_must_be_%d[
    (sizeof(struct jpeg_decompress_struct) == %d) ? 1 : -1];
"""


def ensure_jpeg_headers() -> bool:
    """jpeg：arm64 的 .a 要 4 字节 `boolean`，3.6 的头是 1 字节 → 单开 include/android64。

    详见模块 docstring 第 4 条。一句话：结构体大小对不上时
    `jpeg_CreateDecompress()` 会 `ERREXIT`，而 cocos 的 `myErrorExit()` 只 longjmp、
    不打印，最后就是「所有 .jpg 背景变黑、png 正常」这种毫无线索的症状。
    """
    inc = os.path.join(EXT, "jpeg", "include")
    a64 = os.path.join(inc, "android64")
    os.makedirs(a64, exist_ok=True)
    for f in ("jpeglib.h", "jmorecfg.h", "jerror.h"):
        shutil.copy2(os.path.join(inc, "android", f), os.path.join(a64, f))
    text = open(os.path.join(inc, "android", "jconfig.h"), encoding="utf-8").read()
    text = re.sub(r"typedef\s+unsigned\s+char\s+boolean\s*;",
                  "/* arm64 预编译库要 4 字节 boolean（jmorecfg.h 的 enum），这里不能 typedef */",
                  text)
    text = re.sub(r"(?m)^#define[ \t]+HAVE_BOOLEAN\b.*$",
                  "/* HAVE_BOOLEAN 不能定义，否则 jmorecfg.h 不会提供 boolean */", text)
    if re.search(r"(?m)^#define[ \t]+HAVE_BOOLEAN\b", text) or \
       "typedef unsigned char boolean" in text:
        log("!! jconfig.h 里 boolean 那两句没删干净，arm64 结构体大小会对不上")
        return False
    with open(os.path.join(a64, "jconfig.h"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    cc, _ar, sysroot = toolchain()
    src = os.path.join(WORK, "jpeg-size-check.c")
    with open(src, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(JPEG_SIZE_CHECK_C % (JPEG_STRUCT_SIZE, JPEG_STRUCT_SIZE))
    proc = subprocess.run([cc, "-c", f"--sysroot={sysroot}", f"-I{a64}",
                           "-o", os.path.join(WORK, "jpeg-size-check.o"), src],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"!! arm64 的 sizeof(struct jpeg_decompress_struct) 不等于 {JPEG_STRUCT_SIZE}，"
            "会和预编译 libjpeg.a 对不上（所有 .jpg 会变黑）")
        log("   " + (proc.stderr or proc.stdout).strip().replace("\n", "\n   ")[:800])
        return False
    log(f"  jpeg: include/android64（boolean 4 字节，sizeof={JPEG_STRUCT_SIZE}，与 .a 一致）")
    return True


def patch_android_mk() -> None:
    """三个 Android.mk 按 ABI 选头文件目录（幂等：已经是新版就跳过）。"""
    sm_mk = os.path.join(SM, "prebuilt", "android", "Android.mk")
    text = open(sm_mk, encoding="utf-8").read()
    if "SM_INCLUDES" not in text:
        new = text.replace(
            "LOCAL_EXPORT_C_INCLUDES := $(LOCAL_PATH)/../../include/android",
            "ifeq ($(TARGET_ARCH_ABI),arm64-v8a)\n"
            "# 64 位要用 PUNBOX64 那份 js-config（32 位是 NUNBOX32），ABI 必须和 .a 对上\n"
            "SM_INCLUDES := $(LOCAL_PATH)/../../include/android64 $(LOCAL_PATH)/../../include/android\n"
            "else\n"
            "SM_INCLUDES := $(LOCAL_PATH)/../../include/android\n"
            "endif\n\n"
            "LOCAL_EXPORT_C_INCLUDES := $(SM_INCLUDES)", 1)
        open(sm_mk, "w", encoding="utf-8", newline="\n").write(new)
        log("  spidermonkey/prebuilt/android/Android.mk: 按 ABI 选 js-config")
    curl_mk = os.path.join(EXT, "curl", "prebuilt", "android", "Android.mk")
    text = open(curl_mk, encoding="utf-8").read()
    if "CURL_INCLUDES" not in text:
        new = text.replace(
            "LOCAL_EXPORT_C_INCLUDES := $(LOCAL_PATH)/../../include/android",
            "ifeq ($(TARGET_ARCH_ABI),arm64-v8a)\n"
            "# arm64 用 7.52 的头（自带按架构选 curlbuild），32 位保持 7.26\n"
            "CURL_INCLUDES := $(LOCAL_PATH)/../../include/android64\n"
            "else\n"
            "CURL_INCLUDES := $(LOCAL_PATH)/../../include/android\n"
            "endif\n\n"
            "LOCAL_EXPORT_C_INCLUDES := $(CURL_INCLUDES)", 1)
        open(curl_mk, "w", encoding="utf-8", newline="\n").write(new)
        log("  curl/prebuilt/android/Android.mk: 按 ABI 选头文件目录")
    jpeg_mk = os.path.join(EXT, "jpeg", "prebuilt", "android", "Android.mk")
    text = open(jpeg_mk, encoding="utf-8").read()
    if "JPEG_INCLUDES" not in text:
        new = text.replace(
            "LOCAL_EXPORT_C_INCLUDES := $(LOCAL_PATH)/../../include/android",
            "ifeq ($(TARGET_ARCH_ABI),arm64-v8a)\n"
            "# arm64 的 .a 用 4 字节 boolean（结构体 664），32 位那两份用 1 字节（632/452）\n"
            "JPEG_INCLUDES := $(LOCAL_PATH)/../../include/android64\n"
            "else\n"
            "JPEG_INCLUDES := $(LOCAL_PATH)/../../include/android\n"
            "endif\n\n"
            "LOCAL_EXPORT_C_INCLUDES := $(JPEG_INCLUDES)", 1)
        open(jpeg_mk, "w", encoding="utf-8", newline="\n").write(new)
        log("  jpeg/prebuilt/android/Android.mk: 按 ABI 选头文件目录")


def main() -> int:
    ap = argparse.ArgumentParser(description="准备 arm64-v8a 的引擎依赖")
    ap.add_argument("--check", action="store_true", help="只报告，不动文件")
    ap.add_argument("--verbose", action="store_true", help="把 git/gcc 的输出也打出来")
    args = ap.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    cc, ar, sysroot = toolchain()
    if not os.path.isfile(cc):
        log(f"!! 找不到 NDK 工具链 {cc}")
        return 1

    # 依赖仓库的检出目录（和 tag 对应，重跑时复用）
    deps = os.path.join(WORK, "deps-arm64")
    era = os.path.join(WORK, "deps-3.6era")
    if args.check:
        missing = [n for n in list(PREBUILT_LIBS) + ["spidermonkey", "curl", "openssl"]
                   if not os.path.isdir(os.path.join(deps, n, "prebuilt", "android", "arm64-v8a"))]
        log(f"依赖仓库 {DEPS_TAG} 里缺 arm64 的：{missing or '（无）'}")
        for p in ("chipmunk", "websockets"):
            dst = os.path.join(EXT, p, "prebuilt", "android", "arm64-v8a", f"lib{p}.a")
            log(f"自建 {p}: {'有' if os.path.isfile(dst) else '缺'} {os.path.relpath(dst, ROOT)}")
        jcfg = os.path.join(EXT, "jpeg", "include", "android64", "jconfig.h")
        ok = os.path.isfile(jcfg) and not re.search(
            r"(?m)^#define[ \t]+HAVE_BOOLEAN\b", open(jcfg, encoding="utf-8").read())
        log(f"jpeg arm64 头 (include/android64, boolean 4 字节 / sizeof={JPEG_STRUCT_SIZE}): "
            f"{'就绪' if ok else '缺（跑一次不带 --check 的）'}")
        return 0

    log(f"1) 取依赖仓库 {DEPS_TAG} 的 arm64 部分（稀疏，只下要用的）")
    paths = [f"{n}/prebuilt/android/arm64-v8a" for n in
             list(PREBUILT_LIBS) + ["chipmunk", "curl", "websockets", "zlib"]]
    paths += ["openssl/prebuilt/android/arm64-v8a",
              "spidermonkey/prebuilt/android/arm64-v8a", "spidermonkey/include",
              "curl/include"]
    if not clone_sparse(DEPS_REPO, DEPS_TAG, deps, paths):
        log("!! 依赖仓库拉取失败（网络？）")
        return 1

    log("2) 装预编译 .a（curl 那份还要带上 openssl 的 ssl/crypto）")
    for name, libs in PREBUILT_LIBS.items():
        dst = os.path.join(EXT, name, "prebuilt", "android", "arm64-v8a")
        os.makedirs(dst, exist_ok=True)
        for lib in libs:
            src = os.path.join(deps, name, "prebuilt", "android", "arm64-v8a", lib)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(dst, lib))
        log(f"  {name}: {len(os.listdir(dst))} 个 .a")
    for lib in CURL_LIBS:
        shutil.copy2(os.path.join(deps, "curl", "prebuilt", "android", "arm64-v8a", lib),
                     os.path.join(EXT, "curl", "prebuilt", "android", "arm64-v8a", lib))
    for lib in OPENSSL_LIBS:
        shutil.copy2(os.path.join(deps, "openssl", "prebuilt", "android", "arm64-v8a", lib),
                     os.path.join(EXT, "curl", "prebuilt", "android", "arm64-v8a", lib))
    log(f"  curl(+openssl): {len(os.listdir(os.path.join(EXT, 'curl', 'prebuilt', 'android', 'arm64-v8a')))} 个 .a")
    sm_dst = os.path.join(SM, "prebuilt", "android", "arm64-v8a")
    os.makedirs(sm_dst, exist_ok=True)
    shutil.copy2(os.path.join(deps, "spidermonkey", "prebuilt", "android", "arm64-v8a",
                              "libjs_static.a"), os.path.join(sm_dst, "libjs_static.a"))
    log(f"  spidermonkey: {os.path.getsize(os.path.join(sm_dst, 'libjs_static.a'))/1048576:.1f} MB")

    log("3) chipmunk：依赖包是 7.0，cocos2d-x 3.6 要 6.2.1 → 用上游源码编")
    ck = os.path.join(WORK, f"chipmunk-{CHIPMUNK_TAG.replace('Chipmunk-', '')}")
    if not clone_full(CHIPMUNK_REPO, CHIPMUNK_TAG, ck):
        log("!! chipmunk 源码拉取失败")
        return 1
    srcs = [os.path.join(ck, "src", f) for f in sorted(os.listdir(os.path.join(ck, "src")))
            if f.endswith(".c")]
    cdir = os.path.join(ck, "src", "constraints")
    if os.path.isdir(cdir):     # ⚠️ 约束在子目录里，漏了会缺 26 个符号
        srcs += [os.path.join(cdir, f) for f in sorted(os.listdir(cdir)) if f.endswith(".c")]
    obj = os.path.join(WORK, "chipmunk-arm64-obj")
    failed = compile_dir(srcs, obj, cc, sysroot,
                         [f"-I{ck}/include", f"-I{ck}/include/chipmunk", f"-I{ck}/src"])
    if failed:
        log(f"!! chipmunk 编译失败：{failed}")
        return 1
    archive(ar, obj, os.path.join(WORK, "libchipmunk-arm64.a"),
            os.path.join(EXT, "chipmunk", "prebuilt", "android", "arm64-v8a", "libchipmunk.a"))

    log("4) libwebsockets：依赖包是 2.1(API 改名)，3.6 要 v1.23 → 用上游源码编")
    lws = os.path.join(WORK, "lws-1.23")
    if not clone_full(LWS_REPO, LWS_TAG, lws):
        log("!! libwebsockets 源码拉取失败")
        return 1
    lib = os.path.join(lws, "lib")
    cfg = os.path.join(lib, "config.h")
    if not os.path.isfile(cfg):
        open(cfg, "w", encoding="utf-8", newline="\n").write(LWS_CONFIG_H)
        log("  生成 config.h（官方那份是 CMake 生成的，仓库里没有）")
    shim = os.path.join(lib, "cocos_android_compat.c")
    if not os.path.isfile(shim):
        open(shim, "w", encoding="utf-8", newline="\n").write(LWS_COMPAT_C)
        log("  补 getdtablesize() shim（bionic 没有这个 BSD 函数）")
    hdr = os.path.join(lib, "libwebsockets.h")
    text = open(hdr, encoding="utf-8", errors="replace").read()
    if "http_proxy_address" not in text:
        # ⚠️ cocos 的头文件比上游多这 3 个字段；不补回同一位置，两边 struct 布局不一致，
        #    库会把 info->gid 读成 0 → setgid(0) → Android seccomp SIGSYS。
        text = text.replace(
            "\tstruct libwebsocket_extension *extensions;\n",
            "\tstruct libwebsocket_extension *extensions;\n"
            "\t/* cocos 补丁：与 cocos2d-x 自带的头文件保持同一布局，见 build_arm64_deps.py */\n"
            "\tstruct lws_token_limits *token_limits;\n", 1)
        text = text.replace(
            "\tconst char *ssl_cipher_list;\n",
            "\tconst char *ssl_cipher_list;\n"
            "\tconst char *http_proxy_address;\n"
            "\tuint32_t http_proxy_port;\n", 1)
        open(hdr, "w", encoding="utf-8", newline="\n").write(text)
        log("  补 struct lws_context_creation_info 的 3 个字段（和 cocos 头文件对齐）")
    srcs = [os.path.join(lib, s + ".c") for s in LWS_SRCS]
    obj = os.path.join(WORK, "lws-arm64-obj")
    failed = compile_dir(srcs, obj, cc, sysroot, [f"-I{lib}"])
    if failed:
        log(f"!! libwebsockets 编译失败：{failed}")
        return 1
    archive(ar, obj, os.path.join(WORK, "libwebsockets-arm64.a"),
            os.path.join(EXT, "websockets", "prebuilt", "android", "arm64-v8a", "libwebsockets.a"))

    log("5) 头文件按 ABI 分 + 复原 32 位那份 curl 头")
    if clone_sparse(DEPS_REPO, DEPS_ERA_TAG, era, ["curl/include", "websockets/include"]):
        cur = os.path.join(EXT, "curl", "include")
        shutil.copytree(os.path.join(era, "curl", "include", "android"),
                        os.path.join(cur, "android"), dirs_exist_ok=True)
        shutil.copytree(os.path.join(deps, "curl", "include", "android"),
                        os.path.join(cur, "android64"), dirs_exist_ok=True)
        log("  curl/include: android=7.26（32 位）/ android64=7.52（arm64）")
    ensure_sm_headers(deps)
    if not ensure_jpeg_headers():
        return 1
    patch_android_mk()

    log(r"完成。接下来：.\build.ps1 -Engine -Abi arm64-v8a（打包加 -PackAbis arm64-v8a）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
