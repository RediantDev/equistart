from django.db import migrations, models


def forwards(apps, schema_editor):
    ProgramItem = apps.get_model("tournaments", "ProgramItem")
    StartSlot = apps.get_model("conduct", "StartSlot")
    # выездка, у которой время уже считалось, сохраняет расчёт; у конкура время стартов убирается
    ProgramItem.objects.filter(discipline="dressage").update(start_times=True)
    StartSlot.objects.filter(program__discipline="jumping").update(start_time=None)


class Migration(migrations.Migration):
    dependencies = [("tournaments", "0004_sport_routes_classes"), ("conduct", "0002_scoring")]
    operations = [
        migrations.AddField(model_name="programitem", name="start_times",
                            field=models.BooleanField(default=False, help_text="Только для выездки. В конкуре время стартов не рассчитывается (RUN-04).",
                                                      verbose_name="рассчитывать время стартов")),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
