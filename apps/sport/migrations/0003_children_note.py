from django.db import migrations


def forwards(apps, schema_editor):
    # Подсказка для организатора не должна попадать в примечание зачёта, которое видят участники
    ClassTemplate = apps.get_model("sport", "ClassTemplate")
    ClassTemplate.objects.filter(name="Дети", note__startswith="Возраст по году рождения").update(note="")
    TournamentClass = apps.get_model("tournaments", "TournamentClass")
    TournamentClass.objects.filter(note__startswith="Возраст по году рождения. В выездке дети").update(note="")


class Migration(migrations.Migration):
    dependencies = [("sport", "0002_seed"), ("tournaments", "0005_programitem_start_times")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
