"""Учёт денег исключён из MVP (решение 20.10.2026): таблицы бывшего приложения finance удаляются.

Код модуля сохранён в истории Git (бэклог, ТЗ раздел 17).
"""

from django.db import migrations

TABLES = ["finance_allocation", "finance_refund", "finance_adjustment", "finance_payment", "finance_charge"]


class Migration(migrations.Migration):
    dependencies = [("core", "0002_notification")]

    operations = [
        migrations.RunSQL(
            [f"DROP TABLE IF EXISTS {t} CASCADE" for t in TABLES] + ["DELETE FROM django_migrations WHERE app = 'finance'"],
            migrations.RunSQL.noop,
        ),
    ]
