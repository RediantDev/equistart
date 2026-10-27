# Запуск сайта на Windows без Docker: http://localhost:8000
# Нужны: Python 3.13 (python.org) и PostgreSQL 16 (postgresql.org/download/windows).
# Запуск: двойной щелчок по start-windows.bat в папке проекта (или в PowerShell: .\start-windows.bat)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
function Stop-With($msg) { Write-Host ""; Write-Host $msg -ForegroundColor Red; Read-Host "Нажмите Enter, чтобы закрыть"; exit 1 }

# 1. Python
$py = $null
foreach ($c in @("py -3.13", "py -3.12", "python")) {
  $parts = $c.Split(" ")
  try { $v = & $parts[0] $parts[1..9] -c "import sys;print(sys.version_info[:2]>=(3,12))" 2>$null; if ($v -eq "True") { $py = $parts; break } } catch {}
}
if (-not $py) { Stop-With "Не найден Python 3.12 или 3.13. Установите с https://www.python.org/downloads/ (галочка «Add python.exe to PATH») и запустите снова." }

# 2. PostgreSQL
$psql = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $psql) { Stop-With "Не найден PostgreSQL. Установите версию 16 с https://www.postgresql.org/download/windows/ (запомните пароль пользователя postgres) и запустите снова." }
$psql = $psql.FullName

# 3. Окружение Python и пакеты (первый раз — пару минут)
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "Создаю окружение Python..." -ForegroundColor Cyan
  & $py[0] $py[1..9] -m venv .venv
  if ($LASTEXITCODE) { Stop-With "Не удалось создать окружение Python." }
}
$vpy = Join-Path $root ".venv\Scripts\python.exe"
$stamp = ".venv\.req"
$req = (Get-FileHash requirements.txt).Hash
if (-not (Test-Path $stamp) -or (Get-Content $stamp) -ne $req) {
  Write-Host "Устанавливаю пакеты..." -ForegroundColor Cyan
  & $vpy -m pip install --disable-pip-version-check -q -r requirements.txt
  if ($LASTEXITCODE) { Stop-With "Не удалось установить пакеты (нужен интернет до pypi.org)." }
  Set-Content $stamp $req
}

# 4. База данных equistart (создаётся один раз)
$env:PGPASSWORD = "equistart"
& $psql -h 127.0.0.1 -U equistart -d equistart -tAc "select 1" 2>$null | Out-Null
if ($LASTEXITCODE) {
  Write-Host "Создаю базу equistart. Нужен пароль пользователя postgres, который вы задали при установке PostgreSQL." -ForegroundColor Cyan
  $sec = Read-Host "Пароль postgres" -AsSecureString
  $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
  & $psql -h 127.0.0.1 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "DO `$`$BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='equistart') THEN CREATE ROLE equistart LOGIN PASSWORD 'equistart' CREATEDB; END IF; END`$`$;"
  if ($LASTEXITCODE) { Stop-With "Не удалось подключиться к PostgreSQL. Проверьте пароль postgres и что служба PostgreSQL запущена." }
  & $psql -h 127.0.0.1 -U postgres -d postgres -tAc "select 1 from pg_database where datname='equistart'" | Out-String | ForEach-Object { if ($_.Trim() -ne "1") { & $psql -h 127.0.0.1 -U postgres -d postgres -c "CREATE DATABASE equistart OWNER equistart ENCODING 'UTF8' TEMPLATE template0;" } }
  $env:PGPASSWORD = ""
}

# 5. Настройки как у локального просмотра
$env:DJANGO_ENV = "development"
$env:DJANGO_SECRET_KEY = "local-preview-only"
$env:DATABASE_URL = "postgresql://equistart:equistart@127.0.0.1:5432/equistart"
$env:APP_ORIGIN = "http://localhost:8000"
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
$env:PHONE_VERIFICATION_REQUIRED = "false"
$env:SMS_BACKEND = "console"
$env:STAND_BANNER = "Локальный просмотр · тестовые данные · пароль всех аккаунтов Demo-Pass-2026"
$env:PYTHONUTF8 = "1"

Write-Host "Обновляю базу..." -ForegroundColor Cyan
& $vpy manage.py migrate --noinput;            if ($LASTEXITCODE) { Stop-With "Ошибка при обновлении базы (текст выше)." }
& $vpy manage.py createcachetable
& $vpy manage.py seed_demo --password Demo-Pass-2026; if ($LASTEXITCODE) { Stop-With "Ошибка при заполнении тестовых данных (текст выше)." }

Write-Host ""
Write-Host ">>> Сайт готов: http://localhost:8000   (остановить — Ctrl+C)" -ForegroundColor Green
Write-Host ""
Start-Process "http://localhost:8000"
& $vpy manage.py runserver 127.0.0.1:8000
