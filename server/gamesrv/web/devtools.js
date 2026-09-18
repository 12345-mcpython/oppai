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
(function () {
  function banner(title, detail) {
    try {
      var box = document.getElementById('fatal');
      if (!box) {
        box = document.createElement('div');
        box.id = 'fatal';
        box.style.cssText = 'position:fixed;left:0;right:0;top:0;z-index:999;' +
          'background:#5a2329;border-bottom:2px solid #e06c75;color:#ffd8d8;' +
          'padding:8px 14px;font:12px/1.6 Consolas,monospace;white-space:pre-wrap';
        document.body.appendChild(box);
      }
      box.textContent += (box.textContent ? '\n' : '') + title + ' :: ' + detail;
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
function toast(message, kind) {
  let node = $('toast');
  if (!node) {
    node = el('div', 'toast');
    node.id = 'toast';
    document.body.appendChild(node);
  }
  node.className = 'toast' + (kind ? ' ' + kind : '');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, kind === 'err' ? 8000 : 3500);
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
      state.since = data.seq;
      for (const ev of data.events || []) routeEvent(ev);
      if (data.stats) updateStats(data.stats);
    } catch (e) {
      // 服务端重启 / 网络断开：歇一下再重连，别把控制台刷满
      await sleep(1500);
    }
  }
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function routeEvent(ev) {
  switch (ev.kind) {
    case 'traffic': addTraffic(ev); break;
    case 'client': addLog('client', ev.source || 'logcat', '', ev.line); break;
    case 'server': addLog('server', ev.logger || 'server', ev.level, ev.message); break;
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
  $('cc-out').scrollTop = $('cc-out').scrollHeight;

  const r = await post('/api/console', { code: code, timeout: timeout });
  if (r.ok) {
    out.textContent = r.value;
  } else {
    item.classList.add('err');
    out.textContent = r.error || '失败';
  }
  if (r.ms !== undefined) item.appendChild(el('div', 'meta', r.ms + 'ms'));
  $('cc-out').scrollTop = $('cc-out').scrollHeight;
}

function addConsole(ev) {
  // 从 repl.py 之外的入口跑的（比如 tools/repl.py）也显示出来
  const item = el('div', 'cc-item' + (ev.ok ? '' : ' err'));
  item.appendChild(el('div', 'in', '> ' + ev.code));
  item.appendChild(el('div', 'out', ev.value));
  item.appendChild(el('div', 'meta', (ev.ms || 0) + 'ms（来自 ' + (ev.source || '客户端 REPL') + '）'));
  $('cc-out').appendChild(item);
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

function addLog(source, tag, level, body, kind) {
  const cls = kind || source;
  const entry = { source: cls, tag: tag, level: level, body: body, ts: Date.now() / 1000 };
  state.logs.push(entry);
  if (state.logs.length > state.logsMax) state.logs.shift();
  if (matchesLogFilter(entry)) appendLogLine(entry);
}

function matchesLogFilter(entry) {
  const mode = ($$('input[name=lgsrc]').find((n) => n.checked) || {}).value || 'all';
  if (mode !== 'all' && entry.source !== mode) return false;
  const needle = $('lg-filter').value.trim().toLowerCase();
  if (needle && (entry.body + ' ' + entry.tag).toLowerCase().indexOf(needle) < 0) return false;
  return true;
}

function logLineNode(entry) {
  const line = el('div', 'ln ' + entry.source + ' ' + (entry.level || ''));
  const label = entry.source === 'server'
    ? entry.tag + (entry.level ? '/' + entry.level : '')
    : entry.source;
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
  $('dt-limit-wrap').hidden = kind === 'routes';

  const out = $('dt-out');
  out.textContent = '';

  if (kind === 'routes') {
    const data = await api('/api/routes');
    if (!data.ok) { toast(data.error || '失败', 'err'); return; }
    const rows = (data.routes || []).filter((r) =>
      !q || (r.route + ' ' + r.handler + ' ' + r.doc).toLowerCase().indexOf(q.toLowerCase()) >= 0);
    $('dt-info').textContent = rows.length + ' / ' + (data.routes || []).length + ' 条路由';
    out.appendChild(buildTable(['route', '实现', '说明'], rows.map((r) => [r.route, r.handler, r.doc])));
    return;
  }

  if (kind === 'dict') {
    if (!q) { $('dt-info').textContent = '输入关键词再搜（比如 指挥部 / 军士 / 未开启）'; return; }
    const data = await api('/api/dict?q=' + encodeURIComponent(q) + '&limit=' + limit);
    if (!data.ok) { toast(data.error || '失败', 'err'); return; }
    const keys = Object.keys(data.rows || {});
    $('dt-info').textContent = keys.length + ' 条命中';
    out.appendChild(buildTable(['id', '文案'], keys.map((k) => [k, String(data.rows[k])])));
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
    tb.appendChild(r);
  });
  t.appendChild(tb);
  return t;
}

// ---------------------------------------------------------------- 初始化

function setupTabs() {
  $$('#tabs button').forEach((b) => {
    b.addEventListener('click', () => {
      $$('#tabs button').forEach((x) => x.classList.toggle('on', x === b));
      $$('.tab').forEach((s) => s.classList.toggle('on', s.id === 'tab-' + b.dataset.tab));
      if (b.dataset.tab === 'data') renderData();
    });
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

async function init() {
  setupTabs();
  setupTop();
  setupTraffic();
  setupConsole();
  setupPlayer();
  setupLogs();
  setupData();

  // 先把缓冲里的历史一次性拉出来（timeout=0 = 立刻返回），再转长轮询
  try {
    const data = await api('/api/events?since=0&timeout=0&limit=2000');
    if (data.ok) {
      state.since = data.seq;
      (data.events || []).forEach(routeEvent);
    }
  } catch (e) { /* 忽略：下面的轮询会重试 */ }

  await refreshOverview();
  await loadBackups();
  setInterval(refreshOverview, 5000);
  pump();
}

init();
