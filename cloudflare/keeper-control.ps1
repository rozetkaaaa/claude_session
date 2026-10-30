# Управление Cloudflare-будильником: status | start [ЧЧ:ММ] | stop | unblock
param([string]$Cmd = "status", [string]$At = "")
$dir = Join-Path $env:USERPROFILE ".claude-keeper-cf"
$cfgFile = Join-Path $dir "control.json"
if (-not (Test-Path $cfgFile)) { Write-Host "Не найден $cfgFile. Сначала запустите setup.bat."; exit 1 }
$c = Get-Content $cfgFile -Raw -Encoding UTF8 | ConvertFrom-Json
$ss = ConvertTo-SecureString $c.secret
$secret = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($ss))
$hdr = @{ "x-keeper-secret" = $secret }
switch ($Cmd) {
  "status"  { Invoke-RestMethod -Method Get  -Uri ($c.url + "/status") -Headers $hdr }
  "start"   { $q = ""; if ($At) { $q = "?at=" + [uri]::EscapeDataString($At) }; Invoke-RestMethod -Method Post -Uri ($c.url + "/start" + $q) -Headers $hdr }
  "stop"    { Invoke-RestMethod -Method Post -Uri ($c.url + "/stop") -Headers $hdr }
  "unblock" { Invoke-RestMethod -Method Post -Uri ($c.url + "/unblock") -Headers $hdr }
  default   { Write-Host "Команды: status | start [ЧЧ:ММ] | stop | unblock" }
}
