import { decide, applyReport, inActive, parseAt } from "./src/index.js";
const cfg = { from: "", to: "", tzOffsetH: 5, maxPings: 6 }, H = 3600e3; let now = Date.UTC(2026, 9, 30, 5, 0);
let r = decide({ armed: true }, now, cfg); console.log("1 первый запуск:", r.action);
let st = applyReport(r.st, { next_due: (now + 5 * H + 9e4) / 1000, blocked_until: 0, failures: 0 }, now);
console.log("2 через 1 ч:", decide(st, now + H, cfg).action, "| через 5ч01:", decide(st, now + 5 * H + 100e3, cfg).action);
st = applyReport(st, { next_due: (now + 2 * H) / 1000, blocked_until: (now + 2 * 864e5) / 1000, blocked_reason: "недельный лимит", failures: 0 }, now);
console.log("3 недельный лимит, +3ч/+47ч/+49ч:", [3, 47, 49].map(h => decide(st, now + h * H, cfg).action).join(","));
let s = { armed: true }; const acts = []; for (let i = 0; i < 7; i++) { const d = decide(s, now + i * 21 * 60e3, cfg); acts.push(d.action); s = d.st; }
console.log("4 Actions молчит (7 попыток каждые 21 мин):", acts.join(","));
let f = {}; for (let i = 0; i < 5; i++) f = applyReport(f, { next_due: now / 1000 + 900, failures: 1 }, now); console.log("5 5 сбоев ->", f.reason);
console.log("6 active 07:00-23:30 (UTC+5): 03:00 UTC =", inActive(Date.UTC(2026, 9, 30, 3), { from: "07:00", to: "23:30", tzOffsetH: 5 }), "(08:00 местн.); 20:00 UTC =", inActive(Date.UTC(2026, 9, 30, 20), { from: "07:00", to: "23:30", tzOffsetH: 5 }), "(01:00 местн.)");
console.log("7 без /start:", decide({}, now, cfg).action);
const c5 = { tzOffsetH: 5 }, n = Date.UTC(2026, 9, 30, 5, 7);   // 10:07 местного
console.log("8 at=14:48 ->", new Date(parseAt("14:48", n, c5) + 5 * H).toISOString().slice(0, 16), "| at=10:00 (прошёл 7 мин) ->", new Date(parseAt("10:00", n, c5) + 5 * H).toISOString().slice(0, 16), "| at=09:00 (давно) ->", new Date(parseAt("09:00", n, c5) + 5 * H).toISOString().slice(0, 16));
import { nextWake, Keeper } from "./src/index.js";
const w = Date.UTC(2026, 9, 30, 5, 0);
console.log("9 будильник: остановлен ->", nextWake({}, w), "| ждём пинга через 5ч ->", (nextWake({ armed: true, next_due: w + 5 * H }, w) - w) / H, "ч | блок на 2 суток ->", (nextWake({ armed: true, next_due: w + 5 * H, blocked_until: w + 48 * H }, w) - w) / H, "ч");
// имитация DO: хранилище + будильник
const store = new Map(); let alarm = null, hits = 0;
const k = new Keeper({ storage: { get: async x => store.get(x), put: async (x, v) => store.set(x, v), setAlarm: async t => (alarm = t), deleteAlarm: async () => (alarm = null) } }, { REPORT_SECRET: "s", GH_REPO: "a/b", GH_TOKEN: "t", TZ_OFFSET_H: "5" });
globalThis.fetch = async () => (hits++, { ok: true });
await k.fetch(new Request("https://x/start?at=" + new Date(Date.now() + 3600e3 + 5 * H).toISOString().slice(11, 16), { method: "POST" }));
console.log("10 после /start будильник через ~1ч:", Math.round((alarm - Date.now()) / 6e4), "мин");
{ const s2 = store.get("st"); s2.next_due = Date.now() - 1; store.set("st", s2); }
await k.alarm(); console.log("11 alarm -> запусков Actions:", hits, "| повторный будильник через", Math.round((alarm - Date.now()) / 6e4), "мин (страховка)");
await k.fetch(new Request("https://x/report", { method: "POST", body: JSON.stringify({ next_due: (Date.now() + 5 * H) / 1000, failures: 0 }) }));
console.log("12 после отчёта будильник через", ((alarm - Date.now()) / H).toFixed(2), "ч");
