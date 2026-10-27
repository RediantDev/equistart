@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Запускаю тесты EquiStart... Это займёт несколько минут.
docker compose -f docker-compose.local.yml -f docker-compose.code.yml up -d db
docker compose -f docker-compose.local.yml -f docker-compose.code.yml run --rm web python manage.py test --noinput > test-result.txt 2>&1
type test-result.txt
echo.
echo Результат сохранён в файл test-result.txt
pause
