# Решение заказчика 27.10.2026: разряд / звание — одно поле, без деления на конкур и выездку.
# Перенос данных: из двух прежних полей берём старший разряд по шкале ЕВСК (порядок RANKS).

from django.db import migrations, models

RANKS = [
    ("none", "б/р"),
    ("3y", "III юношеский"),
    ("2y", "II юношеский"),
    ("1y", "I юношеский"),
    ("3", "III разряд"),
    ("2", "II разряд"),
    ("1", "I разряд"),
    ("kms", "КМС"),
    ("ms", "МС"),
    ("msmk", "МСМК"),
]
ORDER = {"": -1, **{code: i for i, (code, _) in enumerate(RANKS)}}


def merge_ranks(apps, schema_editor):
    Athlete = apps.get_model("profiles", "Athlete")
    for a in Athlete.objects.exclude(rank_jumping="", rank_dressage="").only("pk", "rank_jumping", "rank_dressage"):
        best = max(a.rank_jumping or "", a.rank_dressage or "", key=lambda r: ORDER.get(r, -1))
        Athlete.objects.filter(pk=a.pk).update(rank=best)


def split_ranks(apps, schema_editor):
    Athlete = apps.get_model("profiles", "Athlete")
    Athlete.objects.update(rank_jumping=models.F("rank"), rank_dressage=models.F("rank"))


class Migration(migrations.Migration):

    dependencies = [
        ("profiles", "0005_phone_any_country"),
    ]

    operations = [
        migrations.AddField(
            model_name="athlete",
            name="rank",
            field=models.CharField(blank=True, choices=RANKS, max_length=8, verbose_name="разряд / звание"),
        ),
        migrations.RunPython(merge_ranks, split_ranks),
        migrations.RemoveField(model_name="athlete", name="rank_dressage"),
        migrations.RemoveField(model_name="athlete", name="rank_jumping"),
    ]
