"""打包 APK —— 走完整的 apktool 流程。

设计原则：**所有改动都落在解包目录里，然后让 apktool 打完整包**。
不再手动拼 zip —— 那样虽然快一点，但绕过了 aapt2 的资源组装，
`--strip` 也只能靠字符串前缀匹配，不够可控。

    python tools/build_apk.py --host 10.110.29.230

流程：
    1. 改解包目录里的资源
         assets/srcex/urlconfig.jsc        CDN 地址（原地等长替换）
         assets/src/util/server.jsc        登录服务地址
         assets/src/data/share.jsc         分享服务地址
         assets/src/patch/project.manifest 热更地址
         assets/project.json               jsList 里加 hook.js
         assets/src/patch/hook.js          写入探针
       并删掉用不到的：
         assets/res/adimage, adcolumn      广告图
         assets/bdpwxpayplugin.apk         百度支付插件
         assets/quicksdk.xml / lib/*.so    已由 strip.py 删过
    2. apktool b <解包目录> -o <work>/zcsmw-mod.apk --no-crunch
    3. zipalign -f -p 4
    4. apksigner sign（v1 + v2）

所有步骤都是**幂等**的：已经替换过的地址会被识别出来直接跳过。

路径可用环境变量覆盖：GS_APK_DIR / GS_WORK_DIR / GS_JAVA_HOME / GS_BUILD_TOOLS
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\apk\zcsmw")
WORK = os.environ.get("GS_WORK_DIR", r"E:\code\apk\work")
DEFAULT_HOOK = os.path.join(BASE_DIR, "client", "hook.js")
APKTOOL = os.environ.get("GS_APKTOOL", r"E:\code\apk\apktool.bat")

BUILD_TOOLS = os.environ.get("GS_BUILD_TOOLS", r"D:\Android\android-sdk\build-tools\36.0.0")
JAVA_HOME = os.environ.get("GS_JAVA_HOME", r"D:\java\zulu17.68.203-ca-jdk17.0.20.1-win_x64")
KEYSTORE = os.path.join(WORK, "debug.keystore")

OLD_HOST = b"cdn.shuangmawei.net"          # 19 字节
OLD_WWW = b"www.shuangmawei.net"           # 19 字节
OLD_OAUTH = b"http://114.55.66.97:16840"   # 25 字节
OLD_SHARE = b"http://114.55.66.97:14589"   # 25 字节

# 直接删掉的资源（已经停服/用不到）
DROP_ASSETS = (
    "assets/res/adimage",                     # 广告原图（广告服务早停）
    "assets/res/adcolumn",
    "assets/bdpwxpayplugin.apk",              # 百度支付插件
    "assets/quicksdk.xml",                    # QuickSDK 配置
    "assets/com.qk.plugin.qkfx.Manager",      # QuickSDK 插件管理器
    "assets/open_sdk_file.dat",               # QQ 互联 SDK
    # apktool 把「不认识的散装文件」放在 unknown/ 下，打包时会原样导回去。
    # 这里只剩 SDK 残留：微博的 CA 证书 x2 + 百度渠道号，整目录删掉。
    "unknown",
)


def log(*a):
    print("[build]", *a, flush=True)


# ---------------------------------------------------------------------------
# 地址替换（必须等长：jsc 里字符串是长度前缀存的）
# ---------------------------------------------------------------------------
def make_host_token(host: str, port: int) -> bytes:
    token = f"{host}:{port}".encode()
    if len(token) != len(OLD_HOST):
        raise SystemExit(
            f"host:port 必须是 {len(OLD_HOST)} 字节，'{token.decode()}' 是 {len(token)} 字节"
        )
    return token


def make_login_base(host: str, port: int) -> bytes:
    token = f"http://{host}:{port}".encode()
    if len(token) != len(OLD_OAUTH):
        raise SystemExit(
            f"登录服务地址必须是 {len(OLD_OAUTH)} 字节，'{token.decode()}' 是 {len(token)} 字节 "
            f"—— 端口请用 4 位数"
        )
    return token


def _replace_in_file(path: str, pairs, what: str) -> int:
    """把 (旧, 新) 逐个做原地等长替换；已经换过的直接跳过（幂等）。"""
    with open(path, "rb") as fh:
        data = fh.read()
    orig = data
    done = 0
    for old, new in pairs:
        if new in data:
            continue                      # 已经替换过
        if old not in data:
            log(f"  ! {os.path.relpath(path, APK_DIR)}: 找不到 {old.decode()}，跳过")
            continue
        data = data.replace(old, new)
        done += 1
    if data != orig:
        with open(path, "wb") as fh:
            fh.write(data)
    log(f"  {what}: 替换 {done} 处")
    return done


def prune_do_not_compress() -> None:
    """剪掉 apktool.yml 里 doNotCompress 指向已删文件的条目。

    这些残留条目会让 apktool 把已经不存在的路径也当成「不压缩」，
    有时还会把原始 APK 里的 unknown file 一起带进产物。
    注意不要动 `arsc` / `png` / `mp3` 这种**扩展名**条目（它们不是路径）。
    """
    path = os.path.join(APK_DIR, "apktool.yml")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    out, removed = [], []
    in_dnc = False
    for line in lines:
        if line.startswith("doNotCompress:"):
            in_dnc = True
            out.append(line)
            continue
        if in_dnc:
            if line.startswith("- "):
                rel = line[2:].strip()
                if "/" in rel and not os.path.exists(os.path.join(APK_DIR, rel)):
                    removed.append(rel)
                    continue
            elif line.strip():
                in_dnc = False
        out.append(line)

    if removed:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out))
        log(f"  apktool.yml: 剪掉 {len(removed)} 条已失效的 doNotCompress")
        for r in removed[:8]:
            log(f"      - {r}")


def prepare_assets(host: str, port: int, login_port: int, hook_path: str) -> None:
    token = make_host_token(host, port)
    login_base = make_login_base(host, login_port)
    log(f"CDN      -> {token.decode()}")
    log(f"登录服务 -> {login_base.decode()}")

    _replace_in_file(os.path.join(APK_DIR, "assets/srcex/urlconfig.jsc"),
                     [(OLD_HOST, token)], "urlconfig.jsc")
    _replace_in_file(os.path.join(APK_DIR, "assets/src/util/server.jsc"),
                     [(OLD_OAUTH, login_base)], "server.jsc")
    _replace_in_file(os.path.join(APK_DIR, "assets/src/data/share.jsc"),
                     [(OLD_SHARE, login_base)], "share.jsc")
    _replace_in_file(os.path.join(APK_DIR, "assets/src/patch/project.manifest"),
                     [(OLD_HOST, token), (OLD_WWW, token)], "project.manifest")

    # project.json：jsList 里加探针
    pj = os.path.join(APK_DIR, "assets/project.json")
    cfg = json.loads(open(pj, "r", encoding="utf-8").read())
    js_list = cfg.setdefault("jsList", [])
    if "src/patch/hook.js" not in js_list:
        js_list.append("src/patch/hook.js")
        with open(pj, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(cfg, indent=4, ensure_ascii=False))
        log("  project.json: 已加入 src/patch/hook.js")
    else:
        log("  project.json: 已包含探针")

    # 写探针（把 __CDN_BASE__ 换成真实地址）
    with open(hook_path, "rb") as fh:
        hook_js = fh.read()
    if b"__CDN_BASE__" not in hook_js:
        raise SystemExit("hook.js 里没有 __CDN_BASE__ 占位符")
    hook_js = hook_js.replace(b"__CDN_BASE__", f"http://{host}:{port}".encode())
    dst = os.path.join(APK_DIR, "assets/src/patch/hook.js")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(hook_js)
    log(f"  探针已写入 assets/src/patch/hook.js ({len(hook_js)} B)")

    # 删无用资源
    for rel in DROP_ASSETS:
        p = os.path.join(APK_DIR, rel)
        if os.path.exists(p):
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            log(f"  删除 {rel}")

    # 剪掉 apktool.yml 里指向已删文件的 doNotCompress 条目
    prune_do_not_compress()


# ---------------------------------------------------------------------------
# apktool / zipalign / apksigner
# ---------------------------------------------------------------------------
def run(cmd, **kw):
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    env["PATH"] = os.path.join(JAVA_HOME, "bin") + os.pathsep + env.get("PATH", "")
    subprocess.run(cmd, check=True, env=env, **kw)


def apktool_build(out_apk: str) -> None:
    log("apktool b（完整打包，可能需要 1 分钟左右）...")
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    env["PATH"] = os.path.join(JAVA_HOME, "bin") + os.pathsep + env.get("PATH", "")
    subprocess.run(
        [APKTOOL, "b", APK_DIR, "-o", out_apk, "--no-crunch"],
        check=True, env=env, input=b"\n",
    )


def align(path: str) -> str:
    out = path.replace(".apk", "-aligned.apk")
    if os.path.exists(out):
        os.remove(out)
    run([os.path.join(BUILD_TOOLS, "zipalign.exe"), "-f", "-p", "4", path, out])
    log("zipalign 完成")
    return out


def ensure_keystore() -> None:
    if os.path.exists(KEYSTORE):
        return
    log("生成调试签名证书 ...")
    keytool = os.path.join(JAVA_HOME, "bin", "keytool.exe")
    run([
        keytool, "-genkeypair", "-v", "-keystore", KEYSTORE, "-alias", "oppai",
        "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
        "-storepass", "android", "-keypass", "android",
        "-dname", "CN=Oppai Emulator, OU=Dev, O=Emu, L=CN, S=CN, C=CN",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sign(path: str) -> str:
    ensure_keystore()
    out = path.replace("-aligned.apk", "-signed.apk")
    if os.path.exists(out):
        os.remove(out)
    run([
        os.path.join(BUILD_TOOLS, "apksigner.bat"), "sign",
        "--ks", KEYSTORE, "--ks-pass", "pass:android", "--key-pass", "pass:android",
        "--ks-key-alias", "oppai",
        "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
        "--out", out, path,
    ])
    log("签名完成")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="10.110.29.230")
    ap.add_argument("--port", type=int, default=18080, help="CDN 端口（必须 5 位）")
    ap.add_argument("--login-port", type=int, default=8080, help="登录端口（必须 4 位）")
    ap.add_argument("--hook", default=DEFAULT_HOOK)
    ap.add_argument("--out", default=os.path.join(WORK, "zcsmw-mod.apk"))
    ap.add_argument("--skip-prepare", action="store_true", help="只打包，不重新改资源")
    ap.add_argument("--keep-intermediate", action="store_true", help="保留 aligned 中间产物")
    args = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)

    if not args.skip_prepare:
        prepare_assets(args.host, args.port, args.login_port, args.hook)

    apktool_build(args.out)
    aligned = align(args.out)
    signed = sign(aligned)
    if not args.keep_intermediate:
        os.remove(args.out)
        os.remove(aligned)
        log("已清理中间产物（--keep-intermediate 可保留）")
    log("最终产物:", signed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
