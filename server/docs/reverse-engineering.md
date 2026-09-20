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
python script\gen_opcodes.py                        # 生成操作码表
python script\jsc_disasm.py <file.jsc>              # 全量反汇编
python script\jsc_disasm.py <file.jsc> 0x100 0x200  # 指定区间
python script\disasm_func.py <file.jsc> --list      # 列函数
python script\disasm_func.py <file.jsc> initUserData
```

### 1.8 闭包变量（`getaliasedvar slot=N`）怎么对回名字

反汇编里最常卡住的一步：**模块级常量是数字槽位**，看不到名字。

```
132  swap | this | getprop "teams" | newinit 1 | getaliasedvar hops=0 slot=6 | initprop "index" ...
```

`slot=6` 是哪个变量？规则有两条：

1. **`hops` 是「往外套几层 CallObject」**。函数自己有没有 CallObject，看它内部有没有内层函数
   （字节码里的 `lambda obj#N`）—— 有才需要把被捕获的绑定搬进 CallObject。
   所以**没有 lambda 的函数，`hops=0` 直接落到模块作用域**。
   一眼分辨的办法：帧内局部变量走 `getlocal/setlocal`，模块级/被捕获的走
   `getaliasedvar/setaliasedvar`。例：`TeamDetailLayer._initData` 里
   `getlocal 0..5` 是它自己的 6 个局部（player/character/i/mechaArr/mecha/charGroups），
   而 `setaliasedvar hops=0 slot=3` 写的是**模块级**的 `SCOMBATANT_LIMIT`
   （上阵人数上限的缓存，模块体里初值是 `null` —— 正好反过来印证了这条）。

2. **模块作用域的槽位 = 声明序号 + 2**（0/1 被 `this`/`arguments` 两个保留槽占了）。
   也就是 `bindings[i]` ↔ `slot(i+2)`。`bindings` 顺序就是源码里的声明序。

   > ⚠️ **这条一定要用已知信息复核，别直接信**。两个便宜又硬的校验点：
   >
   > * **lambda 的形参个数**：模块体里 `slot17/18/19 ← lambda obj#0/#1/#2`，
   >   而子函数表里 `newCharItem` / `newCusArmature` / `updateArmatureShader`
   >   的 `nargs` 正好是 `5 / 1 / 2`，和 `bindings[15..17]` 一一对上；
   > * **跨层引用的用法**：`newCharItem` 里 `getaliasedvar hops=1 slot=16` 被当
   >   **2 参函数**调用（`seekNodeByName(node, "pitchon")`），
   >   而 `bindings[14]` 正是 `seekNodeByName`。

   实战例子（队伍详情页为什么默认第 2 队）：`teamdetaillayer.jsc` 的模块常量
   按这个规则解出来是

   ```
   slot2=ITEM_SIZE_WIDTH=150   slot3=SCOMBATANT_LIMIT=null  slot4=ARMATURE_LIMIT=11
   slot5=DEFAULT_CHAR_POS="0"  slot6=DEFAULT_TEAM_IDX=1     slot7=ATTACK_SELECT_MAX=5
   slot8=CTM=CHAR_TYPE.MECHA   slot9=HP=clone(HERO_ROLE)    slot10=CTS=CHAR_TYPE.SOLDIER
   slot11=SP=clone(SOLDIER_POSITIONING)   …   slot16=seekNodeByName
   ```

   每一步都能和用法互证（`slot5` 被赋给 `curCharPos`、`slot6` 进了
   `findIndex{index: …}`、`slot8/10` 当 `charGroups` 的 key、`slot9/11` 取
   `.ALL/.FRONT/.MIDDLE/.BACK`），所以 `DEFAULT_TEAM_IDX = 1` 是可信的。
   结论和处置见 [`differences.md`](differences.md) A3d。

工具：

```powershell
python script\jsc_scope.py <file.jsc> --only "TeamDetailLayer<"   # bindings + 槽位
python script\alias_use.py <file.jsc> 6 --func _initData          # 某槽位的所有引用 + 上下文
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

宿主机侧用 `script/repl.py` / `script/probe.py` 下命令。

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
`script/bisect_init.py` 的做法：**每次只构造一个模块，然后立刻发一个 `1+1` 探测**，
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
| `script/jsc_disasm.py` | jsc 反汇编 |
| `script/disasm_func.py` | 按函数名反汇编 |
| `script/jsc_strings.py` | 只扒 atom（标识符）表，按源码顺序，定位函数逻辑最快的一把 🔪 |
| `script/jsc_scope.py` | 打印各 script 的 bindings/槽位（把 `getaliasedvar slot=N` 对回变量名，见 §1.8） |
| `script/alias_use.py` | 列出某个槽位的全部引用 + 上下文（看它被当函数调还是被当常量用） |
| `script/gen_opcodes.py` | 生成操作码表 |
| `script/repl.py` | 在游戏进程里执行 JS |
| `script/probe.py` | 重启客户端 + 批量执行 + 打日志 |
| `script/bisect_init.py` | 逐模块二分找死的循环 |
| `script/selftest_game.py` | 不开游戏自测业务协议 |
| `script/check_des.py` | DES 两条路线（纯 Python / libcrypto）对拍参考向量 + 测速率 |
| `script/bench_login.py` | 量登录包耗时（`--parts` 拆到 组包 / JSON / DES / base64） |
| `script/shots.py` | 连续截图 |
| `script/sdk_strip/` | 删掉没用的第三方 SDK（见 README 4.0） |

> ⚠️ **REPL 一路 504 但游戏明明正常** —— 十有八九是服务端重启过。
> 客户端探针收命令时会 `if (cmd.id <= replSeq) return;`（防重放），
> 而服务端命令 id 原来是从 1 数的，一重启就回退，新命令全被当旧命令丢掉。
> 已经改成从时间戳起步（`gamesrv/repl.py`），但仍然要记住这条：
> 排查 REPL 超时之前，先确认服务端是不是刚重启过。

### 3.2 「先扒 atom，再上 REPL」——定位客户端问题最快的两步

`script/jsc_disasm.py` 只能反汇编**顶层脚本**，真正的业务代码全在嵌套 lambda
（对象字面量里的方法）里，所以很多时候不如换个思路：

**第一步：扒 atom 表。** SM33 的 XDR 里每个函数脚本自带一组 atom，编码是

```
<uint32 (2 * len + 1)> <len 个单字节 ASCII>
```

长度字段是「UTF-16 长度 + 1」，内容却是 ASCII。按这个规则扫一遍就能拿到
**按源码顺序排的标识符表**（`script/jsc_strings.py`）：每个函数先是它的参数和
局部变量名，然后是函数体里按出现顺序用到的属性名/方法名。信息量非常大，例如

```
$ python script\jsc_strings.py zcsmw\assets\src\ui\main\mainlayer.jsc _initModuleButtons
   8664   29  MainLayer<._initModuleButtons     <- 函数（debug name）
   8761   11  mainUiLayer                        <- 局部变量（按声明顺序）
   8776    7  modules
   8787    3  key
   ...
   9615   12  _mainUiLayer                       <- 函数体，按出现顺序
   9631   11  dataManager
   9646    6  player
   9656   11  moduleState
   ...
```

一眼就能看出 `var modules = dataManager.player.moduleState;` 这一句 —— 私服
没发 `moduleState` 时它就是 `undefined`，紧接着 `modules[key]` 抛
`TypeError: modules is undefined`，主界面黑屏。

**第二步：REPL 验证。** 猜测只有落到运行时才算数（`script/repl.py`），
而且可以直接 `new Xxx(data)` 试各种数据形状，几秒钟就能试出客户端要的字段结构
（比如 `TalentCenter` 要的是「天赋类型 -> 当前天赋 key」的平铺映射，
不是嵌套结构）。

### 3.3 一个高频坑：`initUserData` 中途抛异常 = 各种莫名其妙的表现

`dataManager.initUserData(data)` 是个「一把梭」的长函数：先 `new` 三十来个数据
模块，再依次调 `player.setCharacter / initTeams / initAsst / guideManager.init /
uiLayoutManager.init / player.initModuleState / initXgNotifications`。

**中间任何一步抛异常，后面的全都不执行**，于是表现千奇百怪：

| 没跑到的步骤 | 现象 |
|---|---|
| `player.initModuleState()` | `TypeError: modules is undefined @ mainlayer.js:188` → 登录后黑屏 |
| `guideManager.init()` | 引导状态是默认值，引导乱走 |
| 某个数据模块的构造 | `dataManager.xxx` 是半成品，点进对应界面才炸 |

排查办法：`server/client/probe.js` 的 `hookInitUserData` 会把异常和 stack 打出来，
`hookInterfaceTrace` 会把这一段调用逐个打 `CALL xxx`，一眼就能看出死在哪一步。

`server/client/patch.js` 里的 `INITUSERDATA-GUARD` 是正式版兜底：无论上面死在哪一步，
都保证 `player._moduleState` 建出来，至少不会黑屏。


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
python script\repl.py "(function(){var out=[];function w(n,d,p){...}...})()"

# 4) 反查节点是哪个 JS 类
for (var k in window) if (typeof window[k]==='function' && node instanceof window[k]) ...

# 5) SDK / 原生层的失败经常被 try/catch 吞掉，只能从 logcat 里捞
adb shell logcat -d | findstr /i "onFailed failed error ClassNotFound JNI"
adb shell logcat -d -b crash | findstr "Abort message"
```

**教训**：改 targetSdk / 删依赖这类事，光看「App 能不能起来」不够 ——
SDK 的失败是被它自己的 try/catch 吞掉的，只在 logcat 里留一行 `onFailed`。
