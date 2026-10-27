from django.db import migrations

NOTE = "Никогда не выполнявшие норматив указанного разряда и выше; проверяется судьями на месте."


def forwards(apps, schema_editor):
    # Тест 4, п. 3: пояснение дублирует колонку «Условия участия» — убираем из шаблона и из уже созданных зачётов.
    apps.get_model("sport", "ClassTemplate").objects.filter(note=NOTE).update(note="")
    apps.get_model("tournaments", "TournamentClass").objects.filter(note=NOTE).update(note="")


class Migration(migrations.Migration):
    dependencies = [("tournaments", "0006_cancel_terms_label"), ("sport", "0003_children_note")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
