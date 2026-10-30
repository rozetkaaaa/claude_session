#!/usr/bin/env python3
"""
claude-session-keeper: запускает 5-часовое окно Claude Code сразу после сброса предыдущего.

Идея: окно подписки стартует с первого сообщения. Мы шлём одну точку на самой дешёвой
модели (Haiku, минимальный effort, без инструментов, без сохранения сессии) и запоминаем
точное время сброса из rate_limit_event. Следующий пинг = сброс + небольшой запас.

Запуск: python keeper.py            (один "тик", вызывать из планировщика каждые 5-15 минут)
        python keeper.py --status   (показать состояние)
        python keeper.py --selftest (пинг прямо сейчас + диагностика)
        python keeper.py --force    (пинг игнорируя расписание, но не блокировки лимитов)
        python keeper.py --unblock  (сбросить блокировку/счётчик ошибок)
Только стандартная библиотека. Для mode=interactive нужен pexpect (Linux/Mac) или pywinpty (Windows).
"""
import json, os, sys, re, time, shutil, subprocess, argparse, datetime as dt
from pathlib import Path

HOME = Path(os.environ.get("KEEPER_HOME") or Path.home() / ".claude-keeper")
HOME.mkdir(parents=True, exist_ok=True)
CFG_FILE, STATE_FILE, LOG_FILE, LOCK_FILE = (HOME / n for n in ("config.json", "state.json", "keeper.log", "lock"))
WORKDIR = HOME / "workspace"  # пустая папка: чтобы пинг не тащил контекст проекта и не засорял ваши проекты

DEFAULTS = {
    "claude_bin": "claude",
    "model": "haiku",             # алиас на последний Haiku (сейчас claude-haiku-5-5)
    "effort": "low",              # минимальное рассуждение
    "prompt": ".",
    "mode": "print",              # print | interactive | auto (print -> interactive при сбое)
    "window_hours": 5,
    "margin_seconds": 90,         # запас после времени сброса
    "active_hours": None,         # например ["07:00", "23:30"] по локальному времени; None = круглосуточно
    "max_pings_per_day": 6,       # предохранитель от циклов (5ч окно => нужно максимум 5)
    "timeout_seconds": 120,
    "notify_url": None,           # ntfy.sh/<topic> или любой URL, принимающий POST с текстом
    "max_consecutive_failures": 5,
}

def log(msg):
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 512_000:
            LOG_FILE.replace(LOG_FILE.with_suffix(".log.old"))
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass

def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default

def save_json(p, data):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)

def load_cfg():
    cfg = dict(DEFAULTS)
    cfg.update(load_json(CFG_FILE, {}))
    for k, envk in (("model", "KEEPER_MODEL"), ("mode", "KEEPER_MODE"), ("notify_url", "KEEPER_NOTIFY_URL")):
        if os.environ.get(envk):
            cfg[k] = os.environ[envk]
    return cfg

def notify(cfg, text):
    log(f"NOTIFY: {text}")
    if cfg.get("notify_url"):
        try:
            import urllib.request
            urllib.request.urlopen(urllib.request.Request(cfg["notify_url"], data=text.encode("utf-8")), timeout=10)
        except Exception as e:  # уведомление - best effort
            log(f"notify failed: {e}")

# ---------- блокировка от параллельных запусков ----------
class Lock:
    def __enter__(self):
        try:
            if LOCK_FILE.exists() and time.time() - LOCK_FILE.stat().st_mtime > 600:
                LOCK_FILE.unlink()
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode()); os.close(fd)
            self.ok = True
        except FileExistsError:
            self.ok = False
        return self
    def __exit__(self, *a):
        if self.ok:
            try: LOCK_FILE.unlink()
            except OSError: pass

# ---------- запуск claude ----------
def clean_env(cfg):
    env = dict(os.environ)
    # Критично: ключи API переключили бы пинг на оплату API вместо подписки
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(k, None)
    env["MAX_THINKING_TOKENS"] = "0"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["DISABLE_AUTOUPDATER"] = "1"
    return env

LIMIT_RE = re.compile(r"(limit reached|usage limit|rate.?limit|exceeded|too many requests|out of (extra )?usage)", re.I)
WEEK_RE = re.compile(r"(week|7[- ]?day|seven)", re.I)
AUTH_RE = re.compile(r"(not logged in|please run /login|invalid api key|oauth token|authentication|401|unauthorized|expired)", re.I)

def analyze(text, events=None):
    """Возвращает dict: limit_type (None|'five_hour'|'seven_day'), resets_at, auth_error, five_hour_resets_at."""
    res = {"limit_type": None, "resets_at": None, "auth_error": False, "five_hour_resets_at": None}
    for ev in events or []:
        info = ev.get("rate_limit_info") or ev
        t, ra, st = info.get("rateLimitType"), info.get("resetsAt"), info.get("status")
        if ra and ra > 1e12: ra = ra / 1000.0
        if t == "five_hour" and ra:
            res["five_hour_resets_at"] = ra
        if st and str(st).startswith("rejected"):
            res["limit_type"] = "seven_day" if (t and "seven" in t) else "five_hour"
            res["resets_at"] = ra
    if not res["limit_type"] and text and LIMIT_RE.search(text):
        res["limit_type"] = "seven_day" if WEEK_RE.search(text) else "five_hour"
    if text and AUTH_RE.search(text) and not res["limit_type"]:
        res["auth_error"] = True
    return res

def ping_print(cfg):
    WORKDIR.mkdir(exist_ok=True)
    cmd = [cfg["claude_bin"], "-p", cfg["prompt"], "--model", cfg["model"], "--effort", cfg["effort"],
           "--tools", "", "--system-prompt", ".", "--disable-slash-commands", "--strict-mcp-config",
           "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
    try:
        p = subprocess.run(cmd, cwd=WORKDIR, env=clean_env(cfg), capture_output=True, text=True,
                           timeout=cfg["timeout_seconds"], encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"ok": False, "fatal": f"не найден {cfg['claude_bin']} (задайте claude_bin в config.json)", "info": analyze("")}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "info": analyze("")}
    events, result_text, is_error = [], "", False
    for line in p.stdout.splitlines():
        try: ev = json.loads(line)
        except ValueError: continue
        if ev.get("type") == "rate_limit_event": events.append(ev)
        if ev.get("type") == "result":
            is_error = bool(ev.get("is_error")); result_text = str(ev.get("result") or "")
    blob = (result_text + "\n" + p.stderr + ("" if events else "\n" + p.stdout[-2000:] if p.returncode else ""))
    info = analyze(blob, events)
    ok = p.returncode == 0 and not is_error and not info["limit_type"] and not info["auth_error"]
    return {"ok": ok, "info": info, "error": None if ok else (blob.strip()[-300:] or f"rc={p.returncode}")}

def ping_interactive(cfg):
    """Живая TUI-сессия в псевдотерминале (считается как интерактивное использование).
    После пинга удаляем папку проектов Claude для нашего workspace, чтобы чат не копился."""
    WORKDIR.mkdir(exist_ok=True)
    cmd = [cfg["claude_bin"], "--model", cfg["model"], "--effort", cfg["effort"], "--tools", "",
           "--disable-slash-commands", "--strict-mcp-config"]
    env = clean_env(cfg)
    buf = []
    if os.name == "nt":
        try: from winpty import PtyProcess
        except ImportError:
            return {"ok": False, "fatal": "для mode=interactive на Windows: pip install pywinpty", "info": analyze("")}
        proc = PtyProcess.spawn(cmd, cwd=str(WORKDIR), env=env)
        read = lambda: proc.read(4096)
        write, alive, kill = proc.write, proc.isalive, proc.terminate
    else:
        try: import pexpect
        except ImportError:
            return {"ok": False, "fatal": "для mode=interactive: pip install pexpect", "info": analyze("")}
        proc = pexpect.spawn(cmd[0], cmd[1:], cwd=str(WORKDIR), env=env, encoding="utf-8", timeout=2)
        def read():
            try: return proc.read_nonblocking(4096, timeout=1)
            except pexpect.TIMEOUT: return ""
            except pexpect.EOF: raise EOFError
        write, alive, kill = proc.send, proc.isalive, lambda: proc.terminate(force=True)
    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end and alive():
            try: chunk = read()
            except (EOFError, OSError): break
            if chunk: buf.append(chunk)
            else: time.sleep(0.2)
    try:
        pump(8)
        if re.search(r"trust", "".join(buf), re.I): write("\r"); pump(4)   # диалог доверия папке
        write(cfg["prompt"]); time.sleep(0.5); write("\r")
        pump(min(60, cfg["timeout_seconds"] - 20))
        write("/exit\r"); pump(4)
    finally:
        try: kill()
        except Exception: pass
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(WORKDIR))
        shutil.rmtree(Path.home() / ".claude" / "projects" / slug, ignore_errors=True)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", "".join(buf))
    info = analyze(text)
    return {"ok": not info["limit_type"] and not info["auth_error"], "info": info, "error": text.strip()[-300:]}

def do_ping(cfg):
    mode = cfg["mode"]
    if mode == "interactive": return ping_interactive(cfg)
    r = ping_print(cfg)
    if mode == "auto" and not r["ok"] and not r["info"]["limit_type"] and not r["info"]["auth_error"] and not r.get("fatal"):
        log("print-режим не сработал, пробую interactive")
        r = ping_interactive(cfg)
    return r

# ---------- логика ----------
def in_active_hours(cfg, now):
    ah = cfg.get("active_hours")
    if not ah: return True
    t = dt.datetime.fromtimestamp(now).strftime("%H:%M")
    a, b = ah
    return (a <= t <= b) if a <= b else (t >= a or t <= b)

def fmt(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"

def tick(force=False):
    cfg, st, now = load_cfg(), load_json(STATE_FILE, {}), time.time()
    st.setdefault("pings", []); st.setdefault("failures", 0)
    st["pings"] = [t for t in st["pings"] if now - t < 86400]

    if st.get("blocked_until", 0) > now:
        log(f"заблокировано до {fmt(st['blocked_until'])} ({st.get('blocked_reason')}); жду")
        return 0
    if not force:
        if now < st.get("next_due", 0):
            log(f"рано: следующий пинг {fmt(st['next_due'])}"); return 0
        if not in_active_hours(cfg, now):
            log("вне active_hours, пропуск"); return 0
    if len(st["pings"]) >= cfg["max_pings_per_day"]:
        st["blocked_until"] = min(st["pings"]) + 86400
        st["blocked_reason"] = "дневной предохранитель max_pings_per_day"
        save_json(STATE_FILE, st); log(st["blocked_reason"]); return 1

    log(f"пинг: mode={cfg['mode']} model={cfg['model']} effort={cfg['effort']}")
    r = do_ping(cfg)
    info = r["info"]; st["pings"].append(now); st["last_ping"] = now
    margin = cfg["margin_seconds"]

    if r.get("fatal"):
        st["blocked_until"] = now + 6 * 3600; st["blocked_reason"] = r["fatal"]
        notify(cfg, f"Keeper остановлен: {r['fatal']}"); save_json(STATE_FILE, st); return 2

    if info["limit_type"] == "seven_day":
        # Недельный лимит: НЕ долбим. Ждём известного сброса или растущий бэкофф.
        n = st.get("weekly_hits", 0)
        until = info["resets_at"] + margin if info["resets_at"] and info["resets_at"] > now else now + min(3600 * 2 ** n, 24 * 3600)
        st.update(blocked_until=until, blocked_reason="недельный лимит", weekly_hits=n + 1, next_due=until)
        notify(cfg, f"Недельный лимит исчерпан. Пинги приостановлены до {fmt(until)}")
    elif info["limit_type"] == "five_hour":
        # Окно уже идёт и исчерпано - новое окно стартует после сброса
        until = info["resets_at"] + margin if info["resets_at"] and info["resets_at"] > now else now + 1800
        st.update(next_due=until, blocked_reason="5ч лимит уже исчерпан (окно активно)", failures=0)
        log(f"5ч окно уже активно и исчерпано, следующий пинг {fmt(until)}")
    elif info["auth_error"]:
        st.update(blocked_until=now + 6 * 3600, blocked_reason="нет авторизации", needs_login=True)
        notify(cfg, "Keeper: Claude Code не авторизован. Выполните `claude` и /login (или claude setup-token)")
    elif r["ok"]:
        ra = info["five_hour_resets_at"]
        valid = ra and now < ra <= now + cfg["window_hours"] * 3600 + 600
        due = (ra if valid else now + cfg["window_hours"] * 3600) + margin
        st.update(next_due=due, failures=0, weekly_hits=0, blocked_reason=None)
        log(f"OK. Сброс окна: {fmt(ra) if valid else 'оценка ' + fmt(now + cfg['window_hours'] * 3600)}; следующий пинг {fmt(due)}")
    else:
        st["failures"] += 1
        wait = min(900 * 2 ** (st["failures"] - 1), 4 * 3600)
        st["next_due"] = now + wait
        log(f"сбой #{st['failures']}: {r.get('error')}; повтор через {wait // 60} мин")
        if st["failures"] >= cfg["max_consecutive_failures"]:
            st.update(blocked_until=now + 12 * 3600, blocked_reason="слишком много сбоев подряд")
            notify(cfg, f"Keeper: {st['failures']} сбоев подряд, пауза 12ч. Последняя ошибка: {r.get('error')}")
    save_json(STATE_FILE, st)
    return 0

def report():
    """Отправляет итог в Cloudflare Worker (он хранит расписание). Включается переменной KEEPER_REPORT_URL."""
    url = os.environ.get("KEEPER_REPORT_URL")
    if not url: return
    st, now = load_json(STATE_FILE, {}), time.time()
    body = {"next_due": st.get("next_due", 0), "blocked_until": st.get("blocked_until", 0),
            "blocked_reason": st.get("blocked_reason"), "failures": st.get("failures", 0), "now": now}
    try:
        import urllib.request
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
              headers={"content-type": "application/json", "x-keeper-secret": os.environ.get("KEEPER_REPORT_SECRET", ""),
                       "user-agent": "claude-keeper"})
        urllib.request.urlopen(req, timeout=15); log("отчёт отправлен в Worker")
    except Exception as e:
        log(f"отчёт не отправлен: {e}")

def is_due(force=False):
    """Только проверка, без пинга: пора ли пинговать (для быстрого предварительного шага в Actions)."""
    cfg, st, now = load_cfg(), load_json(STATE_FILE, {}), time.time()
    if st.get("blocked_until", 0) > now: return False
    if force: return True
    return now >= st.get("next_due", 0) and in_active_hours(cfg, now)

def eta():
    """Секунд до ближайшего пинга (0 = пора сейчас; 10**6 = пинга не будет: блокировка или вне active_hours)."""
    import math
    cfg, st, now = load_cfg(), load_json(STATE_FILE, {}), time.time()
    if st.get("blocked_until", 0) > now or not in_active_hours(cfg, now): return 10**6
    return max(0, math.ceil(st.get("next_due", 0) - now))

def status():
    st, cfg = load_json(STATE_FILE, {}), load_cfg()
    print(f"home: {HOME}\nmode={cfg['mode']} model={cfg['model']} effort={cfg['effort']}")
    for k in ("last_ping", "next_due", "blocked_until"):
        print(f"{k}: {fmt(st.get(k))}")
    print(f"blocked_reason: {st.get('blocked_reason')}  failures: {st.get('failures', 0)}  pings/24h: {len(st.get('pings', []))}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true"); ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--force", action="store_true"); ap.add_argument("--check", action="store_true"); ap.add_argument("--eta", action="store_true"); ap.add_argument("--unblock", action="store_true")
    a = ap.parse_args()
    if a.eta: print(eta()); return 0
    if a.check: return 0 if is_due() else 3
    if a.status: return status()
    if a.unblock:
        st = load_json(STATE_FILE, {})
        for k in ("blocked_until", "blocked_reason", "needs_login", "weekly_hits", "next_due"): st.pop(k, None)
        st["failures"] = 0; save_json(STATE_FILE, st); print("сброшено"); return 0
    if a.selftest:
        cfg = load_cfg(); r = do_ping(cfg)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
        ra = r["info"]["five_hour_resets_at"]
        print("\nОкно сбросится:", fmt(ra) if ra else "claude не сообщил (проверьте /usage в Claude Code: 5h reset должен быть ~через 5 часов)")
        return 0 if r["ok"] else 1
    with Lock() as lk:
        if not lk.ok: log("другой экземпляр работает"); return 0
        rc = tick(force=a.force)
        report()
        return rc

if __name__ == "__main__":
    sys.exit(main() or 0)
