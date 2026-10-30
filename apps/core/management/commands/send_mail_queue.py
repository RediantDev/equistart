from django.core.management.base import BaseCommand

from apps.core.mail import retry_pending


class Command(BaseCommand):
    help = "Повторяет отправку писем, которые не ушли с первого раза (cron: */5 * * * *)."

    def handle(self, *args, **opts):
        ok, failed = retry_pending()
        self.stdout.write(f"отправлено: {ok}, ошибок: {failed}")
