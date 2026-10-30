# Установка за 3 шага (только GitHub, без Cloudflare)

1. Получить токен. PowerShell:
   npm i -g @anthropic-ai/claude-code
   (закрыть окно, открыть новое)
   claude setup-token
   Откроется браузер, подтвердить вход, скопировать длинный токен.

2. Положить токен на GitHub: https://github.com/rozetkaaaa/claude_session/settings/secrets/actions/new
   Name: CLAUDE_CODE_OAUTH_TOKEN   Secret: (вставить токен)   -> Add secret

3. Запустить: https://github.com/rozetkaaaa/claude_session/actions -> claude-session-keeper -> Run workflow
   Нажимать в момент, когда ваша сессия Claude Code обновилась. Дальше всё само: пинг сразу после каждого сброса (~5 в сутки).

Уведомления: приложение ntfy, топик claude_session.
