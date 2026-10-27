@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "RUNNING="
for /f %%i in ('docker compose -f docker-compose.local.yml ps -q web 2^>nul') do set "RUNNING=1"
if defined RUNNING (
  echo Сайт запущен в Docker - сбрасываю пароли там...
  docker compose -f docker-compose.local.yml exec -T web python manage.py shell < tools\reset_demo_passwords.py
  goto end
)
if not exist ".venv\Scripts\python.exe" (
  echo Не нашёл запущенный сайт. Сначала запустите start-site.bat или start-windows.bat, потом этот файл.
  goto end
)
echo Сбрасываю пароли в локальной базе...
set DJANGO_ENV=development
set DJANGO_SECRET_KEY=local-preview-only
set DATABASE_URL=postgresql://equistart:equistart@127.0.0.1:5432/equistart
set DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
set PYTHONUTF8=1
.venv\Scripts\python.exe manage.py shell < tools\reset_demo_passwords.py
:end
echo.
pause
