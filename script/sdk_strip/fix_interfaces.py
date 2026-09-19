"""把名字形如 I<大写开头> 的 SDK 桩从 class 改成 interface。

背景：真实 SDK 里 com.tencent.mm.sdk.openapi.IWXAPI 是接口，
但 gen_stubs.py 是照着方法调用生成的，不知道它是接口还是类，
默认都做成了 class。于是游戏里这句：

    IWXAPI api = WXAPIFactory.createWXAPI(ctx, APP_ID);

在 Dalvik 校验时直接抛：
    IncompatibleClassChangeError: Found class ...IWXAPI, but interface was expected

修法：
  1) I*.smali 改成 interface（方法变成 public abstract，去掉方法体）
  2) 生成一个 I*Impl 实现类，把原来的方法体搬过去
  3) 把 new-instance I* 的地方改成 new-instance I*Impl
"""

from __future__ import annotations

import io
import os
import re
# --- 路径自举（项目已重排：脚本在 script/、服务端在 server/）---
# 从自己往上找带 _paths.py 的那一层，把它和 server/ 都塞进 sys.path。
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402




ROOT = r"E:\code\zcsmw\game\smali"

# 只处理这些包下的桩
PKG_PREFIXES = (
    "com\\tencent\\mm\\sdk",
    "com\\sina\\weibo",
    "com\\tencent\\android\\tpush",
    "com\\tencent\\bugly",
    "com\\tendcloud",
    "com\\quicksdk",
    "com\\chukong",
)

# 已知必须保持 class 的（哪怕是 I 开头）
WHITELIST_CLASS = set()


def find_stub_interfaces() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        if not any(rel.lower().startswith(p.lower().rstrip("\\")) for p in PKG_PREFIXES):
            continue
        for fn in filenames:
            if not fn.endswith(".smali"):
                continue
            name = fn[:-6]
            # I 开头 + 第二个字符大写 → 大概率是接口
            if len(name) >= 3 and name[0] == "I" and name[1].isupper() and name not in WHITELIST_CLASS:
                out.append(os.path.join(dirpath, fn))
    return out


def convert(path: str) -> bool:
    """把一个 class 桩转成 interface + 生成 Impl。"""
    s = io.open(path, encoding="utf-8").read()
    if ".class public interface" in s or ".class public abstract interface" in s:
        return False

    m = re.search(r"^\.class public (L[\w/$]+;)", s, re.M)
    if not m:
        return False
    desc = m.group(1)                      # Lcom/tencent/mm/sdk/openapi/IWXAPI;
    cls = desc[1:-1]                       # com/tencent/mm/sdk/openapi/IWXAPI
    simple = cls.rsplit("/", 1)[-1]
    impl_cls = cls + "Impl"
    impl_desc = "L" + impl_cls + ";"
    impl_simple = simple + "Impl"

    # 接口：方法体去掉
    iface = re.sub(r"^\.class public L[\w/$]+;",
                   ".class public interface abstract " + desc, s, count=1, flags=re.M)
    iface = iface.replace(".super Ljava/lang/Object;", ".super Ljava/lang/Object;", 1)

    def strip_body(mm: re.Match) -> str:
        sig = mm.group(1)
        return sig + "\n.end method\n"

    iface = re.sub(r"(\.method public [^\n]+)\n(?:.*?\n)??\.end method\n",
                   strip_body, iface, flags=re.S)

    # Impl：保留原来的方法体
    impl = s
    impl = re.sub(r"^\.class public L[\w/$]+;",
                  ".class public " + impl_desc, impl, count=1, flags=re.M)
    impl = impl.replace('.source "%s.java"' % simple, '.source "%s.java"' % impl_simple, 1)
    impl = impl.replace(".super Ljava/lang/Object;",
                        ".super Ljava/lang/Object;\n.implements " + desc, 1)

    io.open(os.path.join(os.path.dirname(path), impl_simple + ".smali"),
            "w", encoding="utf-8", newline="\n").write(impl)
    io.open(path, "w", encoding="utf-8", newline="\n").write(iface)
    return True


def fix_instantiations(impl_map: dict[str, str]) -> int:
    """把 new-instance I* 改成 new-instance I*Impl。"""
    n = 0
    for dirpath, _, filenames in os.walk(ROOT):
        for fn in filenames:
            if not fn.endswith(".smali"):
                continue
            p = os.path.join(dirpath, fn)
            s = io.open(p, encoding="utf-8").read()
            orig = s
            for desc, impl_desc in impl_map.items():
                s = s.replace("new-instance", "new-instance")  # no-op，保持结构
                s = re.sub(r"(new-instance\s+\w+,\s+)" + re.escape(desc), r"\g<1>" + impl_desc, s)
                s = re.sub(r"(invoke-direct\s+\{\s*\w+\s*\},\s+)" + re.escape(desc) + r"-><init>",
                           r"\g<1>" + impl_desc + "-><init>", s)
            if s != orig:
                io.open(p, "w", encoding="utf-8", newline="\n").write(s)
                n += 1
    return n


def main():
    targets = find_stub_interfaces()
    print("发现 %d 个疑似接口的桩：" % len(targets))
    for t in targets:
        print("  ", os.path.relpath(t, ROOT))

    impl_map = {}
    changed = 0
    for t in targets:
        s = io.open(t, encoding="utf-8").read()
        m = re.search(r"^\.class public (L[\w/$]+;)", s, re.M)
        if not m:
            continue
        desc = m.group(1)
        if convert(t):
            impl_map[desc] = "L" + desc[1:-1] + "Impl;"
            changed += 1

    print("\n转换了 %d 个接口" % changed)
    if impl_map:
        n = fix_instantiations(impl_map)
        print("修正了 %d 个文件里的实例化" % n)


if __name__ == "__main__":
    main()
