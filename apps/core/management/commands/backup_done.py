"""Отметка об успешной резервной копии — для пульта администратора.

Вызывается в конце deploy/backup.sh:
    docker compose exec -T web python manage.py backup_done 20261026-0315
"""

from django.core.management.base import BaseCommand

from apps.core.models import AuditLog


class Command(BaseCommand):
    help = "Записать в журнал, что резервная копия сделана и проверена"

    def add_arguments(self, parser):
        parser.add_argument("stamp", nargs="?", default="", help="метка копии из backup.sh")

    def handle(self, *args, stamp="", **options):
        AuditLog.objects.create(action="service.backup", target_type="service", target_id=0, data={"stamp": stamp[:40]})
        self.stdout.write("backup recorded")
