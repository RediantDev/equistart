# Сброс паролей тестовых аккаунтов на Demo-Pass-2026 и снятие блокировки входа.
# Запуск: reset-passwords.bat в папке проекта.
from django.core.cache import cache
from apps.accounts.models import User

n = 0
for u in User.objects.filter(phone__startswith="+7999000000"):
    u.set_password("Demo-Pass-2026")
    u.is_active = True
    u.save()
    n += 1
cache.clear()
print(f"Готово: пароль Demo-Pass-2026 задан для {n} тестовых аккаунтов, блокировка входа снята.")
