"""Тест 5.3 (29.10.2026): «Мои соревнования» без отменённых, режим правки заявки, ошибка экрана изменения."""

from django.test import override_settings
from django.urls import reverse

from apps.entries import services
from apps.entries.models import Entry
from apps.entries.tests.test_entries import TMP, Base


@override_settings(MEDIA_ROOT=TMP)
class T53Tests(Base):
    def setUp(self):
        super().setUp()
        self.app = self.submit(self.coach, [services.Line(self.a2, self.h1, self.c_open), services.Line(self.a1, self.h1, self.c_kids)])
        self.e_open = self.app.entries.get(tclass=self.c_open)

    def test_competitions_hide_cancelled(self):
        Entry.objects.filter(pk=self.e_open.pk).update(decision=Entry.Decision.CANCELLED)
        self.client.force_login(self.coach)
        r = self.client.get(reverse("entries:competitions"))
        page = r.content.decode()
        self.assertIn("Выступлений: 1", page)
        self.assertNotIn('data-status="cancelled"', page)
        self.assertNotIn(">Отменено</button>", page)
        # всё отменено — турнира в списке нет
        Entry.objects.filter(application=self.app).update(decision=Entry.Decision.CANCELLED)
        r = self.client.get(reverse("entries:competitions"))
        self.assertNotIn("data-mc-card data-title", r.content.decode())
        self.assertEqual(r.context["counts"]["upcoming"], 0)

    def test_competitions_guest_and_stranger(self):
        self.assertIn("login", self.client.get(reverse("entries:competitions"))["Location"])
        self.client.force_login(self.other)
        self.assertNotIn("data-mc-card data-title", self.client.get(reverse("entries:competitions")).content.decode())

    def test_edit_mode_back_to_application(self):
        self.client.force_login(self.coach)
        url = reverse("entries:application", args=[self.app.pk])
        page = self.client.get(url + "?edit=1").content.decode()
        self.assertNotIn("Выйти из правки", page)
        self.assertIn(f'<a class="back" href="{url}">← Заявка №{self.app.number}</a>', page)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url + "?edit=1").status_code, 403)

    def test_change_error_once(self):
        self.client.force_login(self.coach)
        r = self.client.post(reverse("entries:propose", args=[self.e_open.pk]), {"athlete": self.a2.pk, "horse": self.h1.pk, "tclass": self.c_open.pk})
        page = r.content.decode()
        self.assertEqual(page.count("Ничего не изменено"), 1)
        self.assertEqual(page.count("alert--error"), 1)
