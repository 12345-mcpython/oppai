"""浏览器调试台（`/devtools`）。

挂在 CDN 端口上（默认 http://127.0.0.1:18080/devtools），
宿主机浏览器直接开，**不用改 APK 也不用重装**。

五个面板（前端在 `gamesrv/web/devtools.*`）：

    流量      每条业务 route 的请求/响应（明文 msg + 回包 + 耗时 + 结果码），可筛选、可重放
    控制台    在手机上那个游戏进程里跑 JS（repl.py 的网页版）
    玩家      存档浏览/编辑 + 作弊按钮 + 快照回滚
    日志      客户端探针日志（adb logcat 尾随 / probe.js 上报）+ 服务端日志
    数据      路由清单、table_* 反查、table_dictionary 文案对照

拉实时数据用的是**长轮询**而不是 SSE：`httpd.py` 是一线程一连接 +
一定写 Content-Length 的极简实现，SSE 要给它加 chunked 流式分支，
改共享代码去赌游戏不受影响不划算。见 `gamesrv/devbus.py` 的说明。

安全：这东西能任意改存档、还能在客户端里跑 JS，**只应该绑在本机**。
默认 `GS_BIND_HOST=0.0.0.0`（模拟器要能连），所以这里加了一道
「只允许回环地址 + 私网地址」的检查 —— 见 `_local_only()`。
"""

from __future__ import annotations

import io
import collections
import json
import os
import re
import shutil
import subprocess
import threading
import time

from . import config, devbus, instance, logx, repl, soldier, store
from .httpd import Response

log = logx.get("devtools")

BASE_DIR = config.BASE_DIR
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
BACKUP_DIR = os.path.join(config.VAR_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

# adb：和 tools/probe.py / tools/shots.py 里保持一致。
# 用环境变量覆盖是为了换机器/换模拟器端口时不用改代码。
ADB = os.environ.get("GS_ADB", r"D:\Android\android-sdk\platform-tools\adb.exe")
ADB_SERIAL = os.environ.get("GS_ADB_SERIAL", "127.0.0.1:21503")
LOGCAT_MARK = "OPPAIHOOK"

_JSON_CT = "application/json; charset=utf-8"


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _reply(payload, status: int = 200) -> Response:
    return Response(status, payload, content_type=_JSON_CT)


def _err(message: str, status: int = 400) -> Response:
    return _reply({"ok": False, "error": message}, status)


def _body(req) -> dict:
    """GET 用 query、POST/PUT 用 JSON body，两边都认，省得前端纠结。"""
    payload = req.json()
    if isinstance(payload, dict):
        return payload
    return {k: v[0] for k, v in req.query.items()}


def _arg(req, payload: dict, key: str, default=None):
    if key in payload:
        return payload[key]
    return req.q(key, default)


def _local_only(req) -> bool:
    """只允许从本机 / 局域网回环访问。

    这个接口能改存档、能在客户端跑 JS，暴露到公网等于把机器交出去。
    MuMu 是 NAT 的，宿主机看到的是 127.0.0.1；跨机调试才需要私网地址，
    所以放行 10./172.16-31./192.168. 三段。
    """
    host = (req.client or "").rsplit(":", 1)[0].strip("[]")
    if host in ("127.0.0.1", "::1", "localhost"):
        return True
    if host.startswith("10.") or host.startswith("192.168."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def _read_web(name: str) -> str | None:
    path = os.path.join(WEB_DIR, name)
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 客户端日志：adb logcat 尾随
# ---------------------------------------------------------------------------

# cocos2d-x 的 `LOGD` / `log()` / `console.log()` 全部落在这个 tag 下
COCOS_TAG = "cocos2d-x debug info"

# 游戏包名（用来只收这个进程的 console 输出，别把模拟器里别的 App 也捞进来）
GAME_PKG = os.environ.get("GS_APK_PKG", "com.cm.zcsmw.baidu")

# 解析过的游戏 pid（`pidof` 每次重启都会变，所以带 TTL）
_pid_cache: int | None = None
_pid_at = 0.0
_pid_lock = threading.Lock()


def _game_pid(force: bool = False) -> int | None:
    global _pid_cache, _pid_at
    now = time.time()
    with _pid_lock:
        if not force and _pid_cache and now - _pid_at < 30:
            return _pid_cache
    pid = None
    try:
        out = subprocess.run([ADB, "-s", ADB_SERIAL, "shell", "pidof", GAME_PKG],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=8).stdout or ""
        pid = int(out.split()[0])
    except Exception:  # noqa: BLE001
        pid = None
    with _pid_lock:
        _pid_cache = pid
        _pid_at = time.time()
    return pid


class _LogcatTailer:
    """`adb logcat -v brief` 长跑，把客户端的日志喂进总线。

    为什么用 logcat 而不是直接读设备上的 `hook.log`：
        logcat 是**流式**的，不用轮询文件、也不依赖 root。

    收两类行：

    **① 探针行**（含 `OPPAIHOOK|`）—— `probe.js` 的 `emit()` 打的，
    里面有 `GAMELOG cc.log: …`（客户端 `cc.log` 被探针包了一层）、
    `GAME REQ/RESP`、`CRYPT` 之类。来源标成 `client`。

    **② 原生 console 行** —— 直接是 `console.log()` 的输出。
    为什么要单独收：**`console.log` 在 JSB 里是 non-configurable + non-writable，
    客户端根本没法从 JS 侧包一层**（实测 `console.log = fn` 静默失败、
    `Object.defineProperty` 直接抛 `can't redefine non-configurable property`）。
    所以 `probe.js` 的 `hookLogging()` 里那几行 `console.*` 一直是空转 ——
    只有 `cc.log` 真的被包上了。想看到 `console.log` 就只能从 logcat 收原文。
    来源标成 `console`，前端默认单独一档过滤。

    ⚠️ `-v brief` 只给消息的第一行加前缀，多行消息的后续行是"裸"的。
    所以这里记住「上一行属于哪个来源」，裸行跟着上一行一起收，
    否则 `safeJson` 展开的多行对象会被吃掉一半。
    """

    # `D/cocos2d-x debug info( 1725): 正文`
    LINE_RE = re.compile(r"^[VDIWEF]/(?P<tag>[^(]+)\(\s*(?P<pid>\d+)\): (?P<msg>.*)$")
    # 裸行（多行消息的续行）—— 有 logcat 前缀的才算是新行
    PREFIX_RE = re.compile(r"^[VDIWEF]/")

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.running = False
        self.lines = 0
        self.error = ""
        self.started_at = 0.0
        # 最近见过的 `GAMELOG cc.log: X` 的 X —— 原生那行马上会跟着来，
        # 用它去重，免得同一条日志在面板里出现两遍。
        self._recent_gamelog: collections.deque = collections.deque(maxlen=64)

    def start(self) -> bool:
        with self._lock:
            if self.running:
                return True
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="devtools-logcat", daemon=True)
            self.running = True
            self.started_at = time.time()
            self.error = ""
            self._thread.start()
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self.running = False

    def status(self) -> dict:
        return {
            "running": self.running,
            "lines": self.lines,
            "error": self.error,
            "startedAt": self.started_at,
            "adb": ADB,
            "serial": ADB_SERIAL,
            "gamePid": _game_pid(),
            "pkg": GAME_PKG,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            proc = None
            try:
                # -T 1 = 从"现在"开始，不要把缓冲区里几万行历史灌进来
                proc = subprocess.Popen(
                    [ADB, "-s", ADB_SERIAL, "logcat", "-v", "brief", "-T", "1"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
            except Exception as exc:  # noqa: BLE001
                self.error = f"启动 adb 失败: {exc}"
                log.warning("devtools logcat: %s", self.error)
                if self._stop.wait(5.0):
                    break
                continue

            self.error = ""
            _game_pid(force=True)
            last_source: str | None = None
            try:
                assert proc.stdout is not None
                for raw in proc.stdout:
                    if self._stop.is_set():
                        break
                    line = raw.rstrip("\r\n")
                    source, text = self._classify(line, last_source)
                    if source is None:
                        last_source = None
                        continue
                    last_source = source
                    if text:
                        self._emit(text, source)
            except Exception as exc:  # noqa: BLE001
                self.error = f"读取 logcat 失败: {exc}"
            finally:
                if proc.poll() is None:
                    proc.kill()
                try:
                    proc.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass

            if self._stop.is_set():
                break
            # adb 断了（模拟器重启 / adb server 掉了）—— 退避重连，
            # 顺手把 serial 重新连一次，MuMu 重启后 21503 经常要重新 connect。
            self.error = self.error or "logcat 进程结束，正在重连"
            try:
                subprocess.run([ADB, "connect", ADB_SERIAL], capture_output=True, timeout=10)
            except Exception:  # noqa: BLE001
                pass
            if self._stop.wait(3.0):
                break
        self.running = False

    def _classify(self, line: str, last_source: str | None):
        """一行 logcat -> (来源, 正文)。返回 (None, "") 表示丢弃。"""
        match = self.LINE_RE.match(line)
        if match is None:
            # 没有前缀 = 上一条多行消息的续行
            if last_source and line.strip():
                return last_source, line
            return None, ""

        msg = match.group("msg")
        marker = msg.find(LOGCAT_MARK + "|")
        if marker >= 0:
            text = msg[marker + len(LOGCAT_MARK) + 1:]
            self._remember_gamelog(text)
            return "client", text

        # 原生 console 输出
        if match.group("tag").strip() == COCOS_TAG:
            pid = _game_pid()
            try:
                this_pid = int(match.group("pid"))
            except (TypeError, ValueError):
                this_pid = -1
            if pid is not None and this_pid != pid:
                return None, ""
            if self._is_gamelog_echo(msg):
                return None, ""
            return "console", msg

        return None, ""

    def _remember_gamelog(self, text: str) -> None:
        """`GAMELOG cc.log: XXX` -> 记住 XXX（原生那行紧接着就会来）。"""
        if not text.startswith("GAMELOG "):
            return
        sep = text.find(": ")
        if sep >= 0:
            self._recent_gamelog.append((time.time(), text[sep + 2:]))

    def _is_gamelog_echo(self, msg: str) -> bool:
        now = time.time()
        while self._recent_gamelog and now - self._recent_gamelog[0][0] > 5.0:
            self._recent_gamelog.popleft()
        return any(payload == msg for _, payload in self._recent_gamelog)

    def _emit(self, line: str, source: str = "client") -> None:
        self.lines += 1
        _publish_client_line(line, source=source)


_tailer = _LogcatTailer()

# probe.js 自己会上报（`POST /hook/log`）。一旦这个通道活着，
# 就把 logcat 的发布静音，免得同一条日志出现两遍。
# （只读写一个 float，CPython 下是原子的，不需要锁。）
_push_seen_at = 0.0


# 客户端行的级别**在产生这一端就判好**，不留给前端猜。
# 只有从 logcat 收原文这条路能判（原文里级别信息是隐含的），规则按**行首**：
#   1) 行首（可带 `|` 前缀）就是级别词 → 用它
#   2) 行首是探针的逐包追踪标签（CRYPT / GAME REQ / GAME RESP / REQ / RES / …）→ debug
#   3) 行里有 JS 异常特征 → error；有 WARN → warning
#   4) 都不是 → info（**永远给一个级别**，留空的话前端按级别过滤会失效）
#
# 前端 `guessClientLevel()` 里有一份同样的规则，只作兜底（老事件 / 别的来源）。
_LEVEL_WORD = re.compile(
    r"^(DEBUG|TRACE|INFO|NOTICE|WARN(?:ING)?|ERROR|ERR|FATAL|CRITICAL|CRIT|ASSERT)\b", re.I)
_LEVEL_DEBUG_TAG = re.compile(r"^(CRYPT|GAME REQ|GAME RESP|REQ|RES|SEND|RECV|POPUP|HOOK)\b")
_LEVEL_ERROR_HINT = re.compile(
    r"\b(ERROR|TypeError|ReferenceError|SyntaxError|is undefined|cannot read)\b", re.I)
_LEVEL_WARN_HINT = re.compile(r"\bWARN(?:ING)?\b", re.I)


def _guess_client_level(line: str) -> str:
    """按行首判客户端日志的级别（永远返回一个级别，不会是空串）。"""
    s = (line or "").strip()
    head = s.lstrip("|").lstrip()
    m = _LEVEL_WORD.match(head)
    if m:
        w = m.group(1).upper()
        if w in ("DEBUG", "TRACE"):
            return "debug"
        if w in ("INFO", "NOTICE"):
            return "info"
        if w.startswith("WARN"):
            return "warning"
        if w in ("ERROR", "ERR"):
            return "error"
        return "fatal"
    if _LEVEL_DEBUG_TAG.match(head):
        return "debug"
    if _LEVEL_ERROR_HINT.search(s):
        return "error"
    if _LEVEL_WARN_HINT.search(s):
        return "warning"
    return "info"


def _publish_client_line(line: str, source: str = "client") -> None:
    """把一条客户端日志发到总线上。

    `source` 是**前端过滤用的档位**：
      `client`  —— 探针行（`OPPAIHOOK|…`，含 `GAMELOG cc.log: …` / `GAME REQ` / `CRYPT`）
      `console` —— 原生 `console.log()` 的输出（JSB 里没法从 JS 侧包，只能从 logcat 收）
      `probe`   —— `probe.js` 通过 `POST /hook/log` 主动上报的
    """
    if source in ("client", "console") and time.time() - _push_seen_at < 15.0:
        # 探针主动上报那条路活着时，logcat 这边静音，免得重复
        return
    devbus.publish("client", source=source, line=line[:4000],
                   level=_guess_client_level(line))


# ---------------------------------------------------------------------------
# 存档快照
# ---------------------------------------------------------------------------

PLAYERS_PATH = os.path.join(config.DATA_DIR, "players.json")


def _snapshot(label: str = "") -> str:
    """把 players.json 存一份快照，返回文件名。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label or "")[:40]
    name = f"players-{stamp}{'-' + safe if safe else ''}.json"
    if os.path.isfile(PLAYERS_PATH):
        shutil.copy2(PLAYERS_PATH, os.path.join(BACKUP_DIR, name))
    else:
        with io.open(os.path.join(BACKUP_DIR, name), "w", encoding="utf-8") as fh:
            fh.write("{}")
    return name


def _list_backups() -> list:
    out = []
    for fn in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(BACKUP_DIR, fn)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append({
            "name": fn,
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
            "auto": fn.startswith("players-") and "-auto" in fn,
        })
    return out


def _restore(name: str) -> None:
    path = os.path.join(BACKUP_DIR, os.path.basename(name))
    if not os.path.isfile(path):
        raise FileNotFoundError(name)
    # 回滚前先给"当前"存一份，免得点错了没得救
    _snapshot("auto-before-restore")
    shutil.copy2(path, PLAYERS_PATH)


# ---------------------------------------------------------------------------
# 玩家
# ---------------------------------------------------------------------------

def _player_summary(account: str, player: dict) -> dict:
    levels = player.get("levels") or {}
    passed = sum(1 for v in levels.values() if int((v or {}).get("starMark") or -1) >= 0)
    stars = sum(1 for v in levels.values() if int((v or {}).get("starMark") or -1) >= 7)
    return {
        "account": account,
        "id": player.get("id"),
        "name": player.get("name"),
        "lv": player.get("lv"),
        "curExp": player.get("curExp"),
        "soldiers": len(player.get("soldiers") or []),
        "questsDone": len((player.get("quests") or {}).get("done") or []),
        "teams": len(player.get("teams") or []),
        "levelsPassed": passed,
        "levels3Star": stars,
        "rosterVersion": player.get("rosterVersion"),
    }


def _accounts() -> list:
    return sorted(store.all_players().keys())


def _get_player(account: str) -> dict | None:
    db = store.all_players()
    player = db.get(account)
    return player if isinstance(player, dict) else None


def _save_player_direct(account: str, player: dict) -> None:
    """整份覆盖某个账号的存档（用于「玩家」面板里直接编辑 JSON）。"""
    store.save_player_dict(account, player)


# ---------------------------------------------------------------------------
# 作弊
# ---------------------------------------------------------------------------

def _cheat(account: str, action: str, args: dict) -> dict:
    player = _get_player(account)
    if player is None:
        raise KeyError(account)
    store._migrate(player)                                # noqa: SLF001

    if action == "set_base":
        if args.get("lv") is not None:
            player["lv"] = int(args["lv"])
        if args.get("curExp") is not None:
            player["curExp"] = int(args["curExp"])
        detail = f"指挥部等级 = {player['lv']}，经验 = {player['curExp']}"

    elif action == "unlock_modules":
        state = player.setdefault("moduleState", {})
        for key in state:
            state[key]["isUnlock"] = 1
        detail = f"{len(state)} 个功能模块全部解锁"

    elif action == "unlock_levels":
        table = (instance._level_table().get("level") or {})   # noqa: SLF001
        rec = player.setdefault("levels", {})
        for level_id in table:
            rec[str(level_id)] = {
                "starMark": 7,
                "challengeTimes": max(1, int((rec.get(str(level_id)) or {}).get("challengeTimes") or 0)),
                "lastUpdateTimeSec": int(time.time()),
            }
        detail = f"{len(table)} 个关卡全部标记为三星通关"

    elif action == "clear_levels":
        player["levels"] = {}
        detail = "关卡进度已清空"

    elif action == "max_soldiers":
        n = 0
        for s in store.ensure_soldiers(player):
            quality = int(s.get("quality") or 1)
            star = soldier.max_star(quality) or s.get("star") or 1
            s["star"] = star
            s["lv"] = soldier.max_lv(quality, star) or s.get("lv") or 1
            s["skillLv"] = soldier.max_skill_lv(quality) or 1
            s["skillLvList"] = [s["skillLv"]] * max(1, len(s.get("skillLvList") or [1]))
            s["curExp"] = 0
            n += 1
        detail = f"{n} 个军士拉满（星级/等级/技能都到上限）"

    elif action == "reset_soldiers":
        player["soldiers"] = store.new_soldiers()
        player["rosterVersion"] = store.ROSTER_VERSION
        detail = f"军士名单重置成初始 {len(player['soldiers'])} 个"

    elif action == "add_soldier":
        key = str(args.get("key") or "").strip()
        if not key:
            raise ValueError("要给 key（比如 sasm010104）")
        row = (soldier.tables().get("card") or {}).get(key)
        if not isinstance(row, dict):
            raise ValueError(f"table_soldier 里没有 {key}")
        if not soldier.is_teammate(key):
            raise ValueError(
                f"{key} 的 card_type 是 {soldier.card_type(key)}（不是自军卡 1），"
                "发进去会让客户端的培养算出 NaN")
        soldiers = store.ensure_soldiers(player)
        new_id = max([int(s.get("id") or 0) for s in soldiers] + [0]) + 1
        quality = int(args.get("quality") or row.get("q") or 4)
        positioning = int(args.get("positioning") or row.get("p") or 1)
        soldiers.append(store.new_soldier(new_id, key, positioning, quality))
        detail = f"加了军士 {key}（id={new_id}，站位 {positioning}，品质 {quality}）"

    elif action == "clear_quests":
        player.setdefault("quests", {})["done"] = []
        player.pop("questStats", None)
        detail = "任务进度已清空"

    elif action == "finish_quests":
        from . import quests
        total, detail = quests.finish_all(player)
        player.setdefault("quests", {})["done"] = total

    elif action == "set_items":
        # 背包现在是 `agent._module_stubs` 里硬编码的，改存档不影响它；
        # 这里只是把"想要的道具"记下来，方便以后背包落盘时对齐。
        detail = "背包目前还是硬编码的（见 docs/overview.md 待办 2），这一步只改存档字段"
        player["items"] = {str(k): int(v) for k, v in (args.get("items") or {}).items()}

    elif action == "reset_player":
        fresh = store.new_player(account)
        fresh["id"] = player.get("id") or 1
        player.clear()
        player.update(fresh)
        detail = "存档已重置成新号"

    else:
        raise ValueError(f"未知的作弊项: {action}")

    store.save_player_dict(account, player)
    devbus.publish("action", action=action, account=account, detail=detail)
    log.info("devtools 作弊 %s @%s：%s", action, account, detail)
    return {"ok": True, "detail": detail, "player": _player_summary(account, player)}


# ---------------------------------------------------------------------------
# 客户端表 / 路由 / 文案
# ---------------------------------------------------------------------------

_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_client_tables: list[str] = []
_client_tables_at = 0.0


def _extracted_tables() -> list:
    """已经抽到 `gamesrv/data/*.json` 的表名。

    这些表服务端自己也在用（任务 / 关卡奖励 / 军士养成 / 助战 NPC），
    查起来不用惊动客户端，所以「数据」面板优先走它们。
    """
    out = []
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    if not os.path.isdir(data_dir):
        return out
    for fn in sorted(os.listdir(data_dir)):
        if fn.endswith(".json"):
            out.append(fn[:-5])
    return out


def _page_rows(table: dict, q: str, limit: int, offset: int) -> dict:
    """在一张 dict 形状的表里做「子串过滤 + 分页」。

    客户端那些 `table_*` 全是 `{key: row}` 的字典，`table_dictionary` 还是
    `{key: "文案"}`。搜索逻辑和服务端跑在客户端里的那份保持一致：
    `key + JSON(row)` 里出现子串就算命中，这样「按 key 查」和「按内容查」
    用同一个输入框就够了。
    """
    keys = []
    needle = (q or "").strip()
    for key, row in table.items():
        if needle:
            blob = key + " " + json.dumps(row, ensure_ascii=False) if isinstance(row, (dict, list)) else f"{key} {row}"
            if needle not in blob:
                continue
        keys.append(key)
    keys.sort()
    page = keys[offset:offset + limit]
    return {
        "ok": True,
        "total": len(keys),
        "offset": offset,
        "limit": limit,
        "keys": page,
        "rows": {k: table[k] for k in page},
    }


def _load_extracted(name: str):
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    path = os.path.join(data_dir, name + ".json")
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _probe_alive() -> bool:
    """探针最近有没有在轮询。`repl.poll()` 每次都会更新 last_poll_ts。"""
    return (time.time() - getattr(repl, "last_poll_ts", 0.0)) < 10.0


def _eval(code: str, timeout: float = 15.0) -> dict:
    """在客户端跑一段 JS。返回值统一成 `{ok, value, error}`。"""
    if not _probe_alive():
        return {"ok": False, "error": "客户端探针没在响应（游戏没开？装了 --no-probe 的包？）"}
    started = time.time()
    try:
        result = repl.submit(code, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    elapsed = int((time.time() - started) * 1000)
    if result is None:
        devbus.publish("console", code=code, ok=False, value="超时", ms=elapsed)
        return {"ok": False, "error": f"{timeout:.0f}s 超时（客户端没回结果）", "ms": elapsed}
    value = result.get("value")
    ok = bool(result.get("ok"))
    devbus.publish("console", code=code, ok=ok, value=str(value)[:4000], ms=elapsed)
    return {"ok": ok, "value": value, "ms": elapsed}


def _client_table_list(force: bool = False) -> list:
    global _client_tables, _client_tables_at
    if _client_tables and not force and time.time() - _client_tables_at < 60:
        return _client_tables
    got = _eval("JSON.stringify(Object.keys(window).filter(function(k){return /^table_/.test(k);}))",
                timeout=10.0)
    names: list[str] = []
    if got.get("ok"):
        value = got.get("value")
        try:
            while isinstance(value, str):
                value = json.loads(value)
            if isinstance(value, list):
                names = sorted(value)
        except Exception:  # noqa: BLE001
            names = []
    if names:
        _client_tables = names
        _client_tables_at = time.time()
    return _client_tables


def _client_table_search(name: str, q: str, limit: int, offset: int) -> dict:
    if not _TABLE_NAME_RE.match(name):
        raise ValueError("表名不合法")
    code = (
        "(function(){"
        f"var t = (typeof {name} !== 'undefined') ? {name} : null;"
        "if (!t) { return JSON.stringify({ok:false, error:'客户端没有这张表'}); }"
        f"var q = {json.dumps(q, ensure_ascii=False)};"
        "var keys = [], k, row, s;"
        "for (k in t) {"
        "  row = t[k];"
        "  if (q) { s = k + ' ' + ((row && typeof row === 'object') ? JSON.stringify(row) : String(row));"
        "           if (s.indexOf(q) < 0) { continue; } }"
        "  keys.push(k);"
        "}"
        "keys.sort();"
        f"var page = keys.slice({offset}, {offset + limit});"
        "var rows = {};"
        "for (var i = 0; i < page.length; i++) { rows[page[i]] = t[page[i]]; }"
        "return JSON.stringify({ok:true, total:keys.length, offset:" + str(offset) +
        ", rows:rows});"
        "})()"
    )
    got = _eval(code, timeout=20.0)
    if not got.get("ok"):
        return {"ok": False, "error": got.get("error")}
    value = got.get("value")
    try:
        while isinstance(value, str):
            value = json.loads(value)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"结果解析失败: {exc}", "raw": str(value)[:500]}
    if isinstance(value, dict):
        value["name"] = name
        value["query"] = q
    return value


def _dict_lookup(q: str, limit: int = 200) -> dict:
    """搜 table_dictionary（客户端所有提示文案都在这儿）。"""
    if not q:
        return {"ok": True, "rows": {}, "total": 0, "hint": "给个关键词再搜"}
    code = (
        "(function(){"
        "if (typeof table_dictionary === 'undefined') { return JSON.stringify({ok:false, error:'没有 table_dictionary'}); }"
        f"var q = {json.dumps(q, ensure_ascii=False)};"
        "var rows = {}, n = 0;"
        "for (var k in table_dictionary) {"
        "  var v = table_dictionary[k];"
        "  if (String(k).indexOf(q) >= 0 || String(v).indexOf(q) >= 0) {"
        "    rows[k] = v;"
        f"    if (++n >= {limit}) {{ break; }}"
        "  }"
        "}"
        "return JSON.stringify({ok:true, rows:rows, total:n});"
        "})()"
    )
    got = _eval(code, timeout=15.0)
    if not got.get("ok"):
        return {"ok": False, "error": got.get("error")}
    value = got.get("value")
    try:
        while isinstance(value, str):
            value = json.loads(value)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"结果解析失败: {exc}"}
    return value if isinstance(value, dict) else {"ok": False, "error": "形状不对"}


def _routes() -> list:
    from . import handlers
    out = []
    for name in handlers.registered():
        fn = handlers._HANDLERS.get(name)          # noqa: SLF001
        out.append({
            "route": name,
            "handler": getattr(fn, "__module__", "").replace("gamesrv.handlers.", "", 1)
            + "." + getattr(fn, "__name__", "?"),
            "doc": (getattr(fn, "__doc__", "") or "").strip().split("\n")[0][:160],
        })
    return out


# ---------------------------------------------------------------------------
# 路由注册
# ---------------------------------------------------------------------------

def build(service) -> None:
    """把这些路由挂到 CDN 服务的 router 上（apps.build_cdn 里调）。"""
    router = service.router

    def _guard(req):
        if not _local_only(req):
            log.warning("devtools 拒绝了非本地访问: %s", req.client)
            return _err("devtools 只允许本机 / 局域网访问", 403)
        return None

    # ---------------- 静态页面 ----------------

    @router.any("/devtools")
    @router.any("/devtools/")
    def _page(req):
        denied = _guard(req)
        if denied:
            return denied
        html = _read_web("devtools.html")
        if html is None:
            return Response(500, f"缺少 {os.path.join(WEB_DIR, 'devtools.html')}",
                            content_type="text/plain; charset=utf-8")
        return Response(200, html, content_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store"})

    @router.any("/devtools/app.js")
    def _js(req):
        text = _read_web("devtools.js") or "console.error('devtools.js 缺失');"
        return Response(200, text, content_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-store"})

    @router.any("/devtools/app.css")
    def _css(req):
        text = _read_web("devtools.css") or ""
        return Response(200, text, content_type="text/css; charset=utf-8",
                        headers={"Cache-Control": "no-store"})

    # ---------------- 实时事件（长轮询） ----------------

    @router.any("/devtools/api/events")
    def _events(req):
        denied = _guard(req)
        if denied:
            return denied
        try:
            since = int(_arg(req, _body(req), "since", 0) or 0)
        except (TypeError, ValueError):
            since = 0
        try:
            timeout = float(_arg(req, _body(req), "timeout", 25) or 0)
        except (TypeError, ValueError):
            timeout = 25.0
        timeout = max(0.0, min(timeout, 55.0))
        try:
            limit = int(_arg(req, _body(req), "limit", 500) or 500)
        except (TypeError, ValueError):
            limit = 500
        limit = max(1, min(limit, devbus.RING_MAX))

        if timeout > 0:
            events = devbus.bus.wait(since, timeout)
        else:
            events = devbus.bus.since(since, limit=limit)

        # 游标语义（前端据此推进 since）：
        #   有事件  -> 最后一条的 seq（这样被 limit 截断时下一轮能接着拿）
        #   没事件  -> 服务端当前最新 seq
        # 第二种是关键：环形缓冲满了之后旧事件会被丢掉，如果这里回 since，
        # 前端会卡在一个永远拿不到事件的区间里变成忙循环。
        cursor = events[-1]["seq"] if events else max(since, devbus.bus.latest())
        return _reply({
            "ok": True,
            "seq": cursor,
            "latest": devbus.bus.latest(),
            "events": events,
            "stats": devbus.bus.stats(),
        })

    @router.any("/devtools/api/events/clear")
    def _events_clear(req):
        devbus.bus.clear()
        return _reply({"ok": True})

    # ---------------- 总览 ----------------

    @router.any("/devtools/api/overview")
    def _overview(req):
        denied = _guard(req)
        if denied:
            return denied
        from . import session as session_mod
        sessions = []
        for sid, data in list(getattr(session_mod, "_sessions", {}).items()):
            info = data.get("info") or {}
            sessions.append({
                "session": sid[:8] + "…",
                "account": info.get("account"),
                "ageSec": int(time.time() - int(data.get("createTime") or 0)),
            })
        return _reply({
            "ok": True,
            "server": {
                "version": config.APP_VERSION,
                "patchVersion": config.PATCH_VERSION,
                "publicHost": config.PUBLIC_HOST,
                "ports": {
                    "cdn": config.CDN_PORT,
                    "gate": config.GATE_PORT,
                    "login": config.LOGIN_PORT,
                    "game": config.GAME_PORT,
                },
                "url": f"http://127.0.0.1:{config.CDN_PORT}/devtools",
            },
            "sessions": sessions,
            "accounts": _accounts(),
            "defaultAccount": config.DEFAULT_ACCOUNT,
            "probeAlive": _probe_alive(),
            "logcat": _tailer.status(),
            "bus": devbus.bus.stats(),
            "extractedTables": _extracted_tables(),
            "routes": len(_routes()),
            "adb": {"path": ADB, "serial": ADB_SERIAL},
            "backups": len(_list_backups()),
        })

    # ---------------- 玩家 ----------------

    @router.any("/devtools/api/players")
    def _players(req):
        denied = _guard(req)
        if denied:
            return denied
        out = []
        for account, player in sorted(store.all_players().items()):
            if isinstance(player, dict):
                out.append(_player_summary(account, player))
        return _reply({"ok": True, "players": out})

    @router.any("/devtools/api/player")
    def _player(req):
        denied = _guard(req)
        if denied:
            return denied
        account = str(_arg(req, _body(req), "account", config.DEFAULT_ACCOUNT))
        player = _get_player(account)
        if player is None:
            return _err(f"没有这个账号: {account}", 404)
        return _reply({"ok": True, "account": account, "player": player})

    @router.any("/devtools/api/player/save")
    def _player_save(req):
        denied = _guard(req)
        if denied:
            return denied
        payload = _body(req)
        account = str(payload.get("account") or config.DEFAULT_ACCOUNT)
        player = payload.get("player")
        if not isinstance(player, dict):
            return _err("player 必须是一个对象")
        if _get_player(account) is None:
            return _err(f"没有这个账号: {account}", 404)
        snapshot = _snapshot(f"auto-before-edit-{account}")
        _save_player_direct(account, player)
        devbus.publish("action", action="save_player", account=account,
                       detail=f"手动保存存档（改前已快照 {snapshot}）")
        log.info("devtools 保存存档 %s（快照 %s）", account, snapshot)
        return _reply({"ok": True, "snapshot": snapshot,
                       "player": _player_summary(account, _get_player(account) or {})})

    @router.any("/devtools/api/cheat")
    def _cheat_api(req):
        denied = _guard(req)
        if denied:
            return denied
        payload = _body(req)
        account = str(payload.get("account") or config.DEFAULT_ACCOUNT)
        action = str(payload.get("action") or "")
        args = payload.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        try:
            _snapshot(f"auto-before-{action}")
            return _reply(_cheat(account, action, args))
        except KeyError:
            return _err(f"没有这个账号: {account}", 404)
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("devtools 作弊失败 %s", action)
            return _err(f"{type(exc).__name__}: {exc}", 500)

    @router.any("/devtools/api/backups")
    def _backups(req):
        denied = _guard(req)
        if denied:
            return denied
        return _reply({"ok": True, "backups": _list_backups()})

    @router.any("/devtools/api/backup")
    def _backup(req):
        denied = _guard(req)
        if denied:
            return denied
        label = str(_arg(req, _body(req), "label", "") or "")
        name = _snapshot(label)
        devbus.publish("action", action="backup", detail=f"新建快照 {name}")
        return _reply({"ok": True, "name": name, "backups": _list_backups()})

    @router.any("/devtools/api/restore")
    def _restore_api(req):
        denied = _guard(req)
        if denied:
            return denied
        name = str(_arg(req, _body(req), "name", "") or "")
        try:
            _restore(name)
        except FileNotFoundError:
            return _err(f"没有这个快照: {name}", 404)
        devbus.publish("action", action="restore", detail=f"回滚到 {name}")
        log.info("devtools 回滚存档到 %s", name)
        return _reply({"ok": True, "restored": name, "backups": _list_backups()})

    # ---------------- 客户端控制台 ----------------

    @router.any("/devtools/api/console")
    def _console(req):
        denied = _guard(req)
        if denied:
            return denied
        payload = _body(req)
        code = str(payload.get("code") or "")
        if not code.strip():
            return _err("code 不能为空")
        try:
            timeout = float(payload.get("timeout") or 15.0)
        except (TypeError, ValueError):
            timeout = 15.0
        timeout = max(1.0, min(timeout, 60.0))
        return _reply(_eval(code, timeout))

    @router.any("/devtools/api/replay")
    def _replay(req):
        """把一条抓到的 msg 原样重放给客户端。

        走的是**客户端自己的** `server.request`（而不是服务端直接 dispatch），
        这样探针的 `GAME REQ`/`GAME RESP` 会照常打日志，
        而且真的会经过加密/解密/响应派发那条完整链路 —— 排障时这才是要验的东西。

        ⚠️ 回包用 `__oppaiHook__.log` 打，**不能用 `console.log`**：
        原生 `console.log` 在 JSB 里是 non-configurable + non-writable，
        客户端包不了，探针也就不可能把它的输出打上 `OPPAIHOOK|` 前缀，
        日志面板按前缀过滤时是看不到的（说明见 `_LogcatTailer` 的 docstring）。
        """
        denied = _guard(req)
        if denied:
            return denied
        payload = _body(req)
        route = str(payload.get("route") or "")
        msg = payload.get("msg")
        if not route:
            return _err("route 不能为空")
        if msg is None:
            msg = {}
        route_js = json.dumps(route)
        code = (
            "(function(){"
            "  var say = (window.__oppaiHook__ && __oppaiHook__.log) || (window.cc && cc.log);"
            f"  server.request({route_js}, {json.dumps(msg, ensure_ascii=False)},"
            "    function(e, d){"
            "      try { say('REPLAY ' + " + route_js +
            "        + ' => ' + JSON.stringify(e ? {err: String(e)} : d)); } catch (x) {}"
            "    });"
            "  return 'sent';"
            "})()"
        )
        got = _eval(code, timeout=10.0)
        if got.get("ok"):
            return _reply({"ok": True, "detail": "已发给客户端，回包看流量/日志面板"})
        return _reply(got)

    # ---------------- 表 / 路由 / 文案 ----------------

    @router.any("/devtools/api/routes")
    def _routes_api(req):
        denied = _guard(req)
        if denied:
            return denied
        return _reply({"ok": True, "routes": _routes()})

    @router.any("/devtools/api/tables")
    def _tables_api(req):
        denied = _guard(req)
        if denied:
            return denied
        force = str(_arg(req, _body(req), "refresh", "")) in ("1", "true", "yes")
        return _reply({
            "ok": True,
            "extracted": _extracted_tables(),
            "client": _client_table_list(force=force),
            "probeAlive": _probe_alive(),
        })

    @router.any("/devtools/api/table")
    def _table_api(req):
        denied = _guard(req)
        if denied:
            return denied
        payload = _body(req)
        name = str(_arg(req, payload, "name", "") or "")
        if not name:
            return _err("name 不能为空")
        q = str(_arg(req, payload, "q", "") or "")
        try:
            limit = int(_arg(req, payload, "limit", 60) or 60)
            offset = int(_arg(req, payload, "offset", 0) or 0)
        except (TypeError, ValueError):
            limit, offset = 60, 0
        limit = max(1, min(limit, 500))
        offset = max(0, offset)

        extracted = _load_extracted(name)
        if extracted is not None:
            return _reply({"ok": True, "source": "extracted", **_page_rows(extracted, q, limit, offset)})
        got = _client_table_search(name, q, limit, offset)
        got["source"] = "client"
        return _reply(got)

    @router.any("/devtools/api/dict")
    def _dict_api(req):
        denied = _guard(req)
        if denied:
            return denied
        payload = _body(req)
        q = str(_arg(req, payload, "q", "") or "")
        try:
            limit = int(_arg(req, payload, "limit", 200) or 200)
        except (TypeError, ValueError):
            limit = 200
        return _reply(_dict_lookup(q, limit=max(1, min(limit, 1000))))

    # ---------------- 客户端日志开关 ----------------

    @router.any("/devtools/api/logcat")
    def _logcat_api(req):
        denied = _guard(req)
        if denied:
            return denied
        payload = _body(req)
        action = str(_arg(req, payload, "action", "status") or "status")
        if action == "start":
            _tailer.start()
        elif action == "stop":
            _tailer.stop()
        elif action == "clear":
            try:
                subprocess.run([ADB, "-s", ADB_SERIAL, "logcat", "-c"],
                               capture_output=True, timeout=10)
            except Exception as exc:  # noqa: BLE001
                return _err(f"清空 logcat 失败: {exc}")
            devbus.publish("action", action="logcat-clear", detail="已清空 logcat 缓冲")
        elif action == "status":
            pass
        else:
            return _err(f"未知 action: {action}")
        return _reply({"ok": True, "logcat": _tailer.status(), "probeAlive": _probe_alive()})

    @router.any("/hook/log")
    @router.any("/devtools/api/log/push")
    def _log_push(req):
        """probe.js 主动上报探针日志。

        比 logcat 尾随更可靠：不受 adb 断线 / logcat 环形缓冲被冲掉的影响，
        也不需要 root。`probe.js` 的 `flush()` 里会 POST 到这里（下一版 APK 起生效）。
        一旦这个通道活着，logcat 那边就自动静音，免得同一条日志出现两遍。
        """
        global _push_seen_at
        payload = req.payload() or {}
        lines = payload.get("lines")
        if isinstance(lines, str):
            lines = lines.splitlines()
        if isinstance(lines, list):
            _push_seen_at = time.time()
            for line in lines[:2000]:
                devbus.publish("client", source="probe", line=str(line)[:4000],
                               level=_guess_client_level(str(line)))
            return _reply({"ok": True, "n": len(lines)})
        return _err("lines 必须是数组或字符串")

    # ---------------- 引擎 JS 调试器 ----------------
    #
    # 后端是 gamesrv/jsdlink.py —— 引擎自带的那个远程 JS 调试器
    # （SpiderMonkey Debugger API + Firefox 远程调试协议）。
    # 引擎侧怎么打开、踩过哪些坑，见 docs/engine-debug.md。

    @router.any("/devtools/api/jsd/connect")
    def _jsd_connect(req):
        denied = _guard(req)
        if denied:
            return denied
        from . import jsdlink
        try:
            return _reply({"ok": True, **jsdlink.session.connect()})
        except Exception as exc:  # noqa: BLE001
            log.warning("devtools jsd connect 失败: %s", exc)
            return _reply({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    @router.any("/devtools/api/jsd/disconnect")
    def _jsd_disconnect(req):
        denied = _guard(req)
        if denied:
            return denied
        from . import jsdlink
        return _reply({"ok": True, **jsdlink.session.disconnect()})

    @router.any("/devtools/api/jsd/status")
    def _jsd_status(req):
        denied = _guard(req)
        if denied:
            return denied
        from . import jsdlink
        st = jsdlink.session.status()
        st["ok"] = True
        if jsdlink.session.state == "paused":
            st["frames"] = jsdlink.frames_brief(jsdlink.session.frames())
        return _reply(st)

    @router.any("/devtools/api/jsd/sources")
    def _jsd_sources(req):
        denied = _guard(req)
        if denied:
            return denied
        from . import jsdlink
        payload = _body(req)
        q = str(_arg(req, payload, "q", "") or "")
        try:
            limit = int(_arg(req, payload, "limit", 200) or 200)
        except (TypeError, ValueError):
            limit = 200
        try:
            hits = jsdlink.session.sources(q)
            return _reply({"ok": True, "total": len(hits),
                           "sources": [{"actor": s.get("actor"), "url": s.get("url")}
                                       for s in hits[:max(1, min(limit, 2000))]]})
        except Exception as exc:  # noqa: BLE001
            return _reply({"ok": False, "error": str(exc)})

    @router.any("/devtools/api/jsd/bp")
    def _jsd_bp(req):
        denied = _guard(req)
        if denied:
            return denied
        from . import jsdlink
        payload = _body(req)
        url = str(payload.get("url") or "")
        if not url:
            return _err("要给 url")
        try:
            line = int(payload.get("line") or 0)
        except (TypeError, ValueError):
            return _err("line 要是数字")
        try:
            reply = jsdlink.session.set_breakpoint(url, line)
            devbus.publish("action", action="jsd-breakpoint",
                           detail=f"引擎断点 {url}:{line}")
            return _reply({"ok": True, "breakpoint": reply,
                           "breakpoints": jsdlink.session.breakpoints()})
        except Exception as exc:  # noqa: BLE001
            return _reply({"ok": False, "error": str(exc)})

    @router.any("/devtools/api/jsd/control")
    def _jsd_control(req):
        denied = _guard(req)
        if denied:
            return denied
        from . import jsdlink
        payload = _body(req)
        action = str(payload.get("action") or "")
        try:
            if action == "resume":
                jsdlink.session.resume(payload.get("limit") or None)
            elif action == "pause":
                jsdlink.session.pause()
            else:
                return _err(f"未知 action: {action}")
        except Exception as exc:  # noqa: BLE001
            return _reply({"ok": False, "error": str(exc)})
        return _reply({"ok": True, **jsdlink.session.status()})

    @router.any("/devtools/api/jsd/eval")
    def _jsd_eval(req):
        denied = _guard(req)
        if denied:
            return denied
        from . import jsdlink
        payload = _body(req)
        expr = str(payload.get("expression") or "")
        if not expr.strip():
            return _err("expression 不能为空")
        try:
            reply = jsdlink.session.evaluate(expr, frame=payload.get("frame") or None)
        except Exception as exc:  # noqa: BLE001
            return _reply({"ok": False, "error": str(exc)})
        fin = ((reply.get("why") or {}).get("frameFinished") or {})
        devbus.publish("console", code=expr, ok=True,
                       value=json.dumps(fin, ensure_ascii=False)[:2000], ms=0,
                       source="引擎调试器")
        return _reply({"ok": True, "result": fin})
