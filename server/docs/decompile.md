# jsc → js 反编译

```
python script\jsc_decompile.py <file.jsc>            # 打到 stdout
python script\jsc_decompile.py <file.jsc> -o out.js
python script\jsc_decompile.py --filter soldier      # 批量（路径子串）
python script\jsc_decompile.py --check               # 全部反编译 + node --check 验语法
```

## 结果

| 范围 | 通过语法检查 |
|---|---|
| `assets/src/**`（不含 `table/`，575 个） | **572**（99.5%） |
| 失败 | 3 个 —— 另一种 `for-in` 形状没认出来，位置退化成 `/* iter(x) */` |

「通过」= 反编译出来的文本能被 `node --check` 接受。**语法对不等于语义全对**，
但这已经是能读的程度了。逐条对照过的样本见下面「验证」。

## 它是怎么工作的

三层，都建立在 `script/jsc_disasm.py` 之上（XDR 解析 + 脚本树 + atom 表）：

1. **解码** —— 字节码 → `Ins`（操作数、跳转目标、atom / const / 对象索引）。
   SM 的 XDR 立即数一律**大端**。
2. **表达式重建** —— SM 的字节码是**栈机**，模拟操作数栈就能还原表达式树。
   每个函数维护 `self.stack`，`pop` 一条指令时把栈顶当表达式语句发出去。
3. **结构恢复** —— 回边 → 循环；向前的条件跳转 → `if/else`；
   两个分支都只往栈上留一个值就拼成 `?:`。

### 三个值得记的形状

**`&&` / `||`（`and`/`or` 是跳转且不弹栈）**

```
  <A>
  and L        ; A 为假就跳 L（A 留在栈上）
  pop
  <B>
L:
```

实现：先弹出 A，把 `(pc+len, L)` 当**表达式区域**跑一遍（表达式模式下 `pop` 是空操作），
跑完栈顶就是 B，拼成 `A && B`。

**`if/else` 和 `?:` 是同一个形状**，区别只在分支里有没有语句：

```
  <c>
  ifeq Lelse
  <A>
  goto Lend
Lelse:
  <B>
Lend:
```

`_cond_jump` 先**试探性**地按表达式跑两块：都只留一个值、都没产生语句 → 三元；
否则回退成语句形式的 `if/else`。

**`for-in`（SM 33 的实际形状，和文档里写的不一样）**

```
  <obj>
  iter 1
  goto TEST
BODY:
  loophead
  iternext          ; 把键压栈
  setlocal K        ; K = 键
  pop
  <循环体>
TEST:
  loopentry 129
  moreiter
  ifne BODY         ; 还有下一项就回循环体
  enditer           ; 落空 = 循环出口
```

出口是 `enditer` **之后**那条路，不是 `ifne` 的目标。一开始按「`moreiter` 后面跟 `ifeq`」
去找，结果一个都没认出来。

## 顺带挖出来的两个 SpiderMonkey 事实

**① `getlocal` / `setlocal` 的操作数是 3 字节（uint24），不是 2 字节。**

指令长度是 4（1 opcode + 3 操作数），`JSOP_GETLOCAL` 用 `GetLocalNo(pc) = GET_UINT24(pc)` 取。
早先反汇编器按 u16 读，**所有局部变量的槽号都被解成 0**
（槽 3 编码成 `00 00 03`，读前两字节就是 0），反汇编和反编译全错。
`script/jsc_disasm.py` 和 `script/jsc_decompile.py` 都已修。

**② 局部变量槽号里不含参数。**

扫了 `soldier.jsc` 里所有函数，`max(getlocal/setlocal 操作数)` **恒等于 `nvars - 1`**。
也就是说槽号空间只覆盖 vars，参数走 `getarg`/`setarg`（那是另一套下标，直接索引 `bindings`）。
所以名字要取 `bindings[nargs + S]`：

```python
def local_name(slot):
    return bindings[script.nargs + slot][0]
```

早先按 `bindings[slot]` 取名，结果是**所有局部变量都借用了参数的名字**
（`row` 显示成 `npcId`，`attrBase` 显示成 `robotParams`）——
这种错很难看出来，因为代码"读起来通顺"，只是语义完全不对。

## 验证

**`calcSoldierUpgrade`**（`src/data/charcenter.jsc`）—— 这个函数之前是**手工**从反汇编
逐条推出来的（见 `gamesrv/soldier.py` 的 docstring，服务端复刻了它的经验曲线），
正好拿来当标准答案。反编译输出：

```js
calcSoldierUpgrade: function calcSoldierUpgrade(materials, target) {
    var gainExp = 0; var needMoney = 0; var skillLv = 0;
    var tarLv = 0; var tarExp = 0; var tarSkillLv = 0;
    for (var k in materials) {
        soldier;
        if ((typeof materials[k] == "string" || typeof materials[k] == "number")) {
            soldier = this._soldiers[materials[k]];
        } else if ((typeof materials[k] == "object")) {
            soldier = materials[k];
        } else { /* 冗余的一层重复，见下 */ }
        gainExp = gainExp + table_soldier_to_exp[soldier.lv]["quality_" + soldier.quality + "_" + soldier.star];
        gainExp = gainExp + table_soldier[soldier.key].base_exp;
        needMoney = needMoney + table_soldier[soldier.key].base_cost;
        needMoney = needMoney + table_soldier_to_cost_for_upgrade[soldier.lv][...];
        ...
```

对照手工推的版本：

```js
for (k in materials) {
    soldier = (typeof materials[k] === "string" || typeof materials[k] === "number")
              ? this._soldiers[materials[k]] : materials[k];
    gainExp += table_soldier_to_exp[soldier.lv]["quality_"+soldier.quality+"_"+soldier.star];
    gainExp += table_soldier[soldier.key].base_exp;
    needMoney += table_soldier[soldier.key].base_cost;
    needMoney += table_soldier_to_cost_for_upgrade[soldier.lv][...];
    ...
```

**逐条对得上**（`base_exp` 那行手工推的时候漏了，反编译反而更全）。

## 已知问题（都是"能读但不够漂亮"这一档）

* **`?:` 有时没收敛成三元**，而是展开成 `if/else`，并且在 `else` 里**冗余地重复**一遍同样的
  条件判断，最后以 `continue` 收尾。语义等价（同一套条件、结果一样），但啰嗦。
  上面 `calcSoldierUpgrade` 里那段 `else { while (true) { ... continue; } }` 就是这个。
* **`for(;;)` 一律发成 `while (true)` + `if (!cond) break;`**，不还原成 `for (init; test; update)`。
* **`try/catch` 没做**：只发 `/* try 开始 */` 注释，`throw` 能出，`catch` 体展开不平整。
* **3 个文件**的 `for-in` 没认出来（位置退化成 `/* iter(x) */`）。
* **13/798 个 jsc** 的 XDR 有一段没搞清楚的布局（见 `jsc_disasm.resync_object`），只解出一部分。
* `i = +i + 1` 这种（`JSOP_POS` 是 `++` 的真实语义）保留了原样，没美化成 `i++`。

## 实现上踩过的坑（留给以后的自己）

| 现象 | 原因 |
|---|---|
| 整个文件跑不完（真卡死过） | `goto` 指回循环头 → `emit_loop` 无限递归。现在有 `active_loops` 去重 + 指令预算 `_Budget` |
| 所有局部变量用参数的名字 | 槽号映射错了，见上面 ② |
| `cc.assert(...)` 变成 `undefined(...)` | `JSOP_CALLPROP` 就是 `GETPROP`（弹对象压函数），我多弹了一个；调用时的 `this` 是前面那条 `dup` 留下的副本 |
| `this._x = ...` 变成 `""._x = ...` | `and`/`or` 的表达式区域里那个 `pop` 把外面等着用的值吃了 —— 表达式模式下 `pop` 必须是空操作 |
| 对象字面量崩成 `undefined /* [...] */` 套几十层 | `INITPROP`/`INITELEM` 系列**不弹容器**，容器一直在栈顶，只弹值和键 |
| `new X()` 变成 `new undefined()` | `new` 的栈形状是 `[callee, thisArg, args...]`，漏弹了 `thisArg` |
| `function() {}` 当语句发出去语法错 | 匿名函数表达式的语句形式要包括号 |
