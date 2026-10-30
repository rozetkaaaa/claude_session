// Cloudflare Worker: точный "будильник" для claude-session-keeper.
// Точный будильник (Durable Object alarm) просыпается ТОЛЬКО когда пора пинговать, без опроса. Если пора - запускает GitHub Actions (workflow_dispatch).
// Actions делает ОДИН запрос к Claude и присылает результат на /report. К Claude Worker сам не обращается.
const H = 3600e3, DAY = 864e5;

export function inActive(now, cfg) {
  if (!cfg.from || !cfg.to) return true;
  const d = new Date(now + cfg.tzOffsetH * H);
  const t = String(d.getUTCHours()).padStart(2, "0") + ":" + String(d.getUTCMinutes()).padStart(2, "0");
  return cfg.from <= cfg.to ? (t >= cfg.from && t <= cfg.to) : (t >= cfg.from || t <= cfg.to);
}

export function decide(st0, now, cfg) {
  const st = { armed: false, next_due: 0, blocked_until: 0, dispatches: 0, fails: 0, pings: [], ...st0 };
  st.pings = st.pings.filter(t => now - t < DAY);
  if (!st.armed || st.blocked_until > now || now < st.next_due || !inActive(now, cfg)) return { action: "wait", st };
  if (st.pings.length >= cfg.maxPings) {
    st.blocked_until = Math.min(...st.pings) + DAY;
    return { action: "blocked", reason: "дневной предохранитель", st };
  }
  if (st.dispatches >= 5) {   // 5 запусков подряд без отчёта: что-то сломано (токен GitHub, workflow)
    st.blocked_until = now + 12 * H; st.dispatches = 0;
    return { action: "blocked", reason: "5 запусков Actions без отчёта (проверьте GH_TOKEN/workflow)", st };
  }
  st.dispatches++; st.pings.push(now); st.next_due = now + 20 * 60e3;   // страховка: повтор через 20 мин, если отчёта не будет
  return { action: "dispatch", st };
}

export function applyReport(st0, r, now, leadMs = 0) {
  const st = { armed: false, next_due: 0, blocked_until: 0, dispatches: 0, fails: 0, pings: [], ...st0 };
  st.dispatches = 0;
  st.fails = r.failures > 0 ? st.fails + 1 : 0;
  st.next_due = Math.max((r.next_due * 1000 || 0) - leadMs, 0);
  st.blocked_until = r.blocked_until * 1000 > now ? r.blocked_until * 1000 : 0;
  st.reason = r.blocked_reason || null;
  if (st.fails >= 5) { st.blocked_until = now + 12 * H; st.reason = "5 сбоев подряд"; st.fails = 0; }
  return st;
}

export function parseAt(at, now, cfg) {
  if (!at) return now;
  const m = /^(\d{1,2}):(\d{2})$/.exec(at);
  if (m) {
    const loc = new Date(now + cfg.tzOffsetH * H);
    let t = Date.UTC(loc.getUTCFullYear(), loc.getUTCMonth(), loc.getUTCDate(), +m[1], +m[2]) - cfg.tzOffsetH * H;
    if (t < now - 30 * 60e3) t += DAY;       // время уже прошло давно -> это завтра
    return t;
  }
  const n = /^\d+$/.test(at) ? Number(at) : Date.parse(at);
  return Number.isFinite(n) ? n : now;
}
const fmtLocal = (t, cfg) => new Date(t + cfg.tzOffsetH * H).toISOString().replace("T", " ").slice(0, 16) + ` (UTC+${cfg.tzOffsetH})`;
const cfgOf = env => ({
  from: env.ACTIVE_FROM || "", to: env.ACTIVE_TO || "", tzOffsetH: Number(env.TZ_OFFSET_H ?? 5),
  maxPings: Number(env.MAX_PINGS_PER_DAY ?? 6),
  leadMs: Number(env.LEAD_S ?? 30) * 1000,
});

async function tg(env, text) {      // уведомления через ntfy (секрет NTFY_URL: адрес вашего топика)
  if (!env.NTFY_URL) return;
  await fetch(env.NTFY_URL.trim(), { method: "POST", headers: { Title: "Claude keeper" }, body: text }).catch(() => {});
}

async function notifyOnce(env, st, reason) {
  if (reason && st.notified !== reason) { st.notified = reason; await tg(env, reason + (st.blocked_until ? ` (до ${fmtLocal(st.blocked_until, cfgOf(env))})` : "")); }
  if (!reason) st.notified = null;
}

async function dispatch(env) {
  const r = await fetch(`https://api.github.com/repos/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW || "keeper.yml"}/dispatches`, {
    method: "POST",
    headers: { authorization: `Bearer ${(env.GH_TOKEN || "").trim()}`, accept: "application/vnd.github+json", "user-agent": "claude-keeper-worker", "x-github-api-version": "2022-11-28" },
    body: JSON.stringify({ ref: env.GH_BRANCH || "main" }),
  });
  if (!r.ok) throw new Error(`GitHub ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

// Когда в следующий раз проснуться (null = никогда, цикл остановлен)
export function nextWake(st, now) {
  if (!st.armed) return null;
  if (st.blocked_until > now) return st.blocked_until;
  if (st.next_due > now) return st.next_due;
  return now + 10 * 60e3;          // пора, но ждём active_hours
}

export class Keeper {
  constructor(state, env) { this.state = state; this.env = env; }
  async load() { return (await this.state.storage.get("st")) || {}; }
  async save(st) {
    await this.state.storage.put("st", st);
    const t = nextWake(st, Date.now());
    if (t) await this.state.storage.setAlarm(t); else await this.state.storage.deleteAlarm();
  }

  async alarm() {
    const { action, reason, st } = decide(await this.load(), Date.now(), cfgOf(this.env));
    if (action === "dispatch") {
      try { await dispatch(this.env); st.last_error = null; } catch (e) { st.last_error = String(e); }
    }
    if (action === "blocked") await notifyOnce(this.env, st, reason);
    await this.save(st);
  }

  async fetch(req) {
    const url = new URL(req.url), env = this.env, cfg = cfgOf(env);
    if (url.pathname === "/report" && req.method === "POST") {
      const st = applyReport(await this.load(), await req.json(), Date.now(), cfg.leadMs);
      await notifyOnce(env, st, st.blocked_until > Date.now() ? (st.reason || "пинги приостановлены") : null);
      await this.save(st); return new Response("ok");
    }
    if (url.pathname === "/start" && req.method === "POST") {
      // ?at=HH:MM (локальное время сброса сессии из /usage) | ISO | epoch мс | пусто = сейчас
      const now = Date.now(), st = await this.load();
      Object.assign(st, { armed: true, next_due: parseAt(url.searchParams.get("at"), now, cfg), blocked_until: 0, dispatches: 0, fails: 0, notified: null });
      await this.save(st); return new Response("запущено; первый пинг: " + fmtLocal(st.next_due, cfg));
    }
    if (url.pathname === "/stop" && req.method === "POST") {
      const st = await this.load(); st.armed = false; await this.save(st); return new Response("остановлено");
    }
    if (url.pathname === "/unblock" && req.method === "POST") {
      const st = await this.load(); Object.assign(st, { blocked_until: 0, next_due: Date.now(), dispatches: 0, fails: 0, notified: null });
      await this.save(st); return new Response("разблокировано");
    }
    if (url.pathname === "/status") {
      const st = await this.load(), f = t => (t ? fmtLocal(t, cfg) : "-");
      return new Response(JSON.stringify({ armed: !!st.armed, next_due: f(st.next_due), blocked_until: f(st.blocked_until), reason: st.reason, pings_24h: (st.pings || []).length, last_error: st.last_error }, null, 2));
    }
    return new Response("not found", { status: 404 });
  }
}

// Сравнение секрета за постоянное время: хэшируем обе стороны и сравниваем побайтово без раннего выхода
async function authed(req, env) {
  const secret = (env.REPORT_SECRET || "").trim();
  if (!secret) return false;
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(req.headers.get("x-keeper-secret") || "")),
    crypto.subtle.digest("SHA-256", enc.encode(secret)),
  ]);
  const x = new Uint8Array(a), y = new Uint8Array(b);
  let d = 0;
  for (let i = 0; i < x.length; i++) d |= x[i] ^ y[i];
  return d === 0;
}

export default {
  // Worker проверяет пароль и передаёт запрос в единственный экземпляр Keeper. Чужим отвечает так, будто тут ничего нет.
  async fetch(req, env) {
    if (!(await authed(req, env))) return new Response("Not found", { status: 404 });
    return env.KEEPER.get(env.KEEPER.idFromName("main")).fetch(req);
  },
};
