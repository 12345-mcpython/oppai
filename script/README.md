# script/ 索引

按用途分五类。**日常只会用到前两类**，后面的是排障、体检和追溯用的。

> 想先了解整个项目，读 [`../docs/overview.md`](../docs/overview.md)；
> 想从零复刻，读 [`../REPRODUCE.md`](../REPRODUCE.md)；
> 这份只管「哪个脚本干什么」。
>
> 所有脚本自带路径自举（`_paths.py`）：从哪个目录调都行，路径不会写死。

---

## 1. 构建 / 运行（平时就是这几个）

| 脚本 | 干什么 |
|---|---|
| `build_apk.py` | 打包 APK 全套：从原版包恢复带地址的文件 → 改 assets → 写 `patch.js`/`probe.js` → **规范化清单**（targetSdk 33 / exported / 明文 HTTP / 缺 `<uses-sdk>` 就补）→ 删死代码与 SDK 资源 → apktool 完整打包 → zipalign → 签名。`--no-probe` 出正式包，`--abis` 只带指定 ABI。**幂等**，可以反复跑 |
| `serve.py` | 服务端守护：`run.py` 挂了自动拉起。**推荐用这个起服务端**，别用 `Start-Process python run.py`（会被回收） |
| `merge_dex.py` | 把 apktool 拆出来的 `smali_classesN` 合并进 `smali/`（合成单个 dex）。⚠️ 必须在 `build_apk.py` **之前**跑 —— `DROP_SMALI` 里的路径都是按合并后写的 |
| `build_arm64_deps.py` | 备齐 arm64-v8a 的引擎依赖（预编译库 + 自建 chipmunk 6.2.1 / libwebsockets 1.23 + 按 ABI 分的 SM/curl/**jpeg** 头文件）。`--check` 只报告。详见 [`../engine/ARM64.md`](../engine/ARM64.md) |
| `patch_js_debugger.py` | 把调试器自己的 JS（`assets/script/jsb_debugger.js` + `assets/script/debugger/**`，**注意是游戏 assets 里的 `script/`，不是本目录**）换成**明文并打补丁**，并删掉同名 `.jsc`（`.jsc` 是缓存，删掉引擎才改读 `.js`）—— 不用重编引擎就能改调试器 |
| `sdk_strip/` | 删掉没用到的第三方 SDK：扫引用 → 生成桩类 → 删 smali → 清 manifest / assets / lib。细目见 §5 |

## 2. 逆向 / 取数据（加新功能时用）

| 脚本 | 干什么 |
|---|---|
| `jsc_strings.py` | **最好用的一把刀**：只扒 `.jsc` 的 atom（标识符）表，按源码顺序输出「参数/局部变量 → 函数体里用到的属性名」。没源码也能看懂一个函数在干什么 |
| `jsc_disasm.py` | SM33.1.1 XDR 字节码反汇编器（`_opcodes_gen.py` 是它的操作码表，别删） |
| `disasm_func.py` | 按函数名反汇编，会自动带上嵌套函数 |
| **`jsc_decompile.py`** | ★ **jsc → js 反编译器**（栈机模拟 + 结构恢复）：`assets/src/**` **572/575** 能过 `node --check`。见 [`../docs/decompile.md`](../docs/decompile.md) |
| **`jsc_find.py`** | ★ **按原子反查**：这个 key / route / 方法名在哪个 `.jsc` 的哪个函数里用过。`.jsc` 是二进制，裸 `grep` 搜不到，只能这么查。判断「这个登录包字段到底有没有人读」全靠它 |
| `jsc_funcs.py` | 按**函数**分组打印原子表（看一个类的完整数据流） |
| `jsc_scope.py` | 打印各 script 的 bindings / 槽位 —— 把 `getaliasedvar slot=N` 对回变量名时用 |
| `alias_use.py` | 列出某个 jsc 里所有 `getaliasedvar/setaliasedvar` 的槽位和上下文 |
| `csb_dump.py` | 解析 cocostudio 的 `.csb`（FlatBuffers）：列出动画区间和所有帧事件 |
| `extract_client_tables.py` | **把客户端 `table_*` 抽成服务端 JSON**（`gamesrv/data/table_quest.json` 就是这么来的）。客户端换版本重跑一次。⚠️ **只补新表时加 `--only <子串>`** —— 每张表都是一次 `/control/eval`，整轮全抽会把模拟器压卡 |
| `gen_opcodes.py` | 从 SpiderMonkey 的 `vm/Opcodes.h` 重新生成 `_opcodes_gen.py`（一般不用跑） |

典型用法：

```powershell
# 看某个函数在干什么（最快）
python script\jsc_strings.py <assets>\src\ui\main\mainlayer.jsc _initModuleButtons

# 想看具体字节码
python script\disasm_func.py <assets>\src\data\questcenter.jsc _createQuest

# 看某个战斗动画有哪些帧事件
python script\csb_dump.py <assets>\res\ui\battlebeganui\src\battlebeganui.csb

# 「这个 key 是谁在读？」—— 判登录包字段死活、找响应形状的起点
python script\jsc_find.py useGiftStatus --func
python script\jsc_find.py "favor\..*" --regex
```

## 3. 运行时调试

| 脚本 | 干什么 |
|---|---|
| **`/devtools`** | ★ **浏览器调试台**（`gamesrv/devtools.py` + `gamesrv/web/`，挂在 CDN 端口）。流量 / JS 控制台 / 存档编辑+作弊 / 日志流 / 表查询 / **引擎调试器**，详见 [`../docs/devtools.md`](../docs/devtools.md) |
| `jsd.py` | ★ **引擎自带的远程 JS 调试器**客户端：断点 / 单步 / 调用栈 / 暂停时求值（`tabs` / `sources` / `repl` / `demo`）。见 [`../docs/engine-debug.md`](../docs/engine-debug.md) |
| `repl.py` | **在游戏进程里执行任意 JS（不暂停）**。前提：装了 probe 版 APK + 服务端在跑。验证数据形状、翻运行时状态全靠它 |
| `probe.py` | 重启客户端 + 批量执行 JS 表达式（`repl.py` 的批处理版） |
| `shots.py` | 重启客户端并连续截图 |
| `bisect_init.py` | 逐个构造 `initUserData` 里的数据模块，找会把 JS 主线程卡死的那个 |

> ⚠️ `adb shell input tap` 在 MEmu 上不可靠（注入事件不一定到得了 App），
> `adb screencap` 有时也抓不到 GL 层（截出来一片白）。
> 别用它俩下结论，以用户看到 / 服务端日志为准。

## 4. 自检 / 体检（改完东西跑一遍）

| 脚本 | 干什么 |
|---|---|
| `selftest_game.py` | 不开游戏也能自测业务协议：自己按客户端格式打包加密请求打服务端，验证「加解密 + 路由 + code=200」。末尾还会走一遍**军士培养链路**（喂材料 → 重登确认等级落盘、材料没复活）和**好感度登录块形状**、**模块开启标记**。`--only` 只跑几条 |
| `selftest_favor.py` | **好感度（宿舍）公式自测，不需要模拟器、也不需要服务端在跑**：进程内直接调 handler，把「礼物加多少 / 升级结算 / 回礼概率 / 抚摸次数节流 / 宿舍事件解锁与奖励 / 衣柜发满 / 守护灵升级 / 设置助战」钉死（172 条断言）。存档写到临时目录，不碰真存档 |
| `check_soldier_calc.py` | **交叉验证**：把服务端 `gamesrv/soldier.py` 的升级计算和客户端 `CharCenter.calcSoldierUpgrade` 在 44 组用例上逐字段比对。改升级公式后必跑（要求游戏在跑 + 探针已加载） |
| `check_devtools.py` | 调试台自测：前端 id / 接口路径的静态一致性 + 把 `/devtools/api/*` 全打一遍（分「需要游戏」和「不需要」两组）+ 中文往返 + 快照回滚 |
| `check_docs.py` | **文档自检**：相对链接、目录锚点、搬家前的旧路径残留、README 文档地图是否漏收新文档。改完文档跑一下（重构时加的，专治「同一件事三份说法」） |
| `check_des.py` | DES 自检：`out/des_ref.json` 里 15 组参考向量逐字节比对 + 200 组随机往返 + 吞吐量，**纯 Python 和 libcrypto 两条后端都验** |
| `bench_login.py` | 量一次「登录包」在服务端要花多久（`--parts` 拆成 handler / json / des / base64）—— DES 提速前后的对照尺子 |
| `route_gap.py` | 把客户端里能当 route 的字符串全抽出来，和服务端已实现的对一遍（`--static` 静态盘点：候选 161 / 已实现 72 / 缺 97） |
| `smali_reach.py` | smali 可达性分析 —— 从「清单组件 ∪ .so 类名 ∪ js/jsc 类名」做闭包，找「谁都不引用」的类（`--unused` 只列不可达的）。`build_apk.py` 的 `DROP_SMALI` 就靠它给的结论 |
| `apk_report.py` | APK 体积体检 —— 想精简包的时候先跑这个，别靠猜 |
| `asset_usage.py` | 查 `assets/` 里某个文件到底有没有人用 |

## 5. `sdk_strip/` 细目

| 脚本 | 干什么 |
|---|---|
| `analyze.py` | 分析「删掉 SDK 后还需要保留哪些桩类」，产出 `needed.json`（`--json` 写文件） |
| `strip.py` | **主入口**：删命中 SDK 包名的 smali / manifest 组件 / assets / lib，然后调 `gen_stubs.py` 重建桩类。`--dry-run` 只看要删什么 |
| `gen_stubs.py` | 按 `needed.json` 生成 SDK 桩类（smali）：`implements` → interface、`extends` → class、`getInstance()` → 单例、其余空实现 |
| `native_stubs.py` | 列出 `libcocos2djs.so` 原生层**硬依赖**的类（JNI `FindClass` 的目标），这些必须存在 |
| `gen_native_stubs.py` | 把 `native_stubs.py` 的定义生成 smali（覆盖掉 `gen_stubs.py` 的通用版本） |
| `fix_interfaces.py` | 把名字形如 `I<大写开头>` 的 SDK 桩从 class 改成 interface |
| `find_orphans.py` | 检查 `smali_classes2` 里的包还有没有被保留代码引用（三处：保留 smali / assets 的 js+jsc / manifest） |
| `js_class_refs.py` | 把 JS 里 `jsb.reflection` 调用的 Java 类名/方法名全部捞出来 |
| `so_pairs.py` | 把 `.so` 里「方法名 + JNI 签名」成对挖出来 |
| `check_stubs.py` | 确认剩下的 SDK 桩类是不是还有用 |
| `manifest_clean.py` | 用 ElementTree 删清单里的 SDK 组件（历史脚本；`strip.py` 现在自带同样的逻辑，**别照抄它的权限删除表**，见 `build_apk.py` 的注释） |

> ⚠️ **顺序**：`analyze → strip（内含 gen_stubs）→ gen_native_stubs → merge_dex → patch_smali → patch_js_debugger → build_apk`。
> `patch_smali.py` 必须排在 `strip.py` **之后**（`strip.py` 会重建 `com\quicksdk` 的桩类，早跑会被冲掉），
> `merge_dex.py` 必须排在 `build_apk.py` 之前。完整命令见
> [`../docs/build.md`](../docs/build.md) §「完整重建命令」。

## 6. `archive/` —— 一次性脚本（历史存档，别再跑）

当初改 **`server/client/hook.js`**（现已拆成 `patch.js` + `probe.js`）或服务端源文件用的
「就地改代码」脚本。它们大多：

* 路径写死（`E:\code\zcsmw\server\...`）
* 目标文件已经不存在（`server/client/hook.js`）
* 改动**已经落在**现在的 `patch.js` / `probe.js` / `gamesrv/*` 里

留着是因为每个脚本的 docstring 都记着当时**为什么这么改**（现象 + 证据链），
比 git log 更容易读。分类：

| 前缀 | 例子 | 当时在干什么 |
|---|---|---|
| `add_*` / `upgrade_*` | `add_polyfill.py` `add_webview.py` `upgrade_webview.py` | 往 hook.js 里加 polyfill |
| `fix_*` | `fix_at_anim.py`（★ 让最后一帧回调收到动画名）、`fix_polyfill.py` | 修引擎/界面行为 |
| `prefer_*` / `remove_*` | `prefer_engine_animname.py` `remove_lf_guard.py` | 调整补丁的优先级 / 撤掉误加的守卫 |
| `*_watchdog.py` | `battle_end_watchdog.py` `movie_watchdog.py` | 战斗/视频卡住时的兜底（后来在引擎层根治，已拆掉） |
| `cleanup_*` | `cleanup_watchdogs.py` `cleanup_video_layer.py` | 事后清理上面那些兜底 |
| `trace_*` / `diag_*` | `trace_lgl.py` `diag_frameevent.py` | 加日志定位调用链 |
| `quiet_logs.py` | | 降噪：区分 `emit`（保留）和 `vlog`（verbose 才打） |
| `skip_guide.py` / `wire_soldiers.py` | | 一次性改服务端源文件（改动已进 git） |
| `check_battle_seq.py` | | 扫 logcat 抓 JS ERROR / 动画事件序列 |

---

## 附 1：加一个新玩法模块的推荐流程

1. **扒 atom** —— `jsc_strings.py` 看客户端那个模块的 `ctor` / `updateByServer` 读哪些 key
2. **反查响应形状** —— 先 `jsc_find.py <responseConfig 里的 key>` 找到那个 callback，
   再看它读了哪些字段（`jsc_funcs.py` 按函数分组看原子流）
3. **反汇编** —— 拿不准的字段用 `disasm_func.py` 看具体怎么取的
4. **REPL 试形状** —— 直接 `new Xxx(candidate)` 打日志，几秒钟一个候选，比猜快得多
5. **补服务端** —— 在 `gamesrv/handlers/` 加路由（记得 `handlers/__init__.py` 里 import）
6. **数据缺就抽表** —— 表在客户端 `assets/src/table/*.jsc` 里，用 `extract_client_tables.py --only <子串>`。
   ⚠️ 抽表时**在客户端 JS 里就把字段压到最小**，别把整表原样回传
7. **验证** —— `selftest_game.py`（协议层）/ 数值逻辑单独写一个 `selftest_<模块>.py`（进程内，
   不用模拟器）/ `/devtools`（流量 + 存档 + 运行时状态）/ 服务端日志
8. **记坑** —— 踩到的形状坑写进 `docs/pitfalls.md`（坑速查），不然下次还得再踩一遍

## 附 2：`out/` 里哪些能删

`out/` 是**构建产物 + 缓存**，整个都在 `.gitignore` 里，删了不影响仓库；但下面这些删了要付代价：

| 路径 | 能不能删 |
|---|---|
| `out/debug.keystore` | ❌ **别删** —— 签名密钥。删了重新生成的就是**另一把钥匙**，覆盖安装会 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`（得先卸载重装） |
| `out/des_ref.json` | ❌ 别删 —— `check_des.py` 拿它当参考向量 |
| `out/engine-build-*.log` | ⚠️ 建议留 —— 引擎编译的全量日志（`REPRODUCE.md` 的验证点就是看它） |
| `out/lib-abi-cache/<abi>/` | ⚠️ 建议留 —— `--abis` 没选中的 `.so` 挪这儿存着（删了就只剩 `game/lib` 里那一份） |
| `out/deps-*/`、`out/chipmunk-*/`、`out/lws-*/` | ✅ 可删 —— `build_arm64_deps.py` 会重新稀疏拉取/重编（几分钟） |
| `out/removed-*/` | ✅ 可删 —— 历次删掉的 smali/res 备份，`game/original/zcsmw-original.apk` 才是权威原件 |
| `out/*-signed.apk` | ✅ 可删 —— 重新打包 ~1 分钟 |
| `out/<abi>_libcocos2djs.so` | ✅ 可删 —— `build.ps1 -Engine` 的产物副本 |
