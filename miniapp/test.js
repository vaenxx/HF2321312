// E2E-тест miniapp API с настоящей подписью initData.
const crypto = require('crypto');
const fs = require('fs');
const env = fs.readFileSync(__dirname + '/../.env', 'utf8');
const token = env.match(/BOT_TOKEN=(\S+)/)[1];
const user = { id: 7918461586, username: 'sovesz', first_name: 'sovesz' };
const params = new URLSearchParams();
params.set('user', JSON.stringify(user));
params.set('auth_date', String(Math.floor(Date.now() / 1000)));
params.set('query_id', 'test');
const dcs = [...params.entries()].map(([k, v]) => k + '=' + v).sort().join('\n');
const secret = crypto.createHmac('sha256', 'WebAppData').update(token).digest();
params.set('hash', crypto.createHmac('sha256', secret).update(dcs).digest('hex'));
(async () => {
  const auth = await fetch('http://localhost:8787/api/auth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ initData: params.toString() }) });
  const a = await auth.json();
  console.log('auth:', auth.status, 'token:', a.token ? 'ok' : a.error);
  const h = { Authorization: 'Bearer ' + a.token, 'Content-Type': 'application/json' };
  const me = await (await fetch('http://localhost:8787/api/me', { headers: h })).json();
  console.log('me:', me.user && me.user.nickname, 'uuid=' + (me.user && me.user.uuid), 'admin=' + (me.user && me.user.is_admin));
  const mods = await (await fetch('http://localhost:8787/api/moderators', { headers: h })).json();
  console.log('moderators:', mods.moderators ? mods.moderators.length : mods.error);
  const keys = await (await fetch('http://localhost:8787/api/keys', { headers: h })).json();
  console.log('keys:', keys.keys ? keys.keys.length : keys.error);
  const stats = await (await fetch('http://localhost:8787/api/stats', { headers: h })).json();
  console.log('stats: users=' + stats.users, 'keys=' + stats.keys);
  const sess = await (await fetch('http://localhost:8787/api/sessions', { headers: h })).json();
  console.log('sessions: pending=' + sess.pending.length, 'history=' + sess.history.length);
})().catch(e => console.error('FAIL', e.message));
