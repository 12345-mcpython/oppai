# 调试台（devtools）

浏览器里用的调试面板，**服务端自己托管**，不用改 APK、不用重装。

```
http://127.0.0.1:18080/devtools
```

（端口就是 CDN 那个 `GS_CDN_PORT`。`python tools\serve.py` 起服务端时会自动把它挂上，
顺便把 `adb logcat` 的尾随线程也拉起来。）

> 为什么是「浏览器页面」而不是「游戏内悬浮面板」：
> 面板要改 `probe.js` 就得走 apktool 打包 + `adb install`（约 1.5 分钟），
> 而且手机上打字、翻 JSON 都很别扭。调试台挂在服务端上，改一行
> `gamesrv/web/devtools.js` 刷新页面就生效 —— 见下面的「开发这个页面本身」。

---

## 1. 五个面板

| 面板 | 干什么 |
|---|---|
| **流量** | 每条业务 `route` 的请求 msg 和回包，成对展示（路由 / 结果码 / 耗时 / 是否实现）。可筛选、可导出、可**重放** |
| **控制台** | 在手机上那个游戏进程里跑 JS，等于 [`tools/repl.py`](../tools/repl.py) 的网页版。带历史记录（↑↓）和一组常用片段 |
| **玩家** | 存档浏览 / 直接编辑 JSON + 一组作弊按钮 + 快照（新建 / 回滚） |
| **日志** | 客户端探针日志（`adb logcat` 尾随或 probe 上报）和服务端日志，按来源过滤 |
| **数据** | 路由清单、`table_*` 反查（本地已抽取的 + 客户端里的）、`table_dictionary` 文案对照 |

### 1.1 流量

服务端在 `apps.build_game` 里给每个请求打了一条 `traffic` 事件：

```jsonc
{"seq": 42, "kind": "traffic", "route": "char.upgradesoldierlv",
 "reqId": 17, "account": "test", "session": "dba7198a", "known": true,
 "ok": true, "code": 200, "ms": 3,
 "msg": {"id": 1, "key": "sasm010104", "materials": [2, 3]},
 "res": {"code": 200, "msg": "", "data": {"soldier": {"lv": 16}}}}
```

`known: false` 是**「客户端调了但服务端没实现」**——排「点了没反应」时按这个筛最省事。
解不开的包也会发一条（`route: "<解包失败>"`），因为那种情况以前只在 logcat 里留个警告。

**重放**走的是客户端自己的 `server.request`，不是服务端直接 `dispatch`：

```js
server.request(route, msg, function (e, d) { console.log('REPLAY ' + route + ' => ' + JSON.stringify(e ? {err:String(e)} : d)); });
```

这样才能真的经过「加密 → 发送 → 解密 → 响应派发」整条链路，回包也会照常进日志面板。

### 1.2 控制台

底层还是 `POST /control/eval` → 探针轮询 `/hook/poll` → 执行 → `POST /hook/result`
（和 `tools/repl.py` 完全同一条路，只是从浏览器发）。

几个**形状坑**（片段里已经绕开，自己写的时候注意）：

| 想拿什么 | 正确写法 |
|---|---|
| 军士列表 | `dataManager.character.soldiers` 是**以 id 为键的对象**，不是数组（用 `Object.keys`） |
| 任务 | 模块叫 `dataManager.questCenter`，**没有** `dataManager.quest` |
| 道具数量 | `dataManager.bag._items[key]._count` |
| 功能模块 | `dataManager.player._moduleState` |
| 队伍里的军士 | `team._soldierKeys` |
| 关卡进度 | `dataManager.instance._levels`（全 1142 关都在，没打的 `_starMark` 是 -1） |

想打日志的话：**用 `cc.log`（或 `__oppaiHook__.log`），别用 `console.log`** ——
`console.log` 只有一个参数的容量，多传一个就抛
`js_console_log : wrong number of arguments`，而且它包不了。
详见 [§1.4](#14-日志)。

### 1.3 玩家 / 作弊

| 作弊项 | 具体做什么 |
|---|---|
| `set_base` | 改 `lv` / `curExp`（指挥部等级，一堆功能按它解锁 —— 见 [protocol.md §11.1](protocol.md)） |
| `unlock_modules` | `moduleState` 全部 `isUnlock = 1` |
| `unlock_levels` | 把 `instance` 表里 **1142 关**全标成三星通关 |
| `clear_levels` | 清空 `player.levels` |
| `max_soldiers` | 每个军士的星级 / 等级 / 技能都拉到上限 |
| `reset_soldiers` | 军士名单重置成初始 18 个（会把 `rosterVersion` 对齐） |
| `add_soldier` | 按 key 加一个军士（key 要在 `table_soldier` 里且 `card_type==1`） |
| `finish_quests` | 主线全标已领奖 + `questStats` 灌到 9999 |
| `clear_quests` | 清空 `player.quests.done` 和 `questStats` |
| `reset_player` | 整个存档换成新号 |

**每次作弊和每次手动保存存档之前都会自动建一份快照**（`var/backups/`），
右上角的下拉框选一个点「回滚」就能回去（回滚前又会给「当前」存一份）。

### 1.4 日志

三个来源，会自动去重：

| 来源（面板上的过滤档） | 是什么 | 怎么来的 |
|---|---|---|
| **探针** (`client`) | `emit()` 打的行：`GAMELOG cc.log: …` / `GAME REQ` / `GAME RESP` / `CRYPT` / `POPUP` / `REPLAY` … | `probe.js` 的 `console.log("OPPAIHOOK\|" + line)` → logcat |
| **console.log** (`console`) | 原生 `console.log()` 的输出原文 | logcat 里 `cocos2d-x debug info` 这个 tag、属于游戏 pid 的行 |
| **上报** (`probe`) | 和 `client` 同一批内容 | `probe.js` 每 2 秒 `POST /hook/log`（**要重打包才生效**） |
| **服务端** (`server`) | 服务端自己的 logging | `devbus` 里挂的 logging Handler |

#### ⚠️ `console.log` 能不能看到？

**能，但和 `cc.log` 走的不是同一条路。** 这是这次专门查过的一个坑：

```js
Object.getOwnPropertyDescriptor(console, 'log')
// => {value: [native code], writable: false, configurable: false}

console.log = function () {}                  // 非严格模式下静默失败，读回来还是原生的
Object.defineProperty(console, 'log', {...})  // TypeError: can't redefine non-configurable property
```

也就是说 **`console.log` 在 JSB 里根本没法从 JS 侧包一层**。
`probe.js` 里那句 `wrap("log", window.console, "console.")` 一直是**空转** ——
它甚至掩盖了「为什么 console.log 看不到」这个问题的答案（赋值失败是静默的）。
真正被包上的只有 `cc.log`（它是普通的可写属性）。

所以调试台改成**从 logcat 收原文**：
`adb logcat -v brief` 里 tag 为 `cocos2d-x debug info`、pid 等于游戏进程的行，
来源标成 `console`，面板上单独一档。这样 `console.log` 不用改客户端就能看到。

实测对照（同一时刻分别跑三句）：

| 写法 | 面板里的档 | 长什么样 |
|---|---|---|
| `console.log('X')` | `console.log` | `X` |
| `cc.log('X')` | `探针` | `GAMELOG cc.log: X` |
| `__oppaiHook__.log('X')` | `探针` | `X` |

两个附带的坑：

* **原生 `console.log` 只吃一个参数。** `console.log('a', b)` 会抛
  `Error: js_console_log : wrong number of arguments`。要多个值就自己 join。
* `cc.log` 的包装会「先 emit 再调原生」，所以同一条日志在 logcat 里会出现两遍。
  服务端记下最近的 `GAMELOG …: X`，5 秒内遇到内容完全相同的原生行就丢掉。

#### 日志来源的优先级

1. **`adb logcat` 尾随**（默认，现在就能用）
   服务端开一个后台线程跑 `adb -s 127.0.0.1:21503 logcat -v brief -T 1`。
   模拟器重启 / adb 掉线会自动退避重连，并顺手 `adb connect` 一次。
2. **`probe.js` 主动上报**
   每 2 秒把攒下的日志 `POST` 到 `<cdn>/hook/log`。
   比 logcat 可靠（不依赖 adb、不会被 logcat 环形缓冲冲掉），
   **但要重新打包 + 安装 APK 才生效**：

   ```powershell
   python tools\build_apk.py --host <主机IP>
   adb install -r -d E:\code\apk\work\zcsmw-mod-signed.apk
   ```

   一旦这个通道有数据（15 秒内有上报），logcat 那条路会自动静音，不会出现两份。

### 1.5 数据

- **路由清单** —— 列出 `handlers.registered()` 的 34 条路由 + 实现函数 + docstring 首行
- **表浏览** —— 优先读 `gamesrv/data/*.json`（已经抽出来的 4 张表），
  其余的走客户端 `table_*`（在客户端里做子串过滤 + 分页，不把整张表搬过来）
- **文案对照** —— 搜 `table_dictionary`。所有界面提示都在这儿，
  比如「指挥部等级不足哦~OAQ」是 `201`、「培养系统」的开启条件在 `table_function_open[100005]`

---

## 2. 架构（为什么这么写）

```
浏览器  ──长轮询──▶  /devtools/api/events?since=N&timeout=25
        ──REST────▶  /devtools/api/{overview,players,player,cheat,console,replay,table,dict,...}
                            │
游戏服务端 ──devbus.publish()─┤
  apps._game (traffic)        │
  logcat 尾随线程 (client)     ├─▶ devbus 环形缓冲（2000 条）+ Condition
  probe /hook/log (client)    │
  logging Handler (server)    │
  repl / control.eval (console)┘
```

### 2.1 为什么用长轮询而不是 SSE / WebSocket

`gamesrv/httpd.py` 是「一线程一连接 + 一定写 `Content-Length`」的极简实现。
SSE 要给它加一条 `Transfer-Encoding: chunked` 的流式分支 ——
那条路改的是**游戏自己也跑在上面**的共享代码，风险不划算。

长轮询在本机单标签页的场景下没有实质差别（事件到达延迟 < 100ms）。
两个细节要注意：

* 游标语义：有事件时回**最后一条的 seq**（这样被 `limit` 截断时下一轮能接着拿），
  没事件时回**服务端当前最新 seq**。第二种是关键 —— 环形缓冲满了会丢旧事件，
  如果回 `since`，前端会卡在一个永远拿不到事件的区间里变成忙循环。
* `/devtools/api/events` 被加进了 `httpd.py` 的 `is_poll` 白名单，
  否则每次长轮询的回包都会被 `logx.capture` 整份写进 `var/capture/http-resp-*.jsonl`，
  几十秒就能把那个文件撑到几百 MB。

### 2.2 安全边界

这个页面能**任意改存档、还能在客户端里跑 JS**，所以 `_local_only()` 做了限制：
只放行回环地址和 `10.` / `172.16-31.` / `192.168.` 三段私网（跨机调试用）。
`GS_BIND_HOST` 默认是 `0.0.0.0`（模拟器要能连），所以这道检查是必须的，别删。

---

## 3. 开发这个页面本身

前端就是三个**明文文件**，从磁盘现读现发（`Cache-Control: no-store`）：

```
gamesrv/web/devtools.html   结构
gamesrv/web/devtools.css    样式
gamesrv/web/devtools.js     逻辑
```

**改完直接刷新浏览器**，不用重启服务端。页面顶部有一条红色横幅专门接
`window.onerror` / `unhandledrejection` —— 它自己坏了会当场说出来，
不会给你一个安静的白板。

自测：

```powershell
python tools\check_devtools.py
```

* 静态检查：JS 里 `$('id')` 引用的 id 在 HTML 里存不存在、
  请求的接口路径后端有没有注册（这两个是最容易犯又最难在浏览器里定位的错）
* 接口全打一遍（分「需要游戏在跑」和「不需要」两组）
* 中文往返（专门验 UTF-8 没被吃掉）
* 快照 / 回滚

---

## 4. 加一个面板字段的流程

1. **服务端产出** —— 在合适的地方 `devbus.publish(kind, **fields)`。
   现成的 kind：`traffic` / `client` / `console` / `server` / `action`
2. **前端消费** —— `devtools.js` 的 `routeEvent(ev)` 里加一个 `case`，
   渲染进对应面板
3. **静态检查** —— 如果新加了 `$('xxx')`，记得 HTML 里也要有（`check_devtools.py` 会查）
4. **验证** —— `python tools\check_devtools.py`，然后浏览器刷新看
