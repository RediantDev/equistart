# Generated for decision 25.10.2026: phone numbers of any country (E.164, up to 15 digits)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("stables", "0002_mvp_no_money"),
    ]

    operations = [
        migrations.AlterField(
            model_name="booking",
            name="contact_phone",
            field=models.CharField(blank=True, max_length=16, verbose_name="телефон для связи по брони"),
        ),
    ]
