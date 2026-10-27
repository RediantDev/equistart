# Generated for decision 25.10.2026: phone numbers of any country (E.164, up to 15 digits)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tournaments", "0010_deadline_time_reject"),
    ]

    operations = [
        migrations.AlterField(
            model_name="teammember",
            name="phone",
            field=models.CharField(max_length=16, verbose_name="телефон"),
        ),
    ]
