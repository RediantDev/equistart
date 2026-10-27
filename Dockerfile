FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 1000 app && mkdir -p /app/media && chown app:app /app/media

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN DJANGO_ENV=production DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

USER app
EXPOSE 8000

# Миграции выполняются отдельной командой (см. README), не при старте контейнера.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--access-logfile", "-"]
