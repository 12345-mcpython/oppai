# 逆向方法

这个项目对客户端的逆向没有用现成的反编译工具（市面上的
`cocos2d-jsc-decompiler` 都是要在 Linux 上重新编译整个 SpiderMonkey），
而是两条路并用：

1. **自己写 jsc 反汇编器** —— 拿确定性的字节码，读逻辑
2. **运行时探测** —— 往客户端里注入明文 JS，开 REPL / 打日志 / 套 Proxy

---

## 一、jsc 反汇编器

### 1.1 为什么要自己写

`.jsc` 是 SpiderMonkey 的 **XDR 字节码**：

* 没有加密（不像 cocos2d-x 的 Lua 那样 XXTEA）
* **不保留源码**：`Function.prototype.toString()` 返回 `[sourceless code]`，
  `Error().stack` 只给到行列号
* 格式完全由引擎版本决定

引擎版本可以直接从 `libcocos2djs.so` 里读出来：

```
$ strings libcocos2djs.so | grep JavaScript-C
JavaScript-C33.1.1
```

文件头的 4 字节 `2c c0 73 b9`（LE）= `0xb973c02c`，而 SM 33.1.1 的
`js/src/vm/Xdr.h` 里写着：

```cpp
static const uint32_t XDR_BYTECODE_VERSION = uint32_t(0xb973c0de - 178);
// 0xb973c0de - 178 = 0xb973c02c   ✅ 完全对上
```

版本对上就意味着可以照源码实现解析器。

### 1.2 需要的源码

从 `hg-edge.mozilla.org` 直接拉 `FIREFOX_33_1_1_RELEASE`：

```
js/src/jsscript.cpp      XDRScript / XDRScriptConst / XDRScriptBindings / ScriptSource::performXDR
js/src/vm/Xdr.h          XDR_BYTECODE_VERSION
js/src/vm/Opcodes.h      操作码表（值 / 名字 / 长度 / 格式）
js/src/jsatom.cpp        XDRAtom（atom 的编码）
js/src/jsfun.cpp         XDRInterpretedFunction（嵌套函数）
```

### 1.3 XDRScript 的字段顺序

```
uint16 nargs, uint16 nblocklocals, uint32 nvars
uint32 length                      // 字节码长度
uint32 prologLength, uint32 version
uint32 natoms, nsrcnotes, nconsts, nobjects, nregexps, ntrynotes,
       nblockscopes, nTypeSets, funLength, scriptBits
[bindings]                         // nargs+nvars 个 atom，再 nargs+nvars 个 u8
[ScriptSource]                     // 仅当 scriptBits & (1<<12)（OwnSource）
uint32 sourceStart, sourceEnd
uint32 lineno, column, nslots, staticLevel
byte   code[length]                // ← 字节码
byte   notes[nsrcnotes]
atom   atoms[natoms]
...                                // consts / objects / regexps / trynotes / blockscopes
```

`ScriptSource::performXDR`：

```
u8 hasSource, u8 retrievable
if (hasSource && !retrievable):
    u32 length, u32 compressedLength, u8 argumentsNotIncluded,
    bytes[compressedLength ? compressedLength : length*2]
u8 haveSourceMap,   [u32 len + len*2 bytes]
u8 haveDisplayURL,  [u32 len + len*2 bytes]
u8 haveFilename,    [C 字符串]
```

游戏里是 `hasSource=0, retrievable=1`，只剩一个文件名。

atom 的编码（`jsatom.cpp`）：

```
u32 lengthAndEncoding = (length << 1) | isLatin1
isLatin1 ? length 字节 : length*2 字节（UTF-16LE）
```

### 1.4 真正的代码在嵌套 lambda 里

`data/*.jsc` 的**外层脚本只有 20 多字节**：

```
// ===== dataManager.jsc 外层 =====
 0  defvar "dataManager"
 5  bindname "dataManager"
10  lambda obj#0
15  undefined
16  call 0
19  setname "dataManager"
25  retrval
```

也就是整份文件其实是一个 `(function(){...}).call()`，全部逻辑在 `objects[0]`。
所以必须递归解析 `objects`：

```
u32 classk              // 0=CK_BlockObject 1=CK_WithObject 2=CK_JSFunction 3=CK_JSObject
classk == 2 (JSFunction):
    u32 funEnclosingScopeIndex
    u32 firstword       // HasAtom=1 IsStarGenerator=2 IsLazy=4 HasSingletonType=8
    if firstword & 1: atom（函数名）
    u32 flagsword       // (nargs << 16) | flags
    XDRScript           // ← 递归
```

递归下来就能直接看到函数名，非常直观：

```
outer: code=26B atoms=1 objects=1
  script name=None            code=26B  objects=1
    script name=dataManager<  code=599B objects=12
      script name=syncTime                              code=129B
      script name=dataManager.initUserData              code=984B
      script name=dataManager.userLogin                 code=27B
      script name=dataManager.cb4AfterLogin             code=91B
      script name=dataManager.playerLogin               code=30B objects=1
        script name=dataManager.playerLogin/<           code=71B  objects=1
          script name=dataManager.playerLogin/</<       code=145B
      ...
```

### 1.5 最坑的一点：立即数是大端序

字节码里的所有立即数（u16/u24/u32）都是**大端**：

```
54 00 02        getlocal 2      ← 0x0002 而不是 0x0200
52 00 00 00 01  callprop "requestToken"   ← atom 索引 1
```

一开始按小端读，整个反汇编全是乱的（还以为是表错了）。
用 `struct.unpack_from(">H"/">I"/">i")` 就对了。

### 1.6 操作码表从 Opcodes.h 生成

不要手抄，直接解析：

```python
PAT = re.compile(
    r"macro\(JSOP_(\w+),\s*(\d+),\s*(?:\"[^\"]*\"|\w+),\s*[^,]+,\s*"
    r"(-?\d+),\s*-?\d+,\s*-?\d+,\s*([A-Za-z_0-9| ]+)\)"
)
```

注意格式字段里有 `JOF_TMPSLOT2` / `JOF_TMPSLOT3`（带数字），
正则只写 `[A-Z_|]` 会漏掉一批操作码（共 229 个）。

### 1.7 用法

```powershell
python tools\gen_opcodes.py                        # 生成操作码表
python tools\jsc_disasm.py <file.jsc>              # 全量反汇编
python tools\jsc_disasm.py <file.jsc> 0x100 0x200  # 指定区间
python tools\disasm_func.py <file.jsc> --list      # 列函数
python tools\disasm_func.py <file.jsc> initUserData
```

---

## 二、运行时探测

反汇编解决"代码是什么"，运行时探测解决"实际给了什么参数"。

### 2.1 注入明文 JS

`project.json` 的 `jsList` 里加一个 `.js`。实测 cocos2d-js 的加载器
在 `.jsc` 不存在时会回退到明文（`assets/src/patch/hook.js`）。

### 2.2 REPL

探针轮询本地 HTTP 拿表达式，`eval` 后回传结果：

```
GET  <cdn>/hook/poll?data=<lastId>   → {"id":N,"code":"js 表达式"}
POST <cdn>/hook/result               → {"id":N,"ok":true,"value":"..."}
```

宿主机侧用 `tools/repl.py` / `tools/probe.py` 下命令。

**两个坑**：

* cocos2d-js 原生环境下 `new XMLHttpRequest()` 发不出去，必须用
  `cc.loader.getXMLHttpRequest()`
* GET 的 body 传数字会**静默卡住**（不报错也不发请求），必须 `String(...)`

### 2.3 `GAMELOG`

游戏自己的 `cc.log` / `console.log` **不进 logcat**。把
`console.log` / `cc.log` 包一层转发出来，才能看到客户端内部报错
（例如 `[sync serv status] failed`、`saf birthday error`）。

### 2.4 `Proxy` 探字段

给响应数据/模块对象套 `Proxy`，记录客户端读了哪些 key。
最有用的是给全局 `op`（util 和 remoteConfig 的共享命名空间）套：

```js
OPGET appVersion = "2.2.0"
```

一下就看出远程配置是按 `op.appVersion` 索引的。

### 2.5 `Error().stack` 定位调用点

返回值换成 `Proxy`，在 `get` 里打 stack，就能拿到
`update.js:251:35` 这种行列号：

```
VERPROP .2.2.0  stk=UpdateScene<._startUpdate@.../src/patch/update.js:251:35
```

配合"把返回值换成对象看它取哪个属性"，就确定了 `version[appVersion]` 这种结构。

### 2.6 逐模块二分找死的循环

JS 主线程被死循环卡住时，REPL 也发不出去（探针本身跑在 JS 线程上）。
`tools/bisect_init.py` 的做法：**每次只构造一个模块，然后立刻发一个 `1+1` 探测**，
超时就说明上一个模块把主线程跑死了。

这次就是靠它把范围从 31 个模块缩到 0 个（模块构造函数都没问题），
最后反汇编 `initUserData` 才发现是第一步 `syncTime(data.agent)` 的
`timeSec` 是 `undefined` → `NaN`。

### 2.7 客户端自己的 log 与 popup

把 `ccuiManager.popup` / `popupTop` / `toast` 包一层打 stack，
就能知道弹窗是谁弹的 —— 这是定位「温馨提示显示 JSON」的最快办法。

---

## 三、工具速查

| 工具 | 用途 |
|------|------|
| `tools/jsc_disasm.py` | jsc 反汇编 |
| `tools/disasm_func.py` | 按函数名反汇编 |
| `tools/gen_opcodes.py` | 生成操作码表 |
| `tools/repl.py` | 在游戏进程里执行 JS |
| `tools/probe.py` | 重启客户端 + 批量执行 + 打日志 |
| `tools/bisect_init.py` | 逐模块二分找死的循环 |
| `tools/selftest_game.py` | 不开游戏自测业务协议 |
| `tools/shots.py` | 连续截图 |
| `tools/sdk_strip/` | 删掉没用的第三方 SDK（见 README 4.0） |

### 3.1 排障套路（复用性最高的几条）

这几招在这次逆向里反复用到，遇到「界面卡住 / 白屏 / 闪烁 / 崩溃」都能按顺序试：

```powershell
# 1) 先分清是 Java 层还是引擎层
adb shell dumpsys activity top | findstr "ProgressBar Dialog GLSurfaceView EditText"
adb shell dumpsys window windows | findstr "Window # mFrame ty="     # 有没有小窗口在闪

# 2) 抓帧看是不是在闪（内容帧 vs 空白帧交替）
1..10 | % { adb shell screencap -p /sdcard/f$_.png; adb pull /sdcard/f$_.png . }
#   空白帧通常 <9KB

# 3) 列出 JS 场景里「可见 + opacity>0 + 有正在跑的动作」的节点
python tools\repl.py "(function(){var out=[];function w(n,d,p){...}...})()"

# 4) 反查节点是哪个 JS 类
for (var k in window) if (typeof window[k]==='function' && node instanceof window[k]) ...

# 5) SDK / 原生层的失败经常被 try/catch 吞掉，只能从 logcat 里捞
adb shell logcat -d | findstr /i "onFailed failed error ClassNotFound JNI"
adb shell logcat -d -b crash | findstr "Abort message"
```

**教训**：改 targetSdk / 删依赖这类事，光看「App 能不能起来」不够 ——
SDK 的失败是被它自己的 try/catch 吞掉的，只在 logcat 里留一行 `onFailed`。
