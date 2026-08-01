/* ══════════════════════════════════════════════════════════════════════════
   CodeTails — a browser front end for the Claude Code CLI.
   Vanilla JS, no build step, no CDN. Talks to server.py over fetch + SSE.
   ══════════════════════════════════════════════════════════════════════════ */
'use strict';

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  boot: null,
  cfg: null,
  session: null,          // live session meta
  mode: 'empty',          // empty | live | history
  history: null,
  es: null,
  blocks: new Map(),
  commands: [],
  projects: [],
  openProject: null,
  stick: true,
  lastSeq: 0,
  turnStart: null,
  outTokens: 0,
  verbIdx: 0,
  denials: new Set(),
  lastTs: 0,
  coarse: matchMedia('(pointer: coarse)').matches,
};

/* ─────────────────────────────── helpers ─────────────────────────────── */
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
}

function ago(ts) {
  const s = Math.max(0, (Date.now() / 1000) - ts);
  if (s < 60) return `${Math.floor(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

const fmtTok = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M'
  : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n | 0);

const fmtCost = (c) => c == null ? '' : '$' + (c < 0.01 ? c.toFixed(4) : c.toFixed(2));

function rel(p) {
  if (!p) return '';
  const cwd = state.session?.cwd || state.history?.meta?.cwd;
  if (cwd && p.startsWith(cwd + '/')) return p.slice(cwd.length + 1);
  const home = state.boot?.home;
  if (home && p.startsWith(home)) return '~' + p.slice(home.length);
  return p;
}

function toast(msg, kind = '') {
  const t = el('div', 'toast ' + kind, esc(msg));
  $('#toasts').append(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transform = 'translateY(6px)'; }, 2400);
  setTimeout(() => t.remove(), 2900);
}

async function api(path, opts = {}) {
  const r = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin', ...opts,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

/* ─────────────────────────────── skins ───────────────────────────────── */
const LIGHT = new Set(['paper']);

function themeObject(name) {
  return window.CT_THEMES[name] || state.cfg?.custom_themes?.[name] || window.CT_THEMES.clay;
}

function applyTheme(name, overrides) {
  const base = { ...themeObject(name), ...(overrides || {}) };
  const root = document.documentElement;
  const all = [...window.CT_TOKENS, ...window.CT_TOKENS_MODERN];
  for (const [k] of all) root.style.removeProperty('--' + k);
  for (const [k] of all) if (base[k]) root.style.setProperty('--' + k, base[k]);
  root.dataset.theme = name;
  document.body.dataset.surface = base.style || 'terminal';
  document.body.dataset.holo = base.holo ? '1' : '0';
  document.body.dataset.space = base.space ? '1' : '0';
  document.body.dataset.thread = base.thread ? '1' : '0';
  paintBackdrop(base);
  const lum = luminance(base.bg || '#000');
  document.body.dataset.light = lum > 0.5 ? '1' : '0';
  document.querySelector('meta[name=theme-color]')?.setAttribute('content', base.bg || '#000');
  state.theme = base;
}

/* ── holographic foil ───────────────────────────────────────────────────
   The reference is shattered iridescent flake on near-black. Rather than ship
   a photo, we scatter one: a few thousand tiny spectral shards drawn onto a
   tiling canvas, wrapped at the edges so the tile is seamless. */
const HOLO_HUES = ['#7b5cff', '#35e8f2', '#3dff9e', '#ffd24a', '#ff7a45',
                   '#ff4fd8', '#4d7bff', '#b8ff5c', '#ffffff'];

function glitterTile(size = 512, count = 8600) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');

  const shard = (x, y, r, color, alpha) => {
    g.beginPath();
    const pts = 3 + ((Math.random() * 3) | 0);
    for (let i = 0; i < pts; i++) {
      const a = (i / pts) * Math.PI * 2 + Math.random() * 0.9;
      const rr = r * (0.45 + Math.random() * 0.75);
      const px = x + Math.cos(a) * rr, py = y + Math.sin(a) * rr;
      i ? g.lineTo(px, py) : g.moveTo(px, py);
    }
    g.closePath();
    g.globalAlpha = alpha;
    g.fillStyle = color;
    g.fill();
  };

  // two passes: a dense field of fine grain, then a few big glints on top
  for (let i = 0; i < count; i++) {
    const hero = i > count - 130;
    const x = Math.random() * size, y = Math.random() * size;
    const r = hero ? 3.2 + Math.random() * 3.4
                   : 0.55 + Math.pow(Math.random(), 3.4) * 2.9;
    const color = HOLO_HUES[(Math.random() * HOLO_HUES.length) | 0];
    const alpha = hero ? 0.55 + Math.random() * 0.4 : 0.22 + Math.random() * 0.66;
    // draw the shard, plus wrapped copies so the tile has no visible seam
    for (const dx of [0, -size, size]) {
      for (const dy of [0, -size, size]) {
        if ((dx || dy) && (Math.min(x, size - x) > r + 2 && Math.min(y, size - y) > r + 2)) continue;
        shard(x + dx, y + dy, r, color, alpha);
      }
    }
  }
  g.globalAlpha = 1;
  return c.toDataURL('image/png');
}

/* ── deep field ─────────────────────────────────────────────────────────
   Same idea as the foil, colder physics: dust, ordinary stars, and a handful
   of bright ones with a real glow. Wrapped at the edges so the sky tiles. */
const STAR_TINTS = ['#ffffff', '#ffffff', '#cfe0ff', '#a9c6ff', '#ffe4bd', '#ffd0d8'];

function starTile(size = 640) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');

  const at = (fn, x, y, pad) => {
    for (const dx of [0, -size, size]) {
      for (const dy of [0, -size, size]) {
        if ((dx || dy) && Math.min(x, size - x) > pad && Math.min(y, size - y) > pad) continue;
        fn(x + dx, y + dy);
      }
    }
  };

  // dust — barely there, but it is what stops the sky looking empty
  for (let i = 0; i < 2600; i++) {
    g.globalAlpha = 0.05 + Math.random() * 0.24;
    g.fillStyle = Math.random() < 0.82 ? '#ffffff' : '#bcd4ff';
    g.fillRect(Math.random() * size, Math.random() * size, 1, 1);
  }

  // ordinary stars
  for (let i = 0; i < 520; i++) {
    const x = Math.random() * size, y = Math.random() * size;
    const r = 0.5 + Math.pow(Math.random(), 2) * 1.1;
    const tint = STAR_TINTS[(Math.random() * STAR_TINTS.length) | 0];
    const a = 0.32 + Math.random() * 0.6;
    at((px, py) => {
      g.globalAlpha = a; g.fillStyle = tint;
      g.beginPath(); g.arc(px, py, r, 0, 7); g.fill();
    }, x, y, r + 2);
  }

  // the bright few, with a halo
  for (let i = 0; i < 34; i++) {
    const x = Math.random() * size, y = Math.random() * size;
    const r = 1.3 + Math.random() * 1.2;
    const halo = 7 + Math.random() * 11;
    const tint = STAR_TINTS[(Math.random() * STAR_TINTS.length) | 0];
    at((px, py) => {
      const grad = g.createRadialGradient(px, py, 0, px, py, halo);
      grad.addColorStop(0, tint);
      grad.addColorStop(0.18, tint);
      grad.addColorStop(1, 'rgba(255,255,255,0)');
      g.globalAlpha = 0.5;
      g.fillStyle = grad;
      g.beginPath(); g.arc(px, py, halo, 0, 7); g.fill();
      g.globalAlpha = 0.95; g.fillStyle = tint;
      g.beginPath(); g.arc(px, py, r, 0, 7); g.fill();
    }, x, y, halo + 2);
  }

  g.globalAlpha = 1;
  return c.toDataURL('image/png');
}

/* one <style> element holds whichever backdrop the current skin asked for */
function paintBackdrop(theme) {
  const want = theme.holo ? 'holo' : theme.space ? 'space' : null;
  const node = $('#ct-backdrop');
  if (!want) { node?.remove(); return; }
  if (node && node.dataset.kind === want) return;
  node?.remove();
  const style = el('style');
  style.id = 'ct-backdrop';
  style.dataset.kind = want;
  style.textContent = want === 'holo'
    ? `:root{--holo-tex:url(${glitterTile()})}`
    : `:root{--star-tex:url(${starTile()})}`;
  document.head.append(style);
}

function luminance(hex) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '');
  if (!m) return 0;
  const [r, g, b] = [1, 2, 3].map((i) => parseInt(m[i], 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function applyUI(ui) {
  const root = document.documentElement;
  root.style.setProperty('--fs', (ui.fontSize || 13) + 'px');
  root.style.setProperty('--radius', (ui.radius ?? 8) + 'px');
  root.style.setProperty('--glow', String(ui.glow ?? 0.5));
  root.dataset.anim = ui.animations === false ? 'off' : 'on';
  document.body.dataset.density = ui.density || 'cozy';
  document.body.dataset.texture = ui.texture || 'none';
  document.body.dataset.layout = ui.layout || 'messages';
  document.body.classList.toggle('hide-stderr', !ui.showStderr);
}

async function saveCfg(patch) {
  state.cfg = { ...state.cfg, ...patch, ui: { ...state.cfg.ui, ...(patch.ui || {}) } };
  applyUI(state.cfg.ui);
  try { localStorage.setItem('codetails.cfg', JSON.stringify(state.cfg)); } catch (_) {}
  try { await post('/api/config', patch); } catch (e) { /* offline is fine */ }
}

/* ─────────────────────────────── boot ────────────────────────────────── */
async function boot() {
  try { state.cfg = JSON.parse(localStorage.getItem('codetails.cfg') || 'null'); } catch (_) {}
  if (state.cfg) { applyTheme(state.cfg.theme, state.cfg.custom_themes?.[state.cfg.theme]); applyUI(state.cfg.ui); }

  const b = await api('/api/boot');
  state.boot = b;
  state.cfg = b.config;
  state.projects = b.projects;
  applyTheme(b.config.theme, b.config.custom_themes?.[b.config.theme]);
  applyUI(b.config.ui);
  try { localStorage.setItem('codetails.cfg', JSON.stringify(b.config)); } catch (_) {}

  renderProjects();
  renderLive(b.sessions);
  netStatus(true, b.endpoints);
  $('#app').classList.remove('booting');
  if (window.innerWidth <= 900) $('#app').classList.add('drawer-closed');

  if (b.sessions.length) attachSession(b.sessions[0]);
  else welcome();

  setInterval(refreshLive, 6000);
  setInterval(tickRun, 1000);
}

function netStatus(on, ends) {
  const line = $('#net-line');
  line.classList.toggle('on', !!on);
  line.classList.toggle('off', !on);
  const e = ends || state.boot?.endpoints;
  let txt = 'offline';
  if (on && e) {
    if (e.tailnet?.ip) txt = `tailnet · ${e.tailnet.ip}`;
    else txt = `local · :${state.cfg?.port ?? ''}`;
  }
  $('#net-text').textContent = txt;
}

/* ───────────────────────────── drawer lists ──────────────────────────── */
function renderProjects() {
  const box = $('#project-list');
  box.textContent = '';
  state.projects.forEach((p, i) => {
    const row = el('button', 'row');
    row.style.animationDelay = Math.min(i * 14, 260) + 'ms';
    row.innerHTML = `<div class="r1">
        <span class="name">${esc(p.name)}</span>
        <span class="pill">${p.sessions}</span>
      </div>
      <div class="r2">${esc(rel(p.cwd))}</div>`;
    row.onclick = () => toggleProject(p, row);
    box.append(row);
    if (state.openProject === p.slug) toggleProject(p, row, true);
  });
}

async function toggleProject(p, row, force) {
  const next = row.nextElementSibling;
  if (next?.classList.contains('sessions-sub') && !force) {
    next.remove(); state.openProject = null; return;
  }
  $$('.sessions-sub').forEach((n) => n.remove());
  state.openProject = p.slug;
  const holder = el('div', 'sessions-sub');
  holder.innerHTML = '<div class="sub-empty">loading…</div>';
  row.after(holder);
  try {
    const slugs = (p.slugs || [p.slug]).join('|');
    const { sessions } = await api('/api/project-sessions?slug=' + encodeURIComponent(slugs));
    holder.textContent = '';
    const newBtn = el('button', 'row');
    newBtn.innerHTML = `<div class="r1"><span class="name" style="color:var(--accent)">＋ new here</span></div>`;
    newBtn.onclick = () => createSession({ cwd: p.cwd });
    holder.append(newBtn);
    if (!sessions.length) holder.append(el('div', 'sub-empty', 'no past sessions'));
    sessions.forEach((s) => {
      const r = el('button', 'row');
      r.innerHTML = `<div class="r1"><span class="name">${esc(s.title)}</span></div>
        <div class="r2">${ago(s.mtime)} ago · ${(s.size / 1024).toFixed(0)}k</div>`;
      r.onclick = () => openHistory(s, p);
      holder.append(r);
    });
  } catch (e) {
    holder.innerHTML = `<div class="sub-empty">${esc(e.message)}</div>`;
  }
}

function renderLive(sessions) {
  const g = $('#live-group'); const box = $('#live-list');
  g.hidden = !sessions.length;
  $('#live-count').textContent = sessions.length;
  box.textContent = '';
  sessions.forEach((s) => {
    const r = el('button', 'row' + (state.session?.id === s.id ? ' active' : ''));
    const cls = !s.alive ? 'dead' : s.status === 'running' ? 'busy' : '';
    r.innerHTML = `<div class="r1">
        <span class="live-dot ${cls}"></span>
        <span class="name">${esc(s.title)}</span>
      </div>
      <div class="r2">${esc(rel(s.cwd))} · ${fmtCost(s.stats.cost) || '$0'}</div>`;
    r.onclick = () => attachSession(s);
    box.append(r);
  });
}

async function refreshLive() {
  try {
    const { sessions } = await api('/api/sessions');
    renderLive(sessions);
    const mine = sessions.find((s) => s.id === state.session?.id);
    if (mine) { state.session = mine; paintTopbar(); }
  } catch (_) { netStatus(false); }
}

/* ─────────────────────────── session lifecycle ───────────────────────── */
function welcome() {
  state.mode = 'empty';
  clearStream();
  const w = el('div', 'welcome');
  const e = state.boot?.endpoints || {};
  w.innerHTML = `
    <h1><span class="spark">✻</span> Welcome to CodeTails</h1>
    <p><span class="k">host</span> ${esc(state.boot?.host || '')}
       ${e.tailnet?.ip ? `<span class="k">· tailnet</span> ${esc(e.tailnet.ip)}` : ''}</p>
    <p><span class="k">claude</span> stream-json bridge · sessions resume automatically</p>
    <div class="tips">
      <button data-act="new">＋ new session</button>
      <button data-act="share">get the phone link</button>
      <button data-act="skins">change skin</button>
    </div>`;
  $('#stream').append(w);
  paintTopbar();
}

async function createSession(opts) {
  closeSheet();
  try {
    const { session } = await post('/api/sessions', {
      cwd: opts.cwd,
      model: opts.model || state.cfg.default_model,
      permission_mode: opts.permission_mode || state.cfg.default_permission_mode,
      effort: opts.effort || 'default',
      resume: opts.resume || null,
      title: opts.title || null,
    });
    await saveCfg({ default_cwd: session.cwd });
    attachSession(session);
    refreshLive();
    toast(opts.resume ? 'session resumed' : 'session started', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

function attachSession(meta) {
  if (state.es) { state.es.close(); state.es = null; }
  state.session = meta;
  state.history = null;
  state.mode = 'live';
  state.lastSeq = 0;
  clearStream();
  paintTopbar();
  renderLive(state.boot?.sessions || []);
  refreshLive();
  closeDrawerMobile();

  const es = new EventSource(`/api/sessions/${meta.id}/events`);
  state.es = es;
  es.onmessage = (m) => {
    let ev; try { ev = JSON.parse(m.data); } catch (_) { return; }
    state.lastSeq = ev.seq || state.lastSeq;
    handleEvent(ev);
  };
  es.onopen = () => netStatus(true);
  es.onerror = () => netStatus(false);
  $('#input').focus({ preventScroll: true });
}

async function openHistory(s, project) {
  if (state.es) { state.es.close(); state.es = null; }
  state.mode = 'history';
  state.session = null;
  clearStream();
  closeDrawerMobile();
  $('#tb-project').textContent = project?.name || '';
  $('#tb-title').textContent = s.title;
  const banner = el('div', 'welcome');
  banner.innerHTML = `<h1><span class="spark">✻</span> ${esc(s.title)}</h1>
    <p><span class="k">cwd</span> ${esc(rel(s.cwd || project?.cwd || ''))}</p>
    <p><span class="k">last active</span> ${ago(s.mtime)} ago · <span class="k">id</span> ${esc(s.id.slice(0, 8))}</p>
    <div class="tips"><button data-act="resume">↻ resume this session</button></div>`;
  $('#stream').append(banner);
  banner.querySelector('[data-act=resume]').onclick =
    () => createSession({ cwd: s.cwd || project?.cwd, resume: s.id, title: s.title });

  try {
    const h = await api('/api/history?id=' + encodeURIComponent(s.id));
    state.history = h;
    h.events.forEach(handleEvent);
    scrollBottom(true);
  } catch (e) { addNotice('could not load transcript: ' + e.message, 'err'); }
  paintTopbar();
}

function clearStream() {
  $('#stream').textContent = '';
  state.blocks.clear();
  state.denials.clear();
  state.lastTs = 0;
  state.stick = true;
  $('#jump').hidden = true;
  setRunning(false);
}

/* ───────────────────────────── topbar paint ──────────────────────────── */
function paintTopbar() {
  const s = state.session;
  const m = s || state.history?.meta;
  $('#tb-project').textContent = m ? (m.cwd || '').split('/').filter(Boolean).pop() || '/' : '—';
  $('#tb-title').textContent = m ? (m.title || 'session') : 'no session';

  const dot = $('#status-dot');
  dot.className = 'status-dot ' + (s ? (s.status === 'running' ? 'running' : s.alive ? 'idle' : 'exited') : '');
  dot.title = s ? s.status : 'no session';

  const cm = $('#chip-model'), cmo = $('#chip-mode');
  cm.hidden = !s; cmo.hidden = !s;
  if (s) {
    cm.innerHTML = `<span class="k">model</span> ${esc(s.model)}`;
    const short = { acceptEdits: 'edits', bypassPermissions: 'bypass', plan: 'plan', default: 'ask' }[s.permission_mode] || s.permission_mode;
    cmo.className = 'chip mode-' + s.permission_mode;
    cmo.innerHTML = `<span class="k">perm</span> ${esc(short)}`;
    $('#q-mode').textContent = short;

    const cost = $('#chip-cost');
    cost.hidden = !s.stats.cost;
    cost.textContent = fmtCost(s.stats.cost);

    const ctx = $('#chip-ctx');
    if (s.stats.window && s.stats.context) {
      ctx.hidden = false;
      const pct = Math.min(100, (s.stats.context / s.stats.window) * 100);
      ctx.querySelector('.meter-fill').style.width = pct.toFixed(1) + '%';
      ctx.querySelector('.meter-label').textContent = `ctx ${pct.toFixed(0)}%`;
    } else ctx.hidden = true;
    gitChip(s.cwd);
  } else {
    $('#chip-cost').hidden = true; $('#chip-ctx').hidden = true; $('#chip-git').hidden = true;
  }
  document.title = (s?.status === 'running' ? '● ' : '') + 'CodeTails' + (m ? ' — ' + (m.title || '') : '');
}

let gitCache = {};
async function gitChip(cwd) {
  if (!cwd) return;
  const chip = $('#chip-git');
  const now = Date.now();
  if (gitCache[cwd] && now - gitCache[cwd].at < 15000) return paintGit(gitCache[cwd].info);
  try {
    const info = await api('/api/git?cwd=' + encodeURIComponent(cwd));
    gitCache[cwd] = { at: now, info };
    paintGit(info);
  } catch (_) { chip.hidden = true; }
}
function paintGit(info) {
  const chip = $('#chip-git');
  chip.hidden = !info.repo;
  if (!info.repo) return;
  chip.innerHTML = `<span class="k">⎇</span> ${esc(info.branch || 'detached')}` +
    (info.dirty ? ` <span style="color:var(--warn)">+${info.dirty}</span>` : '');
}

/* ─────────────────────────── event → blocks ──────────────────────────── */
function handleEvent(ev) {
  switch (ev.t) {
    case 'session': {
      const wasRunning = state.session?.status === 'running';
      state.session = { ...(state.session || {}), ...ev };
      paintTopbar();
      setRunning(ev.status === 'running');
      if (wasRunning && ev.status !== 'running') turnDone();
      break;
    }
    case 'commands': state.commands = ev.commands || []; break;
    case 'user': addUser(ev); break;
    case 'delta': killTyping(); applyDelta(ev); break;
    case 'text': killTyping(); setTextBlock(ev); break;
    case 'thinking': killTyping(); setThinkBlock(ev); break;
    case 'tool_use': killTyping(); upsertTool(ev); break;
    case 'tool_result': finishTool(ev); ensureTyping(); break;
    case 'turn_start': state.turnStart = Date.now(); state.outTokens = 0; state.denials.clear();
      setRunning(true); markDelivered(); ensureTyping(); break;
    case 'status': setRunning(true); ensureTyping(); break;
    case 'usage': state.outTokens = ev.output || state.outTokens; break;
    case 'result': killTyping(); addResult(ev); setRunning(false); turnDone(); break;
    case 'rate_limit': paintRate(ev.info); break;
    case 'summary': if (ev.text) addNotice('· ' + ev.text); break;
    case 'notice': addNotice(ev.text, ev.kind === 'spawn' ? 'spawn' : ''); break;
    case 'stderr': addNotice(ev.text, 'stderr'); break;
    case 'error': addNotice(ev.text, 'err'); break;
    case 'exit': addNotice(`claude exited${ev.code != null ? ' (' + ev.code + ')' : ''}${ev.text ? ' — ' + ev.text : ''} · your next message resumes it`, 'warn'); break;
    case 'permission': permCard(ev); break;
    case 'permission_done': state.blocks.get('perm:' + ev.id)?.node.remove(); break;
  }
  autoScroll();
}

function mount(key, node, ts, side) {
  const prev = state.blocks.get(key);
  if (prev) return prev;
  timeSep(ts);
  const rec = { node };
  state.blocks.set(key, rec);
  $('#stream').insertBefore(node, $('#typing'));
  if (side) flow(node, side);
  return rec;
}

/* who said it, and is this the last bubble of their run? only the last one
   gets a tail — the same grouping every messaging app uses */
function flow(node, side) {
  node.dataset.side = side;
  let prev = node.previousElementSibling;
  while (prev && !prev.dataset.side) prev = prev.previousElementSibling;
  if (prev && prev.dataset.side === side) {
    prev.classList.remove('tail');
    node.classList.add('tail', 'cont');
  } else {
    node.classList.add('tail', 'head');
  }
}

/* iMessage-style time break when the conversation has been quiet a while */
function timeSep(ts) {
  if (!ts) return;
  // live events carry epoch seconds, replayed transcripts carry ISO strings
  const t = typeof ts === 'number' ? ts * 1000 : Date.parse(ts);
  if (!Number.isFinite(t)) return;
  if (state.lastTs && t - state.lastTs < 15 * 60 * 1000) { state.lastTs = t; return; }
  const typing = $('#typing');
  const prev = typing ? typing.previousElementSibling : $('#stream').lastElementChild;
  if (prev?.classList.contains('time-sep')) { state.lastTs = t; return; }
  const d = new Date(t);
  const today = new Date().toDateString() === d.toDateString();
  const day = today ? 'Today' : d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  const n = el('div', 'time-sep', `<b>${esc(day)}</b> ${esc(time)}`);
  $('#stream').insertBefore(n, $('#typing'));
  state.lastTs = t;
}

function ensureTyping() {
  if (state.cfg?.ui?.layout === 'terminal') return;
  let n = $('#typing');
  if (!n) {
    n = el('div', 'typing');
    n.id = 'typing';
    n.innerHTML = '<div class="bub"><i></i><i></i><i></i></div>';
    $('#stream').append(n);
  }
  n.hidden = false;
  autoScroll();
}
function killTyping() { $('#typing')?.remove(); }

function markDelivered() {
  const users = $$('.msg-user');
  const last = users[users.length - 1];
  if (last && !last.querySelector('.delivered')) {
    const cap = el('span', 'delivered', 'delivered');
    last.after(cap);
  }
}

function addUser(ev) {
  const key = 'u:' + ev.seq;
  if (state.blocks.has(key)) return;
  const n = el('div', 'blk msg-user');
  n.innerHTML = `<span class="caret">&gt;</span><div class="body">${esc(ev.text)}</div>`;
  mount(key, n, ev.ts, 'me');
  state.stick = true;
}

function textBlock(ev) {
  const key = 't:' + (ev.mid || 'x') + ':' + ev.idx;
  let rec = state.blocks.get(key);
  if (!rec) {
    const n = el('div', 'blk msg-assistant');
    n.innerHTML = '<div class="prose"></div>';
    rec = mount(key, n, ev.ts, 'ai');
    rec.raw = '';
    rec.prose = n.querySelector('.prose');
  }
  return rec;
}

function applyDelta(ev) {
  if (ev.kind === 'thinking') {
    if (state.cfg?.ui?.showThinking === false) return;
    const rec = thinkBlock(ev);
    rec.raw += ev.text;
    rec.body.textContent = rec.raw;
    return;
  }
  const rec = textBlock(ev);
  rec.raw += ev.text;
  rec.prose.innerHTML = md(rec.raw);
  rec.prose.classList.add('streaming');
}

function setTextBlock(ev) {
  const rec = textBlock(ev);
  rec.raw = ev.text;
  rec.prose.innerHTML = md(ev.text);
  rec.prose.classList.remove('streaming');
  wireCode(rec.prose);
}

function thinkBlock(ev) {
  const key = 'k:' + (ev.mid || 'x') + ':' + ev.idx;
  let rec = state.blocks.get(key);
  if (!rec) {
    const n = el('div', 'blk thinking collapsed');
    n.innerHTML = '<span class="th-head">thinking</span><div class="th-text"></div>';
    rec = mount(key, n, ev.ts, 'note');
    rec.raw = '';
    rec.body = n.querySelector('.th-text');
    n.querySelector('.th-head').onclick = () => n.classList.toggle('collapsed');
    n.onclick = () => n.classList.toggle('collapsed');
  }
  return rec;
}

function setThinkBlock(ev) {
  if (state.cfg?.ui?.showThinking === false) return;
  const rec = thinkBlock(ev);
  rec.raw = ev.text;
  rec.body.textContent = ev.text;
}

/* ───────────────────────────── tool rows ─────────────────────────────── */
const TOOL_LABEL = { Edit: 'Update', NotebookEdit: 'Notebook', Task: 'Agent', WebFetch: 'Fetch', WebSearch: 'Search' };

const TOOL_FAMILY = {
  Bash: 'shell', Edit: 'write', Write: 'write', NotebookEdit: 'write',
  Read: 'read', Glob: 'find', Grep: 'find',
  WebFetch: 'web', WebSearch: 'web',
  Task: 'agent', Skill: 'agent', Agent: 'agent',
  TodoWrite: 'plan', TaskCreate: 'plan', TaskUpdate: 'plan', TaskList: 'plan',
};
const toolFamily = (name) => TOOL_FAMILY[name] || (String(name).startsWith('mcp__') ? 'web' : 'other');

const ICONS = {
  shell: '<path d="M4 17l5-5-5-5"/><path d="M12.5 19H20"/>',
  write: '<path d="M4 20h4L20 8l-4-4L4 16v4z"/>',
  read:  '<path d="M6 3h8l5 5v13H6z"/><path d="M14 3v5h5"/>',
  find:  '<circle cx="11" cy="11" r="6.2"/><path d="M19.5 19.5l-4-4"/>',
  web:   '<circle cx="12" cy="12" r="8.2"/><path d="M3.8 12h16.4"/><path d="M12 3.8a15 15 0 010 16.4a15 15 0 010-16.4"/>',
  agent: '<path d="M12 3l2 6.2L20 11l-6 1.8L12 19l-2-6.2L4 11l6-1.8z"/>',
  plan:  '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
  other: '<circle cx="12" cy="12" r="4.6"/>',
};

const toolIcon = (name) =>
  `<svg class="ticon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[toolFamily(name)]}</svg>`;

function toolSummary(name, input) {
  const i = input || {};
  switch (name) {
    case 'Bash': return i.command || '';
    case 'Read': return rel(i.file_path) + (i.offset ? `:${i.offset}` : '');
    case 'Edit': case 'Write': case 'NotebookEdit': return rel(i.file_path);
    case 'Glob': return i.pattern + (i.path ? ' in ' + rel(i.path) : '');
    case 'Grep': return i.pattern + (i.path ? ' in ' + rel(i.path) : '');
    case 'WebFetch': return i.url || '';
    case 'WebSearch': return i.query || '';
    case 'Task': return (i.subagent_type ? i.subagent_type + ': ' : '') + (i.description || '');
    case 'Skill': return i.skill || '';
    case 'TodoWrite': case 'TaskCreate': case 'TaskUpdate': return '';
    default: {
      const s = JSON.stringify(i);
      return s === '{}' ? '' : s.slice(1, -1).replace(/"/g, '');
    }
  }
}

function upsertTool(ev) {
  const key = 'tool:' + ev.id;
  let rec = state.blocks.get(key);
  if (!rec) {
    const n = el('div', 'blk tool pending');
    n.innerHTML = `<div class="tool-head">
        <span class="bullet"><span class="glyph">⏺</span></span>
        <span class="tool-name"></span>
        <span class="tool-args"></span>
        <span class="tool-meta"></span>
      </div>
      <div class="tool-body hidden"></div>`;
    rec = mount(key, n, ev.ts, 'tool');
    rec.body = n.querySelector('.tool-body');
    rec.open = false;
    n.querySelector('.tool-head').onclick = () => {
      rec.open = !rec.open;
      rec.body.classList.toggle('hidden', !rec.open);
    };
  }
  rec.name = ev.name || rec.name;
  rec.input = (ev.input && Object.keys(ev.input).length) ? ev.input : rec.input;
  rec.node.dataset.tool = toolFamily(rec.name);
  const bullet = rec.node.querySelector('.bullet');
  if (bullet && !bullet.querySelector('svg')) bullet.insertAdjacentHTML('beforeend', toolIcon(rec.name));
  const head = rec.node.querySelector('.tool-head');
  head.querySelector('.tool-name').textContent = TOOL_LABEL[rec.name] || rec.name || '…';
  head.querySelector('.tool-args').textContent = toolSummary(rec.name, rec.input);
  if (rec.input && !rec.painted) paintToolInput(rec);
  return rec;
}

function paintToolInput(rec) {
  const i = rec.input || {};
  const body = rec.body;
  const pre = [];
  if (rec.name === 'Bash' && i.description) pre.push(`<div class="tool-out">${esc(i.description)}</div>`);
  if (rec.name === 'Edit') pre.push(diffHTML(rel(i.file_path), i.old_string || '', i.new_string || ''));
  if (rec.name === 'Write') pre.push(diffHTML(rel(i.file_path), '', i.content || '', true));
  if (rec.name === 'Task' && i.prompt) pre.push(`<div class="tool-out">${esc(i.prompt.slice(0, 600))}</div>`);
  if (['TodoWrite', 'TaskCreate', 'TaskUpdate'].includes(rec.name)) {
    const items = i.todos || i.tasks || (i.task ? [i.task] : []);
    if (items.length) pre.push(todosHTML(items));
  }
  if (pre.length) {
    body.innerHTML = pre.join('') + body.innerHTML;
    rec.painted = true;
    if (rec.name === 'Edit' || rec.name === 'Write' || pre[0].includes('todos')) openTool(rec);
  }
}

function openTool(rec) { rec.open = true; rec.body.classList.remove('hidden'); }

function finishTool(ev) {
  const rec = state.blocks.get('tool:' + ev.id) || upsertTool({ id: ev.id, name: '?', input: {} });
  rec.node.classList.remove('pending');
  rec.node.classList.toggle('err', !ev.ok);
  const out = (ev.content || '').replace(/\s+$/, '');
  const lines = out ? out.split('\n') : [];
  const meta = rec.node.querySelector('.tool-meta');

  if (rec.name === 'Read') meta.textContent = `${lines.length} lines`;
  else if (rec.name === 'Bash') meta.textContent = lines.length ? `${lines.length} lines` : '';
  else if (['Grep', 'Glob'].includes(rec.name)) meta.textContent = `${lines.length} hits`;

  if (!out) {
    if (!rec.painted) rec.body.innerHTML = `<div class="tool-out">${ev.ok ? '(no output)' : 'failed'}</div>`;
    return;
  }
  const holder = el('div');
  const cap = 14;
  const head = lines.slice(0, cap).join('\n');
  const outEl = el('div', 'tool-out' + (ev.ok ? '' : ' err'));
  outEl.innerHTML = `<span class="elbow">⎿</span>${esc(head)}`;
  holder.append(outEl);
  if (lines.length > cap) {
    const more = el('button', 'more-btn', `+${lines.length - cap} more lines`);
    const rest = el('div', 'tool-out');
    rest.style.display = 'none';
    rest.textContent = lines.slice(cap).join('\n');
    more.onclick = (e) => {
      e.stopPropagation();
      const show = rest.style.display === 'none';
      rest.style.display = show ? '' : 'none';
      more.textContent = show ? '− collapse' : `+${lines.length - cap} more lines`;
    };
    holder.append(rest, more);
  }
  rec.body.append(holder);

  const interesting = !ev.ok || ['Bash', 'Grep', 'Glob', 'WebSearch', 'Task'].includes(rec.name);
  if (interesting || state.cfg?.ui?.collapseTools === false) openTool(rec);

  if (!ev.ok && DENIED_RE.test(out)) denialCard(out, rec.name);
}

function todosHTML(items) {
  const rows = items.map((t) => {
    const st = (t.status || '').toLowerCase();
    const cls = st.startsWith('completed') ? 'done' : st.startsWith('in_prog') || st === 'active' ? 'active' : '';
    const box = cls === 'done' ? '☑' : cls === 'active' ? '◐' : '☐';
    const text = t.content || t.title || t.description || t.name || String(t);
    return `<div class="todo ${cls}"><span class="box">${box}</span><span>${esc(text)}</span></div>`;
  }).join('');
  return `<div class="todos">${rows}</div>`;
}

/* ───────────────────────────── diffing ───────────────────────────────── */
function lcsDiff(a, b) {
  const n = a.length, m = b.length;
  if (n * m > 250000) return [...a.map((l) => ['-', l]), ...b.map((l) => ['+', l])];
  const dp = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push([' ', a[i]]); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push(['-', a[i++]]); }
    else { out.push(['+', b[j++]]); }
  }
  while (i < n) out.push(['-', a[i++]]);
  while (j < m) out.push(['+', b[j++]]);
  return out;
}

function diffHTML(file, oldS, newS, isNew) {
  const a = oldS ? oldS.split('\n') : [];
  const b = newS ? newS.split('\n') : [];
  const rows = isNew ? b.map((l) => ['+', l]) : lcsDiff(a, b);
  let adds = 0, dels = 0;
  const body = rows.slice(0, 400).map(([k, line]) => {
    if (k === '+') adds++; if (k === '-') dels++;
    const cls = k === '+' ? 'add' : k === '-' ? 'del' : 'ctx';
    return `<span class="l ${cls}">${esc(k + ' ' + line)}</span>`;
  }).join('');
  const extra = rows.length > 400 ? `<span class="l ctx">  … ${rows.length - 400} more lines</span>` : '';
  return `<div class="diff">
    <div class="diff-file"><span>${esc(file || 'file')}</span>
      <span class="stat"><span class="add">+${adds}</span> <span class="del">−${dels}</span></span></div>
    <pre>${body}${extra}</pre></div>`;
}

/* ───────────────────────── notices / results ─────────────────────────── */
function push(node) { $('#stream').insertBefore(node, $('#typing')); }

function addNotice(text, kind = '') {
  if (!text) return;
  if (kind === 'stderr' && !state.cfg?.ui?.showStderr) return;
  const n = el('div', 'blk notice ' + (kind === 'err' ? 'err' : kind === 'warn' ? 'warn' : ''));
  n.innerHTML = `<span class="dotmark">·</span><span>${esc(text)}</span>`;
  push(n);
}

function addResult(ev) {
  const n = el('div', 'blk turn-result');
  const st = ev.stats || {};
  const bits = [
    ev.is_error ? '<span class="bad">error</span>' : '<span class="ok">done</span>',
    ev.duration ? (ev.duration / 1000).toFixed(1) + 's' : '',
    ev.cost != null ? fmtCost(ev.cost) : '',
    st.output ? '↓ ' + fmtTok(st.output) : '',
    st.window && st.context ? `ctx ${((st.context / st.window) * 100).toFixed(0)}%` : '',
  ].filter(Boolean);
  n.innerHTML = bits.map((b) => `<span>${b}</span>`).join('');
  push(n);
  if (ev.text) addNotice(ev.text, 'err');
  (ev.denials || []).forEach((d) => denialCard(d.message || 'permission denied', d.tool_name));
}

const DENIED_RE = /requires? (approval|permission)|requested permission|haven'?t granted|permission denied|not allowed|user (denied|rejected)/i;

/* Headless Claude can't pop its own permission prompt, so we turn the refusal
   into one tap: widen the rules, resume the same session, carry on. */
function denialCard(text, tool) {
  const t = tool && tool !== '?' ? tool : null;
  const key = 'deny:' + (t || '') + ':' + String(text).slice(0, 60);
  if (state.denials.has(key)) return;            // one card per tool per turn
  state.denials.add(key);
  const n = el('div', 'blk denial');
  n.innerHTML = `<b>permission needed</b>${t ? ' — <b>' + esc(t) + '</b>' : ''} ${esc(text.slice(0, 200))}
    <div class="row-actions">
      ${t ? `<button class="tiny-btn go" data-allow="${esc(t)}">allow ${esc(t)} &amp; retry</button>` : ''}
      <button class="tiny-btn" data-m="acceptEdits">accept edits</button>
      <button class="tiny-btn" data-m="bypassPermissions">bypass all</button>
      <button class="tiny-btn" data-x>dismiss</button>
    </div>`;
  push(n);
  n.querySelector('[data-allow]')?.addEventListener('click', async () => {
    try {
      await post(`/api/sessions/${state.session.id}/config`, { allow_tool: t });
      n.remove();
      toast(`${t} allowed for this session`, 'ok');
      setTimeout(() => send('Please retry that step now — it has been approved.'), 400);
    } catch (e) { toast(e.message, 'err'); }
  });
  n.querySelectorAll('[data-m]').forEach((b) => {
    b.onclick = async () => {
      try {
        await setMode(b.dataset.m);
        n.remove();
        toast('permissions → ' + b.dataset.m, 'ok');
        setTimeout(() => send('Please retry that step now — it has been approved.'), 400);
      } catch (e) { toast(e.message, 'err'); }
    };
  });
  n.querySelector('[data-x]').onclick = () => n.remove();
  state.stick = true;
}

function permCard(ev) {
  const key = 'perm:' + ev.id;
  if (state.blocks.has(key)) return;
  const n = el('div', 'blk perm-card');
  n.innerHTML = `<h4>${esc(ev.tool || 'tool')} wants to run</h4>
    <pre>${esc(JSON.stringify(ev.input || {}, null, 1).slice(0, 700))}</pre>
    <div class="perm-actions">
      <button class="primary" data-a>allow once</button>
      <button class="ghost" data-d>deny</button>
    </div>`;
  mount(key, n, ev.ts);
  const answer = (allow) => post(`/api/sessions/${state.session.id}/permission`, { id: ev.id, allow })
    .then(() => n.remove()).catch((e) => toast(e.message, 'err'));
  n.querySelector('[data-a]').onclick = () => answer(true);
  n.querySelector('[data-d]').onclick = () => answer(false);
  state.stick = true;
}

function paintRate(info) {
  if (!info) return;
  state.rate = info;
}

/* ─────────────────────────── running status ──────────────────────────── */
const VERBS = ['Thinking', 'Pondering', 'Cogitating', 'Divining', 'Noodling', 'Percolating',
  'Puzzling', 'Ruminating', 'Simmering', 'Spelunking', 'Synthesising', 'Tinkering',
  'Whirring', 'Conjuring', 'Marinating', 'Untangling', 'Wrangling', 'Sculpting'];
const GLYPHS = ['✻', '✽', '✳', '✶', '✷', '✵'];

function setRunning(on) {
  document.body.classList.toggle('running', on);
  if (!on) killTyping();
  $('#runline').hidden = !on;
  if (on && !state.turnStart) state.turnStart = Date.now();
  if (!on) { state.turnStart = null; $('#run-meta').textContent = ''; }
}

function tickRun() {
  if (!document.body.classList.contains('running')) return;
  const secs = state.turnStart ? Math.floor((Date.now() - state.turnStart) / 1000) : 0;
  if (secs % 4 === 0) {
    state.verbIdx = (state.verbIdx + 1) % VERBS.length;
    $('#run-verb').textContent = VERBS[state.verbIdx] + '…';
  }
  $('.spin').textContent = GLYPHS[secs % GLYPHS.length];
  const bits = [`${secs}s`];
  if (state.outTokens) bits.push(`↓ ${fmtTok(state.outTokens)} tokens`);
  $('#run-meta').textContent = '(' + bits.join(' · ') + ')';
}

function turnDone() {
  if (document.hidden) {
    if (state.cfg?.ui?.sound) blip();
    if (navigator.vibrate) try { navigator.vibrate(18); } catch (_) {}
  }
}

function blip() {
  try {
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    const o = ac.createOscillator(), g = ac.createGain();
    o.type = 'sine'; o.frequency.value = 660;
    g.gain.setValueAtTime(0.0001, ac.currentTime);
    g.gain.exponentialRampToValueAtTime(0.05, ac.currentTime + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + 0.22);
    o.connect(g).connect(ac.destination); o.start(); o.stop(ac.currentTime + 0.24);
  } catch (_) {}
}

/* ───────────────────────────── markdown ──────────────────────────────── */
/* only ever emit links we would be happy clicking ourselves */
function safeHref(href) {
  const v = String(href).trim().replace(/&amp;/g, '&').toLowerCase();
  if (/^(https?:|mailto:)/.test(v)) return true;
  return !/^[a-z][a-z0-9+.-]*:/.test(v);        // relative links are fine
}

function md(src) {
  if (!src) return '';
  const codes = [];
  let s = String(src).replace(/\r/g, '');

  s = s.replace(/```([\w+.-]*)\n([\s\S]*?)```/g, (_, lang, body) => {
    codes.push({ lang, body });
    return ` CODE${codes.length - 1} `;
  });

  s = esc(s);

  const inline = (t) => t
    .replace(/`([^`\n]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\W)\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, href) =>
      safeHref(href) ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>` : m)
    .replace(/(^|[\s(])((?:https?:\/\/)[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');

  const lines = s.split('\n');
  const out = [];
  let list = null, para = [], table = null;

  const flushP = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; } };
  const flushL = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const flushT = () => {
    if (!table) return;
    const [head, ...rows] = table;
    out.push('<table><thead><tr>' + head.map((h) => `<th>${inline(h)}</th>`).join('') +
      '</tr></thead><tbody>' + rows.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('') +
      '</tbody></table>');
    table = null;
  };

  for (let raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line.trim().slice(1, -1).split('|').map((c) => c.trim());
      if (/^[\s|:-]+$/.test(line)) continue;
      flushP(); flushL();
      (table = table || []).push(cells);
      continue;
    }
    flushT();
    if (!line.trim()) { flushP(); flushL(); continue; }
    let m;
    if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) {
      flushP(); flushL();
      out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
    } else if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      flushP(); flushL(); out.push('<hr>');
    } else if ((m = /^\s*&gt;\s?(.*)$/.exec(line))) {
      flushP(); flushL(); out.push(`<blockquote>${inline(m[1])}</blockquote>`);
    } else if ((m = /^\s*[-*+]\s+(.*)$/.exec(line))) {
      flushP();
      if (list !== 'ul') { flushL(); out.push('<ul>'); list = 'ul'; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = /^\s*\d+[.)]\s+(.*)$/.exec(line))) {
      flushP();
      if (list !== 'ol') { flushL(); out.push('<ol>'); list = 'ol'; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if (/^ CODE\d+ $/.test(line.trim())) {
      flushP(); flushL(); out.push(line.trim());
    } else {
      if (list) { out.push(`<li>${inline(line.trim())}</li>`); }
      else para.push(line.trim());
    }
  }
  flushP(); flushL(); flushT();

  let html = out.join('\n');
  html = html.replace(/ CODE(\d+) /g, (_, i) => {
    const { lang, body } = codes[i];
    return `<div class="codewrap">${lang ? `<span class="lang">${esc(lang)}</span>` : ''}` +
      `<button class="copy">copy</button><pre><code>${highlight(body, lang)}</code></pre></div>`;
  });
  return html;
}

const KEYWORDS = 'async|await|break|case|catch|class|const|continue|def|default|del|elif|else|' +
  'except|export|extends|finally|for|from|func|function|global|if|import|in|is|lambda|let|new|' +
  'nonlocal|not|or|and|pass|raise|return|self|static|super|switch|then|this|throw|try|typeof|' +
  'var|while|with|yield|True|False|None|null|true|false|undefined|struct|impl|fn|pub|use|mod|' +
  'match|echo|fi|do|done|esac|local|set|elsif|end|module|require';

/* one pass, one regex: never re-scan text we have already turned into markup */
const HL_RE = new RegExp([
  '(\\/\\*[\\s\\S]*?\\*\\/)',                      // 1 block comment
  '(^|\\s)((?:#|\\/\\/)[^\\n]*)',                      // 2 lead, 3 line comment
  '(&quot;[^\\n]*?&quot;|&#39;[^\\n]*?&#39;|`[^`]*`)',      // 4 string
  '\\b(\\d+(?:\\.\\d+)?)\\b',                        // 5 number
  '\\b(' + KEYWORDS + ')\\b',                              // 6 keyword
  '([A-Za-z_$][\\w$]*)(?=\\()',                            // 7 call
].join('|'), 'gm');

function highlight(code, lang) {
  const s = esc(code);
  if (lang === 'text' || lang === 'diff') return s;
  return s.replace(HL_RE, (m, block, lead, line, str, num, kw, fn) => {
    if (block) return `<span class="tk-com">${block}</span>`;
    if (line) return `${lead}<span class="tk-com">${line}</span>`;
    if (str) return `<span class="tk-str">${str}</span>`;
    if (num) return `<span class="tk-num">${num}</span>`;
    if (kw) return `<span class="tk-kw">${kw}</span>`;
    if (fn) return `<span class="tk-fn">${fn}</span>`;
    return m;
  });
}

function wireCode(root) {
  $$('.copy', root).forEach((b) => {
    if (b._w) return; b._w = true;
    b.onclick = (e) => {
      e.stopPropagation();
      const code = b.parentElement.querySelector('code')?.innerText || '';
      navigator.clipboard?.writeText(code);
      b.textContent = 'copied'; setTimeout(() => (b.textContent = 'copy'), 1200);
    };
  });
}

/* ─────────────────────────────── scroll ──────────────────────────────── */
function autoScroll() { if (state.stick) scrollBottom(); }
function scrollBottom(force) {
  const s = $('#stream');
  if (force) state.stick = true;
  s.scrollTop = s.scrollHeight;
}
$('#stream').addEventListener('scroll', () => {
  const s = $('#stream');
  const near = s.scrollHeight - s.scrollTop - s.clientHeight < 90;
  state.stick = near;
  $('#jump').hidden = near;
}, { passive: true });
$('#jump').onclick = () => scrollBottom(true);

/* ─────────────────────────────── sending ─────────────────────────────── */
async function send(textOverride) {
  const input = $('#input');
  const text = (textOverride ?? input.value).trim();
  if (!text) return;
  if (!state.session) {
    if (state.mode === 'history') return toast('resume this session first', 'err');
    return openSheet('new');
  }
  if (textOverride == null) { input.value = ''; grow(); }
  document.body.classList.remove('has-text');
  try { await post(`/api/sessions/${state.session.id}/send`, { text }); }
  catch (e) { toast(e.message, 'err'); }
  state.stick = true;
  scrollBottom(true);
}

async function stop() {
  if (!state.session) return;
  try { await post(`/api/sessions/${state.session.id}/interrupt`); toast('interrupted'); }
  catch (e) { toast(e.message, 'err'); }
}

async function setMode(mode) {
  if (!state.session) return;
  const { session } = await post(`/api/sessions/${state.session.id}/config`, { permission_mode: mode });
  state.session = session; paintTopbar();
}

async function setModel(model) {
  if (!state.session) return;
  const { session } = await post(`/api/sessions/${state.session.id}/config`, { model });
  state.session = session; paintTopbar();
  toast('model → ' + model, 'ok');
}

function grow() {
  const t = $('#input');
  t.style.height = 'auto';
  t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + 'px';
}

/* ─────────────────────────────── sheets ──────────────────────────────── */
function openSheet(name) {
  closeSheet();
  const s = $('#sheet-' + name);
  if (!s) return;
  s.hidden = false;
  document.body.classList.add('scrim');
  if (name === 'new') fillNew();
  if (name === 'share') fillShare();
  if (name === 'skins') fillSkins();
  if (name === 'settings') fillSettings();
}
function closeSheet() {
  $$('.sheet').forEach((s) => (s.hidden = true));
  $('#palette').hidden = true;
  document.body.classList.remove('scrim');
}

function fillNew() {
  const b = state.boot;
  const cwd = $('#new-cwd');
  cwd.value = state.session?.cwd || state.cfg.default_cwd || b.home;
  const mSel = $('#new-model'), pSel = $('#new-mode'), eSel = $('#new-effort');
  mSel.innerHTML = b.models.map((m) => `<option value="${m.id}">${m.label}</option>`).join('');
  pSel.innerHTML = b.permission_modes.map((m) => `<option value="${m.id}">${m.label} — ${m.hint}</option>`).join('');
  eSel.innerHTML = b.efforts.map((e) => `<option value="${e}">${e}</option>`).join('');
  mSel.value = state.cfg.default_model;
  pSel.value = state.cfg.default_permission_mode;
  $('#browser').hidden = true;
  const rec = $('#new-recent');
  rec.innerHTML = '';
  state.projects.slice(0, 8).forEach((p) => {
    const btn = el('button', '', esc(p.name));
    btn.onclick = () => { cwd.value = p.cwd; };
    rec.append(btn);
  });
}

async function browse(path) {
  const box = $('#browser');
  box.hidden = false;
  box.innerHTML = '<div class="b-row">loading…</div>';
  const d = await api('/api/fs?path=' + encodeURIComponent(path || $('#new-cwd').value));
  box.textContent = '';
  const here = el('button', 'b-row', `<span class="g">●</span><span>use ${esc(rel(d.path))}</span>`);
  here.onclick = () => { $('#new-cwd').value = d.path; box.hidden = true; };
  box.append(here);
  if (d.parent) {
    const up = el('button', 'b-row', '<span>↑</span><span>..</span>');
    up.onclick = () => browse(d.parent);
    box.append(up);
  }
  d.entries.forEach((e) => {
    const r = el('button', 'b-row', `<span class="${e.git ? 'g' : ''}">${e.git ? '⎇' : '/'}</span><span>${esc(e.name)}</span>`);
    r.onclick = () => browse(e.path);
    box.append(r);
  });
}

function fillShare() {
  const e = state.boot.endpoints;
  const holder = $('#qr-holder');
  const url = e.tailnet_url || e.lan || e.local;
  holder.innerHTML = `<img alt="QR code" src="/api/qr?d=${encodeURIComponent(url)}">`;
  const rows = [
    ['tailnet', e.tailnet_url, true],
    ['magicdns', e.tailnet_dns_url, false],
    ['lan', e.lan, false],
    ['local', e.local, false],
  ].filter(([, v]) => v);
  const box = $('#share-urls');
  box.textContent = '';
  rows.forEach(([lbl, val, best]) => {
    const r = el('button', 'u' + (best ? ' best' : ''));
    r.innerHTML = `<span class="lbl">${lbl}</span><span class="val">${esc(val)}</span><span class="lbl">copy</span>`;
    r.onclick = () => { navigator.clipboard?.writeText(val); toast('copied', 'ok'); };
    box.append(r);
  });
  if (!e.tailnet?.ip) {
    box.append(el('div', 'muted', 'Tailscale is not running — start it and reopen this panel to get a link that works off your LAN.'));
  }
}

function fillSkins() {
  const grid = $('#skin-grid');
  grid.textContent = '';
  const all = { ...window.CT_THEMES, ...(state.cfg.custom_themes || {}) };
  const fam = { modern: [], terminal: [] };
  Object.entries(all).forEach(([name, t]) => fam[(t.style === 'modern') ? 'modern' : 'terminal'].push([name, t]));
  [['modern', 'Messaging'], ['terminal', 'Terminal']].forEach(([key, title]) => {
    if (!fam[key].length) return;
    const h = el('div', 'fam-title', esc(title));
    grid.append(h);
    fam[key].forEach(([name, t]) => {
      const b = el('button', 'skin' + (state.cfg.theme === name ? ' on' : '') + (t.holo ? ' holo' : ''));
      const swatch = t.holo
        ? '<i class="foil"></i><i class="foil"></i><i class="foil"></i><i class="foil"></i><i class="foil"></i>'
        : [t.bg, t.accent, t.text, t.ok, t.border].map((c) => `<i style="background:${c}"></i>`).join('');
      b.innerHTML = `<div class="sw">${swatch}</div>
        <div class="nm">${esc(t.label || name)}</div>
        <div class="nt">${esc(t.note || 'custom')}</div>`;
      b.onclick = () => { applyTheme(name); saveCfg({ theme: name }); fillSkins(); };
      grid.append(b);
    });
  });

  const ui = state.cfg.ui;
  $('#tuner').innerHTML = `
    <label><span>Text size <b>${ui.fontSize}px</b></span>
      <input type="range" min="11" max="18" step="0.5" value="${ui.fontSize}" data-ui="fontSize"></label>
    <label><span>Corner radius <b>${ui.radius}px</b></span>
      <input type="range" min="0" max="18" value="${ui.radius}" data-ui="radius"></label>
    <label><span>Glow <b>${Math.round(ui.glow * 100)}%</b></span>
      <input type="range" min="0" max="1" step="0.05" value="${ui.glow}" data-ui="glow"></label>
    <label><span>Conversation</span>
      <select data-ui="layout">${['messages', 'terminal'].map((d) =>
        `<option ${d === (ui.layout || 'messages') ? 'selected' : ''}>${d}</option>`).join('')}</select></label>
    <label><span>Density</span>
      <select data-ui="density">${['compact', 'cozy', 'airy'].map((d) =>
        `<option ${d === ui.density ? 'selected' : ''}>${d}</option>`).join('')}</select></label>
    <label><span>Texture</span>
      <select data-ui="texture">${window.CT_TEXTURES.map((d) =>
        `<option ${d === ui.texture ? 'selected' : ''}>${d}</option>`).join('')}</select></label>`;

  $$('#tuner [data-ui]').forEach((inp) => {
    inp.oninput = () => {
      const key = inp.dataset.ui;
      const val = inp.type === 'range' ? parseFloat(inp.value) : inp.value;
      state.cfg.ui[key] = val;
      applyUI(state.cfg.ui);
      const b = inp.parentElement.querySelector('b');
      if (b) b.textContent = key === 'glow' ? Math.round(val * 100) + '%' : val + 'px';
    };
    inp.onchange = () => {
      saveCfg({ ui: state.cfg.ui });
      if (inp.dataset.ui === 'layout' && state.session) attachSession(state.session);
    };
  });

  const sw = $('#swatches');
  sw.textContent = '';
  const cur = state.theme;
  const tokens = document.body.dataset.surface === 'modern'
    ? [...window.CT_TOKENS, ...window.CT_TOKENS_MODERN]
    : window.CT_TOKENS;
  tokens.forEach(([k, label]) => {
    const w = el('label', 'swatch');
    w.innerHTML = `<input type="color" value="${cur[k] || '#000000'}"><span>${label}</span>`;
    w.querySelector('input').oninput = (e) => {
      document.documentElement.style.setProperty('--' + k, e.target.value);
      state.theme[k] = e.target.value;
      document.body.dataset.light = luminance(state.theme.bg) > 0.5 ? '1' : '0';
    };
    sw.append(w);
  });
}

function fillSettings() {
  const ui = state.cfg.ui;
  const b = state.boot;
  const box = $('#settings-body');
  const toggles = [
    ['animations', 'Animations', 'motion on blocks, glow and spinners'],
    ['showThinking', 'Show thinking', 'render Claude\'s reasoning blocks'],
    ['collapseTools', 'Collapse tool output', 'keep long results folded away'],
    ['showStderr', 'Show stderr', 'raw CLI warnings in the stream'],
    ['sound', 'Sound on finish', 'soft blip when a turn ends in a background tab'],
  ];
  box.innerHTML = toggles.map(([k, label, hint]) => `
    <div class="toggle-row">
      <div class="lab">${label}<small>${hint}</small></div>
      <button class="switch ${ui[k] ? 'on' : ''}" data-t="${k}"></button>
    </div>`).join('') + `
    <div class="toggle-row">
      <div class="lab">Conversation style<small>bubbles like Messages, or a flat terminal log</small></div>
      <select id="set-layout" style="width:150px;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:4px 7px">
        ${['messages', 'terminal'].map((d) => `<option value="${d}" ${d === (ui.layout || 'messages') ? 'selected' : ''}>${d}</option>`).join('')}
      </select>
    </div>
    <div class="toggle-row">
      <div class="lab">Default model<small>used for new sessions</small></div>
      <select id="set-model" style="width:150px;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:4px 7px">
        ${b.models.map((m) => `<option value="${m.id}" ${m.id === state.cfg.default_model ? 'selected' : ''}>${m.label}</option>`).join('')}
      </select>
    </div>
    <div class="toggle-row">
      <div class="lab">Default permissions<small>headless can't prompt — pick what you trust</small></div>
      <select id="set-mode" style="width:150px;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:4px 7px">
        ${b.permission_modes.map((m) => `<option value="${m.id}" ${m.id === state.cfg.default_permission_mode ? 'selected' : ''}>${m.label}</option>`).join('')}
      </select>
    </div>
    <div class="toggle-row">
      <div class="lab">Open browser on launch<small>when the launcher starts the server</small></div>
      <button class="switch ${state.cfg.open_browser ? 'on' : ''}" data-t2="open_browser"></button>
    </div>
    <p class="muted">CodeTails ${esc(b.version)} · ${esc(b.host)} · port ${b.config.port}<br>
      config lives beside the app in <code>config.json</code></p>`;

  $$('[data-t]', box).forEach((btn) => {
    btn.onclick = () => {
      const k = btn.dataset.t;
      const val = !state.cfg.ui[k];
      btn.classList.toggle('on', val);
      state.cfg.ui[k] = val;
      saveCfg({ ui: state.cfg.ui });
    };
  });
  $$('[data-t2]', box).forEach((btn) => {
    btn.onclick = () => {
      const k = btn.dataset.t2;
      const val = !state.cfg[k];
      btn.classList.toggle('on', val);
      saveCfg({ [k]: val });
    };
  });
  $('#set-layout', box).onchange = (e) => {
    state.cfg.ui.layout = e.target.value;
    saveCfg({ ui: state.cfg.ui });
    if (state.session) attachSession(state.session);      // re-lay the thread
  };
  $('#set-model', box).onchange = (e) => saveCfg({ default_model: e.target.value });
  $('#set-mode', box).onchange = (e) => saveCfg({ default_permission_mode: e.target.value });
}

/* ─────────────────────────────── palette ─────────────────────────────── */
const APP_CMDS = [
  { cmd: 'new session', desc: 'start Claude Code somewhere', run: () => openSheet('new') },
  { cmd: 'share link', desc: 'QR + tailnet URL for your phone', run: () => openSheet('share') },
  { cmd: 'skins', desc: 'change the look', run: () => openSheet('skins') },
  { cmd: 'settings', desc: 'preferences', run: () => openSheet('settings') },
  { cmd: 'stop', desc: 'interrupt the current turn', run: stop },
  { cmd: 'model: opus', desc: 'switch this session', run: () => setModel('opus') },
  { cmd: 'model: sonnet', desc: 'switch this session', run: () => setModel('sonnet') },
  { cmd: 'model: fable', desc: 'switch this session', run: () => setModel('fable') },
  { cmd: 'perms: accept edits', desc: 'auto-approve file edits', run: () => setMode('acceptEdits') },
  { cmd: 'perms: plan', desc: 'read-only planning mode', run: () => setMode('plan') },
  { cmd: 'perms: bypass', desc: 'no guardrails', run: () => setMode('bypassPermissions') },
];

let palIdx = 0;
function openPalette() {
  closeSheet();
  $('#palette').hidden = false;
  document.body.classList.add('scrim');
  const inp = $('#palette-input');
  inp.value = ''; palIdx = 0;
  paintPalette('');
  setTimeout(() => inp.focus(), 20);
}

function paletteItems(q) {
  const slash = (state.session?.commands || state.commands || []).map((c) => ({
    cmd: '/' + c.name, desc: c.description || 'slash command',
    run: () => { const i = $('#input'); i.value = '/' + c.name + ' '; closeSheet(); i.focus(); grow(); },
  }));
  const all = [...APP_CMDS, ...slash];
  if (!q) return all.slice(0, 40);
  const ql = q.toLowerCase();
  return all.filter((c) => (c.cmd + ' ' + c.desc).toLowerCase().includes(ql)).slice(0, 40);
}

function paintPalette(q) {
  const list = $('#palette-list');
  const items = paletteItems(q);
  palIdx = Math.min(palIdx, Math.max(0, items.length - 1));
  list.textContent = '';
  items.forEach((it, i) => {
    const r = el('button', 'p-row' + (i === palIdx ? ' sel' : ''));
    r.innerHTML = `<span class="cmd">${esc(it.cmd)}</span><span class="desc">${esc(it.desc)}</span>`;
    r.onclick = () => { closeSheet(); it.run(); };
    list.append(r);
  });
  list._items = items;
}

/* ───────────────────────────── interactions ──────────────────────────── */
document.addEventListener('click', (e) => {
  const act = e.target.closest('[data-act]')?.dataset.act;
  if (act) {
    e.preventDefault();
    ({
      'toggle-drawer': toggleDrawer,
      'close-drawer': closeDrawerMobile,
      new: () => openSheet('new'),
      share: () => openSheet('share'),
      skins: () => openSheet('skins'),
      settings: () => openSheet('settings'),
      palette: openPalette,
      send: () => send(),
      stop,
      browse: () => browse(),
      create: () => createSession({
        cwd: $('#new-cwd').value.trim(),
        model: $('#new-model').value,
        permission_mode: $('#new-mode').value,
        effort: $('#new-effort').value,
      }),
      'cycle-mode': cycleMode,
      'cycle-model': cycleModel,
      'save-skin': saveSkin,
      'export-skin': exportSkin,
      'import-skin': importSkin,
      'reset-skin': () => { applyTheme(state.cfg.theme); fillSkins(); },
    }[act] || (() => {}))();
    return;
  }
  if (e.target.closest('[data-close]') || e.target.id === 'scrim') closeSheet();
  const ins = e.target.closest('[data-insert]');
  if (ins) {
    const i = $('#input');
    i.value = (i.value ? i.value.replace(/\s*$/, ' ') : '') + ins.dataset.insert;
    i.focus(); grow();
    document.body.classList.add('has-text');
  }
});

function toggleDrawer() {
  if (window.innerWidth <= 900) document.body.classList.toggle('drawer-open');
  else { state.drawerPinned = $('#app').classList.toggle('drawer-closed') ? 'closed' : 'open'; }
  document.body.classList.toggle('scrim', document.body.classList.contains('drawer-open'));
}
function closeDrawerMobile() {
  document.body.classList.remove('drawer-open');
  if (!$$('.sheet:not([hidden])').length) document.body.classList.remove('scrim');
}

async function cycleMode() {
  if (!state.session) return;
  const order = ['acceptEdits', 'plan', 'bypassPermissions', 'default'];
  const next = order[(order.indexOf(state.session.permission_mode) + 1) % order.length];
  await setMode(next);
  toast('permissions → ' + next, 'ok');
}
async function cycleModel() {
  if (!state.session) return;
  const ids = state.boot.models.map((m) => m.id);
  await setModel(ids[(ids.indexOf(state.session.model) + 1) % ids.length]);
}

function saveSkin() {
  const name = prompt('Name this skin');
  if (!name) return;
  const key = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
  const custom = { ...(state.cfg.custom_themes || {}) };
  custom[key] = { ...state.theme, label: name, note: 'yours' };
  saveCfg({ custom_themes: custom, theme: key });
  applyTheme(key, custom[key]);
  fillSkins();
  toast('skin saved', 'ok');
}
function exportSkin() {
  navigator.clipboard?.writeText(JSON.stringify(state.theme, null, 2));
  toast('skin JSON copied', 'ok');
}
function importSkin() {
  const raw = prompt('Paste skin JSON');
  if (!raw) return;
  try {
    const t = JSON.parse(raw);
    const key = (t.label || 'imported').toLowerCase().replace(/[^a-z0-9]+/g, '-');
    const custom = { ...(state.cfg.custom_themes || {}), [key]: t };
    saveCfg({ custom_themes: custom, theme: key });
    applyTheme(key, t); fillSkins(); toast('skin imported', 'ok');
  } catch (e) { toast('bad JSON', 'err'); }
}

const input = $('#input');
input.addEventListener('input', () => {
  grow();
  document.body.classList.toggle('has-text', !!input.value.trim());
});
input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !state.coarse && !e.isComposing) {
    e.preventDefault(); send();
  }
});

document.addEventListener('keydown', (e) => {
  const meta = e.metaKey || e.ctrlKey;
  if (meta && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); return; }
  if (meta && e.key.toLowerCase() === 'n') { e.preventDefault(); openSheet('new'); return; }
  if (meta && e.key.toLowerCase() === 'j') { e.preventDefault(); openSheet('skins'); return; }
  if (meta && e.key === '/') { e.preventDefault(); toggleDrawer(); return; }
  if (e.key === 'Escape') {
    if (!$('#palette').hidden || $$('.sheet:not([hidden])').length) { closeSheet(); return; }
    if (document.body.classList.contains('running')) { stop(); return; }
  }
  if (!$('#palette').hidden) {
    const items = $('#palette-list')._items || [];
    if (e.key === 'ArrowDown') { e.preventDefault(); palIdx = Math.min(palIdx + 1, items.length - 1); paintPalette($('#palette-input').value); }
    if (e.key === 'ArrowUp') { e.preventDefault(); palIdx = Math.max(palIdx - 1, 0); paintPalette($('#palette-input').value); }
    if (e.key === 'Enter') { e.preventDefault(); const it = items[palIdx]; closeSheet(); it?.run(); }
  }
});
$('#palette-input').addEventListener('input', (e) => { palIdx = 0; paintPalette(e.target.value); });

/* swipe from the left edge opens the drawer on a phone */
let touchX = null, touchY = null;
document.addEventListener('touchstart', (e) => {
  const t = e.touches[0]; touchX = t.clientX; touchY = t.clientY;
}, { passive: true });
document.addEventListener('touchend', (e) => {
  if (touchX == null) return;
  const t = e.changedTouches[0];
  const dx = t.clientX - touchX, dy = Math.abs(t.clientY - touchY);
  if (dy < 60 && window.innerWidth <= 900) {
    if (touchX < 26 && dx > 55) { document.body.classList.add('drawer-open', 'scrim'); }
    else if (dx < -55 && document.body.classList.contains('drawer-open')) closeDrawerMobile();
  }
  touchX = null;
}, { passive: true });

/* iOS shrinks the visual viewport for the keyboard but leaves dvh alone, which
   would bury the composer. Track it directly. */
if (window.visualViewport) {
  const vv = window.visualViewport;
  const fit = () => {
    const h = Math.round(vv.height);
    if (h < 220) return;                                // mid-resize garbage
    $('#app').style.height = h + 'px';
    if (document.activeElement === $('#input')) setTimeout(() => scrollBottom(), 60);
  };
  vv.addEventListener('resize', fit);
  vv.addEventListener('scroll', fit);
}

window.addEventListener('resize', () => {
  grow();
  if (state.drawerPinned) return;                     // respect an explicit choice
  $('#app').classList.toggle('drawer-closed', window.innerWidth <= 900);
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshLive(); });

boot().catch((e) => {
  $('#app').classList.remove('booting');
  $('#stream').innerHTML = `<div class="welcome"><h1>✻ CodeTails</h1>
    <p style="color:var(--err)">${esc(e.message)}</p>
    <p class="k">is the server still running?</p></div>`;
});
