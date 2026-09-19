"""APK 体积体检 —— 想精简包的时候先跑这个，别靠猜。

    python script\\apk_report.py                     # 默认看 out\\zcsmw-mod-signed.apk
    python script\\apk_report.py <某个.apk>
    python script\\apk_report.py --dups 40           # 多列几个重复文件

输出四张表：

  1. 按路径聚合      哪个目录在吃空间（assets/res/sound 这种）
  2. 按扩展名 + 压缩方式   哪些是 STORED（不压）—— STORED 又本来能压的才是漏点
  3. 内容完全重复的文件    同一份数据存了两遍，白白浪费
  4. 最大的单个文件

踩过的坑（别重复踩）：

  * **.mp3 / .mp4 必须 STORED。** Android 的 `AssetManager.openFd()` 对压缩过的
    asset 直接抛 FileNotFoundException，cocos2d-x 的音频后端是拿 fd 去喂
    MediaPlayer 的，一压就没声音。所以看到 mp3 是 STORED 不要"优化"。
  * **.png / .jpg 压不压都行**，但省不了多少（png 本身已经 deflate 过，再压一遍
    只有 1~3%）。真要靠图片省空间得重新量化，那是有损的。
  * **dex 压缩比约 3:1**：删掉 1.14 MB 的 dex，APK 上只少 0.44 MB。
    别用 smali 的字节数估 APK 的收益。
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import os
import sys
import zipfile
import zlib

# --- 路径自举（脚本在 script/，产物在 out/）---
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402


def _key(name: str) -> str:
    """把路径归到「有意义的一层」，免得 assets/src 下 800 个文件各占一行。"""
    parts = name.split("/")
    if name.startswith(("assets/res/", "assets/src/")) and len(parts) > 2:
        return "/".join(parts[:3])
    return "/".join(parts[:2]) if len(parts) > 1 else parts[0]


def main() -> int:
    ap = argparse.ArgumentParser(description="APK 体积体检")
    ap.add_argument("apk", nargs="?", default=os.path.join(_paths.OUT, "zcsmw-mod-signed.apk"))
    ap.add_argument("--dups", type=int, default=15, help="列出多少个重复文件")
    ap.add_argument("--top", type=int, default=25, help="路径聚合表列多少行")
    args = ap.parse_args()

    if not os.path.isfile(args.apk):
        print(f"!! 找不到 {args.apk}", file=sys.stderr)
        return 1

    z = zipfile.ZipFile(args.apk)
    items = z.infolist()
    raw = sum(i.file_size for i in items)
    comp = sum(i.compress_size for i in items)
    print(f"{args.apk}")
    print(f"  {len(items):,} 个条目   原始 {raw/1e6:.1f} MB   包内 {comp/1e6:.1f} MB   "
          f"磁盘 {os.path.getsize(args.apk)/1e6:.1f} MB")

    # 1. 按路径
    agg = collections.Counter()
    cnt = collections.Counter()
    for i in items:
        k = _key(i.filename)
        agg[k] += i.file_size
        cnt[k] += 1
    print(f"\n=== 按路径（原始大小，前 {args.top}）===")
    for k, v in agg.most_common(args.top):
        print(f"  {v/1e6:>8.1f} MB {cnt[k]:>6} 个   {k}")

    # 2. 扩展名 × 压缩方式
    st = collections.Counter()
    df = collections.Counter()
    stn = collections.Counter()
    dfn = collections.Counter()
    for i in items:
        e = os.path.splitext(i.filename)[1].lower() or "(无扩展名)"
        if i.compress_type == 0:
            st[e] += i.file_size
            stn[e] += 1
        else:
            df[e] += i.file_size
            dfn[e] += 1
    print("\n=== 扩展名 × 压缩方式（0=STORED 不压，8=DEFLATE）===")
    print(f"  {'扩展名':<12}{'STORED MB':>11}{'个':>6}{'DEFLATE MB':>12}{'个':>6}")
    for e in sorted(set(st) | set(df), key=lambda k: -(st[k] + df[k]))[:16]:
        print(f"  {e:<12}{st[e]/1e6:>11.1f}{stn[e]:>6}{df[e]/1e6:>12.1f}{dfn[e]:>6}")

    # STORED 里抽样估一下"本来能压多少"——只对有意义的类型测
    suspect = [e for e in st if e in (".png", ".csb", ".jpg", ".plist", ".json", ".txt", ".xml")]
    if suspect:
        print("\n=== STORED 文件再压一遍能省多少（抽样估算）===")
        for e in suspect:
            cand = [i for i in items if i.compress_type == 0
                    and os.path.splitext(i.filename)[1].lower() == e and i.file_size > 4096]
            if not cand:
                continue
            sample = cand[:: max(1, len(cand) // 40)][:40]
            r = c = 0
            for i in sample:
                d = z.read(i.filename)
                r += len(d)
                c += len(zlib.compress(d, 6))
            ratio = c / r if r else 1.0
            print(f"  {e:<8} 压到 {ratio*100:>3.0f}%   {e} 全量 {st[e]/1e6:.1f} MB → "
                  f"约省 {st[e]*(1-ratio)/1e6:.1f} MB")

    # 3. 内容重复
    seen = {}
    dups = []
    for i in items:
        if i.file_size < 8192 or i.filename.startswith("META-INF"):
            continue
        h = hashlib.md5(z.read(i.filename)).hexdigest()
        if h in seen:
            dups.append((i.file_size, seen[h], i.filename))
        else:
            seen[h] = i.filename
    dups.sort(reverse=True)
    print(f"\n=== 内容完全重复：{len(dups)} 个，浪费 {sum(d[0] for d in dups)/1e6:.1f} MB ===")
    for s, a, b in dups[: args.dups]:
        print(f"  {s/1e6:>7.2f} MB  {a}\n              = {b}")

    # 4. 最大文件
    print("\n=== 最大的 15 个文件 ===")
    for i in sorted(items, key=lambda x: -x.file_size)[:15]:
        print(f"  {i.file_size/1e6:>8.2f} MB  {i.filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
