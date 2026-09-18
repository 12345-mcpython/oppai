# tools 索引

按用途分四类。**日常只会用到前两类**，后两类是排障和追溯用的。

> 想先了解整个项目，读 [`../docs/overview.md`](../docs/overview.md)；
> 这份只管「哪个脚本干什么」。

---

## 1. 构建 / 运行（平时就是这几个）

| 脚本 | 干什么 |
|---|---|
| `build_apk.py` | 打包 APK 全套：改 assets（URL 原地等长替换）→ 写 `patch.js`/`probe.js` → apktool 完整打包 → zipalign → 签名。`--no-probe` 出正式包。**幂等**，可以反复跑 |
| `serve.py` | 服务端守护：`run.py` 挂了自动拉起。**推荐用这个起服务端**，别用 `Start-Process python run.py`（会被回收） |
| `merge_dex.py` | 把 apktool 拆出来的 `smali_classesN` 合并成单个 dex |
| `selftest_game.py` | 不开游戏也能自测业务协议：自己按客户端格式打包加密请求打服务端，验证「加解密 + 路由 + code=200」。末尾还会走一遍**军士培养链路**（喂材料 → 重登确认等级落盘、材料没复活） |
| `check_soldier_calc.py` | **交叉验证**：把服务端 `gamesrv/soldier.py` 的升级计算和客户端 `CharCenter.calcSoldierUpgrade` 在 44 组用例上逐字段比对。改升级公式后必跑（要求游戏在跑 + 探针已加载） |
| `check_devtools.py` | 调试台自测：前端 id / 接口路径的静态一致性 + 把 `/devtools/api/*` 全打一遍（分「需要游戏」和「不需要」两组）+ 中文往返 + 快照回滚 |
| `sdk_strip/` | 删掉没用到的第三方 SDK：扫引用 → 生成桩类 → 删 smali → 清 manifest / assets / lib |

## 2. 逆向 / 取数据（加新功能时用）

| 脚本 | 干什么 |
|---|---|
| `jsc_strings.py` | **最好用的一把刀**：只扒 `.jsc` 的 atom（标识符）表，按源码顺序输出「参数/局部变量 → 函数体里用到的属性名」。没源码也能看懂一个函数在干什么 |
| `jsc_disasm.py` | SM33.1.1 XDR 字节码反汇编器（`_opcodes_gen.py` 是它的操作码表，别删） |
| `disasm_func.py` | 按函数名反汇编，会自动带上嵌套函数 |
| `csb_dump.py` | 解析 cocostudio 的 `.csb`（FlatBuffers）：列出动画区间和所有帧事件 |
| `extract_client_tables.py` | **把客户端 `table_*` 抽成服务端 JSON**（`gamesrv/data/table_quest.json` 就是这么来的）。客户端换版本重跑一次 |
| `gen_opcodes.py` | 从 SpiderMonkey 的 `vm/Opcodes.h` 重新生成 `_opcodes_gen.py`（一般不用跑） |

典型用法：

```powershell
# 看某个函数在干什么（最快）
python tools\jsc_strings.py <assets>\src\ui\main\mainlayer.jsc _initModuleButtons

# 想看具体字节码
python tools\disasm_func.py <assets>\src\data\questcenter.jsc _createQuest

# 看某个战斗动画有哪些帧事件
python tools\csb_dump.py <assets>\res\ui\battlebeganui\src\battlebeganui.csb
```

## 3. 运行时调试

| 脚本 | 干什么 |
|---|---|
| **`/devtools`** | ★ **浏览器调试台**（`gamesrv/devtools.py` + `gamesrv/web/`，挂在 CDN 端口）。流量 / JS 控制台 / 存档编辑+作弊 / 日志流 / 表查询，详见 [`../docs/devtools.md`](../docs/devtools.md) |
| `repl.py` | **在游戏进程里执行任意 JS**。前提：装了 probe 版 APK + 服务端在跑。验证数据形状、翻运行时状态全靠它 |
| `probe.py` | 重启客户端 + 批量执行 JS 表达式（`repl.py` 的批处理版） |
| `shots.py` | 重启客户端并连续截图 |
| `bisect_init.py` | 逐个构造 `initUserData` 里的数据模块，找会把 JS 主线程卡死的那个 |

> ⚠️ `adb shell input tap` 在 MuMu 上不可靠（注入事件不一定到得了 App），
> `adb screencap` 有时也抓不到 GL 层（截出来一片白）。
> 别用它俩下结论，以用户看到 / 服务端日志为准。

## 4. `archive/` —— 一次性脚本（历史存档，别再跑）

当初改 **`client/hook.js`**（现已拆成 `patch.js` + `probe.js`）或服务端源文件用的
「就地改代码」脚本。它们大多：

* 路径写死（`E:\code\python\game_server\...`）
* 目标文件已经不存在（`client/hook.js`）
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

## 附：加一个新玩法模块的推荐流程

1. **扒 atom** —— `jsc_strings.py` 看客户端那个模块的 `ctor` / `updateByServer` 读哪些 key
2. **反汇编** —— 拿不准的字段用 `disasm_func.py` 看具体怎么取的
3. **REPL 试形状** —— 直接 `new Xxx(candidate)` 打日志，几秒钟一个候选，比猜快得多
4. **补服务端** —— 在 `gamesrv/handlers/` 加路由（记得 `handlers/__init__.py` 里 import）
5. **数据缺就抽表** —— 表在客户端 `assets/src/table/*.jsc` 里，用 `extract_client_tables.py` 的路子
6. **验证** —— `selftest_game.py`（协议层）/ `/devtools`（流量 + 存档 + 运行时状态）/ 服务端日志
7. **记坑** —— 踩到的形状坑写进 `docs/protocol.md`，不然下次还得再踩一遍
