# Установка (кратко)

Расписание GitHub ненадёжно, поэтому момент пинга задаёт Cloudflare Worker (подробности и защита: cloudflare/README_CLOUDFLARE.md).

1. Токен Claude: в PowerShell `npm i -g --allow-scripts=@anthropic-ai/claude-code @anthropic-ai/claude-code`, затем `claude setup-token`.
   Добавьте его в секреты репозитория: Settings -> Secrets and variables -> Actions -> New repository secret, имя CLAUDE_CODE_OAUTH_TOKEN.
2. По желанию секрет KEEPER_NOTIFY_URL = адрес ntfy-топика (https://ntfy.sh/ваш-длинный-случайный-топик).
3. Откройте папку cloudflare и дважды щёлкните setup.bat: он создаёт отдельный Worker и просит два значения для GitHub (будут в буфере обмена).
