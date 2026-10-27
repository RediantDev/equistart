#!/bin/sh
# Обёртка для полей cloud-init, которые принимают только "#!/bin/sh" (Timeweb Cloud):
# записывает установщик во временный файл, запускает его через bash и удаляет (вместе с токеном).
cat > /root/equistart-install.sh <<'EQUISTART_SCRIPT'
#!/bin/bash
# Тестовый стенд EquiStart на чистом сервере Ubuntu 24.04 — одним сценарием.
# Вставляется целиком в поле «cloud-init» при создании сервера в Timeweb Cloud (или запускается от root).
# Без Docker: Python, PostgreSQL и nginx ставятся из репозиториев Ubuntu, пакеты Python — из PyPI.
# Сайт открывается по http://IP-АДРЕС-СЕРВЕРА через 5–10 минут после создания сервера.
# Только для тестовых данных: без домена и HTTPS. Журнал установки: /var/log/equistart-install.log

# ==== Заполните две строки ====
GITHUB_TOKEN="ВСТАВЬТЕ_ТОКЕН_GITHUB"      # токен только на чтение репозитория RediantDev/equistart
DEMO_PASSWORD="ПридумайтеПароль-2026"     # пароль для всех тестовых аккаунтов (не короче 8 символов)
# ===============================

set -euo pipefail
exec > >(tee -a /var/log/equistart-install.log) 2>&1
echo "== EquiStart: установка $(date -Is)"
export DEBIAN_FRONTEND=noninteractive
APP=/opt/equistart

apt-get update -q
apt-get install -y -q git curl python3-venv python3-dev build-essential postgresql nginx

id equistart >/dev/null 2>&1 || useradd --system --create-home --home-dir $APP --shell /usr/sbin/nologin equistart
if [ ! -d $APP/app/.git ]; then
  git clone --depth 1 "https://x-access-token:${GITHUB_TOKEN}@github.com/RediantDev/equistart.git" $APP/app
  git -C $APP/app remote set-url origin https://github.com/RediantDev/equistart.git   # токен не остаётся на диске
fi
python3 -m venv $APP/venv
$APP/venv/bin/pip install -q --upgrade pip
$APP/venv/bin/pip install -q -r $APP/app/requirements.txt

DB_PASS=$(openssl rand -hex 16)
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='equistart'" | grep -q 1; then
  sudo -u postgres psql -q -c "CREATE USER equistart PASSWORD '${DB_PASS}'"
  sudo -u postgres psql -q -c "CREATE DATABASE equistart OWNER equistart"
else
  sudo -u postgres psql -q -c "ALTER USER equistart PASSWORD '${DB_PASS}'"
fi

IP=$(curl -4 -fsS --max-time 10 https://ifconfig.me || hostname -I | awk '{print $1}')
mkdir -p $APP/media
cat > $APP/.env <<ENV
DJANGO_ENV=production
DJANGO_SECRET_KEY=$(openssl rand -base64 48 | tr -d '\n/+=')
DATABASE_URL=postgresql://equistart:${DB_PASS}@127.0.0.1:5432/equistart
APP_ORIGIN=http://${IP}
DJANGO_ALLOWED_HOSTS=${IP}
HTTPS_ENABLED=false
TRUSTED_PROXY_IPS=127.0.0.1
MEDIA_ROOT=${APP}/media
PHONE_VERIFICATION_REQUIRED=false
SMS_BACKEND=console
STAND_BANNER=Тестовый стенд EquiStart: данные вымышленные, реальные данные не вводите
ENV
chmod 600 $APP/.env
chown -R equistart:equistart $APP

run() { sudo -u equistart bash -c "set -a; . $APP/.env; set +a; cd $APP/app; $APP/venv/bin/python manage.py $*"; }
run migrate --noinput
run createcachetable
run collectstatic --noinput -v0
run seed_demo --password "'${DEMO_PASSWORD}'"

cat > /etc/systemd/system/equistart.service <<UNIT
[Unit]
Description=EquiStart
After=network.target postgresql.service

[Service]
User=equistart
WorkingDirectory=$APP/app
EnvironmentFile=$APP/.env
ExecStart=$APP/venv/bin/gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --access-logfile -
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now equistart

cat > /etc/nginx/sites-available/equistart <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 22m;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX
ln -sf /etc/nginx/sites-available/equistart /etc/nginx/sites-enabled/equistart
rm -f /etc/nginx/sites-enabled/default
systemctl reload nginx

sleep 3
curl -fsS http://127.0.0.1/health/ready && echo
echo "== Готово: откройте http://${IP}"
EQUISTART_SCRIPT
bash /root/equistart-install.sh
rm -f /root/equistart-install.sh
