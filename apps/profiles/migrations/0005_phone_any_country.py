# Generated for decision 25.10.2026: phone numbers of any country (E.164, up to 15 digits)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0004_added_by_judge"),
    ]

    operations = [
        migrations.AlterField(
            model_name="offer",
            name="phone",
            field=models.CharField(max_length=16),
        ),
    ]
