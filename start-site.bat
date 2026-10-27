@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting... open http://localhost:8000 when you see ">>> Сайт готов". Stop: Ctrl+C
docker compose -f docker-compose.local.yml -f docker-compose.code.yml up
pause
