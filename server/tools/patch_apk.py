"""把客户端改成可以连本地模拟服的版本。

做的事：
  1. 把 assets/srcex/urlconfig.jsc 里的 cdn.shuangmawei.net 原地替换成
     <host>:<port>（长度必须一致，字节码里的字符串是长度前缀存储的）
  2. assets/src/util/server.jsc / data/share.jsc 里硬编码的旧服务器地址
  3. project.json 的 jsList 里追加 src/patch/hook.js（明文 JS 探针）
  4. 把 apktool b --no-apk 重新编译出来的 classes*.dex 注入回去
  5. 改写 assets/src/patch/project.manifest 里的 URL
  6. zipalign + apksigner 重新签名

路径都可以用环境变量覆盖：

    GS_APK_SRC    原版 APK（默认 E:\\code\\apk\\zcsmw.apk）
    GS_APK_DIR    apktool 解包目录（默认 E:\\code\\apk\\zcsmw）
    GS_WORK_DIR   产物目录（默认 E:\\code\\apk\\work）
    GS_BUILD_TOOLS Android build-tools（zipalign/apksigner）
    GS_JAVA_HOME  JDK

用法:
    python tools/patch_apk.py --host 10.110.29.230 --port 18080 --login-port 8080
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WORK = os.environ.get("GS_WORK_DIR", r"E:\code\apk\work")
APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\apk\zcsmw")
DEFAULT_SRC = os.environ.get("GS_APK_SRC", r"E:\code\apk\zcsmw.apk")
DEFAULT_HOOK = os.path.join(BASE_DIR, "client", "hook.js")

BUILD_TOOLS = os.environ.get("GS_BUILD_TOOLS", r"D:\Android\android-sdk\build-tools\36.0.0")
JAVA_HOME = os.environ.get("GS_JAVA_HOME", r"D:\java\zulu17.68.203-ca-jdk17.0.20.1-win_x64")
KEYSTORE = os.path.join(WORK, "debug.keystore")

# 重新编译出来的 dex（apktool b --no-apk 的产物）
DEFAULT_DEX_DIR = os.path.join(APK_DIR, "build", "apk")

OLD_HOST = b"cdn.shuangmawei.net"          # 19 字节
OLD_WWW = b"www.shuangmawei.net"           # 19 字节
OLD_OAUTH = b"http://114.55.66.97:16840"   # 25 字节 —— server.js 里硬编码的 OAUTH_HOST
OLD_SHARE = b"http://114.55.66.97:14589"   # 25 字节 —— share.js 里的分享奖励服务


def log(*a):
    print("[patch]", *a, flush=True)


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
            f"登录服务地址必须是 {len(OLD_OAUTH)} 字节（客户端 URL 是原地替换），"
            f"'{token.decode()}' 是 {len(token)} 字节 —— 端口请用 4 位数"
        )
    return token


def patch_urlconfig(data: bytes, token: bytes) -> bytes:
    n = data.count(OLD_HOST)
    if n != 1:
        raise SystemExit(f"urlconfig.jsc 中 cdn 主机名出现 {n} 次，预期 1 次")
    return data.replace(OLD_HOST, token)


def patch_server_js(data: bytes, login_base: bytes) -> bytes:
    if OLD_OAUTH not in data:
        raise SystemExit("server.jsc 里没找到 OAUTH_HOST")
    return data.replace(OLD_OAUTH, login_base)


def patch_share_js(data: bytes, login_base: bytes) -> bytes:
    if OLD_SHARE not in data:
        raise SystemExit("share.jsc 里没找到分享服务地址")
    return data.replace(OLD_SHARE, login_base)


def patch_manifest(data: bytes, token: bytes) -> bytes:
    return data.replace(OLD_HOST, token).replace(OLD_WWW, token)


def patch_project_json(data: bytes) -> bytes:
    cfg = json.loads(data.decode("utf-8"))
    js_list = cfg.setdefault("jsList", [])
    if "src/patch/hook.js" not in js_list:
        js_list.append("src/patch/hook.js")
    return json.dumps(cfg, indent=4, ensure_ascii=False).encode("utf-8")


def ensure_keystore():
    if os.path.exists(KEYSTORE):
        return
    log("生成调试签名证书 ...")
    keytool = os.path.join(JAVA_HOME, "bin", "keytool.exe")
    subprocess.run(
        [
            keytool, "-genkeypair", "-v",
            "-keystore", KEYSTORE,
            "-alias", "oppai",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            "-storepass", "android", "-keypass", "android",
            "-dname", "CN=Oppai Emulator, OU=Dev, O=Emu, L=CN, S=CN, C=CN",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def repack(src_apk: str, dst_apk: str, host: str, port: int, login_port: int, hook_path: str,
           dex_dir: str = None):
    token = make_host_token(host, port)
    login_base = make_login_base(host, login_port)
    log("替换 CDN 主机名 ->", token.decode())
    log("替换登录服务地址 ->", login_base.decode())

    # 重新编译出来的 dex（apktool b --no-apk 的产物），有就替换进包里
    dex_files = {}
    if dex_dir and os.path.isdir(dex_dir):
        for dex_name in ("classes.dex", "classes2.dex", "classes3.dex"):
            dex_path = os.path.join(dex_dir, dex_name)
            if os.path.exists(dex_path):
                with open(dex_path, "rb") as fh:
                    dex_files[dex_name] = fh.read()
        if dex_files:
            log("注入重新编译的 dex:", ", ".join(f"{k}={len(v)}B" for k, v in dex_files.items()))

    with open(hook_path, "rb") as fh:
        hook_js = fh.read()
    base = f"http://{host}:{port}".encode()
    if b"__CDN_BASE__" not in hook_js:
        raise SystemExit("hook.js 里没有 __CDN_BASE__ 占位符")
    hook_js = hook_js.replace(b"__CDN_BASE__", base)

    written = 0
    with zipfile.ZipFile(src_apk, "r") as zin:
        infos = zin.infolist()
        with zipfile.ZipFile(dst_apk, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in infos:
                name = info.filename
                upper = name.upper()
                if upper.startswith("META-INF/") and upper.endswith((".SF", ".RSA", ".DSA", ".MF")):
                    continue

                data = dex_files.get(name) or zin.read(name)
                new_info = zipfile.ZipInfo(name, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr

                if name == "assets/srcex/urlconfig.jsc":
                    data = patch_urlconfig(data, token)
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                elif name == "assets/src/util/server.jsc":
                    data = patch_server_js(data, login_base)
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                elif name == "assets/src/data/share.jsc":
                    data = patch_share_js(data, login_base)
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                elif name == "assets/src/patch/project.manifest":
                    data = patch_manifest(data, token)
                    new_info.compress_type = zipfile.ZIP_DEFLATED
                elif name == "assets/project.json":
                    data = patch_project_json(data)
                    new_info.compress_type = zipfile.ZIP_DEFLATED

                if info.compress_type == zipfile.ZIP_STORED:
                    new_info.compress_type = zipfile.ZIP_STORED
                zout.writestr(new_info, data)
                written += 1

            # 探针
            info = zipfile.ZipInfo("assets/src/patch/hook.js", date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(info, hook_js)
            written += 1

    log(f"重新打包完成，{written} 个条目 -> {dst_apk}")


def align(path: str) -> str:
    out = path.replace(".apk", "-aligned.apk")
    if os.path.exists(out):
        os.remove(out)
    subprocess.run([os.path.join(BUILD_TOOLS, "zipalign.exe"), "-f", "-p", "4", path, out], check=True)
    log("zipalign 完成")
    return out


def sign(path: str) -> str:
    ensure_keystore()
    env = dict(os.environ)
    env["JAVA_HOME"] = JAVA_HOME
    env["PATH"] = os.path.join(JAVA_HOME, "bin") + os.pathsep + env.get("PATH", "")
    out = path.replace("-aligned.apk", "-signed.apk")
    if os.path.exists(out):
        os.remove(out)
    subprocess.run(
        [
            os.path.join(BUILD_TOOLS, "apksigner.bat"), "sign",
            "--ks", KEYSTORE,
            "--ks-pass", "pass:android",
            "--key-pass", "pass:android",
            "--ks-key-alias", "oppai",
            "--v1-signing-enabled", "true",
            "--v2-signing-enabled", "true",
            "--out", out, path,
        ],
        check=True,
        env=env,
    )
    log("签名完成 ->", out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--hook", default=DEFAULT_HOOK)
    ap.add_argument("--host", default="10.110.29.230")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--login-port", type=int, default=8080)
    ap.add_argument("--out", default=os.path.join(WORK, "zcsmw-mod.apk"))
    ap.add_argument("--dex-dir", default=DEFAULT_DEX_DIR,
                    help="apktool b --no-apk 产出的 classes*.dex 目录；不存在就跳过")
    ap.add_argument("--no-dex", action="store_true", help="不注入 dex")
    args = ap.parse_args()

    os.makedirs(WORK, exist_ok=True)
    dex_dir = None if args.no_dex else args.dex_dir
    repack(args.src, args.out, args.host, args.port, args.login_port, args.hook, dex_dir)
    aligned = align(args.out)
    signed = sign(aligned)
    log("最终产物:", signed)


if __name__ == "__main__":
    main()
