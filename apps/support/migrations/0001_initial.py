# Создано вручную по apps/support/models.py (26.10.2026)

import apps.support.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tournaments", "0011_phone_any_country"),
        ("profiles", "0005_phone_any_country"),
        ("clubs", "0002_club_merged_at_club_merged_into_clubmember"),
    ]

    operations = [
        migrations.CreateModel(
            name="Faq",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("section", models.CharField(choices=[("general", "Общие"), ("athlete", "Спортсмен"), ("coach", "Тренер / представитель"), ("organizer", "Организатор"), ("judge", "Судья"), ("club", "Клуб")], default="general", max_length=12, verbose_name="раздел")),
                ("question", models.CharField(max_length=300, verbose_name="вопрос")),
                ("answer", models.TextField(verbose_name="ответ")),
                ("order", models.PositiveIntegerField(default=100, verbose_name="порядок")),
                ("published", models.BooleanField(default=True, verbose_name="опубликован")),
            ],
            options={"verbose_name": "частый вопрос", "verbose_name_plural": "частые вопросы", "ordering": ["section", "order", "id"]},
        ),
        migrations.CreateModel(
            name="Ticket",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(blank=True, max_length=200, verbose_name="имя")),
                ("phone", models.CharField(blank=True, max_length=20, verbose_name="телефон")),
                ("email", models.EmailField(blank=True, max_length=254, verbose_name="email для ответа")),
                ("topic", models.CharField(choices=[("login", "Вход и пароль"), ("data", "Ошибка в данных спортсмена или лошади"), ("duplicate", "Дубль"), ("dispute", "Спор о лошади, спортсмене или клубе"), ("review", "Проверка клуба или турнира"), ("results", "Ошибка в результатах"), ("bug", "Ошибка сервиса"), ("other", "Другое")], max_length=20, verbose_name="тема")),
                ("text", models.TextField(max_length=5000, verbose_name="текст обращения")),
                ("status", models.CharField(choices=[("new", "Новое"), ("in_work", "В работе"), ("waiting", "Ждёт ответа пользователя"), ("resolved", "Решено")], db_index=True, default="new", max_length=10, verbose_name="статус")),
                ("urgent", models.BooleanField(db_index=True, default=False, verbose_name="идёт турнир")),
                ("page", models.CharField(blank=True, max_length=300, verbose_name="страница")),
                ("user_agent", models.CharField(blank=True, max_length=300, verbose_name="устройство и браузер")),
                ("token", models.CharField(default=apps.support.models._token, editable=False, max_length=64, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="изменено")),
                ("last_user_at", models.DateTimeField(blank=True, null=True, verbose_name="последнее сообщение пользователя")),
                ("last_staff_at", models.DateTimeField(blank=True, null=True, verbose_name="последний ответ поддержки")),
                ("transferred_at", models.DateTimeField(blank=True, null=True, verbose_name="передано судьям турнира")),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="support_tickets", to=settings.AUTH_USER_MODEL, verbose_name="аккаунт")),
                ("athlete", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="profiles.athlete", verbose_name="спортсмен")),
                ("club", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="clubs.club", verbose_name="клуб")),
                ("horse", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="profiles.horse", verbose_name="лошадь")),
                ("tournament", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="tournaments.tournament", verbose_name="турнир")),
            ],
            options={"verbose_name": "обращение", "verbose_name_plural": "обращения в поддержку", "ordering": ["-urgent", "-created_at"]},
        ),
        migrations.CreateModel(
            name="TicketAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="support/%Y/%m/")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="support.ticket")),
            ],
            options={"verbose_name": "вложение", "verbose_name_plural": "вложения"},
        ),
        migrations.CreateModel(
            name="TicketMessage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_staff", models.BooleanField(default=False, verbose_name="ответ поддержки")),
                ("text", models.TextField(max_length=5000, verbose_name="текст")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="support.ticket")),
            ],
            options={"verbose_name": "сообщение", "verbose_name_plural": "сообщения", "ordering": ["created_at", "id"]},
        ),
    ]
