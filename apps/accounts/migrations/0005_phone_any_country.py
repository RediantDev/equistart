# Generated for decision 25.10.2026: phone numbers of any country (E.164, up to 15 digits)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_tz22_roles_profiles"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="user",
            name="user_phone_normalized",
        ),
        migrations.AlterField(
            model_name="user",
            name="phone",
            field=models.CharField(
                help_text="Логин. Мобильный номер в международном формате: +7…, +375…, +998… и т. п.",
                max_length=16,
                unique=True,
                verbose_name="телефон",
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("phone__regex", "^\\+(79\\d{9}|77\\d{9}|375(25|29|33|44)\\d{7}|[124-689]\\d{7,14}|3[0-689]\\d{6,13}|37[0-46-9]\\d{5,12})$")
                ),
                name="user_phone_normalized",
            ),
        ),
    ]
