// HF Moderation — Telegram Mini App frontend.
const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();
tg?.setHeaderColor('#0e0f13');
tg?.setBackgroundColor('#0e0f13');

const state = { token: null, me: null, view: 'profile', sessionTimer: null };

const $view = document.getElementById('view');
const $tabs = document.getElementById('tabs');

// ---------- api ----------
async function api(path, options = {}) {
  const res = await fetch(path, {
    method: options.method || 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(state.token ? { Authorization: 'Bearer ' + state.token } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || 'Ошибка ' + res.status);
  return data;
}

function toast(text) {
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------- auth ----------
async function init() {
  const initData = tg?.initData || '';
  try {
    const data = await api('/api/auth', { method: 'POST', body: { initData } });
    state.token = data.token;
    state.me = data.user;
  } catch (e) {
    $view.innerHTML = `<div class="card"><div class="empty">${esc(e.message)}</div></div>`;
    return;
  }
  document.getElementById('me-role').textContent = state.me.role || '';
  buildTabs();
  show(state.me.is_admin ? 'sessions' : 'profile');
  pollSessions();
}

// ---------- tabs ----------
const TABS = [
  { id: 'profile', name: 'Профиль', ic: '👤', show: () => true },
  { id: 'punishments', name: 'Наказания', ic: '🔨', show: () => true },
  { id: 'checks', name: 'Проверки', ic: '🎯', show: () => true },
  { id: 'sessions', name: 'Сессии', ic: '🔐', show: () => true },
  { id: 'keys', name: 'Ключи', ic: '🔑', show: () => state.me?.is_admin },
  { id: 'moderators', name: 'Модераторы', ic: '🛡️', show: () => state.me?.is_admin },
  { id: 'stats', name: 'Статистика', ic: '📊', show: () => state.me?.is_admin },
  { id: 'devlogs', name: 'Devlog', ic: '📰', show: () => true },
];

function buildTabs() {
  $tabs.innerHTML = '';
  for (const tab of TABS.filter(t => t.show())) {
    const btn = document.createElement('button');
    btn.innerHTML = `<span class="ic">${tab.ic}</span>${tab.name}`;
    btn.dataset.view = tab.id;
    btn.onclick = () => show(tab.id);
    $tabs.appendChild(btn);
  }
}

function show(view, arg) {
  state.view = view;
  document.querySelectorAll('.tabs button').forEach(b =>
    b.classList.toggle('active', b.dataset.view === view));
  const renderers = {
    profile: renderProfile,
    punishments: () => renderLogs('punishments'),
    checks: () => renderLogs('checks'),
    sessions: renderSessions,
    keys: renderKeys,
    moderators: renderModerators,
    moderator: () => renderModerator(arg),
    stats: renderStats,
    devlogs: renderDevlogs,
  };
  (renderers[view] || renderProfile)();
}

// ---------- профиль ----------
async function renderProfile() {
  const { user: me } = await api('/api/me');
  state.me = me;
  const days = me.days ? `${me.days} дн.` : '—';
  $view.innerHTML = `
    <div class="card">
      <div class="row"><span class="k">Ник</span><span class="v"><b>${esc(me.nickname)}</b></span></div>
      <div class="row"><span class="k">ID регистрации</span><span class="v">#${me.uuid ?? '—'}</span></div>
      <div class="row"><span class="k">Должность</span><span class="v"><span class="badge accent">${esc(me.role)}</span></span></div>
      <div class="row"><span class="k">Статус</span><span class="v">${
        me.is_banned ? '<span class="badge bad">Заблокирован</span>'
        : me.is_approved ? '<span class="badge ok">Активен</span>'
        : '<span class="badge warn">Ожидает ключ</span>'}</span></div>
      <div class="row"><span class="k">Подписка</span><span class="v">${days}</span></div>
      <div class="row"><span class="k">Режимы</span><span class="v">${esc((me.modes || []).join(', '))}</span></div>
      <div class="row"><span class="k">Discord</span><span class="v">${esc(me.discord_tag || '—')}</span></div>
      <div class="row"><span class="k">В штабе с</span><span class="v">${esc(me.created_at || '—')}</span></div>
    </div>
    <div class="card">
      <h3>Активность</h3>
      <div class="grid">
        <div class="stat"><b>${me.stats.bans || 0}</b><span>Баны</span></div>
        <div class="stat"><b>${me.stats.mutes || 0}</b><span>Муты</span></div>
        <div class="stat"><b>${me.stats.checks || 0}</b><span>Проверки</span></div>
      </div>
    </div>`;
}

// ---------- наказания / проверки ----------
async function renderLogs(kind) {
  const { logs } = await api('/api/logs/' + kind);
  if (!logs.length) {
    $view.innerHTML = `<div class="card"><div class="empty">Пока пусто</div></div>`;
    return;
  }
  $view.innerHTML = `<div class="card">` + logs.map(l => `
    <div class="log-item">
      <div class="log-top"><b>${esc(l.target)}</b><span class="muted">${esc(l.created_at)}</span></div>
      <div class="log-sub">${esc(l.action)}${l.duration ? ' · ' + esc(l.duration) : ''}${l.reason ? ' · ' + esc(l.reason) : ''}</div>
    </div>`).join('') + `</div>`;
}

// ---------- сессии ----------
async function renderSessions() {
  const { pending, history } = await api('/api/sessions');
  const cards = pending.map(s => `
    <div class="card session-card" data-id="${esc(s.id)}">
      <h3>🔐 Попытка входа в клиент</h3>
      <div class="row"><span class="k">Ключ</span><span class="v">${esc(s.key_code)}</span></div>
      <div class="row"><span class="k">IP</span><span class="v">${esc(s.ip || '—')}</span></div>
      <div class="row"><span class="k">Регион</span><span class="v">${esc(s.location || '—')}</span></div>
      <div class="row"><span class="k">Время</span><span class="v">${esc(s.created_at)}</span></div>
      <div class="session-actions">
        <button class="action danger" data-decide="declined">Отклонить</button>
        <button class="action" data-decide="approved">Принять</button>
      </div>
    </div>`).join('');
  const hist = history.length ? `<div class="section-title">История</div><div class="card">` + history.map(s => `
    <div class="log-item session-card ${s.status}">
      <div class="log-top"><span>${esc(s.ip || '—')} · ${esc(s.location || '—')}</span>
      <span class="badge ${s.status === 'approved' ? 'ok' : 'bad'}">${s.status === 'approved' ? 'Принят' : 'Отклонён'}</span></div>
      <div class="log-sub">${esc(s.created_at)}</div>
    </div>`).join('') + `</div>` : '';
  $view.innerHTML = (pending.length ? cards : `<div class="card"><div class="empty">Активных попыток входа нет</div></div>`) + hist;

  $view.querySelectorAll('[data-decide]').forEach(btn => {
    btn.onclick = async () => {
      const card = btn.closest('.session-card');
      try {
        await api('/api/sessions/decide', { method: 'POST', body: { id: card.dataset.id, decision: btn.dataset.decide } });
        toast(btn.dataset.decide === 'approved' ? 'Вход принят' : 'Вход отклонён');
        renderSessions();
      } catch (e) { toast(e.message); }
    };
  });
}

// опрос новых попыток входа даже на других вкладках
function pollSessions() {
  setInterval(async () => {
    if (!state.token) return;
    try {
      const { pending } = await api('/api/sessions');
      if (pending.length && state.view !== 'sessions') {
        toast(`🔐 Попытка входа: ${pending[0].ip || 'неизвестный IP'}`);
        tg?.HapticFeedback?.notificationOccurred('warning');
      }
    } catch (e) { /* offline — тихо */ }
  }, 5000);
}

// ---------- ключи (админ) ----------
async function renderKeys() {
  const { keys } = await api('/api/keys');
  const rows = keys.map(k => `
    <div class="log-item">
      <div class="log-top"><b>${esc(k.target_nickname)}</b>
        <span class="badge ${k.revoked ? 'bad' : k.is_used ? 'ok' : 'warn'}">${k.revoked ? 'Отозван' : k.is_used ? 'Активен' : 'Свободен'}</span></div>
      <div class="key-line">
        <span class="key-code" title="Ключ">${esc(k.key_code)}</span>
        <button class="eye" data-eye>👁</button>
      </div>
      <div class="log-sub">${esc(k.role)} · ${k.days} дн. · создан ${esc(k.created_at)}${k.used_at ? ' · активирован ' + esc(k.used_at) : ''}</div>
      <div class="btn-row">
        ${k.is_used ? `<button class="action secondary" data-act="reset">Сбросить</button>` : ''}
        <button class="action secondary" data-act="extend" data-days="${(k.days || 30) + 30}">+30 дн.</button>
        ${k.is_used ? `<button class="action danger" data-act="revoke">Отозвать</button>` : ''}
        <button class="action ghost" data-act="delete">Удалить</button>
      </div>
    </div>`).join('');

  $view.innerHTML = `
    <div class="card">
      <h3>Выдать ключ</h3>
      <input id="k-nick" placeholder="Ник игрока">
      <select id="k-role">${["HW: Стажер","HW: Мл. Сотрудник","HW: Сотрудник","HW: Мл.Спектатор","HW: Спектатор","HW: Ст.Сотрудник","FT: Стажер","FT: Staff","FT: Агент","Зам Куратора","Куратор","Админ","СтАдмин","Владелец"]
        .map(r => `<option>${r}</option>`).join('')}</select>
      <div class="btn-row" style="margin-top:0">
        <input id="k-days" type="number" value="30" min="1" style="margin:0">
        <button class="action" id="k-create" style="flex:2">Создать</button>
      </div>
    </div>
    <div class="card">${rows || '<div class="empty">Ключей нет</div>'}</div>`;

  // глазик: клик — фикс-показ, ховер — временный
  $view.querySelectorAll('[data-eye]').forEach(eye => {
    eye.onclick = () => {
      const code = eye.parentElement.querySelector('.key-code');
      code.classList.toggle('revealed');
      eye.classList.toggle('on');
    };
  });
  $view.querySelectorAll('[data-act]').forEach(btn => {
    btn.onclick = async () => {
      const item = btn.closest('.log-item');
      const code = item.querySelector('.key-code').textContent.trim();
      try {
        await api('/api/keys/' + encodeURIComponent(code) + '/' + btn.dataset.act, {
          method: 'POST', body: { days: btn.dataset.days } });
        toast('Готово');
        renderKeys();
      } catch (e) { toast(e.message); }
    };
  });
  document.getElementById('k-create').onclick = async () => {
    try {
      const { code } = await api('/api/keys/create', { method: 'POST', body: {
        nickname: document.getElementById('k-nick').value.trim(),
        role: document.getElementById('k-role').value,
        days: document.getElementById('k-days').value,
      }});
      await tg?.clipboard?.writeText?.(code);
      toast('Ключ создан: ' + code);
      renderKeys();
    } catch (e) { toast(e.message); }
  };
}

// ---------- модераторы (админ) ----------
async function renderModerators() {
  const { moderators } = await api('/api/moderators');
  $view.innerHTML = `<div class="card">` + moderators.map(m => `
    <div class="moderator-item" data-id="${m.telegram_id}">
      <div class="avatar">${esc((m.nickname || '?')[0].toUpperCase())}</div>
      <div class="info">
        <b>${esc(m.nickname)} ${m.is_banned ? '<span class="badge bad">Бан</span>' : ''}</b>
        <span>${esc(m.role)}</span>
      </div>
      <div class="total"><b>${m.total}</b><span>действий</span></div>
    </div>`).join('') + `</div>`;

  $view.querySelectorAll('.moderator-item').forEach(item => {
    item.onclick = () => show('moderator', item.dataset.id);
  });
}

async function renderModerator(id) {
  const { moderator: m, logs, key } = await api('/api/moderator/' + id);
  $view.innerHTML = `
    <button class="action ghost" id="back" style="margin-bottom:10px">← К списку</button>
    <div class="card">
      <div class="row"><span class="k">Ник</span><span class="v"><b>${esc(m.nickname)}</b></span></div>
      <div class="row"><span class="k">ID регистрации</span><span class="v">#${m.uuid ?? '—'}</span></div>
      <div class="row"><span class="k">Должность</span><span class="v"><span class="badge accent">${esc(m.role)}</span></span></div>
      <div class="row"><span class="k">Статус</span><span class="v">${m.is_banned ? '<span class="badge bad">Заблокирован</span>' : '<span class="badge ok">Активен</span>'}</span></div>
      <div class="row"><span class="k">TG</span><span class="v">@${esc(m.username)}</span></div>
      <div class="row"><span class="k">Discord</span><span class="v">${esc(m.discord_tag || '—')}</span></div>
      <div class="row"><span class="k">Ключ</span><span class="v">${key ? esc(key.code.slice(0, 10)) + '…' : '—'}</span></div>
      <div class="row"><span class="k">В штабе с</span><span class="v">${esc(m.created_at)}</span></div>
    </div>
    <div class="card">
      <div class="grid">
        <div class="stat"><b>${m.stats.bans || 0}</b><span>Баны</span></div>
        <div class="stat"><b>${m.stats.mutes || 0}</b><span>Муты</span></div>
        <div class="stat"><b>${m.stats.checks || 0}</b><span>Проверки</span></div>
      </div>
    </div>
    <div class="card">
      <h3>Действия</h3>
      <div class="btn-row">
        ${m.is_banned
          ? `<button class="action secondary" data-mod="unban">Разблокировать</button>`
          : `<button class="action danger" data-mod="ban">Заблокировать</button>`}
        <button class="action danger" data-mod="kick">Выгнать из клиента</button>
        <button class="action ghost" data-mod="delete">Удалить профиль</button>
      </div>
    </div>
    <div class="card"><h3>Последние действия</h3>
      ${logs.slice(0, 25).map(l => `
        <div class="log-item">
          <div class="log-top"><b>${esc(l.target)}</b><span class="muted">${esc(l.created_at)}</span></div>
          <div class="log-sub">${esc(l.action)}${l.duration ? ' · ' + esc(l.duration) : ''}${l.reason ? ' · ' + esc(l.reason) : ''}</div>
        </div>`).join('') || '<div class="empty">Пусто</div>'}
    </div>`;

  document.getElementById('back').onclick = () => show('moderators');
  $view.querySelectorAll('[data-mod]').forEach(btn => {
    btn.onclick = async () => {
      if (btn.dataset.mod === 'delete' && !confirm('Удалить профиль и ключ?')) return;
      try {
        await api('/api/moderator/' + id + '/' + btn.dataset.mod, { method: 'POST' });
        toast('Готово');
        show('moderators');
      } catch (e) { toast(e.message); }
    };
  });
}

// ---------- статистика (админ) ----------
async function renderStats() {
  const s = await api('/api/stats');
  $view.innerHTML = `
    <div class="card">
      <h3>Общее</h3>
      <div class="grid">
        <div class="stat"><b>${s.users}</b><span>Модераторов</span></div>
        <div class="stat"><b>${s.activeKeys}</b><span>Активных ключей</span></div>
        <div class="stat"><b>${s.keys}</b><span>Всего ключей</span></div>
      </div>
    </div>
    <div class="card">
      <h3>Всего действий</h3>
      <div class="grid">
        <div class="stat"><b>${s.totals.bans}</b><span>Баны</span></div>
        <div class="stat"><b>${s.totals.mutes}</b><span>Муты</span></div>
        <div class="stat"><b>${s.totals.checks}</b><span>Проверки</span></div>
      </div>
    </div>
    <div class="card"><h3>Топ по активности</h3>
      ${s.top.map((m, i) => `
        <div class="log-item">
          <div class="log-top"><b>${i + 1}. ${esc(m.nickname)}</b><span class="badge accent">${m.total}</span></div>
          <div class="log-sub">${esc(m.role)}</div>
        </div>`).join('') || '<div class="empty">Пусто</div>'}
    </div>`;
}

// ---------- devlog ----------
async function renderDevlogs() {
  const { devlogs } = await api('/api/devlogs');
  const admin = state.me?.is_admin;
  $view.innerHTML = `
    ${admin ? `
    <div class="card">
      <h3>Новая запись</h3>
      <input id="d-version" placeholder="Версия, напр. HF 1.21.4-v3">
      <textarea id="d-changelog" rows="3" placeholder="Что нового..."></textarea>
      <button class="action" id="d-create">Опубликовать</button>
    </div>` : ''}
    <div class="card">
    ${devlogs.map(d => `
      <div class="log-item">
        <div class="log-top"><b>${esc(d.version_name)}</b><span class="muted">${esc(d.created_at)}</span></div>
        <div class="log-sub" style="white-space:pre-wrap">${esc(d.changelog || 'Без описания')}</div>
      </div>`).join('') || '<div class="empty">Записей нет</div>'}
    </div>`;
  const btn = document.getElementById('d-create');
  if (btn) btn.onclick = async () => {
    try {
      await api('/api/devlogs', { method: 'POST', body: {
        version: document.getElementById('d-version').value.trim(),
        changelog: document.getElementById('d-changelog').value.trim(),
      }});
      toast('Опубликовано');
      renderDevlogs();
    } catch (e) { toast(e.message); }
  };
}

init();
