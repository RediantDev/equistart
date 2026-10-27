from django.db import migrations

NOTE = "Никогда не выполнявшие норматив указанного разряда и выше; проверяется судьями на месте."


def forwards(apps, schema_editor):
    # Тест 4, п. 3: то же пояснение убираем и из опубликованных снимков турниров.
    Tournament = apps.get_model("tournaments", "Tournament")
    for t in Tournament.objects.exclude(snapshot=None).iterator():
        snap, changed = t.snapshot or {}, False
        for p in snap.get("programs") or []:
            for c in p.get("classes") or []:
                if c.get("note") == NOTE:
                    c["note"] = ""
                    changed = True
        if changed:
            Tournament.objects.filter(pk=t.pk).update(snapshot=snap)


class Migration(migrations.Migration):
    dependencies = [("tournaments", "0008_test4_levels")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
