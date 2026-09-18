"""分析「删掉 SDK 后还需要保留哪些桩类」。

思路：
  1. 定一组要删的 SDK 包前缀
  2. 扫描**不在**这些前缀下的 smali（也就是游戏自己的代码 + Android 支持库）
  3. 把其中对 SDK 类的引用（类型、方法签名、字段、父类、接口）全部收集出来
  4. 输出一份「必须保留的桩类清单」

用法：
    python tools/sdk_strip/analyze.py            # 报告
    python tools/sdk_strip/analyze.py --json out.json
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from collections import defaultdict

APK_DIR = os.environ.get("GS_APK_DIR", r"E:\code\apk\zcsmw")

# 要从包里删掉的 SDK 包前缀（smali 路径）
STRIP_PREFIXES = (
    "com/baidu",
    "com/duoku",
    "com/unionpay",
    "com/alipay",
    "com/gametalkingdata",
    "com/qk",
    "com/squareup",
    "com/ta/",
    "com/qq/",
    "com/slidingmenu",
    "com/sina",
    "com/tencent",
    "com/tendcloud",
    "com/talkingdata",
    "com/jg",
    "com/chukong",
    "com/UCMobile",
    "com/ut",
    "com/quicksdk",
    "com/kurogame",
)

# 这些是要保留的（游戏自己的）
KEEP_HINTS = ("org/cocos2dx", "com/cm/")

TYPE_RE = re.compile(r"L([A-Za-z0-9_/$]+);")
INVOKE_RE = re.compile(
    r"invoke-(static|virtual|direct|super|interface)\s+\{[^}]*\},\s*"
    r"L([A-Za-z0-9_/$]+);->([A-Za-z0-9_$<>]+)\(([^)]*)\)([^\s]*)"
)
FIELD_RE = re.compile(r"(sget|sput|iget|iput)-(\w+)\s+\{[^}]*\},\s*L([A-Za-z0-9_/$]+);->([A-Za-z0-9_$]+):([^\s]+)")
SUPER_RE = re.compile(r"^\.super\s+L([A-Za-z0-9_/$]+);", re.M)
IMPL_RE = re.compile(r"^\.implements\s+L([A-Za-z0-9_/$]+);", re.M)


def is_stripped(cls: str) -> bool:
    return any(cls == p.rstrip("/") or cls.startswith(p) for p in STRIP_PREFIXES)


def smali_files(root: str):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".smali"):
                yield os.path.join(dirpath, name)


def collect(apk_dir: str):
    """返回 kept_code -> 引用的 SDK 类信息"""
    needed = defaultdict(lambda: {
        "methods": set(),      # (name, args, ret, kind)
        "fields": set(),       # (name, type, static?)
        "as_super": 0,
        "as_impl": 0,
        "new_instance": 0,
        "cast_or_type": 0,
    })

    # 先把「要删的包」里的类名收集起来，方便识别
    sdk_dirs = []
    kept_dirs = []
    for d in ("smali", "smali_classes2", "smali_classes3"):
        p = os.path.join(apk_dir, d)
        if os.path.isdir(p):
            kept_dirs.append(p)

    for root in kept_dirs:
        for path in smali_files(root):
            rel = os.path.relpath(path, root).replace("\\", "/")
            if not rel.endswith(".smali"):
                continue
            # 判断这个文件属于哪一边
            if is_stripped(rel[:-6]):
                sdk_dirs.append(path)
                continue
            try:
                src = io.open(path, encoding="utf-8").read()
            except Exception:  # noqa: BLE001
                continue

            for m in SUPER_RE.finditer(src):
                cls = m.group(1)
                if is_stripped(cls):
                    needed[cls]["as_super"] += 1
            for m in IMPL_RE.finditer(src):
                cls = m.group(1)
                if is_stripped(cls):
                    needed[cls]["as_impl"] += 1
            for m in INVOKE_RE.finditer(src):
                kind, cls, name, args, ret = m.groups()
                if is_stripped(cls):
                    needed[cls]["methods"].add((name, args, ret, kind))
            for m in FIELD_RE.finditer(src):
                _op, _k, cls, name, typ = m.groups()
                if is_stripped(cls):
                    needed[cls]["fields"].add((name, typ))
            for m in re.finditer(r"\b(?:new-instance|const-class|check-cast|instance-of)\s+[vp]\d+,\s*L([A-Za-z0-9_/$]+);", src):
                cls = m.group(1)
                if is_stripped(cls):
                    needed[cls]["cast_or_type"] += 1
            for m in re.finditer(r"\.method[^\n]*\)L([A-Za-z0-9_/$]+);", src):
                cls = m.group(1)
                if is_stripped(cls):
                    needed[cls]["cast_or_type"] += 1
            for m in re.finditer(r"\.method[^\n]*\(([^)]*)\)", src):
                for t in TYPE_RE.finditer(m.group(1)):
                    if is_stripped(t.group(1)):
                        needed[t.group(1)]["cast_or_type"] += 1

    return needed


def main():
    needed = collect(APK_DIR)
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        data = {
            cls: {
                "methods": sorted(list(v["methods"])),
                "fields": sorted(list(v["fields"])),
                "as_super": v["as_super"],
                "as_impl": v["as_impl"],
                "new_instance": v["new_instance"],
                "cast_or_type": v["cast_or_type"],
            }
            for cls, v in needed.items()
        }
        io.open(out, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"已写出 {out}")

    print(f"被游戏代码引用的 SDK 类共 {len(needed)} 个：\n")
    for cls, v in sorted(needed.items()):
        print(f"  {cls}")
        print(f"      父类x{v['as_super']}  接口x{v['as_impl']}  类型引用x{v['cast_or_type']}")
        for name, args, ret in sorted(v["methods"]):
            print(f"      .{name}({args}){ret}")
        for name, typ in sorted(v["fields"]):
            print(f"      f {name}:{typ}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
