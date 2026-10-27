import secrets

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.accounts.phone import normalize_phone
from apps.core.models import audit


class Command(BaseCommand):
    help = "Ручной сброс пароля на закрытом тесте: set_password +79001234567 — выдаёт временный пароль."

    def add_arguments(self, parser):
        parser.add_argument("phone")

    def handle(self, phone, **kw):
        p = normalize_phone(phone)
        u = User.objects.filter(phone=p).first() if p else None
        if not u:
            raise CommandError("Пользователь не найден.")
        tmp = "Es-" + secrets.token_urlsafe(9)
        u.set_password(tmp)
        u.save(update_fields=["password"])
        audit(None, "user.password_reset", u, via="manage.py")
        self.stdout.write(f"Временный пароль для {u.full_name}: {tmp}\nПередайте его лично и попросите сменить.")
