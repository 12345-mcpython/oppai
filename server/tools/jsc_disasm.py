"""SpiderMonkey 33.1.1 (.jsc) 反汇编器。

原理
====
cocos2d-js 编译出来的 .jsc 就是 SpiderMonkey 的 XDR 字节码，没有加密、
也没有保留源码（`Function.prototype.toString()` 只会给 `[sourceless code]`）。
但格式是完全确定的，可以直接解：

  文件 = uint32 magic(0xb973c0de-178) + XDRScript

XDRScript（js/src/jsscript.cpp）的字段顺序：

  uint16 nargs, uint16 nblocklocals, uint32 nvars
  uint32 length                       # 字节码长度
  uint32 prologLength, uint32 version
  uint32 natoms, nsrcnotes, nconsts, nobjects, nregexps, ntrynotes,
         nblockscopes, nTypeSets, funLength, scriptBits
  [bindings]  nargs+nvars 个 atom，然后 nargs+nvars 个 uint8
  uint32 sourceStart, sourceEnd
  uint32 lineno, column, nslots, staticLevel
  byte   code[length]                 # ← 字节码
  byte   notes[nsrcnotes]
  atom   atoms[natoms]                # natoms 个 atom
  ... 后面是 consts/objects/regexps/trynotes/blockscopes（反汇编用不到）

atom 的编码（js/src/jsatom.cpp XDRAtom）：

  uint32 lengthAndEncoding = (length << 1) | isLatin1
  isLatin1 ? length 字节 : length*2 字节（UTF-16LE）

操作码表来自 SpiderMonkey 33.1.1 的 vm/Opcodes.h（本文件里的 OPCODES）。
"""

from __future__ import annotations

import struct
import sys

# ---------------------------------------------------------------------------
# 操作码表： (name, length, format)
# format 取自 Opcodes.h 的 JOF_* 低 5 位
# ---------------------------------------------------------------------------
JOF_BYTE = 0
JOF_JUMP = 1
JOF_ATOM = 2
JOF_UINT16 = 3
JOF_TABLESWITCH = 4
JOF_QARG = 6
JOF_LOCAL = 7
JOF_DOUBLE = 8
JOF_UINT24 = 12
JOF_UINT8 = 13
JOF_INT32 = 14
JOF_OBJECT = 15
JOF_REGEXP = 17
JOF_INT8 = 18
JOF_ATOMOBJECT = 19
JOF_SCOPECOORD = 21

# 操作码表：直接从 SpiderMonkey 33.1.1 的 vm/Opcodes.h 生成（tools/gen_opcodes.py）
try:
    from _opcodes_gen import OPCODES_LIST
except ImportError:  # 允许以包的方式导入
    from ._opcodes_gen import OPCODES_LIST

OPCODES = {val: (name, length, jof) for val, name, length, jof in OPCODES_LIST}





class Reader:
    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def u8(self):
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self):
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return v

    def take(self, n):
        v = self.data[self.pos:self.pos + n]
        self.pos += n
        return v

    def cstring(self):
        end = self.data.index(b"\x00", self.pos)
        v = self.data[self.pos:end].decode("utf-8", "replace")
        self.pos = end + 1
        return v

    def atom(self):
        length_and_encoding = self.u32()
        length = length_and_encoding >> 1
        latin1 = length_and_encoding & 1
        if latin1:
            raw = self.take(length)
            return raw.decode("latin-1")
        raw = self.take(length * 2)
        return raw.decode("utf-16-le", "replace")


class Script:
    def __init__(self):
        self.nargs = self.nblocklocals = self.nvars = 0
        self.length = 0
        self.prolog_length = self.version = 0
        self.natoms = self.nsrcnotes = self.nconsts = 0
        self.nobjects = self.nregexps = self.ntrynotes = self.nblockscopes = 0
        self.fun_length = self.script_bits = 0
        self.source_start = self.source_end = 0
        self.lineno = self.column = self.nslots = self.static_level = 0
        self.bindings = []
        self.code = b""
        self.notes = b""
        self.atoms = []


def parse_script(r: Reader) -> Script:
    s = Script()
    s.nargs = r.u16()
    s.nblocklocals = r.u16()
    s.nvars = r.u32()
    s.length = r.u32()
    s.prolog_length = r.u32()
    s.version = r.u32()
    s.natoms = r.u32()
    s.nsrcnotes = r.u32()
    s.nconsts = r.u32()
    s.nobjects = r.u32()
    s.nregexps = r.u32()
    s.ntrynotes = r.u32()
    s.nblockscopes = r.u32()
    r.u32()  # nTypeSets
    s.fun_length = r.u32()
    s.script_bits = r.u32()

    # bindings: nargs+nvars 个 atom，再 nargs+nvars 个 uint8
    name_count = s.nargs + s.nvars
    names = [r.atom() for _ in range(name_count)]
    kinds = [r.u8() for _ in range(name_count)]
    s.bindings = list(zip(names, kinds))

    # ScriptSource::performXDR（scriptBits 的 OwnSource 位为 1 时才有）
    if s.script_bits & (1 << 12):
        has_source = r.u8()
        retrievable = r.u8()
        if has_source and not retrievable:
            length_ = r.u32()
            compressed_length = r.u32()
            r.u8()  # argumentsNotIncluded
            byte_len = compressed_length if compressed_length else length_ * 2
            r.take(byte_len)
        have_source_map = r.u8()
        if have_source_map:
            n = r.u32()
            r.take(n * 2)
        have_display_url = r.u8()
        if have_display_url:
            n = r.u32()
            r.take(n * 2)
        have_filename = r.u8()
        if have_filename:
            s.filename = r.cstring()

    s.source_start = r.u32()
    s.source_end = r.u32()
    s.lineno = r.u32()
    s.column = r.u32()
    s.nslots = r.u32()
    s.static_level = r.u32()

    s.code = r.take(s.length)
    s.notes = r.take(s.nsrcnotes)
    s.atoms = [r.atom() for _ in range(s.natoms)]
    return s


def read_file(path: str) -> Script:
    data = open(path, "rb").read()
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != 0xB973C02C:
        raise SystemExit(f"不是 SM33.1.1 的 jsc（magic={magic:#x}）")
    r = Reader(data, 4)
    script = parse_script(r)
    script.children = []
    try:
        parse_sections(r, script)
    except Exception as exc:  # noqa: BLE001
        script.trailer_error = str(exc)
    return script


def read_const(r: Reader):
    """XDRScriptConst —— 只要跳过就行。"""
    tag = r.u32()
    if tag == 0:      # SCRIPT_INT
        r.u32()
    elif tag == 1:    # SCRIPT_DOUBLE
        r.u64()
    elif tag == 2:    # SCRIPT_ATOM
        r.atom()
    elif tag == 6:    # SCRIPT_OBJECT
        raise NotImplementedError("const 里出现对象字面量，暂不支持")
    # 3/4/5/7/8 无负载
    return tag


def parse_sections(r: Reader, s: "Script"):
    """consts -> objects（递归解析嵌套脚本）-> regexps -> trynotes -> blockscopes"""
    for _ in range(s.nconsts):
        read_const(r)
    for _ in range(s.nobjects):
        classk = r.u32()
        if classk == 2:  # CK_JSFunction
            r.u32()                      # funEnclosingScopeIndex
            firstword = r.u32()
            name = None
            if firstword & 0x1:          # HasAtom
                name = r.atom()
            r.u32()                      # flagsword = (nargs<<16)|flags
            if firstword & 0x4:          # IsLazy
                raise NotImplementedError("lazy script")
            child = parse_script(r)
            child.children = []
            child.name = name
            s.children.append(child)
            parse_sections(r, child)
        else:
            raise NotImplementedError(f"对象类型 classk={classk} 暂不支持")
    for _ in range(s.nregexps):
        r.atom()   # 实际上是 XDRScriptRegExpObject（flag+atom），先跳过
    for _ in range(s.ntrynotes):
        r.u8(); r.u32(); r.u32(); r.u32()
    for _ in range(s.nblockscopes):
        r.u32(); r.u32(); r.u32(); r.u32()


def disassemble(s: Script, start: int = 0, end: int = None):
    """产出 (offset, text) 列表。"""
    out = []
    code = s.code
    end = len(code) if end is None else end
    pc = start
    lineno = 0
    while pc < end:
        op = code[pc]
        info = OPCODES.get(op)
        if info is None:
            out.append((pc, f".byte {op:#04x}   ; 未知操作码"))
            pc += 1
            continue
        name, length, fmt = info
        if name == "tableswitch":
            # int32 len, int32 low, int32 high, int32 offset[high-low+1]
            length = struct.unpack_from(">i", code, pc + 1)[0]
            low = struct.unpack_from(">i", code, pc + 5)[0]
            high = struct.unpack_from(">i", code, pc + 9)[0]
            out.append((pc, f"tableswitch low={low} high={high}"))
            pc += length
            continue

        operand = code[pc + 1:pc + length]
        text = name
        if fmt == JOF_ATOM and length == 5:
            idx = struct.unpack_from(">I", code, pc + 1)[0]
            atom = s.atoms[idx] if idx < len(s.atoms) else f"<atom#{idx}>"
            text = f'{name} "{atom}"'
        elif fmt == JOF_ATOMOBJECT:
            a = struct.unpack_from(">H", code, pc + 1)[0]
            o = struct.unpack_from(">I", code, pc + 3)[0]
            atom = s.atoms[a] if a < len(s.atoms) else f"<atom#{a}>"
            text = f'{name} "{atom}" obj#{o}'
        elif fmt == JOF_JUMP:
            off = struct.unpack_from(">i", code, pc + 1)[0]
            text = f"{name} -> {pc + off}"
        elif fmt == JOF_UINT16 or fmt == JOF_QARG or fmt == JOF_LOCAL:
            v = struct.unpack_from(">H", code, pc + 1)[0]
            text = f"{name} {v}"
        elif fmt == JOF_INT8:
            v = struct.unpack_from(">b", code, pc + 1)[0]
            text = f"{name} {v}"
        elif fmt == JOF_INT32:
            v = struct.unpack_from(">i", code, pc + 1)[0]
            text = f"{name} {v}"
        elif fmt == JOF_UINT24:
            v = (operand[0] << 16) | (operand[1] << 8) | operand[2]
            text = f"{name} {v}"
        elif fmt == JOF_UINT8:
            text = f"{name} {operand[0]}"
        elif fmt == JOF_OBJECT:
            v = struct.unpack_from(">I", code, pc + 1)[0]
            text = f"{name} obj#{v}"
        elif fmt == JOF_REGEXP:
            v = struct.unpack_from(">I", code, pc + 1)[0]
            text = f"{name} regexp#{v}"
        elif fmt == JOF_DOUBLE:
            v = struct.unpack_from(">I", code, pc + 1)[0]
            text = f"{name} const#{v}"
        elif fmt == JOF_SCOPECOORD:
            hops = operand[0]
            slot = (operand[1] << 16) | (operand[2] << 8) | operand[3]
            text = f"{name} hops={hops} slot={slot}"
        elif len(operand):
            text = f"{name} {' '.join('%02x' % b for b in operand)}"

        out.append((pc, text))
        pc += length
    return out


def emit_js(entries, bindings=None, indent="    "):
    """把反汇编结果伪装成可读的 JS 风格伪代码（便于快速扫逻辑）。"""
    lines = []
    if bindings:
        lines.append(f"{indent}// params/vars: " + ", ".join(n for n, _ in bindings))
    for pc, text in entries:
        lines.append(f"{indent}{pc:6d}  {text}")
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("用法: python jsc_disasm.py <file.jsc> [起始偏移] [结束偏移]")
        return 1
    path = sys.argv[1]
    start = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0
    end = int(sys.argv[3], 0) if len(sys.argv) > 3 else None
    s = read_file(path)
    print(f"// {path}")
    print(f"// code={s.length}B atoms={len(s.atoms)} objects={s.nobjects} "
          f"consts={s.nconsts} notes={s.nsrcnotes} lineno={s.lineno} slots={s.nslots}")
    print(f"// bindings: " + ", ".join(f"{n}({k})" for n, k in s.bindings))
    for pc, text in disassemble(s, start, end):
        print(f"{pc:6d}  {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
