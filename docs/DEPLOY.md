# Развёртывание EquiStart (пилот)

Всё — на сервере в России (152-ФЗ): приложение, база, файлы, резервные копии.

## 1. Сервер

- VPS в российском дата-центре: Yandex Cloud, Selectel, Timeweb Cloud, REG.RU и т.п.
- Для пилота достаточно: 2 vCPU, 4 ГБ RAM, 40 ГБ SSD, Ubuntu 24.04.
- Установить Docker и плагин compose: `curl -fsSL https://get.docker.com | sh`.
- Домен: A-запись на IP сервера. Открыты порты 80 и 443, SSH — по ключу.

## 2. Первый запуск

```bash
git clone https://github.com/RediantDev/equistart.git /opt/equistart && cd /opt/equistart
cp .env.example .env
```

Заполнить `.env`:

| Переменная | Значение |
|---|---|
| `DJANGO_ENV` | `production` |
| `DJANGO_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `APP_ORIGIN` | `https://ваш-домен` |
| `DJANGO_ALLOWED_HOSTS` | `ваш-домен` |
| `TRUSTED_PROXY_IPS` | `172.16.0.0/12` (сеть docker, где работает nginx) |
| `POSTGRES_PASSWORD` | длинный случайный пароль |
| `SECURE_HSTS_SECONDS` | `31536000` после проверки HTTPS |
| `PHONE_VERIFICATION_REQUIRED` | `true` перед открытым запуском |
| `SMS_BACKEND`, `SMSRU_API_ID` | `smsru` и ключ из личного кабинета SMS.ru |
| `OPERATOR_*` | реквизиты ИП — попадут в политику ПДн и согласия |

В `deploy/nginx.conf` заменить `equistart.example` на свой домен.

Сертификат (Let's Encrypt через webroot):

```bash
mkdir -p deploy/acme deploy/certs
# временно запустить nginx только на 80 порту или выпустить сертификат certbot standalone до старта:
sudo certbot certonly --standalone -d ваш-домен
sudo cp /etc/letsencrypt/live/ваш-домен/{fullchain,privkey}.pem deploy/certs/
```

Запуск:

```bash
docker compose up -d db
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createcachetable
docker compose run --rm web python manage.py createsuperuser      # администратор (Иван)
docker compose up -d
curl -fsS https://ваш-домен/health/ready
```

## 3. Обновление

```bash
cd /opt/equistart
./deploy/backup.sh                       # копия перед обновлением
git pull
docker compose build web
docker compose run --rm web python manage.py migrate
docker compose up -d web
```

Миграции применяются отдельной командой — не при старте контейнера.

## 4. Резервные копии

`deploy/backup.sh` — дамп PostgreSQL (`pg_dump --format=custom`) и архив файлов (положения, документы лошадей), проверка читаемости дампа, удаление копий старше `KEEP_DAYS`.

```cron
15 3 * * * cd /opt/equistart && ./deploy/backup.sh >> /var/log/equistart-backup.log 2>&1
```

Второе хранилище (обязательно для пилота): S3-совместимое в России (Yandex Object Storage, Selectel S3). Настроить `rclone config`, указать `RCLONE_REMOTE=имя:бакет/equistart`.

### Восстановление

```bash
docker compose stop web
docker compose exec -T db dropdb -U equistart equistart
docker compose exec -T db createdb -U equistart equistart
docker compose exec -T db pg_restore -U equistart -d equistart < /var/backups/equistart/db-YYYYMMDD-HHMM.dump
docker compose run --rm --no-deps -v /var/backups/equistart:/backup --entrypoint sh web -c "cd /app && tar xzf /backup/media-YYYYMMDD-HHMM.tgz"
docker compose up -d web
```

Проверять восстановление на отдельном сервере раз в месяц.

## 5. Администрирование

| Задача | Команда |
|---|---|
| Назначить модератора | `docker compose run --rm web python manage.py set_moderator +79001234567` (`--off` — снять) |
| Ручной сброс пароля (закрытый тест) | `docker compose run --rm web python manage.py set_password +79001234567` — выдаёт временный пароль, пользователь меняет его в кабинете |
| Панель администратора | `https://ваш-домен/manage/` |
| Журнал приложения | `docker compose logs -f web` |

## 6. Перед открытым запуском (чек-лист)

- [ ] Уведомление в Роскомнадзор подано (pd.rkn.gov.ru) от ИП.
- [ ] Реквизиты `OPERATOR_*` заполнены; политика и согласия проверены юристом.
- [ ] `PHONE_VERIFICATION_REQUIRED=true`, СМС-шлюз проверен на своём номере.
- [ ] HTTPS работает, `SECURE_HSTS_SECONDS` задан, `manage.py check --deploy` без предупреждений.
- [ ] Резервная копия делается по расписанию и уходит во второе хранилище; восстановление проверено.
- [ ] Яндекс Метрика подключена (если нужна) — после согласования текста политики.
- [ ] Тестовые аккаунты и турниры удалены.
