# Установка Cloudflare-будильника для Claude Session Keeper
# Запускать через setup.bat (двойной щелчок)
param([string]$Repo = "")
$ErrorActionPreference = "Continue"   # wrangler пишет предупреждения в stderr; ошибки проверяем по $LASTEXITCODE
$Here = $PSScriptRoot
$DefaultRepo = "rozetkaaaa/claude_session"

function Step($t) { Write-Host ""; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Pause-Enter($t) { Read-Host $t | Out-Null }
function Plain($ss) { [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss)) }
$Browser = $null
$bf = Join-Path $Here "browser.txt"
if (Test-Path $bf) { $Browser = (Get-Content $bf -Raw).Trim() }
function Open-Url($u) {
  if ($Browser -and (Test-Path $Browser)) { Start-Process -FilePath $Browser -ArgumentList $u } else { Start-Process $u }
}
function Fail($t) { Write-Host ""; Write-Host "ОШИБКА: $t" -ForegroundColor Red; Write-Host "Сделайте скриншот окна и отправьте тому, кто дал вам этот файл."; Read-Host "Нажмите Enter, чтобы закрыть" | Out-Null; exit 1 }

try {
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail "Не найден Node.js. Установите его с nodejs.org (версия LTS) и запустите файл снова." }
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) { Fail "Не найден npx (идёт вместе с Node.js)." }

Write-Host "Что сейчас произойдёт:" -ForegroundColor Yellow
Write-Host " 1. Вы войдёте в Cloudflare. Скрипт создаст ОТДЕЛЬНЫЙ новый Worker со случайным именем."
Write-Host "    Ваши существующие Workers, базы и настройки не затрагиваются: скрипт их не читает и не изменяет."
Write-Host " 2. Токен GitHub (только для запуска одного workflow) вы вставите сами, скрыто. Он хранится в Cloudflare зашифрованно."
Write-Host " 3. В конце скрипт выйдет из Cloudflare на этом компьютере (доступ не останется)."
Write-Host ""
Pause-Enter "Нажмите Enter, чтобы продолжить (или закройте окно)"

if (-not $Repo) {
  $Repo = Read-Host "GitHub-репозиторий с workflow (логин/имя). Enter = $DefaultRepo"
  if (-not $Repo) { $Repo = $DefaultRepo }
}
if ($Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { Fail "Неверный формат репозитория: $Repo (нужно логин/имя)." }

# --- рабочая копия Worker с уникальным именем ---
$name = "claude-keeper-" + ([guid]::NewGuid().ToString("N").Substring(0, 10))
$wd = Join-Path $env:TEMP ("ck_worker_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $wd | Out-Null
Copy-Item -Recurse -Force (Join-Path $Here "worker\*") $wd
$tomlPath = Join-Path $wd "wrangler.toml"
$toml = [IO.File]::ReadAllText($tomlPath).Replace("__WORKER_NAME__", $name).Replace("__GH_REPO__", $Repo)
[IO.File]::WriteAllText($tomlPath, $toml, (New-Object Text.UTF8Encoding($false)))

Step "1/6 Вход в Cloudflare"
Push-Location $wd
$who = (npx --yes wrangler whoami 2>&1 | Out-String)
if ($who -match "not authenticated|You are not") {
  Write-Host "Получаю ссылку для входа (до минуты)..."
  $log = Join-Path $env:TEMP ("wr_login_" + [guid]::NewGuid().ToString("N").Substring(0, 6) + ".log")
  $p = Start-Process -FilePath "cmd.exe" -ArgumentList ('/c npx --yes wrangler login --browser=false > "' + $log + '" 2>&1') -PassThru -WindowStyle Hidden
  $url = $null
  for ($i = 0; $i -lt 150 -and -not $url -and -not $p.HasExited; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $log) {
      $mm = [regex]::Match((Get-Content $log -Raw -ErrorAction SilentlyContinue), 'https://[^\s"]+oauth2/auth[^\s"]*')
      if ($mm.Success) { $url = $mm.Value }
    }
  }
  if (-not $url) {
    $txt = ""; if (Test-Path $log) { $txt = Get-Content $log -Raw }
    Write-Host "Не удалось получить ссылку для входа. Вывод wrangler:" -ForegroundColor Red
    Write-Host $txt
    try { $p.Kill() } catch {}
    Fail "Вход в Cloudflare не удался (см. текст выше)."
  }
  Write-Host "Открываю браузер: войдите в Cloudflare и нажмите Allow." -ForegroundColor Green
  Write-Host "(Если окно браузера не появилось, откройте эту ссылку вручную: $url)"
  Open-Url $url
  $p.WaitForExit()
  Remove-Item -Force $log -ErrorAction SilentlyContinue
}
else { Write-Host "Вы уже вошли в Cloudflare на этом компьютере." }
Write-Host ""
npx --yes wrangler whoami
$ok = Read-Host "Это нужный аккаунт? Будет создан новый Worker '$name'. Продолжить? (да/нет)"
if ($ok -notmatch '^(д|y)') { npx --yes wrangler logout; Pop-Location; exit 0 }

Step "2/6 Создание Worker"
$out = (npx --yes wrangler deploy 2>&1 | Out-String)
Write-Host $out
if ($LASTEXITCODE -ne 0) { Fail "Не удалось создать Worker (см. текст выше)." }
$m = [regex]::Match($out, 'https://[A-Za-z0-9.\-]+\.workers\.dev')
if ($m.Success) { $workerUrl = $m.Value } else { $workerUrl = Read-Host "Не смог определить адрес Worker. Вставьте его (https://...workers.dev)" }
if ($workerUrl -notlike "https://$name.*") { Write-Host "Предупреждение: адрес не начинается с имени нового Worker. Проверьте: $workerUrl" -ForegroundColor Yellow }

Step "3/6 Токен GitHub (только запуск workflow)"
Write-Host "Откроется страница создания токена. Настройте ТОЛЬКО так:"
Write-Host "  Repository access: Only select repositories -> $Repo"
Write-Host "  Permissions: Actions = Read and write (больше ничего не выбирайте)"
Write-Host "Нажмите Generate token и скопируйте токен (он начинается с github_pat_)."
Open-Url "https://github.com/settings/personal-access-tokens/new?name=claude-keeper&description=Triggers+the+keeper+workflow+only&expires_in=365&actions=write"
Pause-Enter "Когда токен скопирован, нажмите Enter. Затем вставьте его правой кнопкой мыши (символы не видны) и снова Enter"
npx --yes wrangler secret put GH_TOKEN --name $name
if ($LASTEXITCODE -ne 0) { Fail "Не удалось сохранить токен GitHub." }

Step "4/6 Служебные секреты"
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create(); $rng.GetBytes($bytes)
$secret = ([BitConverter]::ToString($bytes)).Replace("-", "").ToLower()
$secret | npx --yes wrangler secret put REPORT_SECRET --name $name
if ($LASTEXITCODE -ne 0) { Fail "Не удалось сохранить служебный пароль." }
$n = Read-Host "Адрес ntfy для уведомлений Worker (необязательно, вставьте скрыто или Enter = пропустить)" -AsSecureString
$np = Plain $n
if ($np) { $np | npx --yes wrangler secret put NTFY_URL --name $name }
Pop-Location

Step "5/6 Два секрета в GitHub"
Write-Host "Откроется страница. Значения будут в буфере обмена, вставляйте Ctrl+V."
Set-Clipboard -Value ($workerUrl + "/report")
Open-Url "https://github.com/$Repo/settings/secrets/actions/new"
Write-Host "  Секрет 1:  Name = KEEPER_REPORT_URL   Secret = Ctrl+V   -> Add secret"
Pause-Enter "Добавили? Нажмите Enter"
Set-Clipboard -Value $secret
Open-Url "https://github.com/$Repo/settings/secrets/actions/new"
Write-Host "  Секрет 2:  Name = KEEPER_REPORT_SECRET   Secret = Ctrl+V   -> Add secret"
Pause-Enter "Добавили? Нажмите Enter"
Set-Clipboard -Value " "
Write-Host "Буфер обмена очищен. Если у вас включена история буфера (Win+V), удалите из неё эти две записи."

# --- локальный пульт управления (пароль шифруется средствами Windows, привязан к вашей учётной записи) ---
$cfgDir = Join-Path $env:USERPROFILE ".claude-keeper-cf"
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$enc = ConvertTo-SecureString $secret -AsPlainText -Force | ConvertFrom-SecureString
(@{ url = $workerUrl; worker = $name; secret = $enc } | ConvertTo-Json) | Set-Content -Encoding UTF8 (Join-Path $cfgDir "control.json")
Copy-Item -Force (Join-Path $Here "keeper-control.ps1") $cfgDir
Copy-Item -Force (Join-Path $Here "keeper-control.bat") $cfgDir

Step "6/6 Запуск цикла"
Write-Host "Откройте в Claude Code команду /usage и посмотрите, когда сбросится 5-часовое окно (например 19:50)."
$at = Read-Host "Введите это время ЧЧ:ММ (Enter = первый пинг прямо сейчас)"
$q = ""
if ($at) { $q = "?at=" + [uri]::EscapeDataString($at) }
$hdr = @{ "x-keeper-secret" = $secret }
$resp = $null
for ($i = 1; $i -le 8; $i++) {
  try { $resp = Invoke-RestMethod -Method Post -Uri ($workerUrl + "/start" + $q) -Headers $hdr -ErrorAction Stop; break }
  catch { Write-Host "Worker ещё запускается, жду ($i/8)..."; Start-Sleep -Seconds 10 }
}
if (-not $resp) { Fail "Worker не ответил. Подождите минуту и выполните: $cfgDir\keeper-control.bat start" }
Write-Host $resp -ForegroundColor Green

Push-Location $wd
npx --yes wrangler logout
Pop-Location
Remove-Item -Recurse -Force $wd -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "ГОТОВО. Worker: $name" -ForegroundColor Green
Write-Host "Дальше всё работает само: Cloudflare запускает пинг в момент сброса окна, ноутбук не нужен."
Write-Host "Управление: $cfgDir\keeper-control.bat  (status | start ЧЧ:ММ | stop | unblock)"
Read-Host "Нажмите Enter, чтобы закрыть" | Out-Null
}
catch { Fail $_.Exception.Message }
