#!/bin/bash
# Обновить тестовый стенд до последней версии из GitHub (запуск от root на сервере).
# GITHUB_TOKEN нужен, если репозиторий приватный: GITHUB_TOKEN=... bash stand-update.sh
set -euo pipefail
APP=/opt/equistart
URL=https://github.com/RediantDev/equistart.git
[ -n "${GITHUB_TOKEN:-}" ] && URL="https://x-access-token:${GITHUB_TOKEN}@github.com/RediantDev/equistart.git"
sudo -u equistart git -C $APP/app pull --ff-only "$URL" main
$APP/venv/bin/pip install -q -r $APP/app/requirements.txt
sudo -u equistart bash -c "set -a; . $APP/.env; set +a; cd $APP/app; $APP/venv/bin/python manage.py migrate --noinput; $APP/venv/bin/python manage.py collectstatic --noinput -v0"
systemctl restart equistart
echo "Обновлено: $(git -C $APP/app log --oneline -1)"
