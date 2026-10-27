from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.accounts.phone import normalize_phone
from apps.core.models import audit


class Command(BaseCommand):
    help = "Назначить или снять модератора по телефону: set_moderator +79001234567 [--off]"

    def add_arguments(self, parser):
        parser.add_argument("phone")
        parser.add_argument("--off", action="store_true", help="снять роль модератора")

    def handle(self, phone, off, **kw):
        p = normalize_phone(phone)
        u = User.objects.filter(phone=p).first() if p else None
        if not u:
            raise CommandError("Пользователь с таким телефоном не найден — он должен сначала зарегистрироваться.")
        u.is_moderator = not off
        u.save(update_fields=["is_moderator"])
        audit(None, "user.moderator_off" if off else "user.moderator_on", u, via="manage.py")
        self.stdout.write(self.style.SUCCESS(f"{u.full_name}: модератор {'снят' if off else 'назначен'}."))
