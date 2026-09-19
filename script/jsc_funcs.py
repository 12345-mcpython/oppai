"""按**函数**分组打印 .jsc 的字符串原子表。

    python script\\jsc_funcs.py <x.jsc>                 # 列出所有函数
    python script\\jsc_funcs.py <x.jsc> requestNewMails # 只看名字含关键字的函数

## 为什么需要它

`jsc_strings.py` 给的是**一整条**按字节码顺序排的原子流，里面夹着
`BossCenter<.update` / `Mailbox<.requestNewMails/<` 这种 **debug name**，
它们是每个函数的起点。函数名后面到下一个函数名之前的原子，就是这个函数
用到的属性名和方法名**（按出现顺序）**。

不分组的话，看 route 回调会串行 —— 你要在读 `mail.getnewmails` 的回调，
眼睛却停在隔壁函数的字段上。

于是逆一条 route 的响应形状就是：

    $ python script\\jsc_funcs.py mailbox.jsc requestNewMails
    Mailbox<.requestNewMails/<
        self cb server request mail.getnewmails ...
    Mailbox<.requestNewMailsCb
        err getData cb data code mails remindCount ...

「回调读 `data.mails` / `data.remindCount`」一眼就看出来了。
"""

from __future__ import annotations

import os
import re
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.isfile(os.path.join(_d, "_paths.py")):
    _d = os.path.dirname(_d)
sys.path.insert(0, _d)
import _paths  # noqa: F401,E402

import jsc_strings  # noqa: E402

# debug name 的形态：
#     BossCenter<.update            方法
#     Mailbox<.requestNewMails/<    匿名函数（回调）
#     Foo<.bar/baz                  嵌套
# 也可能直接是 `update` / `ctor` 这种短名字，那就没法当分隔符 —— 所以要求带 '<'
RE_FUNC = re.compile(r"^[A-Za-z_$][\w$]*(?:<[^>]*)?<\.|^[A-Za-z_$][\w$]*<[^>]*$")


def parse_functions(path: str):
    """-> [(函数名, [原子…])]"""
    _size, atoms = jsc_strings.parse(path)
    funcs = []
    cur_name, cur = None, []
    for _off, _len, text in atoms:
        if RE_FUNC.match(text):
            if cur_name is not None:
                funcs.append((cur_name, cur))
            cur_name, cur = text, []
        else:
            cur.append(text)
    if cur_name is not None:
        funcs.append((cur_name, cur))
    return funcs


def main(argv) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    if not os.path.isfile(path):
        print(f"!! 找不到 {path}", file=sys.stderr)
        return 1
    keywords = argv[2:]
    funcs = parse_functions(path)
    print(f"# {path}  函数 {len(funcs)} 个")
    for name, atoms in funcs:
        if keywords and not any(k.lower() in name.lower() for k in keywords):
            continue
        print(f"\n== {name}  ({len(atoms)} 个原子) ==")
        # 每行最多 12 个，方便扫
        for i in range(0, len(atoms), 12):
            print("   " + " ".join(atoms[i:i + 12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
