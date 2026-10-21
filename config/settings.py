"""Настройки EquiStart.

Все значения, зависящие от окружения, читаются из переменных окружения.
Окружение задаётся DJANGO_ENV: development | test | production.
"""

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

ENV = os.environ.get("DJANGO_ENV", "development")
if ENV not in {"development", "test", "production"}:
    raise ImproperlyConfigured(f"Неизвестное DJANGO_ENV: {ENV}")
IS_PRODUCTION = ENV == "production"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


def parse_database_url(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL должен указывать на PostgreSQL")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "ATOMIC_REQUESTS": False,
    }


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY обязателен в production")
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

DEBUG = env_bool("DJANGO_DEBUG", default=ENV == "development")
if IS_PRODUCTION and DEBUG:
    raise ImproperlyConfigured("DEBUG запрещён в production")

# Точный origin сайта, например https://equistart.ru
APP_ORIGIN = os.environ.get("APP_ORIGIN", "http://127.0.0.1:8000")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
CSRF_TRUSTED_ORIGINS = [APP_ORIGIN]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core",
    "apps.accounts",
    "apps.tournaments",
    "apps.profiles",
    "apps.entries",
    "apps.conduct",
    "apps.stables",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.accounts.middleware.PhoneVerificationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": parse_database_url(
        os.environ.get("DATABASE_URL", "postgresql://equistart:equistart@127.0.0.1:5432/equistart")
    )
}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.PhoneBackend"]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:cabinet"
LOGOUT_REDIRECT_URL = "core:calendar"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

if ENV == "test":
    # Быстрое хеширование только для автотестов.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Сессии: до 7 дней, продлеваются при активности.
SESSION_COOKIE_AGE = 7 * 24 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# Кэш используется для ограничения попыток входа.
# В production — таблица в PostgreSQL (общая для всех процессов).
if IS_PRODUCTION:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "cache_table"}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# Требование 149-ФЗ: подтверждение телефона обязательно до открытого запуска.
# На закрытом тестовом стенде может быть выключено.
PHONE_VERIFICATION_REQUIRED = env_bool("PHONE_VERIFICATION_REQUIRED", default=False)
# console — код в журнал сервера; smsru — шлюз SMS.ru
SMS_BACKEND = os.environ.get("SMS_BACKEND", "console")
SMSRU_API_ID = os.environ.get("SMSRU_API_ID", "")

# Ограничения попыток входа
LOGIN_MAX_FAILURES_PER_PHONE = 5
LOGIN_MAX_FAILURES_PER_IP = 20
LOGIN_LOCKOUT_SECONDS = 15 * 60
# Если сайт стоит за reverse proxy, указать его адрес; только ему доверяется X-Forwarded-For.
TRUSTED_PROXY_IPS = env_list("TRUSTED_PROXY_IPS")

LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"  # время хранится в UTC; турниры показываются в поясе места проведения
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if IS_PRODUCTION
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Безопасность
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
# Тестовый стенд без HTTPS (по IP-адресу): HTTPS_ENABLED=false. Только для тестовых данных.
HTTPS_ENABLED = env_bool("HTTPS_ENABLED", default=True)
if IS_PRODUCTION and not HTTPS_ENABLED:
    SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008", "security.W012", "security.W016"]
if IS_PRODUCTION and HTTPS_ENABLED:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True)
    SECURE_HSTS_PRELOAD = False
    # Включение в HSTS preload-список — решение владельца домена, принимается отдельно.
    SILENCED_SYSTEM_CHECKS = ["security.W021"]

# Плашка «тестовый стенд» вверху каждой страницы (пусто — без плашки)
STAND_BANNER = os.environ.get("STAND_BANNER", "")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO" if IS_PRODUCTION else "WARNING"},
}

SITE_NAME = "EquiStart"

# В разработке WhiteNoise отдаёт файлы прямо из static/ без collectstatic.
WHITENOISE_USE_FINDERS = not IS_PRODUCTION
WHITENOISE_AUTOREFRESH = not IS_PRODUCTION

# Оператор персональных данных (реквизиты ИП заказчика). Заполняются в .env до запуска.
OPERATOR = {
    "name": (os.environ.get("OPERATOR_NAME") or "[ИП Фамилия Имя Отчество]"),
    "inn": (os.environ.get("OPERATOR_INN") or "[ИНН]"),
    "ogrnip": (os.environ.get("OPERATOR_OGRNIP") or "[ОГРНИП]"),
    "address": (os.environ.get("OPERATOR_ADDRESS") or "[адрес для корреспонденции]"),
    "email": (os.environ.get("OPERATOR_EMAIL") or "[email для обращений по персональным данным]"),
    "site": (os.environ.get("OPERATOR_SITE") or APP_ORIGIN),
}
LEGAL_DOCS_VERSION = "2026-10-14"

# Загруженные файлы (положения турниров, документы лошадей). Не раздаются напрямую —
# только через view с проверкой прав. В production — S3-совместимое хранилище в РФ.
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT") or BASE_DIR / "media")
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
