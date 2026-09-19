"""把原版 libcocos2djs.so 里挖出来的参考数据存成文件，方便后续对照。

    python ref_dump.py
"""

from __future__ import annotations

import io
import os
import re
from collections import defaultdict

SO = r"E:\code\zcsmw\game\lib\armeabi\libcocos2djs.so"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)))


def main():
    d = io.open(SO, "rb").read()
    os.makedirs(OUT, exist_ok=True)

    # 1) 自研绑定的全部导出函数
    syms = defaultdict(set)
    for m in re.finditer(rb"js_oppai_([a-z]+)_([A-Za-z0-9_]+)", d):
        mod, fn = m.group(1).decode(), m.group(2).decode()
        # 去掉 C++ mangled 后缀
        fn = re.sub(r"P9JSContext.*$", "", fn)
        if fn:
            syms[mod].add(fn)
    with io.open(os.path.join(OUT, "oppai_binding_api.txt"), "w", encoding="utf-8") as fh:
        for mod in sorted(syms):
            fh.write(f"# {mod}\n")
            for fn in sorted(syms[mod]):
                fh.write(f"  {fn}\n")
    print("oppai_binding_api.txt:", sum(len(v) for v in syms.values()), "个函数")

    # 2) register_all_* 符号
    with io.open(os.path.join(OUT, "register_symbols.txt"), "w", encoding="utf-8") as fh:
        for m in sorted(set(re.findall(rb"register_all_[A-Za-z0-9_]+", d))):
            s = re.sub(r"P9JSContext.*$", "", m.decode())
            fh.write(s + "\n")
    print("register_symbols.txt")

    # 3) JNI 硬依赖的 Java 类
    with io.open(os.path.join(OUT, "jni_classes.txt"), "w", encoding="utf-8") as fh:
        pat = re.compile(rb"[a-z][a-z0-9_]*(?:/[a-z0-9_]+){1,6}/[A-Z][A-Za-z0-9_]*")
        seen = set()
        for m in pat.finditer(d):
            s = m.group(0).decode("latin-1")
            if 12 <= len(s) <= 90 and s not in seen:
                seen.add(s)
                fh.write(s + "\n")
    print("jni_classes.txt:", len(seen), "个类名")

    # 4) 编进去的源文件（还原 Android.mk 用）
    with io.open(os.path.join(OUT, "compiled_sources.txt"), "w", encoding="utf-8") as fh:
        for m in sorted(set(re.findall(rb"jsb_[a-z0-9_]+\.cpp", d))):
            fh.write(m.decode() + "\n")
    print("compiled_sources.txt")

    # 5) 版本信息
    with io.open(os.path.join(OUT, "version.txt"), "w", encoding="utf-8") as fh:
        fh.write("engine   : cocos2d-x 3.6  (COCOS2D_VERSION 0x00030600)\n")
        fh.write("js       : SpiderMonkey 33.1.1 (JavaScript-C33.1.1)\n")
        fh.write("jsc magic: 0xb973c02c = SM33.1.1 的 XDR_BYTECODE_VERSION\n")
        fh.write("abi      : armeabi (ARMv5TE)\n")
        fh.write("so size  : %.2f MB\n" % (len(d) / 1048576))
    print("version.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
