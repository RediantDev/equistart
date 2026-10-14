# EquiStart

Сервис для организации и проведения конных соревнований в России: календарь, заявки, допуск, стартовые протоколы, результаты, денники и учёт оплат.

Требования к продукту — [docs/TZ_v2.md](docs/TZ_v2.md). Открытые вопросы — [docs/QUESTIONS.md](docs/QUESTIONS.md).

## Стек

Python 3.13, Django 5.2, PostgreSQL 16, HTMX, WhiteNoise, Gunicorn, Docker Compose.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # при необходимости поправьте DATABASE_URL

# PostgreSQL должен быть запущен, база и пользователь созданы:
#   createuser -P equistart  &&  createdb -O equistart equistart

python manage.py migrate
python manage.py createsuperuser   # телефон +79XXXXXXXXX и пароль
python manage.py runserver
```

Сайт: http://127.0.0.1:8000 · Панель управления и модерации: http://127.0.0.1:8000/manage/

Переменные из `.env` подхватываются, если экспортировать их в окружение (например, `set -a; . ./.env; set +a`).

## Проверки

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

Те же проверки и `check --deploy` для production-настроек запускаются в GitHub Actions на каждый push.

## Запуск через Docker Compose

```bash
cp .env.example .env   # задайте DJANGO_SECRET_KEY, APP_ORIGIN, DJANGO_ALLOWED_HOSTS, DJANGO_ENV=production
docker compose up -d db
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py createcachetable
docker compose run --rm web python manage.py createsuperuser
docker compose up -d web
```

Проверка работоспособности: `/health/live` (процесс) и `/health/ready` (доступность БД).

Миграции применяются отдельной командой оператора, не при старте приложения.

## Структура

```
config/            настройки, маршруты
apps/accounts/     аккаунты: телефон как логин, категории, вход, кабинет
apps/core/         календарь, служебные страницы, health
templates/         шаблоны (base.html, partials/)
static/            CSS (дизайн-токены в :root) и htmx
docs/              ТЗ и вопросы
```
