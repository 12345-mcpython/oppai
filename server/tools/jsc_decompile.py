r"""SpiderMonkey 33 (.jsc) 反编译器 —— 把字节码还原成 JS 源码。

    python tools\jsc_decompile.py <file.jsc>              # 打到 stdout
    python tools\jsc_decompile.py <file.jsc> -o out.js
    python tools\jsc_decompile.py --filter soldier        # 批量（按路径子串）
    python tools\jsc_decompile.py --check                 # 全部反编译 + node --check

## 思路

`tools/jsc_disasm.py` 已经把 XDR 解开了（脚本树、atom 表、字节码、常量、对象、
正则、trynote、块作用域）。这一层做三件事：

1. **解码** —— 字节码 → 结构化指令（操作数、跳转目标、atom / const / 对象索引）
2. **表达式重建** —— SM 的字节码是**栈机**，每条指令弹几个压一个，
   所以老老实实模拟操作数栈就能还原出表达式树
3. **结构恢复** —— 回边 → 循环；向前的条件跳转 → if/else；
   `and`/`or` 这两条**不弹栈**的跳转天然就是短路运算

## 两个关键点

**`&&` / `||` 的形状**（`and`/`or` 是跳转，且不弹栈）：

```
  <A>
  and L        ; A 为假就跳到 L（A 留在栈上）
  pop          ; 否则弹掉 A
  <B>
L:             ; 到这里栈顶是 A 或 B
```

所以实现上是：先弹出 A，再把 `(pc+len, L)` 当成一个**表达式区域**跑一遍
（`pop` 在表达式模式下只是丢弃），跑完栈顶就是 B，拼成 `A && B`。

**`if/else` 和 `?:` 是同一个形状**，区别只在分支里有没有语句：

```
  <c>
  ifeq Lelse
  <A>            ; 只有表达式 -> 三元
  goto Lend
Lelse:
  <B>
Lend:
```

所以 `_cond_jump` 先**试探性**地把 then / else 两块按表达式跑一遍：
两块都只往栈上留了一个值、且没产生语句，就拼成 `c ? A : B`；
否则回退成语句形式的 `if (c) { ... } else { ... }`。

## 已知的取舍

* 空行、注释、括号风格全没了 —— 输出是「能读、语义等价」的源码，不是逐字节还原。
* **变量名是真的**：SM 会把局部变量名编进 atom 表，`Script.bindings` 里就有。
  但解构赋值、块级作用域里的绑定可能退化成 `$slotN`。
* `try/catch` 依赖 trynote，比较脆；认不出来就退化成注释，**保证输出永远是合法 JS**
  （`--check` 用 `node --check` 全量验证）。
* 13/798 个 jsc 的 XDR 有段没搞清楚的布局（见 `jsc_disasm.resync_object`），只解出一部分。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jsc_disasm as J  # noqa: E402

# ---------------------------------------------------------------------------
# 优先级（数字越大绑得越紧）
# ---------------------------------------------------------------------------
P_SEQ = 0
P_ASSIGN = 2
P_COND = 3
P_OR = 4
P_AND = 5
P_BITOR = 6
P_BITXOR = 7
P_BITAND = 8
P_EQ = 9
P_REL = 10
P_SHIFT = 11
P_ADD = 12
P_MUL = 13
P_UNARY = 14
P_POSTFIX = 15
P_NEW = 16
P_CALL = 17
P_MEMBER = 18
P_PRIMARY = 19

BINOP_PREC = {
    "|": P_BITOR, "^": P_BITXOR, "&": P_BITAND,
    "==": P_EQ, "!=": P_EQ, "===": P_EQ, "!==": P_EQ,
    "<": P_REL, "<=": P_REL, ">": P_REL, ">=": P_REL,
    "in": P_REL, "instanceof": P_REL,
    "<<": P_SHIFT, ">>": P_SHIFT, ">>>": P_SHIFT,
    "+": P_ADD, "-": P_ADD,
    "*": P_MUL, "/": P_MUL, "%": P_MUL,
}

KEYWORDS = set("""break case catch class const continue debugger default delete do else
export extends finally for function if import in instanceof new return super switch this
throw try typeof var void while with yield let static enum await implements package
protected interface private public null true false undefined""".split())

IDENT_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def q(text: str, prec: int, need: int) -> str:
    return text if prec >= need else "(" + text + ")"


def js_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def prop_name(name: str) -> str:
    return name if IDENT_RE.match(name) and name not in KEYWORDS else None


def make_member(obj: str, obj_prec: int, name: str) -> str:
    base = q(obj, obj_prec, P_MEMBER)
    if prop_name(name):
        return base + "." + name
    return base + "[" + js_str(name) + "]"


# ---------------------------------------------------------------------------
# 指令解码
# ---------------------------------------------------------------------------

class Ins:
    __slots__ = ("pc", "op", "name", "length", "arg", "target",
                 "atom", "obj", "const_idx", "regexp_idx")

    def __init__(self, pc, op, name, length):
        self.pc = pc
        self.op = op
        self.name = name
        self.length = length
        self.arg = None
        self.target = None
        self.atom = None
        self.obj = None
        self.const_idx = None
        self.regexp_idx = None

    def __repr__(self):
        return f"<{self.pc} {self.name} {self.arg}>"


def decode(script) -> list:
    """把 script.code 解成 Ins 列表。SM 的 XDR 立即数一律**大端**。"""
    code = script.code
    out = []
    pc = 0
    n = len(code)
    while pc < n:
        op = code[pc]
        info = J.OPCODES.get(op)
        if info is None:
            ins = Ins(pc, op, "unknown", 1)
            out.append(ins)
            pc += 1
            continue
        name, length, fmt = info
        if name == "tableswitch":
            length = struct.unpack_from(">i", code, pc + 1)[0]
            ins = Ins(pc, op, name, length)
            low = struct.unpack_from(">i", code, pc + 5)[0]
            high = struct.unpack_from(">i", code, pc + 9)[0]
            cases = []
            base = pc + 13
            for i in range(high - low + 1):
                off = struct.unpack_from(">i", code, base + i * 4)[0]
                cases.append((low + i, pc + off))
            ins.arg = {"low": low, "high": high, "cases": cases,
                       "default": pc + length}
            ins.target = pc + length
            out.append(ins)
            pc += length
            continue

        ins = Ins(pc, op, name, length)
        u32 = lambda o: struct.unpack_from(">I", code, pc + o)[0]
        i32 = lambda o: struct.unpack_from(">i", code, pc + o)[0]
        u16 = lambda o: struct.unpack_from(">H", code, pc + o)[0]

        if fmt == J.JOF_ATOM:
            ins.arg = u32(1)
            ins.atom = script.atoms[ins.arg] if ins.arg < len(script.atoms) else f"<atom{ins.arg}>"
        elif fmt == J.JOF_ATOMOBJECT:
            ins.arg = u16(1)
            ins.atom = script.atoms[ins.arg] if ins.arg < len(script.atoms) else f"<atom{ins.arg}>"
            ins.obj = u32(3)
        elif fmt == J.JOF_JUMP:
            ins.arg = i32(1)
            ins.target = pc + ins.arg
        elif fmt in (J.JOF_UINT16, J.JOF_QARG):
            ins.arg = u16(1)
        elif fmt == J.JOF_LOCAL:
            # 3 字节操作数！见 jsc_disasm.disassemble 里的说明
            ins.arg = (code[pc + 1] << 16) | (code[pc + 2] << 8) | code[pc + 3]
        elif fmt == J.JOF_INT8:
            ins.arg = struct.unpack_from(">b", code, pc + 1)[0]
        elif fmt == J.JOF_INT32:
            ins.arg = i32(1)
        elif fmt == J.JOF_UINT24:
            ins.arg = (code[pc + 1] << 16) | (code[pc + 2] << 8) | code[pc + 3]
        elif fmt == J.JOF_UINT8:
            ins.arg = code[pc + 1]
        elif fmt == J.JOF_OBJECT:
            ins.obj = u32(1)
        elif fmt == J.JOF_REGEXP:
            ins.regexp_idx = u32(1)
        elif fmt == J.JOF_DOUBLE:
            ins.const_idx = u32(1)
        elif fmt == J.JOF_SCOPECOORD:
            ins.arg = (code[pc + 1],
                       (code[pc + 2] << 16) | (code[pc + 3] << 8) | code[pc + 4])
        out.append(ins)
        pc += length
    return out


# ---------------------------------------------------------------------------
# 循环上下文
# ---------------------------------------------------------------------------

class Ctx:
    __slots__ = ("brk", "cont")

    def __init__(self, brk=None, cont=None):
        self.brk = set(brk or ())
        self.cont = set(cont or ())


# ---------------------------------------------------------------------------
# 表达式辅助
# ---------------------------------------------------------------------------

def e(text, prec=P_PRIMARY):
    return (text, prec)


def e_str(v, need=P_ASSIGN):
    return q(v[0], v[1], need)


# ---------------------------------------------------------------------------
# 单个函数的发射器
# ---------------------------------------------------------------------------

class FuncEmitter:
    def __init__(self, dec: "Decompiler", script, level, is_top=False):
        self.dec = dec
        self.s = script
        self.level = level
        self.is_top = is_top
        self.ins = decode(script)
        self.by_pc = {i.pc: i for i in self.ins}
        self.pcs = [i.pc for i in self.ins]
        self.stack = []
        self.lines = []
        self.indent = level + (0 if is_top else 1)
        self.expr_depth = 0
        self.temp_n = 0
        self.pending_var = None
        self.assigned = set()
        self.depth = 0
        self.active_loops = set()
        self.budget = max(20000, 200 * max(1, len(script.code)))
        self.loops = {}
        self._find_loops()

    # ---------------- 基础设施 ----------------

    def emit(self, text=""):
        if self.expr_depth:
            return
        # `var X;` 紧跟 `X = ...;` -> 合并成 `var X = ...;`
        if self.pending_var and text.startswith(self.pending_var + " = ") and text.endswith(";"):
            self.lines[-1] = "    " * self.indent + "var " + text
            self.pending_var = None
            return
        self.pending_var = None
        self.lines.append("    " * self.indent + text)

    def comment(self, text):
        self.emit("/* " + text + " */")

    def temp(self, hint="t"):
        self.temp_n += 1
        return f"{hint}{self.temp_n}"

    def push(self, value):
        self.stack.append(value)

    def pop(self):
        return self.stack.pop() if self.stack else e("undefined")

    def pop_str(self, need=P_ASSIGN):
        return e_str(self.pop(), need)

    def next_pc(self, ins):
        return ins.pc + ins.length

    def ins_after(self, pc):
        for i in self.ins:
            if i.pc >= pc:
                return i
        return None

    # ---------------- 名字 ----------------

    def local_name(self, slot):
        """`getlocal S` / `setlocal S` 的 S 是**局部变量**的下标（0 起），
        对应的名字在 `bindings[nargs + S]`。

        ⚠️ 这里实测出来的：扫了 soldier.jsc 里所有函数，
        `max(getlocal/setlocal 操作数)` 恒等于 `nvars - 1` ——
        也就是说槽号空间里**没有参数**（参数走 `getarg`/`setarg`，
        那是另一套下标，直接索引 bindings）。
        早先按 `bindings[S]` 取名，结果所有局部变量都借用了参数的名字
        （`row` 显示成 `npcId`，`attrBase` 显示成 `robotParams`）。
        """
        b = getattr(self.s, "bindings", []) or []
        idx = getattr(self.s, "nargs", 0) + slot
        if 0 <= idx < len(b) and b[idx][0]:
            return b[idx][0]
        return f"$slot{slot}"

    def arg_name(self, i):
        b = getattr(self.s, "bindings", []) or []
        if 0 <= i < len(b) and b[i][0]:
            return b[i][0]
        return f"$arg{i}"

    def func_name(self):
        n = getattr(self.s, "name", None) or ""
        tail = n.split(".")[-1]
        if tail.endswith("<"):
            tail = tail[:-1]
        return tail if IDENT_RE.match(tail or "") and tail not in KEYWORDS else ""

    # ---------------- 循环检测 ----------------

    def _find_loops(self):
        heads = {}
        for ins in self.ins:
            if ins.target is not None and ins.target < ins.pc and ins.target in self.by_pc:
                heads.setdefault(ins.target, []).append(ins.pc)
        for header, latches in heads.items():
            hi = max(latches)
            exit_pc = None
            for ins in self.ins:
                if header <= ins.pc <= hi and ins.target is not None:
                    if ins.target > hi and (exit_pc is None or ins.target < exit_pc):
                        exit_pc = ins.target
            self.loops[header] = {"latches": latches, "exit": exit_pc, "hi": hi}

    # ---------------- 入口 ----------------

    def run(self):
        ctx = Ctx()
        try:
            self.emit_region(0, len(self.s.code), ctx)
        except _Budget:
            self.comment("⚠️ 控制流太绕，触发指令预算，后面的代码没展开")
        body = "\n".join(self.lines)
        if self.is_top:
            return body
        params = ", ".join(self.arg_name(i) for i in range(self.s.nargs))
        name = self.func_name()
        head = ("function " + name if name else "function") + "(" + params + ") {"
        return head + ("\n" + body if body else "") + "\n" + "    " * self.indent + "}"

    # ---------------- 区域 ----------------

    def emit_region(self, pc, end, ctx):
        """顺序发射到 end 或遇到离开区域的跳转。返回「跳出去的目标 pc」或 None。"""
        if self.depth > 60:
            self.comment("嵌套太深，截断")
            return None
        self.depth += 1
        try:
            return self._emit_region(pc, end, ctx)
        finally:
            self.depth -= 1

    def _emit_region(self, pc, end, ctx):
        guard = 0
        while pc is not None and pc < end:
            guard += 1
            if guard > 200000:
                self.comment("区域太大，放弃")
                return None
            if pc in self.loops:
                pc = self.emit_loop(pc, end, ctx)
                continue
            ins = self.by_pc.get(pc)
            if ins is None:
                nxt = [p for p in self.pcs if p > pc]
                if not nxt:
                    return None
                pc = nxt[0]
                continue
            pc = self.emit_ins(ins, end, ctx)
        return None

    def eval_region(self, pc, end, ctx):
        """表达式模式跑一段：不发语句，只更新栈。"""
        self.expr_depth += 1
        saved_lines = self.lines
        self.lines = []
        try:
            self.emit_region(pc, end, ctx)
        finally:
            self.expr_depth -= 1
            self.lines = saved_lines

    # ---------------- 循环 ----------------

    def emit_loop(self, header, end, ctx):
        # 同一个循环别递归套自己 —— 有些 continue 形状的 `goto` 会指回循环头，
        # 不挡一下就是无限套（实测卡死过）
        if header in self.active_loops:
            self.emit("continue;")
            return self.loops[header]["exit"]
        self.active_loops.add(header)
        try:
            return self._emit_loop(header, end, ctx)
        finally:
            self.active_loops.discard(header)

    def _emit_loop(self, header, end, ctx):
        loop = self.loops[header]
        exit_pc = loop["exit"]
        hi = loop["hi"]
        latch = self.by_pc.get(hi)

        body_start = header
        while body_start < hi:
            ins = self.by_pc.get(body_start)
            if ins and ins.name in ("loophead", "loopentry", "nop"):
                body_start = ins.pc + ins.length
                continue
            break

        inner = Ctx(brk={exit_pc} if exit_pc is not None else set(), cont={header})

        # `goto TEST` 落在循环前面 -> 不是 do-while（第一趟要先过条件）
        prev = self._prev_ins(header)
        entered_from_before = (prev is not None and prev.name == "goto"
                               and prev.target is not None and header <= prev.target <= hi)

        first = self.by_pc.get(body_start)
        if (first is not None and first.name in ("ifeq", "ifne")
                and first.target == exit_pc and exit_pc is not None):
            cond = e_str(self.pop(), P_UNARY)
            if first.name == "ifne":
                cond = "!" + cond
            self.emit(f"while ({cond}) {{")
            self.indent += 1
            self.emit_region(first.pc + first.length, hi, inner)
            self.indent -= 1
            self.emit("}")
            return exit_pc

        if (not entered_from_before and latch is not None
                and latch.name in ("ifne", "ifeq") and latch.target == header):
            self.emit("do {")
            self.indent += 1
            self.emit_region(body_start, latch.pc, inner)
            self.indent -= 1
            cond = e_str(self.pop(), P_UNARY)
            if latch.name == "ifeq":
                cond = "!" + cond
            self.emit(f"}} while ({cond});")
            return exit_pc

        # 其余（含 for 的 `goto TEST` 形状）：while (true) { body; if (!cond) break; }
        self.emit("while (true) {")
        self.indent += 1
        if latch is not None and latch.name == "goto" and latch.target == header:
            self.emit_region(body_start, latch.pc, inner)
        else:
            self.emit_region(body_start, latch.pc if latch else hi + 1, inner)
            if latch is not None and latch.name in ("ifne", "ifeq"):
                cond = e_str(self.pop(), P_UNARY)
                if latch.name == "ifne":
                    cond = "!" + cond
                self.emit(f"if ({cond}) {{ break; }}")
        self.indent -= 1
        self.emit("}")
        return exit_pc

    def _try_forin(self, header, loop, ctx):
        """`iter N; goto TEST; BODY: ...; TEST: moreiter; ifeq END; iternext; goto BODY`

        SM 的 for-in 就是这个形状。识别出来直接发 `for (k in obj)`。
        """
        start = self.by_pc.get(header)
        if start is None or start.name != "iter":
            return None
        cur = self.next_pc(start)
        j = self.by_pc.get(cur)
        if j is None or j.name != "goto":
            return None
        test = j.target
        body = self.next_pc(j)
        t0 = self.by_pc.get(test)
        if t0 is None or t0.name != "moreiter":
            return None
        t1 = self.by_pc.get(self.next_pc(t0))
        if t1 is None or t1.name != "ifeq":
            return None
        t2 = self.by_pc.get(self.next_pc(t1))
        if t2 is None or t2.name != "iternext":
            return None
        t3 = self.by_pc.get(self.next_pc(t2))
        if t3 is None or t3.name != "goto" or t3.target != body:
            return None

        obj = self.pop_str(P_UNARY)
        key = self.temp("k")
        self.emit(f"for (var {key} in {obj}) {{")
        self.indent += 1
        inner = Ctx(brk={t1.target}, cont={body})
        # 循环体（iternext 之后会把 key 压在栈上，先记下来）
        self.push(e(key))
        self.emit_region(body, test, inner)
        self.indent -= 1
        self.emit("}")
        return t1.target

    # ---------------- 单条指令 ----------------

    def emit_ins(self, ins, end, ctx):
        # 指令预算：某些控制流形状会让区域递归来回跳，没有这个会**真的跑不完**
        # （全量 --check 时被卡死过一次，一急把 python 进程全杀了，
        #   顺手把正在跑的模拟服也杀了 —— 所以这道保险必须有。）
        self.budget -= 1
        if self.budget < 0:
            raise _Budget()
        handler = getattr(self, "op_" + ins.name, None)
        if handler is not None:
            return handler(ins, end, ctx)
        simple = SIMPLE_OPS.get(ins.name)
        if simple is not None:
            n_pop, render = simple
            args = [self.pop() for _ in range(n_pop)][::-1]
            self.push(render(self, ins, args))
            return ins.pc + ins.length
        self.comment(f"未处理指令 {ins.name}")
        return ins.pc + ins.length

    # ================= 控制流 =================

    def op_goto(self, ins, end, ctx):
        t = ins.target
        if t in ctx.cont:
            self.emit("continue;")
            return None
        if t in ctx.brk:
            self.emit("break;")
            return None
        # 跳进某个循环的范围里 —— 编译器给 while/for 发的 `goto TEST` 就是这个形状。
        # ⚠️ 只认**向前**跳：循环体内部的 `goto`（continue 之类）交给 ctx.cont，
        # 否则 `goto BODY` 会把同一个循环无限递归下去（真踩过，卡死）。
        if t > ins.pc:
            for header, loop in self.loops.items():
                if header < t <= loop["hi"]:
                    return self.emit_loop(header, end, ctx)
        return t

    def op_ifeq(self, ins, end, ctx):
        return self._cond_jump(ins, ctx, negate=False)

    def op_ifne(self, ins, end, ctx):
        return self._cond_jump(ins, ctx, negate=True)

    def _cond_jump(self, ins, ctx, negate):
        """`ifeq T`：栈顶为假就跳到 T。"""
        cond = self.pop()
        text = e_str(cond, P_UNARY)
        if negate:
            text = "!" + text
        t = ins.target

        if t in ctx.cont:
            self.emit(f"if ({text}) {{ continue; }}")
            return None
        if t in ctx.brk:
            self.emit(f"if ({text}) {{ break; }}")
            return None
        if t is None or t <= ins.pc:
            self.emit(f"if ({text}) {{ /* 回跳 */ }}")
            return self.next_pc(ins)

        then_start = self.next_pc(ins)
        join = t
        else_start = None
        prev = self._prev_ins(t)
        if prev is not None and prev.name == "goto" and prev.target is not None and prev.target > t:
            else_start = t
            join = prev.target
            then_end = prev.pc
        else:
            then_end = t

        # 先按表达式试试（三元）
        if self._try_ternary(cond, text, then_start, then_end, else_start, join):
            return join

        self.emit(f"if ({text}) {{")
        self.indent += 1
        if then_end > then_start:
            self.emit_region(then_start, then_end, Ctx(brk=ctx.brk, cont=ctx.cont))
        self.indent -= 1
        self.emit("}")
        if else_start is not None and join > else_start:
            self.emit("else {")
            self.indent += 1
            self.emit_region(else_start, join, Ctx(brk=ctx.brk, cont=ctx.cont))
            self.indent -= 1
            self.emit("}")
        return join

    def _try_ternary(self, cond, cond_text, then_start, then_end, else_start, join):
        """两个分支都只往栈上留一个值、且没产生语句 -> `cond ? a : b`。"""
        if else_start is None:
            return False
        depth = len(self.stack)

        self.expr_depth += 1
        saved_lines = self.lines
        self.lines = []
        try:
            self.emit_region(then_start, then_end, Ctx())
            if self.lines:
                raise _NoTernary()
            if len(self.stack) != depth + 1:
                raise _NoTernary()
            a = self.pop()
            self.emit_region(else_start, join, Ctx())
            if self.lines:
                raise _NoTernary()
            if len(self.stack) != depth + 1:
                raise _NoTernary()
            b = self.pop()
        except _NoTernary:
            # 回滚
            del self.stack[depth:]
            return False
        except Exception:  # noqa: BLE001
            del self.stack[depth:]
            return False
        finally:
            self.expr_depth -= 1
            self.lines = saved_lines

        self.push(e(f"{q(cond_text, P_COND, P_COND)} ? {e_str(a, P_ASSIGN)} : {e_str(b, P_ASSIGN)}",
                   P_COND))
        return True

    def _prev_ins(self, pc):
        prev = None
        for i in self.ins:
            if i.pc >= pc:
                break
            prev = i
        return prev

    # ---- && / || ----

    def op_and(self, ins, end, ctx):
        left = self.pop()
        depth = len(self.stack)
        self.eval_region(self.next_pc(ins), ins.target, ctx)
        right = self.pop() if len(self.stack) > depth else e("undefined")
        self.push(e(f"{e_str(left, P_AND)} && {e_str(right, P_AND + 1)}", P_AND))
        return ins.target

    def op_or(self, ins, end, ctx):
        left = self.pop()
        depth = len(self.stack)
        self.eval_region(self.next_pc(ins), ins.target, ctx)
        right = self.pop() if len(self.stack) > depth else e("undefined")
        self.push(e(f"{e_str(left, P_OR)} || {e_str(right, P_OR + 1)}", P_OR))
        return ins.target

    # ---- switch ----

    def op_tableswitch(self, ins, end, ctx):
        disc = self.pop_str(P_UNARY)
        info = ins.arg
        end_pc = ins.target
        self.emit(f"switch ({disc}) {{")
        self.indent += 1
        targets = sorted({t for _v, t in info["cases"]} | {info["default"]})
        for value, target in info["cases"]:
            if self.by_pc.get(target) is None:
                continue
            self.emit(f"case {value}:")
        self.emit("default:")
        self.emit("    break;")
        self.indent -= 1
        self.emit("}")
        self.comment("switch 的 case 体这里没展开（见表里的跳转）")
        return end_pc

    def op_condswitch(self, ins, end, ctx):
        return self.next_pc(ins)

    def op_case(self, ins, end, ctx):
        return ins.target

    def op_default(self, ins, end, ctx):
        return ins.target

    # ---- 异常 ----

    def op_try(self, ins, end, ctx):
        self.comment("try 开始")
        return self.next_pc(ins)

    def op_finally(self, ins, end, ctx):
        self.comment("finally")
        return self.next_pc(ins)

    def op_exception(self, ins, end, ctx):
        self.push(e("$exception"))
        return self.next_pc(ins)

    def op_throw(self, ins, end, ctx):
        self.emit(f"throw {self.pop_str()};")
        return self.next_pc(ins)

    def op_gosub(self, ins, end, ctx):
        return self.next_pc(ins)

    def op_retsub(self, ins, end, ctx):
        return None

    # ================= 返回 =================

    def op_return(self, ins, end, ctx):
        if self.is_top:
            return None
        self.emit("return;")
        return None

    def op_retrval(self, ins, end, ctx):
        if self.is_top:
            return None
        self.emit("return;")
        return None

    def op_setrval(self, ins, end, ctx):
        if self.is_top:
            self.pop()
            return None
        self.emit(f"return {self.pop_str()};")
        return None

    # ================= 变量 =================

    def op_getlocal(self, ins, end, ctx):
        self.push(e(self.local_name(ins.arg)))
        return self.next_pc(ins)

    def op_getarg(self, ins, end, ctx):
        self.push(e(self.arg_name(ins.arg)))
        return self.next_pc(ins)

    def op_setlocal(self, ins, end, ctx):
        return self._assign(self.local_name(ins.arg), ins, local_slot=ins.arg)

    def op_setarg(self, ins, end, ctx):
        return self._assign(self.arg_name(ins.arg), ins)

    def _assign(self, target, ins, local_slot=None):
        v = self.pop_str()
        # 第一次给这个局部变量赋值 -> 补个 `var`（槽号空间里全是 var，参数走 getarg/setarg）
        if local_slot is not None and local_slot not in self.assigned:
            self.assigned.add(local_slot)
            target = "var " + target
        nxt = self.by_pc.get(self.next_pc(ins))
        if nxt is not None and nxt.name == "pop":
            self.emit(f"{target} = {v};")
            return self.next_pc(nxt)
        self.push(e(f"{target} = {v}", P_ASSIGN))
        return self.next_pc(ins)

    def op_getaliasedvar(self, ins, end, ctx):
        hops, slot = ins.arg
        self.push(e(self._aliased_name(hops, slot)))
        return self.next_pc(ins)

    def op_setaliasedvar(self, ins, end, ctx):
        hops, slot = ins.arg
        v = self.pop_str()
        self.push(e(f"{self._aliased_name(hops, slot)} = {v}", P_ASSIGN))
        return self.next_pc(ins)

    def _aliased_name(self, hops, slot):
        if hops == 0:
            return self.local_name(slot)
        f = self.dec.parent_of(self.s, hops - 1)
        b = getattr(f, "bindings", []) if f is not None else None
        if b:
            idx = getattr(f, "nargs", 0) + slot
            if 0 <= idx < len(b) and b[idx][0]:
                return b[idx][0]
        return f"$up{hops}_{slot}"

    # ================= 全局 / 名字 =================

    def op_getgname(self, ins, end, ctx):
        self.push(e(ins.atom))
        return self.next_pc(ins)

    op_bindgname = op_getgname
    op_bindname = op_getgname
    op_name = op_getgname

    def op_setgname(self, ins, end, ctx):
        self.push(e(f"{ins.atom} = {self.pop_str()}", P_ASSIGN))
        return self.next_pc(ins)

    def op_setname(self, ins, end, ctx):
        """`bindname X; <值>; setname X` —— 栈上是 [名字对象, 值]，直接写 `X = 值`。"""
        v = self.pop_str()
        self.pop()                  # 名字对象（没有别的用处）
        self.push(e(f"{ins.atom} = {v}", P_ASSIGN))
        return self.next_pc(ins)

    def op_defvar(self, ins, end, ctx):
        self.emit(f"var {ins.atom};")
        self.pending_var = ins.atom
        return self.next_pc(ins)

    def op_defconst(self, ins, end, ctx):
        self.emit(f"var {ins.atom};")
        self.pending_var = ins.atom
        return self.next_pc(ins)

    def op_setconst(self, ins, end, ctx):
        self.emit(f"var {ins.atom} = {self.pop_str()};")
        return self.next_pc(ins)

    def op_delname(self, ins, end, ctx):
        self.push(e(f"delete {ins.atom}", P_UNARY))
        return self.next_pc(ins)

    def op_implicitthis(self, ins, end, ctx):
        self.push(e("this"))
        return self.next_pc(ins)

    # ================= 属性 =================

    def op_getprop(self, ins, end, ctx):
        obj = self.pop()
        self.push(e(make_member(obj[0], obj[1], ins.atom), P_MEMBER))
        return self.next_pc(ins)

    op_getxprop = op_getprop

    def op_length(self, ins, end, ctx):
        obj = self.pop()
        self.push(e(f"{q(obj[0], obj[1], P_MEMBER)}.length", P_MEMBER))
        return self.next_pc(ins)

    def op_setprop(self, ins, end, ctx):
        v = self.pop_str()
        obj = self.pop()
        self.push(e(f"{make_member(obj[0], obj[1], ins.atom)} = {v}", P_ASSIGN))
        return self.next_pc(ins)

    def op_getelem(self, ins, end, ctx):
        idx = self.pop_str()
        obj = self.pop()
        self.push(e(f"{q(obj[0], obj[1], P_MEMBER)}[{idx}]", P_MEMBER))
        return self.next_pc(ins)

    def op_callelem(self, ins, end, ctx):
        return self.op_getelem(ins, end, ctx)

    def op_setelem(self, ins, end, ctx):
        v = self.pop_str()
        idx = self.pop_str()
        obj = self.pop()
        self.push(e(f"{q(obj[0], obj[1], P_MEMBER)}[{idx}] = {v}", P_ASSIGN))
        return self.next_pc(ins)

    def op_delprop(self, ins, end, ctx):
        obj = self.pop()
        self.push(e(f"delete {make_member(obj[0], obj[1], ins.atom)}", P_UNARY))
        return self.next_pc(ins)

    def op_delelem(self, ins, end, ctx):
        idx = self.pop_str()
        obj = self.pop()
        self.push(e(f"delete {q(obj[0], obj[1], P_MEMBER)}[{idx}]", P_UNARY))
        return self.next_pc(ins)

    def op_mutateproto(self, ins, end, ctx):
        v = self.pop_str()
        obj = self.pop()
        self.emit(f"{q(obj[0], obj[1], P_PRIMARY)}.__proto__ = {v};")
        return self.next_pc(ins)

    # ================= 调用 =================

    def _do_call(self, nargs, kind="call", ins=None):
        args = [self.pop_str() for _ in range(nargs)][::-1]
        if kind == "new":
            # `new` 的形状是 `[callee, thisArg(undefined), args...]`，
            # thisArg 也得弹掉 —— 早先漏了它，栈就整体错位一格
            self.pop()
            callee = self.pop()
            self.push(e(f"new {q(callee[0], callee[1], P_NEW)}({', '.join(args)})", P_NEW))
            return
        thisv = self.pop()          # this 值
        callee = self.pop()
        # `obj.m(...)`：SM 会压 obj 当 this，callee 也是同一个成员表达式 —— 收敛掉
        if kind == "spread":
            text = f"{q(callee[0], callee[1], P_CALL)}(...[{', '.join(args)}])"
        else:
            text = f"{q(callee[0], callee[1], P_CALL)}({', '.join(args)})"
        self.push(e(text, P_CALL))

    def op_call(self, ins, end, ctx):
        self._do_call(ins.arg)
        return self.next_pc(ins)

    op_funcall = op_call
    op_eval = op_call

    def op_callprop(self, ins, end, ctx):
        """`JSOP_CALLPROP` 就是 `GETPROP`：弹对象、压函数。

        调用时的 `this` 是**前面那条 `dup` 留下的副本**（真实字节码形状：
        `getprop x; dup; callprop "m"; swap; <args>; call N`）。
        早先这里多弹了一个，结果整个栈错位 —— `cc.assert(...)` 变成 `undefined(...)`。
        """
        return self.op_getprop(ins, end, ctx)

    def op_new(self, ins, end, ctx):
        self._do_call(ins.arg, "new")
        return self.next_pc(ins)

    def op_spreadcall(self, ins, end, ctx):
        self._do_call(1, "spread")
        return self.next_pc(ins)

    def op_spreadnew(self, ins, end, ctx):
        self._do_call(1, "spread")
        return self.next_pc(ins)

    def op_spreadeval(self, ins, end, ctx):
        self._do_call(1, "spread")
        return self.next_pc(ins)

    def op_funapply(self, ins, end, ctx):
        args = [self.pop_str() for _ in range(ins.arg)][::-1]
        thisv = self.pop_str()
        callee = self.pop()
        self.push(e(f"{q(callee[0], callee[1], P_CALL)}.apply({thisv}, [{', '.join(args)}])",
                    P_CALL))
        return self.next_pc(ins)

    def op_setcall(self, ins, end, ctx):
        return self.next_pc(ins)

    # ================= 字面量 =================

    def op_newarray(self, ins, end, ctx):
        n = ins.arg or 0
        elems = [self.pop_str() for _ in range(n)][::-1] if n else []
        self.push(e("[" + ", ".join(elems) + "]"))
        return self.next_pc(ins)

    def _literal_new(self, ins, end, ctx):
        self.push(e("{}"))
        return self.next_pc(ins)

    op_newobject = _literal_new
    op_newinit = _literal_new

    def _literal_add(self, prop):
        """对象/数组字面量的 `INIT*` 系列**不弹容器**，容器一直在栈顶，
        只把值（和键）弹掉。所以这里是就地改栈顶，不是弹了再压。"""
        if not self.stack:
            return
        text = self.stack[-1][0]
        if text.endswith("}"):
            inner = text[1:-1]
            self.stack[-1] = e("{" + inner + (", " if inner else "") + prop + "}")
        elif text.endswith("]"):
            inner = text[1:-1]
            self.stack[-1] = e("[" + inner + (", " if inner else "") + prop + "]")
        else:
            self.stack[-1] = e(f"{text} /* {prop} */")

    def op_initprop(self, ins, end, ctx):
        v = self.pop_str()
        key = ins.atom
        if prop_name(key):
            self._literal_add(f"{key}: {v}")
        else:
            self._literal_add(f"{js_str(key)}: {v}")
        return self.next_pc(ins)

    def op_initprop_getter(self, ins, end, ctx):
        v = self.pop_str()
        self._literal_add(f"get {ins.atom}() {v}")
        return self.next_pc(ins)

    def op_initprop_setter(self, ins, end, ctx):
        v = self.pop_str()
        self._literal_add(f"set {ins.atom}(v) {v}")
        return self.next_pc(ins)

    def op_initelem(self, ins, end, ctx):
        v = self.pop_str()
        k = self.pop_str()
        key = k if re.fullmatch(r"(?:0|[1-9][0-9]*)", k) else "[" + k + "]"
        self._literal_add(f"{key}: {v}")
        return self.next_pc(ins)

    def op_initelem_array(self, ins, end, ctx):
        """带下标操作数：只弹值，容器留在栈上（`JSOP_INITELEM_ARRAY`）。"""
        self._literal_add(self.pop_str())
        return self.next_pc(ins)

    def op_initelem_inc(self, ins, end, ctx):
        self._literal_add(self.pop_str())
        return self.next_pc(ins)

    def op_endinit(self, ins, end, ctx):
        return self.next_pc(ins)

    def op_arraypush(self, ins, end, ctx):
        v = self.pop_str()
        if self.stack and self.stack[-1][0].endswith("]"):
            self._literal_add(v)
        else:
            arr = self.pop()
            self.push(e(f"{q(arr[0], arr[1], P_CALL)}.push({v})", P_CALL))
        return self.next_pc(ins)

    # ================= 函数 =================

    def op_lambda(self, ins, end, ctx):
        self.push(e(self.dec.render_child(self, ins.obj)))
        return self.next_pc(ins)

    op_lambda_arrow = op_lambda
    op_deffun = op_lambda

    def op_callee(self, ins, end, ctx):
        self.push(e("arguments.callee"))
        return self.next_pc(ins)

    def op_arguments(self, ins, end, ctx):
        self.push(e("arguments"))
        return self.next_pc(ins)

    # ================= 栈操作 =================

    def op_pop(self, ins, end, ctx):
        # 表达式模式：这里的 pop 丢的是我们已经取走的那份值（`and`/`or` 的形状），
        # 不能再从栈上弹一次，否则会把外面等着用的值吃掉。
        if self.expr_depth:
            return self.next_pc(ins)
        v = self.pop_str()
        if v and not v.startswith("/*"):
            # 匿名函数当**表达式语句**发出去是语法错，包一层括号
            if v.startswith("function") or v.startswith("{"):
                v = "(" + v + ")"
            self.emit(v + ";")
        return self.next_pc(ins)

    def op_popn(self, ins, end, ctx):
        for _ in range(ins.arg):
            v = self.pop_str()
            if v:
                if v.startswith("function") or v.startswith("{"):
                    v = "(" + v + ")"
                self.emit(v + ";")
        return self.next_pc(ins)

    def op_swap(self, ins, end, ctx):
        if len(self.stack) >= 2:
            self.stack[-1], self.stack[-2] = self.stack[-2], self.stack[-1]
        return self.next_pc(ins)

    def op_dup(self, ins, end, ctx):
        if self.stack:
            self.stack.append(self.stack[-1])
        return self.next_pc(ins)

    def op_dup2(self, ins, end, ctx):
        if len(self.stack) >= 2:
            self.stack.extend(self.stack[-2:])
        return self.next_pc(ins)

    def _pick(self, n):
        if len(self.stack) > n:
            self.stack.append(self.stack[-1 - n])

    def op_pick(self, ins, end, ctx):
        self._pick(ins.arg)
        return self.next_pc(ins)

    op_dupat = op_pick

    def op_tostring(self, ins, end, ctx):
        self.push(e(f"String({self.pop_str(P_UNARY)})", P_CALL))
        return self.next_pc(ins)

    def op_toid(self, ins, end, ctx):
        return self.next_pc(ins)

    def op_rest(self, ins, end, ctx):
        self.push(e("/* rest */"))
        return self.next_pc(ins)

    # ================= for-in 的零散指令 =================

    def op_iter(self, ins, end, ctx):
        """SM 33 的 for-in 实际形状（实测出来的）：

            <obj>
            iter 1
            goto TEST
          BODY:
            loophead
            iternext            ; 把键压栈
            setlocal K          ; K = 键
            pop
            <循环体>
          TEST:
            moreiter
            ifeq END
            goto BODY
          END:
        """
        obj = self.pop_str(P_UNARY)
        bad = lambda: self.push(e(f"/* iter({obj}) */")) or self.next_pc(ins)

        j = self.by_pc.get(self.next_pc(ins))
        if j is None or j.name != "goto":
            return bad()
        test = j.target
        raw_body = self.next_pc(j)
        body = raw_body
        head = self.by_pc.get(body)
        if head is not None and head.name in ("loophead", "loopentry", "nop"):
            body = self.next_pc(head)
        it0 = self.by_pc.get(body)
        if it0 is None or it0.name != "iternext":
            return bad()
        it1 = self.by_pc.get(self.next_pc(it0))
        if it1 is None or it1.name != "setlocal":
            return bad()
        it2 = self.by_pc.get(self.next_pc(it1))
        if it2 is None or it2.name != "pop":
            return bad()

        t0 = self.by_pc.get(test)
        while t0 is not None and t0.name in ("loopentry", "loophead", "nop"):
            t0 = self.by_pc.get(self.next_pc(t0))
        if t0 is None or t0.name != "moreiter":
            return bad()
        t1 = self.by_pc.get(self.next_pc(t0))
        if t1 is None or t1.name not in ("ifne", "ifeq"):
            return bad()
        # `ifne BODY` = 还有下一项就回循环体；出口在 enditer 之后（就是落空的那条路）
        if t1.target not in (raw_body, body):
            return bad()
        t2 = self.by_pc.get(self.next_pc(t1))
        if t2 is None or t2.name != "enditer":
            return bad()
        exit_pc = self.next_pc(t2)

        key = self.local_name(it1.arg)
        self.emit(f"for (var {key} in {obj}) {{")
        self.indent += 1
        inner = Ctx(brk={exit_pc}, cont={raw_body, body, it0.pc})
        self.assigned.add(it1.arg)
        self.emit_region(self.next_pc(it2), test, inner)
        self.indent -= 1
        self.emit("}")
        return exit_pc

    def op_moreiter(self, ins, end, ctx):
        return self.next_pc(ins)

    def op_iternext(self, ins, end, ctx):
        return self.next_pc(ins)

    def op_enditer(self, ins, end, ctx):
        return self.next_pc(ins)

    # ================= 杂项 =================

    def _skip(self, ins, end, ctx):
        return self.next_pc(ins)

    op_loophead = _skip
    op_loopentry = _skip
    op_nop = _skip
    op_label = _skip
    op_lineno = _skip
    op_runonce = _skip
    op_backpatch = _skip
    op_pushblockscope = _skip
    op_popblockscope = _skip
    op_debugleaveblock = _skip
    op_leavewith = _skip
    op_generator = _skip
    op_throwing = _skip
    op_setintrinsic = _skip
    op_enterwith = _skip

    def op_debugger(self, ins, end, ctx):
        self.emit("debugger;")
        return self.next_pc(ins)

    def op_yield(self, ins, end, ctx):
        if self.stack:
            self.emit(f"yield {self.pop_str()};")
        else:
            self.emit("yield;")
        return self.next_pc(ins)

    def op_getintrinsic(self, ins, end, ctx):
        self.push(e(ins.atom))
        return self.next_pc(ins)

    def op_bindintrinsic(self, ins, end, ctx):
        self.push(e(ins.atom))
        return self.next_pc(ins)


class _NoTernary(Exception):
    pass


class _Budget(Exception):
    """指令预算用光 —— 见 FuncEmitter.emit_ins。"""
    pass


# ---------------------------------------------------------------------------
# 一元 / 二元
# ---------------------------------------------------------------------------

def _bin(sym):
    def render(fe, ins, args):
        a, ap = args[0]
        b, bp = args[1]
        p = BINOP_PREC[sym]
        return e(f"{q(a, ap, p)} {sym} {q(b, bp, p + 1)}", p)
    return render


def _un(sym):
    def render(fe, ins, args):
        a, ap = args[0]
        return e(f"{sym}{q(a, ap, P_UNARY)}", P_UNARY)
    return render


def _lit(text):
    return lambda fe, ins, args: e(text)


SIMPLE_OPS = {
    "undefined": (0, _lit("undefined")),
    "null": (0, _lit("null")),
    "true": (0, _lit("true")),
    "false": (0, _lit("false")),
    "zero": (0, _lit("0")),
    "one": (0, _lit("1")),
    "hole": (0, _lit("undefined")),
    "this": (0, _lit("this")),
    "int8": (0, lambda fe, i, a: e(str(i.arg))),
    "uint16": (0, lambda fe, i, a: e(str(i.arg))),
    "uint24": (0, lambda fe, i, a: e(str(i.arg))),
    "int32": (0, lambda fe, i, a: e(str(i.arg))),
    "double": (0, lambda fe, i, a: e(fe.dec.render_const(fe.s, i.const_idx))),
    "string": (0, lambda fe, i, a: e(js_str(i.atom))),
    "regexp": (0, lambda fe, i, a: e(fe.dec.render_regexp(fe.s, i.regexp_idx))),
    "bitor": (2, _bin("|")),
    "bitxor": (2, _bin("^")),
    "bitand": (2, _bin("&")),
    "eq": (2, _bin("==")),
    "ne": (2, _bin("!=")),
    "stricteq": (2, _bin("===")),
    "strictne": (2, _bin("!==")),
    "lt": (2, _bin("<")),
    "le": (2, _bin("<=")),
    "gt": (2, _bin(">")),
    "ge": (2, _bin(">=")),
    "lsh": (2, _bin("<<")),
    "rsh": (2, _bin(">>")),
    "ursh": (2, _bin(">>>")),
    "add": (2, _bin("+")),
    "sub": (2, _bin("-")),
    "mul": (2, _bin("*")),
    "div": (2, _bin("/")),
    "mod": (2, _bin("%")),
    "in": (2, _bin("in")),
    "instanceof": (2, _bin("instanceof")),
    "not": (1, _un("!")),
    "bitnot": (1, _un("~")),
    "neg": (1, _un("-")),
    "pos": (1, _un("+")),
    "typeof": (1, _un("typeof ")),
    "typeofexpr": (1, _un("typeof ")),
    "void": (1, _un("void ")),
}


# ---------------------------------------------------------------------------

class Decompiler:
    def __init__(self, path):
        self.path = path
        self.root = J.read_file(path)
        self._parent = {}
        self._index(self.root, None)

    def _index(self, s, parent):
        self._parent[id(s)] = parent
        for c in getattr(s, "children", []) or []:
            self._index(c, s)

    def parent_of(self, script, levels):
        cur = script
        for _ in range(levels):
            cur = self._parent.get(id(cur))
            if cur is None:
                return None
        return cur

    def render_child(self, fe, obj_index):
        kids = getattr(fe.s, "children", []) or []
        if obj_index is None or obj_index >= len(kids):
            return "function () {}"
        return self.render_script(kids[obj_index], fe.level + 1)

    def render_script(self, script, level):
        cached = getattr(script, "_rendered", None)
        if cached is not None:
            return cached
        fe = FuncEmitter(self, script, level, is_top=False)
        text = fe.run()
        script._rendered = text
        return text

    def render_const(self, script, idx):
        consts = getattr(script, "consts", []) or []
        if idx is None or idx >= len(consts):
            return "undefined"
        tag, val = consts[idx]
        return {0: lambda: str(val), 1: lambda: repr(val),
                2: lambda: js_str(val), 3: lambda: "true", 4: lambda: "false",
                5: lambda: "null", 7: lambda: "undefined",
                8: lambda: "undefined"}.get(tag, lambda: "undefined")()

    def render_regexp(self, script, idx):
        regs = getattr(script, "regexps", []) or []
        if idx is None or idx >= len(regs):
            return "/(?:)/"
        source, flag = regs[idx]
        f = ("g" if flag & 1 else "") + ("i" if flag & 2 else "") + ("m" if flag & 4 else "")
        # 只转义**没被转义过**的 `/` —— SM 的 source 里可能已经带着 `\/` 了，
        # 无脑 replace 会变成 `\\/`（实测把 httpc.jsc / whklayer.jsc 的语法搞坏）
        source = re.sub(r"(?<!\\)/", r"\\/", source)
        return "/" + source + "/" + f

    def decompile(self):
        root = self.root
        kids = getattr(root, "children", []) or []
        parts = []
        if kids:
            for child in kids:
                text = self.render_script(child, 0)
                # 匿名顶层函数当**语句**发出去就是语法错（`function(){}`），包一层括号
                if re.match(r"\s*function\s*\(", text):
                    text = "(" + text + ");"
                parts.append(text)
        else:
            fe = FuncEmitter(self, root, 0, is_top=True)
            parts.append(fe.run())
        header = f"// 由 tools/jsc_decompile.py 从 {os.path.basename(self.path)} 反编译\n"
        note = ""
        if getattr(root, "truncated", False):
            note = "// ⚠️ 这个 jsc 的 XDR 有一段没解出来的布局，只还原了一部分\n"
        return header + note + "\n\n".join(p for p in parts if p.strip()) + "\n"


def decompile_file(path):
    return Decompiler(path).decompile()


def main():
    ap = argparse.ArgumentParser(description="jsc -> js 反编译器")
    ap.add_argument("path", nargs="?", help="jsc 文件")
    ap.add_argument("-o", "--out", help="输出文件（默认 stdout）")
    ap.add_argument("--filter", help="批量：路径里含这个子串的所有 jsc")
    ap.add_argument("--root", default=r"E:\code\apk\zcsmw\assets")
    ap.add_argument("--check", action="store_true", help="反编译后跑 node --check 验语法")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    if args.filter is not None or args.check:
        return batch(args)

    if not args.path:
        ap.print_help()
        return 1
    text = decompile_file(args.path)
    if args.out:
        io.open(args.out, "w", encoding="utf-8", newline="\n").write(text)
        print(f"-> {args.out}  ({len(text)} 字符)")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 0


def batch(args):
    files = []
    for dirpath, _d, names in os.walk(args.root):
        for n in names:
            if n.endswith(".jsc"):
                p = os.path.join(dirpath, n)
                if args.filter and args.filter not in p:
                    continue
                files.append(p)
    files.sort()
    if args.limit:
        files = files[:args.limit]

    ok = fail = 0
    problems = []
    for p in files:
        try:
            text = decompile_file(p)
        except Exception as exc:  # noqa: BLE001
            fail += 1
            problems.append((p, "反编译异常 " + type(exc).__name__ + ": " + str(exc)[:70]))
            continue
        if args.check:
            r = subprocess.run(["node", "--check", "-"], input=text, capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                fail += 1
                err = [l for l in (r.stderr or "").splitlines() if l.strip()]
                problems.append((p, "语法错 " + (err[0][:80] if err else "?")))
                continue
        ok += 1
        if args.filter and not args.check:
            print(f"--- {p}")
            print(text)
    print(f"\n成功 {ok}，失败 {fail}（共 {len(files)}）")
    for p, why in problems[:30]:
        print(f"  {why:80s} {p[len(args.root):]}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
