"""重排之后把脚本里算错的目录基准改掉（一次性）。

脚本原来在 `game_server/tools/` 里，`dirname(dirname(__file__))` 正好是
`game_server/`（client / gamesrv 都在那儿）。挪到 `script/` 之后这一层变成了
项目根目录，于是 `client/patch.js` 之类全找不到了。统一改用 `_paths.SERVER`。
"""

from __future__ import annotations

import io
import os

SCRIPT = r"E:\code\zcsmw\script"

FIXES = [
    (r"build_apk.py",
     'BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))',
     'BASE_DIR = _paths.SERVER          # client/patch.js 在服务端那边'),
    (r"check_devtools.py",
     '    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))',
     '    root = _paths.SERVER'),
    (r"extract_client_tables.py",
     'DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),\n'
     '                        "gamesrv", "data")',
     'DATA_DIR = os.path.join(_paths.SERVER, "gamesrv", "data")'),
    (r"sdk_strip\strip.py",
     'BASE_DIR = os.path.dirname(os.path.dirname(HERE))',
     'BASE_DIR = _paths.SERVER'),
    (r"sdk_strip\gen_stubs.py",
     'BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))',
     'BASE_DIR = _paths.SERVER'),
    (r"gen_opcodes.py",
     '    # tools/ -> game_server/ -> python/ -> code/ 下的 apk/sm/\n'
     '    guess = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(BASE_DIR))),\n'
     '                         "apk", "sm", "vm_Opcodes.h")',
     '    # 参考源码现在在 engine/ref/spidermonkey/\n'
     '    guess = os.path.join(_paths.ENGINE, "ref", "spidermonkey", "vm_Opcodes.h")'),
]

n = 0
for rel, old, new in FIXES:
    p = os.path.join(SCRIPT, rel)
    with io.open(p, encoding="utf-8") as fh:
        text = fh.read()
    if new in text:
        print(f"   已经是好的: {rel}")
        continue
    if old not in text:
        print(f"!! 找不到要替换的片段: {rel}")
        continue
    with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace(old, new, 1))
    print(f"   改了 {rel}")
    n += 1
print(f"共 {n} 个")
