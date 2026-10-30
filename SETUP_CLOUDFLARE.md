# Облачный запуск: Cloudflare Worker + GitHub Actions

Worker (бесплатный тариф) использует будильник Durable Object: он просыпается только в нужный момент (≈10-15 вызовов в сутки, без опроса)
и запускает GitHub Actions.
Actions делает ОДИН запрос к Claude (Haiku, effort low) и отчитывается Worker'у. Расписание, лимиты и Telegram-алерты ведёт Worker.
Изолировано: отдельный Worker `claude-keeper`, своё хранилище (Durable Object), свои секреты, ваши другие Workers не затрагиваются.

## 1. GitHub (один раз)
1. Создайте ПРИВАТНЫЙ репозиторий, например `claude-keeper`.
2. Залейте в корень: `keeper.py`, `config.actions.json`; `keeper-workflow.yml` положите как `.github/workflows/keeper.yml`.
3. Локально выполните `claude setup-token` и скопируйте токен.
4. Repo -> Settings -> Secrets and variables -> Actions -> New secret:
   - `CLAUDE_CODE_OAUTH_TOKEN` = токен из шага 3
   - `KEEPER_REPORT_URL` = `https://claude-keeper.<ваш-поддомен>.workers.dev/report` (узнаете после шага 2)
   - `KEEPER_REPORT_SECRET` = любая длинная случайная строка (запомните, она же REPORT_SECRET ниже)
5. GitHub -> Settings -> Developer settings -> Fine-grained tokens: доступ только к этому репо, право **Actions: Read and write**. Это `GH_TOKEN`.

## 2. Cloudflare (в папке worker/)
```
npm i -g wrangler        # или npx wrangler ...
wrangler login
# в wrangler.toml поправьте GH_REPO, при желании ACTIVE_FROM/ACTIVE_TO
wrangler secret put GH_TOKEN
wrangler secret put REPORT_SECRET    # та же строка, что KEEPER_REPORT_SECRET
wrangler deploy
```
Скопируйте выданный URL Worker'а в секрет `KEEPER_REPORT_URL` (шаг 1.4).

## 3. Запуск цикла (когда сессия обновится)
Worker после деплоя ничего не делает, пока вы его не запустите. Откройте Claude Code, команда `/usage`, посмотрите время сброса
5-часового окна (например 14:48) и в этот момент или чуть раньше выполните:
```
curl -X POST -H "x-keeper-secret: <REPORT_SECRET>" "https://claude-keeper.<...>.workers.dev/start?at=14:48"
```
Без `?at=` первый пинг уйдёт сразу. Дальше пинги идут каждые 5 ч + ~2 мин (запас на запуск GitHub), каждый раз сразу после сброса.
Остановить: `POST /stop`.

## 4. Проверка
`curl -H "x-keeper-secret: <REPORT_SECRET>" https://claude-keeper.<...>.workers.dev/status`
В течение минуты после деплоя должен стартовать первый пинг (вкладка Actions). Затем в Claude Code команда `/usage`:
сброс 5-часового окна должен быть примерно через 5 часов.

Снять блокировку: `curl -X POST -H "x-keeper-secret: ..." https://.../unblock`

## Расход
Worker + Durable Object: ≈10-15 вызовов в сутки из 100 000 (будильник + отчёты + ваши ручные команды). Actions: ~5 запусков по ~1 минуте в сутки. Claude: ~5 запросов в сутки.
