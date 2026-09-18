"""对比 manifest 里各组件的完整属性（不只 name），找出重建后变化的地方。"""

import io
import re
import sys

OLD = r"E:\code\apk\zcsmw\AndroidManifest.xml.bak"
NEW = r"E:\code\apk\zcsmw\AndroidManifest.xml"

TAGS = ("meta-data", "provider", "application", "uses-sdk", "activity", "service", "receiver")


def dump(path):
    s = io.open(path, encoding="utf-8").read()
    out = {}
    for tag in TAGS:
        elems = re.findall(rf"<{tag}\b[^>]*?/?>", s)
        out[tag] = sorted(elems)
    return out


def main():
    a, b = dump(OLD), dump(NEW)
    for tag in TAGS:
        sa, sb = set(a[tag]), set(b[tag])
        if sa == sb:
            print(f"  {tag}: 完全一致 ({len(a[tag])})")
            continue
        print(f"### {tag}")
        for x in sorted(sa - sb):
            print("  - 旧:", x[:220])
        for x in sorted(sb - sa):
            print("  + 新:", x[:220])
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
