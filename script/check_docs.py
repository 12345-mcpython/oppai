"""文档自检：链接、目录锚点、旧路径残留、文档地图覆盖。

    python script\\check_docs.py

为什么要它：这份仓库的文档曾经**同一件事有三份说法**——三份「文档地图」、
两份「构建命令清单」，各自过期到互相矛盾（一处写 13 个引擎补丁、另一处写 16 个；
一处写 devtools 五个面板、另一处六个）。重构时把它们收敛成「一件事只留一份权威描述」，
这个脚本负责别让它退化回去。

四项检查：

1. **相对链接**：每份 `.md` 里的 `[](...)` 目标文件必须存在（外链、纯 `#锚点` 跳过）。
2. **目录锚点**：带「## 本节目录」的文档，目录里每个 `#anchor` 都要能在本文的
   二级标题里找到（按 GitHub 的锚点规则算：小写、去标点、空格转 `-`、重名加 `-1`）。
3. **旧路径残留**：`server/docs/` 这类搬家前的路径不能复活。
4. **文档地图覆盖**：`README.md` 的「文档地图」表里列的文档都要存在，
   且 `docs/` 下每份文档都要在地图里出现（漏了就是"写了没人找得到"）。

返回值：0 = 全过；1 = 有毛病（打印到 stdout，方便接 CI）。
"""

from __future__ import annotations

import glob
import io
import os
import re
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

ROOT = _paths.ROOT
FENCE = re.compile(r"^\s*```")
LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def doc_files() -> list:
    out = ["README.md", "REPRODUCE.md", "script/README.md"]
    out += sorted(os.path.relpath(p, ROOT).replace(os.sep, "/")
                  for p in glob.glob(os.path.join(ROOT, "docs", "*.md")))
    out += sorted(os.path.relpath(p, ROOT).replace(os.sep, "/")
                  for p in glob.glob(os.path.join(ROOT, "engine", "*.md")))
    return [f for f in out if os.path.isfile(os.path.join(ROOT, f))]


def read(rel: str) -> str:
    return io.open(os.path.join(ROOT, rel), encoding="utf-8").read()


def h2_titles(text: str) -> list:
    """二级标题（跳过代码块里的 `# ...` 注释）。"""
    out, fence = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            out.append(m.group(1))
    return out


def gh_anchor(title: str) -> str:
    t = title.strip().lower()
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\*\*(.*?)\*\*", r"\1", t)
    t = re.sub(r"\*(.*?)\*", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[^\w \-]", "", t)
    return t.replace(" ", "-")


def check_links(files: list) -> list:
    bad = []
    for f in files:
        base = os.path.dirname(os.path.join(ROOT, f))
        for m in LINK.finditer(read(f)):
            t = m.group(1).strip().split("#")[0]
            if not t or t.startswith(("http://", "https://", "mailto:")):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, t))):
                bad.append("%s -> %s（目标不存在）" % (f, t))
    return bad


def check_toc(files: list) -> list:
    bad = []
    for f in files:
        text = read(f)
        if "## 本节目录" not in text:
            continue
        anchors, dup = set(), {}
        for h in h2_titles(text):
            a = gh_anchor(h)
            if a in anchors:
                dup[a] = dup.get(a, 0) + 1
                a = "%s-%d" % (a, dup[a])
            anchors.add(a)
        for m in LINK.finditer(text):
            t = m.group(1).strip()
            if t.startswith("#") and t[1:] not in anchors:
                bad.append("%s 目录锚点 #%s 在本文里找不到对应标题" % (f, t[1:]))
    return bad


def check_stale_paths(files: list) -> list:
    bad = []
    for f in files:
        for i, line in enumerate(read(f).splitlines(), 1):
            if re.search(r"server[/\\]docs", line):
                bad.append("%s:%d 还写着搬家前的 server/docs 路径" % (f, i))
    return bad


def check_doc_map(files: list) -> list:
    bad = []
    readme = read("README.md")
    m = re.search(r"(?s)## 文档地图(.*?)\n## ", readme)
    if not m:
        return ["README.md 里找不到「## 文档地图」一节"]
    table = m.group(1)
    listed = {os.path.normpath(t.split("#")[0]) for t in LINK.findall(table)}
    for t in sorted(listed):
        if t.startswith(("http", "mailto:")):
            continue
        if not os.path.exists(os.path.join(ROOT, t)):
            bad.append("README 文档地图里列的 %s 不存在" % t)
    for f in files:
        if f in ("README.md",) or not f.startswith(("docs/", "engine/")):
            continue
        if os.path.normpath(f) not in listed:
            bad.append("%s 没被 README 的文档地图收录（写了没人找得到）" % f)
    return bad


def main() -> int:
    files = doc_files()
    problems = []
    for name, fn in (("相对链接", check_links), ("目录锚点", check_toc),
                     ("旧路径残留", check_stale_paths), ("文档地图覆盖", check_doc_map)):
        got = fn(files)
        print("%-10s %s" % (name, "OK" if not got else "%d 处问题" % len(got)))
        problems += got
    print("\n检查了 %d 份文档 / 共 %d 处问题" % (len(files), len(problems)))
    for p in problems:
        print("  !! " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
