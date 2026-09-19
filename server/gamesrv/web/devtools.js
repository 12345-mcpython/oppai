/* 战场双马尾 devtools —— 前端逻辑。
 *
 * 数据来源只有两个：
 *   1. `/devtools/api/events` 的长轮询（实时事件：流量 / 日志 / 控制台 / 作弊记录）
 *   2. 其余 REST 接口（拉存档、跑 JS、查表……）
 *
 * 不引任何外部库：这个页面是拿来排障的，不该因为断网 / CDN 挂掉而变白板。
 */
'use strict';

// ---------------------------------------------------------------- 兜底报错
//
// 这个页面是拿来排障的，它自己坏了必须能一眼看出来。
// 用最原始的 DOM 操作画一条红条（不依赖下面任何代码），
// 并且把 id 引用错误之类的问题直接说清楚。
//
// ⚠️⚠️ **红条必须能关掉、必须有上限、同一条不能重复堆**。踩过一次：
// 它是 `position:fixed;top:0;z-index:999` 而且**只有 `textContent += `**，
// 于是服务端重启期间每 5 秒一次的 fetch 失败（见文件末尾 `poll()` 的说明）
// 把页顶那条红条越堆越长 —— **一直挂在最上面挡着工具栏，而且关不掉**。
// 现在：右上角有「关闭 ✕」，最多留 FATAL_MAX 行，同一条只累加次数。
(function () {
  var FATAL_MAX = 6;
  var lines = [];              // [{text, n}]
  var box = null;
  var out = null;

  function ensure() {
    if (box && box.isConnected) return;
    box = document.createElement('div');
    box.id = 'fatal';
    box.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:999;' +
      'background:#5a2329;border-bottom:2px solid #e06c75;color:#ffd8d8;' +
      'padding:8px 74px 8px 14px;font:12px/1.6 Consolas,monospace';
    out = document.createElement('div');
    out.style.cssText = 'white-space:pre-wrap';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = '关闭 ✕';
    btn.style.cssText = 'position:absolute;right:10px;top:6px;cursor:pointer;' +
      'background:transparent;border:1px solid #e06c75;color:#ffd8d8;border-radius:4px;' +
      'font:11px/1.4 Consolas,monospace;padding:2px 8px';
    btn.onclick = function () {
      if (box) box.remove();
      box = null; out = null; lines = [];
    };
    box.appendChild(out);
    box.appendChild(btn);
    document.body.appendChild(box);
  }

  function banner(title, detail) {
    try {
      var text = title + ' :: ' + detail;
      var hit = null;
      for (var i = 0; i < lines.length; i++) {
        if (lines[i].text === text) { hit = lines[i]; break; }
      }
      if (hit) hit.n += 1;
      else lines.push({ text: text, n: 1 });
      while (lines.length > FATAL_MAX) lines.shift();
      ensure();
      out.textContent = lines.map(function (l) {
        return l.text + (l.n > 1 ? '   （重复 ' + l.n + ' 次）' : '');
      }).join('\n');
    } catch (e) { /* 连这个都挂了就真没辙了 */ }
  }
  window.addEventListener('error', function (e) {
    banner('JS 错误', (e.message || '') + ' @ ' + (e.filename || '?') + ':' + (e.lineno || 0));
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e.reason;
    banner('未处理的 Promise 异常', (r && (r.stack || r.message)) || String(r));
  });
})();

// ---------------------------------------------------------------- 小工具

const $ = (id) => document.getElementById(id);
const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

const state = {
  since: 0,
  paused: false,
  traffic: [],       // 保留最近 N 条用于筛选/导出
  trafficMax: 800,
  logs: [],
  logsMax: 3000,
  selTraffic: null,
  account: null,
  players: [],
  tablesLoaded: false,
  dtOffset: 0,
};

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  const p = (n, w) => String(n).padStart(w || 2, '0');
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds()) + '.' + p(d.getMilliseconds(), 3);
}

function pretty(value) {
  if (value === undefined) return 'undefined';
  if (typeof value === 'string') return value;
  try { return JSON.stringify(value, null, 2); } catch (e) { return String(value); }
}

/** 宽进严出的一行文字预览（对象只显示前若干个 key）。 */
function brief(value, max) {
  max = max || 160;
  let text;
  if (typeof value === 'string') text = value;
  else { try { text = JSON.stringify(value); } catch (e) { text = String(value); } }
  if (text === undefined) return '';
  return text.length > max ? text.slice(0, max) + '…' : text;
}

async function api(path, options) {
  const res = await fetch('/devtools' + path, Object.assign({ headers: {} }, options || {}));
  let data = null;
  try { data = await res.json(); } catch (e) { data = { ok: false, error: 'HTTP ' + res.status + '（返回不是 JSON）' }; }
  if (!res.ok && data && data.error === undefined) data.error = 'HTTP ' + res.status;
  return data;
}

function post(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

let toastTimer = null;
let toastLast = null;
function toast(message, kind) {
  let node = $('toast');
  if (!node) {
    node = el('div', 'toast');
    node.id = 'toast';
    document.body.appendChild(node);
  }
  // ⚠️ 同一条消息**连续重复**时不要重置计时器。
  // 探针一直 `XHR onerror` 那种情况会每秒调一次 toast，每次都 `clearTimeout` + 重新计 8 秒，
  // 结果就是这条红条看着"永远不会关"。这里改成：同一条已经在显示 → 只更新内容，计时照走。
  const same = (message === toastLast && toastTimer);
  toastLast = message;
  node.className = 'toast' + (kind ? ' ' + kind : '');
  node.textContent = message;
  node.hidden = false;
  if (same) return;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    node.hidden = true;
    toastTimer = null;
    toastLast = null;
  }, kind === 'err' ? 5000 : 3000);
}

/** 按钮包一层：跑的时候禁用 + 出错弹提示，省得每个 handler 都写一遍。 */
function onClick(id, fn) {
  const node = $(id);
  if (!node) return;
  node.addEventListener('click', async () => {
    node.disabled = true;
    try {
      await fn();
    } catch (e) {
      toast(String(e && e.message ? e.message : e), 'err');
    } finally {
      node.disabled = false;
    }
  });
}

// ---------------------------------------------------------------- 实时事件

async function pump() {
  for (;;) {
    if (state.paused) {
      await sleep(400);
      continue;
    }
    try {
      const data = await api('/api/events?since=' + state.since + '&timeout=25&limit=2000');
      if (!data || !data.ok) throw new Error((data && data.error) || '事件接口异常');
      // ⚠️ `data.reset` = 我们的游标比服务端还超前（服务端重启过 / 缓冲被清过）。
      // 服务端会从 0 重放整个缓冲，先把面板清空，否则重放的历史会和现有内容交错。
      // **必须无条件采纳 `data.seq`** —— 服务端保证它不会超前。
      if (data.reset) resetPanels();
      state.since = data.seq;
      for (const ev of data.events || []) routeEvent(ev);
      if (data.stats) updateStats(data.stats);
    } catch (e) {
      // 服务端重启 / 网络断开：歇一下再重连，别把控制台刷满
      await sleep(1500);
    }
  }
}

function resetPanels() {
  toast('服务端事件流已重置，重新同步…', 'ok');
  // 三个面板都要清：日志、流量、控制台。state 里的环形缓冲也一起清，
  // 否则后面的「清空/重绘/过滤」还会拿旧数据。
  state.logs = [];
  state.traffic = [];
  $('lg-out').textContent = '';
  TR_LIST().textContent = '';
  $('tr-count').textContent = '0 条';
  redrawLogs();
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function routeEvent(ev) {
  switch (ev.kind) {
    case 'traffic': addTraffic(ev); break;
    // 级别优先用**服务端产生事件时判好的** `ev.level`（见 devtools.py 的
    // `_guess_client_level`）；只有老事件/别的来源缺这个字段时才本地兜底猜一次。
    case 'client': addLog('client', ev.source || 'logcat', ev.level || guessClientLevel(ev.line), ev.line); break;
    // 服务端行正常情况下自带 level；万一没有也补一个，保证每行都有级别可筛
    case 'server': addLog('server', ev.logger || 'server', ev.level || guessClientLevel(ev.message), ev.message); break;
    case 'console': addConsole(ev); break;
    case 'action': addLog('server', 'devtools', '', ev.detail || ev.action, 'action'); break;
    default: break;
  }
}

function updateStats(stats) {
  $('pill-bus').innerHTML = '事件 <b>' + (stats.buffered || 0) + '</b>';
}

async function refreshOverview() {
  const data = await api('/api/overview');
  if (!data.ok) return;
  $('srv-url').textContent = data.server.url;
  const probe = $('pill-probe');
  probe.className = 'pill ' + (data.probeAlive ? 'good' : 'bad');
  probe.innerHTML = '探针 <b>' + (data.probeAlive ? '在线' : '离线') + '</b>';

  const lc = $('pill-logcat');
  const lcs = data.logcat || {};
  lc.className = 'pill ' + (lcs.running ? 'good' : '');
  lc.innerHTML = '日志 <b>' + (lcs.running ? lcs.lines + ' 行' : '停') + '</b>';
  lc.title = lcs.error || (lcs.adb + ' -s ' + lcs.serial);

  if (!state.account) {
    state.account = data.defaultAccount || (data.accounts || [])[0];
    await loadPlayers();
  }
}

// ---------------------------------------------------------------- 流量面板

const TR_LIST = () => $('tr-list');

function addTraffic(ev) {
  state.traffic.push(ev);
  if (state.traffic.length > state.trafficMax) state.traffic.shift();
  if (matchesTrafficFilter(ev)) appendTrafficRow(ev);
  $('tr-count').textContent = state.traffic.length + ' 条';
}

function matchesTrafficFilter(ev) {
  if ($('tr-onlybad').checked && ev.ok) return false;
  if ($('tr-onlyunknown').checked && ev.known) return false;
  const needle = $('tr-filter').value.trim().toLowerCase();
  if (!needle) return true;
  const blob = (ev.route + ' ' + brief(ev.msg, 4000) + ' ' + brief(ev.res, 4000)).toLowerCase();
  return blob.indexOf(needle) >= 0;
}

function appendTrafficRow(ev) {
  const list = TR_LIST();
  const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 40;

  const row = el('div', 'row');
  row.dataset.seq = ev.seq;
  if (ev.route === '<解包失败>') row.classList.add('sys');
  else if (!ev.ok) row.classList.add('bad');
  else row.classList.add('good');
  if (!ev.known && ev.route !== '<解包失败>') row.classList.add('unknown');

  row.appendChild(el('span', 'ts', fmtTime(ev.ts)));
  row.appendChild(el('span', 'route', ev.route));
  row.appendChild(el('span', 'code', ev.code === null || ev.code === undefined ? '—' : ev.code));
  row.appendChild(el('span', 'ms', (ev.ms || 0) + 'ms'));
  row.addEventListener('click', () => selectTraffic(ev.seq, row));
  row.title = brief(ev.msg, 300);
  list.appendChild(row);

  if ($('tr-autoscroll').checked && atBottom) list.scrollTop = list.scrollHeight;
}

function selectTraffic(seq, row) {
  state.selTraffic = seq;
  $$('#tr-list .row').forEach((n) => n.classList.toggle('sel', n === row));
  const ev = state.traffic.find((e) => e.seq === seq);
  if (!ev) return;
  renderTrafficDetail(ev);
}

function renderTrafficDetail(ev) {
  const box = $('tr-detail');
  box.textContent = '';

  const head = el('h4');
  head.appendChild(el('span', 'mono', ev.route));
  head.appendChild(el('span', 'muted', '#' + ev.reqId + ' · ' + (ev.ms || 0) + 'ms · ' +
    (ev.ok ? '成功' : '失败') + (ev.known ? '' : ' · 服务端未实现') +
    (ev.account ? ' · ' + ev.account : '') + (ev.session ? ' · session ' + ev.session : '')));
  const replay = el('button', 'ghost', '重放');
  replay.addEventListener('click', async () => {
    replay.disabled = true;
    try {
      const r = await post('/api/replay', { route: ev.route, msg: ev.msg });
      toast(r.ok ? (r.detail || '已发送') : (r.error || '重放失败'), r.ok ? 'ok' : 'err');
    } finally { replay.disabled = false; }
  });
  head.appendChild(replay);
  const copy = el('button', 'ghost', '复制 msg');
  copy.addEventListener('click', () => copyText(pretty(ev.msg)));
  head.appendChild(copy);
  box.appendChild(head);

  if (ev.error) {
    box.appendChild(el('div', 'lbl', '错误'));
    box.appendChild(el('pre', null, ev.error));
  }
  box.appendChild(el('div', 'lbl', '请求 msg'));
  box.appendChild(el('pre', null, pretty(ev.msg)));
  box.appendChild(el('div', 'lbl', '回包'));
  box.appendChild(el('pre', null, pretty(ev.res)));
}

function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => toast('已复制', 'ok'), () => toast('复制失败', 'err'));
  } else {
    const ta = el('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); toast('已复制', 'ok'); } catch (e) { toast('复制失败', 'err'); }
    ta.remove();
  }
}

function redrawTraffic() {
  const list = TR_LIST();
  list.textContent = '';
  state.traffic.filter(matchesTrafficFilter).forEach(appendTrafficRow);
  $('tr-count').textContent = state.traffic.length + ' 条（显示 ' + list.childElementCount + '）';
}

// ---------------------------------------------------------------- 控制台

/* 这些片段都在真机上跑通过一遍（`tools/check_devtools.py` 的同款式子）。
 * 注意几个形状坑：
 *   dataManager.character.soldiers  是**以 id 为键的对象**，不是数组
 *   dataManager.questCenter         才是任务模块（没有 dataManager.quest）
 *   dataManager.bag._items[key]._count  才是数量
 */
const SNIPPETS = [
  ['环境概览', "JSON.stringify({lv: dataManager.player.lv, soldiers: Object.keys(dataManager.character.soldiers).length, teams: dataManager.player.teams.length, levels: Object.keys(dataManager.instance._levels).length}, null, 1)"],
  ['队伍', "JSON.stringify(dataManager.player.teams.map(function (t) { return t.id + ':' + (t._soldierKeys || []).join('/'); }))"],
  ['军士列表', "Object.keys(dataManager.character.soldiers).map(function (k) { var s = dataManager.character.soldiers[k]; return [s.id, s.key, 'lv' + s.lv + '/' + s.maxLv, 'star' + s.star + '/' + s.maxStar, 'ct' + s.cardType, 'pos' + s.positioning].join(' '); }).join('\\n')"],
  ['任务', "JSON.stringify(Object.keys(dataManager.questCenter._quests).map(function (k) { return k + ':' + dataManager.questCenter._quests[k].state; }))"],
  ['关卡进度', "JSON.stringify(Object.keys(dataManager.instance._levels).filter(function (k) { return dataManager.instance._levels[k]._starMark >= 0; }).map(function (k) { return k + ':' + dataManager.instance._levels[k]._starMark; }))"],
  ['功能模块', "JSON.stringify(dataManager.player._moduleState)"],
  ['背包道具', "JSON.stringify(Object.keys(dataManager.bag._items).map(function (k) { return k + '=' + dataManager.bag._items[k]._count; }).filter(function (s) { return s.indexOf('=0') < 0; }))"],
  ['场景节点', "JSON.stringify(cc.director.getRunningScene().getChildren().map(function (n) { return n.getName ? n.getName() : String(n); }))"],
  ['模块自检', "(function () { var out = []; ['player', 'character', 'bag', 'questCenter', 'instance', 'friendSupport', 'gacha', 'talentCenter', 'mailbox', 'rank', 'exchangeCenter', 'shop', 'bossCenter'].forEach(function (m) { try { out.push(m + ':' + (dataManager[m] ? 'ok' : 'null')); } catch (e) { out.push(m + ':ERR'); } }); return out.join(' | '); })()"],
];

function buildSnippets() {
  const bar = $('cc-snippets');
  SNIPPETS.forEach(([label, code]) => {
    const b = el('button', 'ghost', label);
    b.title = code;
    b.addEventListener('click', () => {
      $('cc-code').value = code;
      runConsole();
    });
    bar.appendChild(b);
  });
}

const consoleHistory = [];
let historyIdx = -1;

async function runConsole() {
  const code = $('cc-code').value;
  if (!code.trim()) return;
  consoleHistory.push(code);
  historyIdx = consoleHistory.length;
  const timeout = Number($('cc-timeout').value) || 15;
  const item = el('div', 'cc-item');
  item.appendChild(el('div', 'in', '> ' + code));
  const out = el('div', 'out', '执行中…');
  item.appendChild(out);
  $('cc-out').appendChild(item);
  stickConsole();

  const r = await post('/api/console', { code: code, timeout: timeout });
  if (r.ok) {
    out.textContent = r.value;
  } else {
    item.classList.add('err');
    out.textContent = r.error || '失败';
  }
  if (r.ms !== undefined) item.appendChild(el('div', 'meta', r.ms + 'ms'));
  stickConsole();
}

function addConsole(ev) {
  // 从 repl.py 之外的入口跑的（比如 tools/repl.py）也显示出来
  const item = el('div', 'cc-item' + (ev.ok ? '' : ' err'));
  item.appendChild(el('div', 'in', '> ' + ev.code));
  item.appendChild(el('div', 'out', ev.value));
  item.appendChild(el('div', 'meta', (ev.ms || 0) + 'ms（来自 ' + (ev.source || '客户端 REPL') + '）'));
  $('cc-out').appendChild(item);
  stickConsole();          // ← 这条路以前完全不滚
}

// ---------------------------------------------------------------- 玩家面板

const CHEATS = [
  {
    title: '指挥部',
    rows: [
      { label: '等级', id: 'ch-lv', type: 'number', value: 30 },
      { label: '经验', id: 'ch-exp', type: 'number', value: 0 },
    ],
    buttons: [
      ['设置等级 / 经验', () => {
        const args = {};
        const lv = $('ch-lv').value; const exp = $('ch-exp').value;
        if (lv !== '') args.lv = Number(lv);
        if (exp !== '') args.curExp = Number(exp);
        return ['set_base', args];
      }],
    ],
    hint: '「培养系统」要 6 级、演习场 27 级、第 5 个上阵位 25 级（table_function_open）。',
  },
  {
    title: '功能模块',
    buttons: [['全部解锁', 'unlock_modules']],
    hint: '把 moduleState 里 32 个 isUnlock 全置 1。',
  },
  {
    title: '关卡',
    buttons: [
      ['全部三星通关', 'unlock_levels'],
      ['清空关卡进度', 'clear_levels'],
    ],
    hint: '直接写 player.levels，不需要真的去打。',
  },
  {
    title: '军士',
    buttons: [
      ['全部拉满（星级/等级/技能）', 'max_soldiers'],
      ['重置成初始名单', 'reset_soldiers'],
    ],
    rows: [{ label: '加一个 key', id: 'ch-soldierkey', type: 'text', value: 'sasm010104' }],
    buttons2: [['加军士', () => ['add_soldier', { key: $('ch-soldierkey').value }]]],
    hint: 'key 要是 card_type==1 的自军卡，比如 sasm010104 / sbd010104 / saf010104。',
  },
  {
    title: '任务',
    buttons: [
      ['全部标记为已领奖', 'finish_quests'],
      ['清空任务进度', 'clear_quests'],
    ],
    hint: '「全部完成」会把 done 填满并把 questStats 灌到 9999。',
  },
  {
    title: '危险操作',
    buttons: [['把存档重置成新号', 'reset_player', 'danger']],
    hint: '每次点作弊前都会自动存一份快照，回滚用右上角的下拉框。',
  },
];

function buildCheats() {
  const box = $('pl-cheats');
  box.textContent = '';
  CHEATS.forEach((group) => {
    box.appendChild(el('h4', null, group.title));
    (group.rows || []).forEach((row) => {
      const line = el('div', 'row2');
      line.appendChild(el('label', null, row.label));
      const input = el('input');
      input.id = row.id;
      input.type = row.type;
      input.value = row.value;
      line.appendChild(input);
      box.appendChild(line);
    });
    const mk = ([label, spec, kind]) => {
      const b = el('button', kind || 'ghost', label);
      b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          let action = spec, args = {};
          if (typeof spec === 'function') {
            const r = spec();
            action = r[0]; args = r[1];
          }
          const out = await post('/api/cheat', { account: state.account, action: action, args: args });
          if (out.ok) {
            toast(out.detail || '已执行', 'ok');
            await loadPlayer(state.account);
            await loadPlayers();
            await loadBackups();
          } else {
            toast(out.error || '执行失败', 'err');
          }
        } catch (e) {
          toast(String(e), 'err');
        } finally {
          b.disabled = false;
        }
      });
      box.appendChild(b);
    };
    (group.buttons || []).forEach(mk);
    (group.buttons2 || []).forEach((spec) => mk(typeof spec[1] === 'function' ? spec : [spec[0], spec[1]]));
    if (group.hint) box.appendChild(el('div', 'hint', group.hint));
  });
}

async function loadPlayers() {
  const data = await api('/api/players');
  if (!data.ok) { toast(data.error || '拉玩家列表失败', 'err'); return; }
  state.players = data.players || [];
  const sel = $('pl-account');
  sel.textContent = '';
  state.players.forEach((p) => {
    const opt = el('option', null, p.account + '  (lv' + p.lv + ' · 军士 ' + p.soldiers + ' · 关卡 ' + p.levelsPassed + ')');
    opt.value = p.account;
    sel.appendChild(opt);
  });
  if (state.players.length) {
    if (!state.account || !state.players.some((p) => p.account === state.account)) {
      state.account = state.players[0].account;
    }
    sel.value = state.account;
    await loadPlayer(state.account);
  }
}

async function loadPlayer(account) {
  const data = await api('/api/player?account=' + encodeURIComponent(account));
  if (!data.ok) { toast(data.error || '拉存档失败', 'err'); return; }
  $('pl-json').value = JSON.stringify(data.player, null, 2);
  renderSummary(state.players.find((p) => p.account === account));
}

function renderSummary(summary) {
  if (!summary) { $('pl-summary').textContent = ''; return; }
  $('pl-summary').textContent =
    '指挥部 lv' + summary.lv + ' · exp ' + summary.curExp +
    ' · 军士 ' + summary.soldiers + ' 个' +
    ' · 已通关 ' + summary.levelsPassed + '（三星 ' + summary.levels3Star + '）' +
    ' · 已领任务 ' + summary.questsDone +
    ' · 队伍 ' + summary.teams + ' 支' +
    ' · roster v' + summary.rosterVersion;
}

async function savePlayer() {
  let parsed;
  try {
    parsed = JSON.parse($('pl-json').value);
  } catch (e) {
    toast('JSON 格式错误：' + e.message, 'err');
    return;
  }
  const out = await post('/api/player/save', { account: state.account, player: parsed });
  if (out.ok) {
    toast('已保存（改前快照 ' + out.snapshot + '）', 'ok');
    await loadPlayers();
    await loadBackups();
  } else {
    toast(out.error || '保存失败', 'err');
  }
}

async function loadBackups() {
  const data = await api('/api/backups');
  if (!data.ok) return;
  const sel = $('pl-backups');
  sel.textContent = '';
  (data.backups || []).forEach((b) => {
    const opt = el('option', null, b.name.replace(/^players-/, '') + '  (' + Math.round(b.size / 1024) + 'KB)');
    opt.value = b.name;
    sel.appendChild(opt);
  });
}

// ---------------------------------------------------------------- 日志面板

// 级别只保留五档：debug / info / warning / error / fatal。
// 别名在这里归一（服务端 devbus 也做了一次同样的映射），这样不管事件从哪来
// 面板上都只有这五个词。
const LEVEL_ALIAS = { critical: 'fatal', crit: 'fatal', warn: 'warning', err: 'error', notice: 'info', trace: 'debug' };

function addLog(source, tag, level, body, kind) {
  const cls = kind || source;
  // 级别一律归一成**小写五档**再存：服务端以前发的是 Python 的 `levelname`（大写、
  // 且 CRITICAL 与 fatal 是两套名字），客户端那份是小写，并排显示很乱。
  const raw = String(level || 'info').toLowerCase();
  const entry = { source: cls, tag: tag, level: LEVEL_ALIAS[raw] || raw, body: body, ts: Date.now() / 1000 };
  state.logs.push(entry);
  if (state.logs.length > state.logsMax) state.logs.shift();
  if (matchesLogFilter(entry)) appendLogLine(entry);
}

// 日志分级。
//
// 服务端行自带 Python 的 `levelname`（DEBUG/INFO/WARNING/ERROR/CRITICAL，见 devbus 的
// `_LogBridge`），**客户端行是从 logcat 收的原文、本身没有级别**，只能按内容猜 ——
// 不猜的话「按级别过滤」对最吵的那批探针行完全无效。
//
// 级别排序。「按级别过滤」用的就是它。
// 服务端行自带 Python 的 levelname（DEBUG/INFO/WARNING/ERROR/CRITICAL，见 devbus 的
// `_LogBridge`）；客户端行是从 logcat 收的原文、本身没有级别，由 `guessClientLevel()` 补。
const LEVEL_RANK = { debug: 10, info: 20, warning: 30, error: 40, fatal: 50, critical: 50 };

function levelRank(lv) {
  return LEVEL_RANK[String(lv || 'info').toLowerCase()] || 20;
}

// 按**行首**判级别。顺序：
//   1) 行首（可以带 `|` 前缀）就是级别词 → 用它：DEBUG/TRACE/INFO/NOTICE/WARN/ERROR/FATAL…
//   2) 行首是探针的逐包追踪标签（CRYPT / GAME REQ / GAME RESP / REQ / RES / …）→ debug
//   3) 行里有 JS 异常特征 → error
//   4) 都不是 → info
//
// ⚠️ **永远返回一个级别，不留空**。留空的话「按级别过滤」对这类行直接失效
// （`entry.level` 为空时既不算 debug 也不算 error，怎么筛都不对）。
function guessClientLevel(line) {
  const s = String(line || '').trim();
  const head = s.replace(/^[|\s]+/, '');

  const m = head.match(/^(DEBUG|TRACE|INFO|NOTICE|WARN(?:ING)?|ERROR|ERR|FATAL|CRITICAL|CRIT|ASSERT)\b/i);
  if (m) {
    const w = m[1].toUpperCase();
    if (w === 'TRACE' || w === 'DEBUG') return 'debug';
    if (w === 'INFO' || w === 'NOTICE') return 'info';
    if (w.startsWith('WARN')) return 'warning';
    if (w === 'ERROR' || w === 'ERR') return 'error';
    return 'fatal';
  }
  if (/^(CRYPT|GAME REQ|GAME RESP|REQ|RES|SEND|RECV|POPUP|HOOK)\b/.test(head)) return 'debug';
  if (/\b(ERROR|TypeError|ReferenceError|SyntaxError|is undefined|cannot read)\b/i.test(s)) return 'error';
  if (/\bWARN(ING)?\b/i.test(s)) return 'warning';
  return 'info';
}

function matchesLogFilter(entry) {
  const mode = ($$('input[name=lgsrc]').find((n) => n.checked) || {}).value || 'all';
  if (mode !== 'all' && entry.source !== mode) return false;
  const min = $('lg-level').value;
  if (min && levelRank(entry.level) < levelRank(min)) return false;
  const needle = $('lg-filter').value.trim().toLowerCase();
  if (needle && (entry.body + ' ' + entry.tag).toLowerCase().indexOf(needle) < 0) return false;
  return true;
}

function logLineNode(entry) {
  const line = el('div', 'ln ' + entry.source + ' ' + (entry.level || ''));
  // ⚠️ 来源后面**一律**跟 `/级别`。以前只有 server 那支拼了 level，
  // 客户端行只显示 `client` —— 按级别过滤是生效了，但看不出这行是什么级别。
  const label = (entry.source === 'server' ? entry.tag : entry.source)
    + (entry.level ? '/' + entry.level : '');
  line.appendChild(el('span', 'tag', label));
  line.appendChild(el('span', 'body', entry.body));
  return line;
}

function appendLogLine(entry) {
  const out = $('lg-out');
  const atBottom = out.scrollTop + out.clientHeight >= out.scrollHeight - 40;
  out.appendChild(logLineNode(entry));
  while (out.childElementCount > state.logsMax) out.removeChild(out.firstChild);
  if ($('lg-autoscroll').checked && atBottom) out.scrollTop = out.scrollHeight;
}

function redrawLogs() {
  const out = $('lg-out');
  out.textContent = '';
  state.logs.filter(matchesLogFilter).forEach((entry) => out.appendChild(logLineNode(entry)));
  out.scrollTop = out.scrollHeight;
}

async function logcat(action) {
  const data = await post('/api/logcat', { action: action });
  if (data.ok) {
    const s = data.logcat || {};
    toast('logcat: ' + (s.running ? '运行中，已收 ' + s.lines + ' 行' : '已停止') + (s.error ? ' | ' + s.error : ''));
  } else {
    toast(data.error || '操作失败', 'err');
  }
}

// ---------------------------------------------------------------- 数据面板

async function loadTables(force) {
  const data = await api('/api/tables' + (force ? '?refresh=1' : ''));
  if (!data.ok) { toast(data.error || '拉表名失败', 'err'); return; }
  const sel = $('dt-table');
  sel.textContent = '';
  const addGroup = (label, names, prefix) => {
    if (!names || !names.length) return;
    const g = el('optgroup');
    g.label = label;
    names.forEach((n) => {
      const o = el('option', null, n);
      o.value = n;
      g.appendChild(o);
    });
    sel.appendChild(g);
  };
  addGroup('服务端已抽取', data.extracted, 'x');
  addGroup('客户端 (需要游戏在跑)', data.client, 'c');
  state.tablesLoaded = true;
  if (!data.probeAlive) toast('客户端探针不在线，客户端表名可能是空的（游戏没开？）', 'err');
}

async function renderData() {
  const kind = $('dt-kind').value;
  const q = $('dt-q').value.trim();
  const limit = Number($('dt-limit').value) || 60;
  $('dt-table').hidden = kind !== 'table';
  $('dt-refresh-tables').hidden = kind !== 'table';
  // 三个视图**都能翻页**了：「路由清单」/「文案对照」在本地切片（本来就一次性全量拿到），
  // 「表浏览」走服务端分页。所以「每页 / 上一页 / 下一页」一律显示、一律有用。
  // 以前 routes/dict 两支提前 return、压根不看 offset，按钮点了没反应。

  const out = $('dt-out');
  out.textContent = '';

  // 本地分页：切出当前页 + 统一 dt-info 文案
  const slicePage = (all) => all.slice(state.dtOffset, state.dtOffset + limit);
  const pageInfo = (total, shown) =>
    '第 ' + (total ? state.dtOffset + 1 : 0) + '–' + (state.dtOffset + shown) +
    ' 条 / 共 ' + total + ' 条';

  if (kind === 'routes') {
    const data = await api('/api/routes');
    if (!data.ok) { toast(data.error || '失败', 'err'); return; }
    const all = (data.routes || []).filter((r) =>
      !q || (r.route + ' ' + r.handler + ' ' + r.doc).toLowerCase().indexOf(q.toLowerCase()) >= 0);
    const page = slicePage(all);
    $('dt-info').textContent = (q ? '命中 ' + all.length + ' 条，' : '') + pageInfo(all.length, page.length);
    out.appendChild(buildTable(['route', '实现', '说明'],
      page.map((r) => [r.route, r.handler, r.doc])));
    return;
  }

  if (kind === 'dict') {
    if (!q) { $('dt-info').textContent = '输入关键词再搜（比如 指挥部 / 军士 / 未开启）'; return; }
    // `/api/dict` 没有 offset，所以一次多要一点、在本地切页
    const data = await api('/api/dict?q=' + encodeURIComponent(q) + '&limit=2000');
    if (!data.ok) { toast(data.error || '失败', 'err'); return; }
    const all = Object.keys(data.rows || {});
    const page = slicePage(all);
    $('dt-info').textContent = '命中 ' + all.length + ' 条，' + pageInfo(all.length, page.length);
    out.appendChild(buildTable(['id', '文案'], page.map((k) => [k, String(data.rows[k])])));
    return;
  }

  // 表浏览
  if (!state.tablesLoaded) await loadTables(false);
  const name = $('dt-table').value;
  if (!name) { $('dt-info').textContent = '没有可浏览的表'; return; }
  const data = await api('/api/table?name=' + encodeURIComponent(name) +
    '&q=' + encodeURIComponent(q) + '&limit=' + limit + '&offset=' + state.dtOffset);
  if (!data.ok) { toast(data.error || '失败', 'err'); return; }
  const keys = data.keys || Object.keys(data.rows || {});
  $('dt-info').textContent = (data.source === 'extracted' ? '[本地] ' : '[客户端] ') +
    '共 ' + data.total + ' 条，第 ' + (state.dtOffset + 1) + '–' + (state.dtOffset + keys.length) + ' 条';
  out.appendChild(buildTable(['key', '内容'], keys.map((k) => [k, data.rows[k]])));
}

function buildTable(headers, rows) {
  const t = el('table');
  const thead = el('thead');
  const tr = el('tr');
  headers.forEach((h) => tr.appendChild(el('th', null, h)));
  thead.appendChild(tr);
  t.appendChild(thead);
  const tb = el('tbody');
  rows.forEach((cells) => {
    const r = el('tr');
    cells.forEach((c, i) => {
      const td = el('td', i === 0 ? 'k' : (typeof c === 'string' ? 'v str' : 'v'));
      td.textContent = typeof c === 'string' ? c : pretty(c);
      r.appendChild(td);
    });
    // 默认一行省略（见 .dt-out td 的 CSS），点整行展开 / 收起看完整内容。
    // 展开后 td 换回 pre-wrap，`pretty()` 打出来的缩进和换行才可读。
    r.title = '点击展开 / 收起';
    r.addEventListener('click', () => r.classList.toggle('expanded'));
    tb.appendChild(r);
  });
  t.appendChild(tb);
  return t;
}

// ---------------------------------------------------------------- 初始化

// ---------------------------------------------------------------- 页签

// 记住停在哪个页签：以前刷新总是弹回「流量」，正看着日志就被拽走了。
const TAB_KEY = 'dt-tab';

function activateTab(name) {
  if (!name) return;
  const btn = $$('#tabs button').find((b) => b.dataset.tab === name);
  const sect = $('tab-' + name);
  if (!btn || !sect) return;      // 存的名字不认识（页签改过名）→ 保持 HTML 里的默认
  $$('#tabs button').forEach((x) => x.classList.toggle('on', x === btn));
  $$('.tab').forEach((s) => s.classList.toggle('on', s === sect));
  if (name === 'data') renderData();
  if (name === 'jsd') { jsdStatus(); jsdLoadSources(); }
  stickToBottom(name);
}

function setupTabs() {
  $$('#tabs button').forEach((b) => {
    b.addEventListener('click', () => {
      activateTab(b.dataset.tab);
      try { localStorage.setItem(TAB_KEY, b.dataset.tab); } catch (e) { /* 隐私模式算了 */ }
    });
  });
  let saved = null;
  try { saved = localStorage.getItem(TAB_KEY); } catch (e) { saved = null; }
  activateTab(saved);             // saved 为空时什么都不做，用 HTML 的默认页签
}

// 控制台输出滚到底。
// 和日志/流量那两个面板统一成同一个开关（`cc-autoscroll`）：
// 不想被拽下去看历史时，取消勾选就行。
// ⚠️ 以前 `runConsole()` 里是无条件滚的，而**从 `script\repl.py` 发过来的**
// （`addConsole`）**一次都没滚** —— 所以用命令行发命令时输出会停在上面。
function stickConsole() {
  if (!$('cc-autoscroll').checked) return;
  const out = $('cc-out');
  out.scrollTop = out.scrollHeight;
}

// 把某个页签里可滚动的面板贴到底（仅当该面板的「自动滚动」勾着）。
// `requestAnimationFrame` 是因为要先等浏览器完成布局，元素才有真实高度。
function stickToBottom(tab) {
  requestAnimationFrame(() => {
    if (tab === 'traffic' && $('tr-autoscroll').checked) {
      const list = TR_LIST();
      list.scrollTop = list.scrollHeight;
    }
    if (tab === 'logs' && $('lg-autoscroll').checked) {
      const out = $('lg-out');
      out.scrollTop = out.scrollHeight;
    }
    if (tab === 'console') stickConsole();
  });
}

function setupTop() {
  $('btn-pause').addEventListener('click', () => {
    state.paused = !state.paused;
    $('btn-pause').textContent = state.paused ? '继续' : '暂停';
    $('pill-paused').hidden = !state.paused;
  });
  onClick('btn-clearbus', async () => {
    await api('/api/events/clear');
    state.since = 0;
    toast('事件缓冲已清空', 'ok');
  });
}

function setupTraffic() {
  ['tr-filter', 'tr-onlybad', 'tr-onlyunknown'].forEach((id) => {
    $(id).addEventListener('input', redrawTraffic);
    $(id).addEventListener('change', redrawTraffic);
  });
  onClick('tr-clear', async () => {
    state.traffic = [];
    redrawTraffic();
    $('tr-detail').textContent = '';
    $('tr-detail').appendChild(el('p', 'empty', '点左边任意一条看请求 / 回包'));
  });
  onClick('tr-export', async () => {
    const blob = new Blob([JSON.stringify(state.traffic, null, 2)], { type: 'application/json' });
    const a = el('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'devtools-traffic-' + Date.now() + '.json';
    a.click();
    URL.revokeObjectURL(a.href);
  });
}

function setupConsole() {
  $('cc-run').addEventListener('click', runConsole);
  $('cc-code').addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); runConsole(); return; }
    if (e.key === 'ArrowUp' && (e.ctrlKey || $('cc-code').value.indexOf('\n') < 0)) {
      if (!consoleHistory.length) return;
      e.preventDefault();
      historyIdx = Math.max(0, historyIdx - 1);
      $('cc-code').value = consoleHistory[historyIdx] || '';
    }
    if (e.key === 'ArrowDown' && (e.ctrlKey || $('cc-code').value.indexOf('\n') < 0)) {
      if (!consoleHistory.length) return;
      e.preventDefault();
      historyIdx = Math.min(consoleHistory.length, historyIdx + 1);
      $('cc-code').value = consoleHistory[historyIdx] || '';
    }
  });
  onClick('cc-clear', async () => { $('cc-out').textContent = ''; });
  buildSnippets();
}

function setupPlayer() {
  buildCheats();
  $('pl-account').addEventListener('change', async () => {
    state.account = $('pl-account').value;
    await loadPlayer(state.account);
  });
  onClick('pl-reload', () => loadPlayer(state.account));
  onClick('pl-save', savePlayer);
  onClick('pl-format', async () => {
    try {
      $('pl-json').value = JSON.stringify(JSON.parse($('pl-json').value), null, 2);
    } catch (e) { toast('JSON 格式错误：' + e.message, 'err'); }
  });
  onClick('pl-snapshot', async () => {
    const data = await post('/api/backup', { label: state.account });
    if (data.ok) { toast('快照已建：' + data.name, 'ok'); await loadBackups(); }
    else toast(data.error || '失败', 'err');
  });
  onClick('pl-restore', async () => {
    const name = $('pl-backups').value;
    if (!name) return;
    if (!confirm('回滚到 ' + name + '？当前的存档会先自动存一份。')) return;
    const data = await post('/api/restore', { name: name });
    if (data.ok) { toast('已回滚到 ' + data.restored, 'ok'); await loadPlayers(); await loadBackups(); }
    else toast(data.error || '失败', 'err');
  });
}

function setupLogs() {
  ['lg-filter'].forEach((id) => $(id).addEventListener('input', redrawLogs));
  $('lg-level').addEventListener('change', redrawLogs);
  $$('input[name=lgsrc]').forEach((n) => n.addEventListener('change', redrawLogs));
  onClick('lg-clear', async () => { state.logs = []; $('lg-out').textContent = ''; });
  onClick('lg-logcat-start', () => logcat('start'));
  onClick('lg-logcat-stop', () => logcat('stop'));
  onClick('lg-logcat-clear', () => logcat('clear'));
  onClick('lg-cc-help', async () => {
    toast(
      'console.log 会显示，但它在 JSB 里是 non-configurable + non-writable —— ' +
      '客户端没法从 JS 侧包一层（赋值静默失败、defineProperty 直接抛异常），\n' +
      '所以 probe.js 的 hookLogging 里 console.* 那几行一直是空转，只有 cc.log 真的被包上了。\n' +
      '调试台直接从 logcat 收原生输出，来源标成 console（顶部单另有一档过滤）。\n\n' +
      '几种写法的差别：\n' +
      '  console.log(x)         -> 进「console.log」档（原文，无前缀）\n' +
      '  cc.log(x)              -> 进「探针」档，带 GAMELOG cc.log: 前缀\n' +
      '__oppaiHook__.log(x)   -> 进「探针」档，原文\n\n' +
      '⚠️ 原生 console.log 只吃一个参数：console.log("a", b) 会抛\n' +
      '   "js_console_log : wrong number of arguments"，自己 join 一下。',
      'ok');
  });
}

function setupData() {
  $('dt-kind').addEventListener('change', () => { state.dtOffset = 0; renderData(); });
  $('dt-table').addEventListener('change', () => { state.dtOffset = 0; renderData(); });
  onClick('dt-refresh-tables', async () => { await loadTables(true); toast('已刷新客户端表名', 'ok'); });
  let timer = null;
  $('dt-q').addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.dtOffset = 0; renderData(); }, 250);
  });
  onClick('dt-prev', () => {
    state.dtOffset = Math.max(0, state.dtOffset - (Number($('dt-limit').value) || 60));
    renderData();
  });
  onClick('dt-next', () => {
    state.dtOffset += Number($('dt-limit').value) || 60;
    renderData();
  });
}

// ---------------------------------------------------------------- 引擎调试器

const JSD_STATE_CLASS = { idle: '', attached: 'warn', paused: 'warn', running: 'good', error: 'bad' };
const JSD_STATE_TEXT = {
  idle: '未连接', attached: '已连接', paused: '已暂停（游戏冻住了）',
  running: '运行中', error: '出错',
};
let jsdSelSource = null;
let jsdSelFrame = null;
let jsdPoll = null;

async function jsdStatus() {
  const data = await api('/api/jsd/status');
  if (!data.ok) return null;

  const pill = $('jd-state');
  pill.className = 'pill ' + (JSD_STATE_CLASS[data.state] || '');
  pill.innerHTML = '状态 <b>' + (JSD_STATE_TEXT[data.state] || data.state) + '</b>';

  $('jd-connect').disabled = data.connected;
  $('jd-disconnect').disabled = !data.connected;
  ['jd-resume', 'jd-pause', 'jd-step-in', 'jd-step-over', 'jd-step-out', 'jd-bp']
    .forEach((id) => { $(id).disabled = !data.connected; });
  $('jd-resume').disabled = data.state !== 'paused';
  $('jd-pause').disabled = data.state !== 'running';
  ['jd-step-in', 'jd-step-over', 'jd-step-out'].forEach((id) => {
    $(id).disabled = data.state !== 'paused';
  });
  $('jd-bp').disabled = data.state !== 'paused';

  const why = data.why ? JSON.stringify(data.why) : '';
  $('jd-why').textContent = data.connected
    ? (data.state === 'paused' ? '暂停原因 ' + why : '游戏在跑，随时可以「暂停」')
    : '没连上。第一次连会**把游戏冻住**（attach 会立刻暂停），这是正常的。';

  $('jd-note').textContent = data.connected
    ? 'ctrl+enter 求值 · 断点只能在暂停时下'
    : '';

  if (data.state === 'paused' && data.frames) renderFrames(data.frames);
  if (data.breakpoints) renderBreakpoints(data.breakpoints);
  return data;
}

function renderFrames(frames) {
  const box = $('jd-frames');
  box.textContent = '';
  if (!frames.length) {
    box.appendChild(el('div', 'item', '（没有栈帧 —— 如果你用「暂停」停的，'
      + '那是停在调试器自己的循环里，拿不到游戏栈帧；让断点命中才有）'));
    return;
  }
  frames.forEach((f) => {
    const node = el('div', 'item frame' + (jsdSelFrame === f.actor ? ' sel' : ''));
    const head = el('span');
    head.appendChild(el('span', 'dim', '#' + f.depth));
    head.appendChild(el('strong', null, f.name));
    node.appendChild(head);
    node.appendChild(el('div', 'loc', (f.url || '?') + ':' + f.line));
    node.addEventListener('click', () => { jsdSelFrame = f.actor; renderFrames(frames); });
    box.appendChild(node);
  });
}

function renderBreakpoints(bps) {
  const box = $('jd-bps');
  box.textContent = '';
  if (!bps.length) { box.appendChild(el('div', 'item', '（还没有断点）')); return; }
  bps.forEach((b) => {
    const node = el('div', 'item');
    node.appendChild(el('span', 'dim', '●'));
    node.textContent = '';
    node.appendChild(el('span', 'dim', '●'));
    node.appendChild(el('span', null, (b.url || '').split(/[\\/]/).slice(-2).join('/') + ':' + b.line));
    node.title = (b.url || '') + ':' + b.line;
    box.appendChild(node);
  });
}

async function jsdLoadSources() {
  const q = $('jd-filter').value.trim();
  const data = await api('/api/jsd/sources?q=' + encodeURIComponent(q) + '&limit=400');
  const box = $('jd-sources');
  box.textContent = '';
  if (!data.ok) {
    box.appendChild(el('div', 'item', data.error || '拉脚本列表失败（先「连接」）'));
    return;
  }
  const list = data.sources || [];
  box.appendChild(el('div', 'item', '共 ' + data.total + ' 个匹配，显示 ' + list.length));
  list.forEach((s) => {
    const node = el('div', 'item' + (jsdSelSource === s.url ? ' sel' : ''));
    // jsc 的 url 是构建机绝对路径，尾巴两段最有辨识度
    const tail = (s.url || '').replace(/\\/g, '/').split('/').slice(-3).join('/');
    node.appendChild(el('span', 'dim', s.actor));
    node.appendChild(el('span', null, tail));
    node.title = s.url;
    node.addEventListener('click', () => {
      jsdSelSource = s.url;
      jsdLoadSources();
    });
    box.appendChild(node);
  });
}

async function jsdEval() {
  const expr = $('jd-expr').value;
  if (!expr.trim()) return;
  const data = await post('/api/jsd/eval', { expression: expr, frame: jsdSelFrame });
  const out = $('jd-out');
  const item = el('div', 'cc-item' + (data.ok ? '' : ' err'));
  item.appendChild(el('div', 'in', '> ' + expr));
  item.appendChild(el('div', 'out', data.ok
    ? JSON.stringify(data.result, null, 2)
    : (data.error || '失败')));
  out.appendChild(item);
  out.scrollTop = out.scrollHeight;
}

async function jsdControl(action, limit) {
  const data = await post('/api/jsd/control', { action: action, limit: limit });
  if (!data.ok) toast(data.error || '失败', 'err');
  else if (action === 'resume') toast('继续运行', 'ok');
  await jsdStatus();
}

function setupJsd() {
  onClick('jd-connect', async () => {
    const data = await post('/api/jsd/connect', {});
    if (!data.ok) { toast(data.error || '连接失败', 'err'); return; }
    toast('已连接 —— 注意游戏现在是冻结的，点「继续」才恢复', 'ok');
    await jsdStatus();
    await jsdLoadSources();
  });
  onClick('jd-disconnect', async () => {
    await post('/api/jsd/disconnect', {});
    toast('已断开，游戏继续', 'ok');
    await jsdStatus();
  });
  onClick('jd-resume', () => jsdControl('resume'));
  onClick('jd-pause', () => jsdControl('pause'));
  onClick('jd-step-in', () => jsdControl('resume', 'step'));
  onClick('jd-step-over', () => jsdControl('resume', 'next'));
  onClick('jd-step-out', () => jsdControl('resume', 'finish'));
  onClick('jd-refresh-src', jsdLoadSources);
  let timer = null;
  $('jd-filter').addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(jsdLoadSources, 300);
  });
  onClick('jd-bp', async () => {
    if (!jsdSelSource) { toast('先在左边选一个脚本', 'err'); return; }
    const line = Number($('jd-line').value) || 1;
    const data = await post('/api/jsd/bp', { url: jsdSelSource, line: line });
    if (!data.ok) { toast(data.error || '下断点失败', 'err'); return; }
    toast('断点已下：' + jsdSelSource.split(/[\\/]/).slice(-2).join('/') + ':' + line, 'ok');
    await jsdStatus();
  });
  $('jd-eval').addEventListener('click', jsdEval);
  $('jd-expr').addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); jsdEval(); }
  });
}

async function init() {
  setupTabs();
  setupTop();
  setupTraffic();
  setupConsole();
  setupPlayer();
  setupLogs();
  setupData();
  setupJsd();

  // 先把缓冲里的历史一次性拉出来（timeout=0 = 立刻返回），再转长轮询
  try {
    const data = await api('/api/events?since=0&timeout=0&limit=2000');
    if (data.ok) {
      state.since = data.seq;
      (data.events || []).forEach(routeEvent);
    }
  } catch (e) { /* 忽略：下面的轮询会重试 */ }

  // 这两个是「拉一下试试」，服务端没起来不该算 fatal
  try { await refreshOverview(); } catch (e) { /* 下面的轮询会重试 */ }
  try { await loadBackups(); } catch (e) { /* 同上 */ }
  // ⚠️⚠️ **后台轮询一律走 poll()，不要直接 setInterval(asyncFn)**。
  // `setInterval(refreshOverview, 5000)` 里 refreshOverview 是 async 的，
  // 服务端一重启 fetch 就 reject —— setInterval 不管返回值，于是每次都是一个
  // **unhandledrejection**，被上面那个兜底 banner 记成"fatal"，
  // 每 5 秒往页顶那条红条里追加一行，一直挂在那儿关不掉。
  poll(refreshOverview, 5000);
  poll(() => {
    // 只在「调试器」页签打开时才轮询，免得平时也一直打服务端
    if ($('tab-jsd').classList.contains('on')) return jsdStatus();
  }, 1000);
  pump();
}

/** 后台轮询：出错吞掉就行 —— 服务端重启时 fetch reject 是**预期内**的，不是 fatal。 */
function poll(fn, ms) {
  const tick = async () => {
    try { await fn(); } catch (e) { /* 下一轮会重试 */ }
  };
  tick();
  return setInterval(tick, ms);
}

init();
