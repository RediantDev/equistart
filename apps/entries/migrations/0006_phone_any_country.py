# Generated for decision 25.10.2026: phone numbers of any country (E.164, up to 15 digits)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("entries", "0005_application_draft"),
    ]

    operations = [
        migrations.AlterField(
            model_name="application",
            name="contact_phone",
            field=models.CharField(blank=True, max_length=16, verbose_name="телефон для связи"),
        ),
    ]
