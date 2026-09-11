// HF Moderation — Telegram Mini App server.
// Node.js >= 18, no external dependencies.
// Serves the SPA and a JSON API backed by the bot's database.json.
const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const ROOT = __dirname;
const BOT_DIR = path.join(ROOT, '..');
const PUBLIC_DIR = path.join(ROOT, 'public');
const THEME = '#7086ff';

// ---- env ----
function loadEnv() {
  const out = {};
  try {
    const raw = fs.readFileSync(path.join(BOT_DIR, '.env'), 'utf8');
    for (const line of raw.split(/\r?\n/)) {
      const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$/);
      if (m) out[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
  } catch (e) { /* .env is optional */ }
  return out;
}
const ENV = loadEnv();
const BOT_TOKEN = ENV.BOT_TOKEN || process.env.BOT_TOKEN || '';
const ADMIN_IDS = new Set(String(ENV.ADMIN_IDS || process.env.ADMIN_IDS || '')
  .split(',').map(s => parseInt(s.trim(), 10)).filter(n => Number.isInteger(n) && n > 0));

// ---- database.json access (shared with the Python bot) ----
const DB_PATH = process.env.DATA_DIR
  ? path.join(process.env.DATA_DIR, 'database.json')
  : path.join(BOT_DIR, 'database.json');
let dbWriteQueue = Promise.resolve();

function readDb() {
  try {
    const raw = fs.readFileSync(DB_PATH, 'utf8');
    return JSON.parse(raw.trim() || '{}');
  } catch (e) {
    return { users: {}, keys: {}, applications: [], mod_versions: [], stats: {} };
  }
}

function writeDb(data) {
  // Serialize writes and keep a .bak like the Python bot does.
  dbWriteQueue = dbWriteQueue.then(() => new Promise((resolve) => {
    try {
      if (fs.existsSync(DB_PATH)) fs.copyFileSync(DB_PATH, DB_PATH.replace(/\.json$/, '.json.bak'));
      const tmp = DB_PATH + '.tmp' + Date.now();
      fs.writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
      fs.renameSync(tmp, DB_PATH);
    } catch (e) {
      console.error('[db] write failed:', e.message);
    }
    resolve();
  }));
  return dbWriteQueue;
}

const ALL_ROLES = ["HW: Стажер", "HW: Мл. Сотрудник", "HW: Сотрудник", "HW: Мл.Спектатор", "HW: Спектатор", "HW: Ст.Сотрудник",
  "FT: Стажер", "FT: Staff", "FT: Агент", "Зам Куратора", "Куратор", "Админ", "СтАдмин", "Владелец"];

// ---- helpers ----
function secretKey() {
  return crypto.createHmac('sha256', 'WebAppData').update(BOT_TOKEN).digest();
}

function validateInitData(initData) {
  if (!BOT_TOKEN || !initData) return null;
  const params = new URLSearchParams(initData);
  const hash = params.get('hash');
  if (!hash) return null;
  params.delete('hash');
  const dataCheckString = [...params.entries()]
    .map(([k, v]) => `${k}=${v}`).sort().join('\n');
  const calculated = crypto.createHmac('sha256', secretKey()).update(dataCheckString).digest('hex');
  if (calculated !== hash) return null;
  const authDate = parseInt(params.get('auth_date') || '0', 10);
  if (!authDate || Math.abs(Date.now() / 1000 - authDate) > 86400) return null; // 24h
  let user = null;
  try { user = JSON.parse(params.get('user') || 'null'); } catch (e) { user = null; }
  return user;
}

function signToken(userId) {
  const payload = `${userId}.${Date.now()}`;
  const sig = crypto.createHmac('sha256', BOT_TOKEN || 'dev').update(payload).digest('hex').slice(0, 32);
  return Buffer.from(`${payload}.${sig}`).toString('base64url');
}

function parseToken(token) {
  try {
    const parts = Buffer.from(token, 'base64url').toString('utf8').split('.');
    if (parts.length !== 3) return null;
    const sig = crypto.createHmac('sha256', BOT_TOKEN || 'dev')
      .update(`${parts[0]}.${parts[1]}`).digest('hex').slice(0, 32);
    if (sig !== parts[2]) return null;
    const ts = parseInt(parts[1], 10);
    if (!ts || Date.now() - ts > 30 * 86400000) return null; // 30 days
    return parseInt(parts[0], 10);
  } catch (e) { return null; }
}

function nowStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function publicUser(db, u) {
  const uid = String(u.telegram_id);
  const uuid = db.uuids && db.uuids[uid] ? db.uuids[uid] : null;
  return {
    telegram_id: u.telegram_id,
    username: u.username || '',
    nickname: u.nickname || '',
    role: u.role || '',
    mode: u.mode || '',
    modes: u.modes || [u.mode].filter(Boolean),
    is_approved: !!u.is_approved,
    is_banned: !!u.is_banned,
    days: u.days || 0,
    key_code: u.key_code || '',
    discord_tag: u.discord_tag || '',
    created_at: u.created_at || '',
    uuid,
    is_admin: ADMIN_IDS.has(u.telegram_id),
    stats: db.stats && db.stats[uid] ? db.stats[uid] : { bans: 0, mutes: 0, checks: 0 },
    settings: u.settings || {},
  };
}

function ensureUuid(db, uid) {
  db.uuids = db.uuids || {};
  if (!db.uuids[uid]) {
    const used = Object.values(db.uuids);
    db.uuids[uid] = used.length ? Math.max(...used) + 1 : 1;
  }
  return db.uuids[uid];
}

function logsOf(db, uid, kind) {
  const out = [];
  if (kind !== 'checks' && db.punishment_logs && db.punishment_logs[uid]) {
    for (const item of db.punishment_logs[uid]) out.push({ ...item, kind: 'punishment' });
  }
  if (kind !== 'punishments' && db.check_logs && db.check_logs[uid]) {
    for (const item of db.check_logs[uid]) out.push({ ...item, kind: 'check' });
  }
  out.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  return out;
}

// ---- api ----
const routes = [];
function route(method, pattern, handler, opts = {}) {
  routes.push({ method, pattern: new RegExp('^' + pattern + '$'), handler, opts });
}

route('POST', '/api/auth', (ctx) => {
  const user = validateInitData(ctx.body.initData);
  if (!user) return ctx.json(401, { error: 'Не удалось подтвердить данные Telegram' });
  const db = readDb();
  const uid = String(user.id);
  db.users[uid] = db.users[uid] || {};
  const u = db.users[uid];
  u.telegram_id = user.id;
  u.username = u.username || user.username || String(user.id);
  // Регистрация без ключа: ждём активации, но профиль создаём сразу.
  if (!u.created_at) { u.created_at = nowStr(); u.is_approved = u.is_approved || 0; }
  ensureUuid(db, uid);
  db.stats[uid] = db.stats[uid] || { user_id: user.id, bans: 0, mutes: 0, checks: 0 };
  return writeDb(db).then(() => ctx.json(200, {
    token: signToken(user.id),
    user: publicUser(db, u),
  }));
}, { public: true });

route('GET', '/api/me', (ctx) => {
  const db = readDb();
  const u = db.users[String(ctx.userId)];
  if (!u) return ctx.json(404, { error: 'Профиль не найден' });
  return ctx.json(200, { user: publicUser(db, u) });
});

route('GET', '/api/logs/(punishments|checks|all)', (ctx) => {
  const db = readDb();
  const kind = ctx.params[0] === 'all' ? 'all' : ctx.params[0];
  return ctx.json(200, { logs: logsOf(db, String(ctx.userId), kind) });
});

// ---- sessions (входы в клиент) ----
route('GET', '/api/sessions', (ctx) => {
  const db = readDb();
  const all = Object.values(db.login_requests || {})
    .filter(r => String(r.owner_id) === String(ctx.userId))
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
    .slice(0, 50);
  const pending = all.filter(r => r.status === 'pending');
  return ctx.json(200, { pending, history: all.filter(r => r.status !== 'pending') });
});

route('POST', '/api/sessions/decide', async (ctx) => {
  const { id, decision } = ctx.body;
  if (!id || !['approved', 'declined'].includes(decision)) return ctx.json(400, { error: 'Неверный запрос' });
  const db = readDb();
  const item = (db.login_requests || {})[id];
  if (!item || String(item.owner_id) !== String(ctx.userId)) return ctx.json(404, { error: 'Заявка не найдена' });
  if (item.status !== 'pending') return ctx.json(409, { error: 'Заявка уже обработана' });
  item.status = decision;
  item.updated_at = nowStr();
  if (decision === 'declined') {
    const u = db.users[String(ctx.userId)];
    if (u) u.client_kicked = true;
  }
  await writeDb(db);
  return ctx.json(200, { ok: true, status: decision });
});

// ---- admin: модераторы ----
function requireAdmin(ctx) {
  const db = readDb();
  const u = db.users[String(ctx.userId)];
  if (!u || !ADMIN_IDS.has(u.telegram_id)) {
    ctx.json(403, { error: 'Только для администраторов' });
    return null;
  }
  return db;
}

route('GET', '/api/moderators', (ctx) => {
  const db = requireAdmin(ctx);
  if (!db) return;
  const list = Object.values(db.users).map(u => {
    const p = publicUser(db, u);
    const s = p.stats;
    return { ...p, total: (s.bans || 0) + (s.mutes || 0) + (s.checks || 0) };
  }).sort((a, b) => b.total - a.total);
  return ctx.json(200, { moderators: list });
});

route('GET', '/api/moderator/(\\d+)', (ctx) => {
  const db = requireAdmin(ctx);
  if (!db) return;
  const uid = ctx.params[0];
  const u = db.users[uid];
  if (!u) return ctx.json(404, { error: 'Модератор не найден' });
  const key = u.key_code && db.keys[u.key_code] ? { ...db.keys[u.key_code] } : null;
  return ctx.json(200, {
    moderator: publicUser(db, u),
    logs: logsOf(db, uid, 'all'),
    key: key ? { code: key.key_code || key.key, days: key.days, is_used: key.is_used, created_at: key.created_at } : null,
  });
});

route('POST', '/api/moderator/(\\d+)/(ban|unban|kick|delete)', async (ctx) => {
  const db = requireAdmin(ctx);
  if (!db) return;
  const uid = ctx.params[0], action = ctx.params[1];
  const u = db.users[uid];
  if (!u) return ctx.json(404, { error: 'Модератор не найден' });
  if (action === 'ban') u.is_banned = 1;
  if (action === 'unban') u.is_banned = 0;
  if (action === 'kick') { delete u.client_kicked; u.client_kicked = true; }
  if (action === 'delete') {
    delete db.users[uid];
    if (u.key_code && db.keys[u.key_code]) delete db.keys[u.key_code];
  }
  await writeDb(db);
  return ctx.json(200, { ok: true });
});

// ---- admin: ключи ----
function keyView(k) {
  return { key_code: k.key_code || k.key, target_nickname: k.target_nickname, role: k.role, mode: k.mode,
    days: k.days || 30, is_used: !!k.is_used, is_active: k.is_active !== 0, created_at: k.created_at || '',
    used_at: k.used_at || '', used_by: k.used_by || null };
}

route('GET', '/api/keys', (ctx) => {
  const db = requireAdmin(ctx);
  if (!db) return;
  const keys = Object.values(db.keys).map(keyView)
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
  return ctx.json(200, { keys });
});

route('POST', '/api/keys/create', async (ctx) => {
  const db = requireAdmin(ctx);
  if (!db) return;
  const { nickname, role, mode, days } = ctx.body;
  if (!nickname || !ALL_ROLES.includes(role)) return ctx.json(400, { error: 'Укажите ник и роль' });
  const code = 'HF-' + crypto.randomBytes(7).toString('hex').toUpperCase();
  db.keys[code] = {
    key: code, key_code: code, target_nickname: String(nickname).slice(0, 32), role,
    mode: mode || 'HolyWorld', days: Math.max(1, parseInt(days, 10) || 30),
    is_used: 0, created_by: ctx.userId, created_at: nowStr(),
  };
  await writeDb(db);
  return ctx.json(200, { code });
});

route('POST', '/api/keys/([A-Za-z0-9-]+)/(revoke|reset|delete|extend)', async (ctx) => {
  const db = requireAdmin(ctx);
  if (!db) return;
  const code = ctx.params[0], action = ctx.params[1];
  const key = db.keys[code];
  if (!key) return ctx.json(404, { error: 'Ключ не найден' });
  if (action === 'revoke') {
    key.is_used = 1;
    key.revoked = 1;
    const u = Object.values(db.users).find(x => x.key_code === code);
    if (u) { u.is_approved = 0; u.client_kicked = true; }
  }
  if (action === 'reset') {
    key.is_used = 0;
    delete key.used_by;
    delete key.used_at;
    delete key.revoked;
    for (const u of Object.values(db.users)) {
      if (u.key_code === code) { u.is_approved = 0; delete u.key_code; }
    }
  }
  if (action === 'delete') delete db.keys[code];
  if (action === 'extend') {
    key.days = Math.max(1, parseInt(ctx.body.days, 10) || key.days);
    const u = Object.values(db.users).find(x => x.key_code === code);
    if (u) u.days = key.days;
  }
  await writeDb(db);
  return ctx.json(200, { ok: true, key: keyView(key) });
});

// ---- devlogs (версии мода) ----
route('GET', '/api/devlogs', (ctx) => {
  const db = readDb();
  const list = [...(db.mod_versions || [])].reverse();
  return ctx.json(200, { devlogs: list });
});

route('POST', '/api/devlogs', async (ctx) => {
  const db = requireAdmin(ctx);
  if (!db) return;
  const { version, changelog } = ctx.body;
  if (!version) return ctx.json(400, { error: 'Укажите версию' });
  db.mod_versions = db.mod_versions || [];
  if (db.mod_versions.some(m => String(m.version_name).toLowerCase() === String(version).toLowerCase())) {
    return ctx.json(409, { error: 'Такая версия уже есть' });
  }
  db.mod_versions.push({
    id: db.mod_versions.reduce((m, x) => Math.max(m, +x.id || 0), 0) + 1,
    version_name: String(version).slice(0, 32),
    changelog: String(changelog || '').slice(0, 1000),
    file_id: '', allowed_roles: ALL_ROLES, created_at: nowStr(),
  });
  await writeDb(db);
  return ctx.json(200, { ok: true });
});

// ---- статистика ----
route('GET', '/api/stats', (ctx) => {
  const db = readDb();
  const users = Object.values(db.users);
  const totals = { bans: 0, mutes: 0, checks: 0 };
  for (const s of Object.values(db.stats || {})) {
    totals.bans += +s.bans || 0; totals.mutes += +s.mutes || 0; totals.checks += +s.checks || 0;
  }
  const byRole = {};
  for (const u of users) byRole[u.role] = (byRole[u.role] || 0) + 1;
  const top = users.map(u => {
    const s = (db.stats || {})[String(u.telegram_id)] || {};
    return { nickname: u.nickname, role: u.role,
      total: (+s.bans || 0) + (+s.mutes || 0) + (+s.checks || 0) };
  }).sort((a, b) => b.total - a.total).slice(0, 10);
  return ctx.json(200, {
    users: users.length,
    keys: Object.keys(db.keys || {}).length,
    activeKeys: Object.values(db.keys || {}).filter(k => k.is_used && k.is_active !== 0).length,
    totals, byRole, top,
  });
});

// ---- http ----
const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.png': 'image/png', '.svg': 'image/svg+xml', '.json': 'application/json' };

function serveStatic(req, res, urlPath) {
  let file = urlPath === '/' ? '/index.html' : urlPath;
  const full = path.normalize(path.join(PUBLIC_DIR, file));
  if (!full.startsWith(PUBLIC_DIR)) { res.writeHead(403); return res.end(); }
  fs.readFile(full, (err, data) => {
    if (err) { res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }); return res.end('404'); }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(full)] || 'application/octet-stream' });
    res.end(data);
  });
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, 'http://localhost');
  const pathname = decodeURIComponent(url.pathname);
  if (!pathname.startsWith('/api/')) return serveStatic(req, res, pathname);

  let raw = '';
  req.on('data', chunk => { raw += chunk; if (raw.length > 1e6) req.destroy(); });
  req.on('end', async () => {
    let body = {};
    try { body = raw ? JSON.parse(raw) : {}; } catch (e) { /* empty body */ }

    const match = routes.find(r => r.method === req.method && r.pattern.test(pathname));
    if (!match) { res.writeHead(404, { 'Content-Type': 'application/json' }); return res.end('{"error":"not found"}'); }

    const ctx = {
      body,
      params: (pathname.match(match.pattern) || []).slice(1),
      json: (code, data) => {
        res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(data));
      },
    };

    if (!match.opts.public) {
      const token = (req.headers.authorization || '').replace(/^Bearer\s+/i, '');
      ctx.userId = parseToken(token);
      if (!ctx.userId) return ctx.json(401, { error: 'Требуется авторизация через Telegram' });
    }
    try {
      await match.handler(ctx);
    } catch (e) {
      console.error('[api]', e);
      if (!res.headersSent) ctx.json(500, { error: 'Внутренняя ошибка' });
    }
  });
});

const PORT = parseInt(process.env.PORT || '8787', 10);
server.listen(PORT, () => console.log(`[miniapp] http://localhost:${PORT}`));
