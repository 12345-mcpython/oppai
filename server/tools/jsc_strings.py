#!/usr/bin/env python
"""从 cocos2d-js 的 .jsc（SpiderMonkey 33 XDR 字节码）里把字符串表扒出来。

为什么要这个：
    .jsc 是编译过的字节码，`Function.prototype.toString()` 只会给你
    "[sourceless code]"，没法反编译。但 SM33 在 XDR 里是按「每个函数脚本一组
    atom」写的，atom 的编码是

        <uint32 (2 * len + 1)> <len 个 ASCII 字节>

    也就是说长度字段是字符数的两倍加一（UTF-16 长度含结尾 NUL），内容却是
    单字节 ASCII。按这个规则扫一遍，就能拿到**按源码顺序排的标识符表**：
    每个函数先是它的参数/局部变量名，然后是函数体里按出现顺序用到的属性名和方法名。

    于是即使没有源码，也能相当准确地还原一个函数在干什么，例如：

        $ python tools/jsc_strings.py zcsmw/assets/src/ui/main/mainlayer.jsc | grep -A 20 _initModuleButtons

        MainLayer<._initModuleButtons      <- 函数（debug name）
        mainUiLayer modules                <- 局部变量
        key row node module beginCb needScale touchSound button
        _mainUiLayer dataManager player moduleState _moduleButtons
        table_main_layer node_name module_key ...

    再配上运行时 REPL（tools/repl.py）验证，定位客户端问题非常快。

用法：
    python tools/jsc_strings.py <x.jsc> [关键字 ...]
    只给文件名则打印全表；给了关键字则只打印匹配行（带上下文请用 grep）。
"""
from __future__ import annotations

import struct
import sys


def parse(path: str):
    data = open(path, "rb").read()
    i = 0
    n = len(data)
    out = []
    while i < n - 4:
        value = struct.unpack_from("<I", data, i)[0]
        if value >= 3 and (value & 1):
            length = value // 2
            if i + 4 + length <= n:
                raw = data[i + 4:i + 4 + length]
                if raw and raw[-1:] != b"\x00" and all(0x20 <= b < 0x7F for b in raw):
                    out.append((i, length, raw.decode("ascii")))
                    i += 4 + length
                    continue
        i += 1
    return len(data), out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    size, atoms = parse(argv[1])
    keywords = argv[2:]
    print("# %s  size=%d  atoms=%d" % (argv[1], size, len(atoms)))
    for off, length, text in atoms:
        if keywords and not any(k in text for k in keywords):
            continue
        print("%7d %4d  %s" % (off, length, text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
