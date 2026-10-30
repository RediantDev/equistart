"""Тест 6.1 (30.10.2026): публикация протокола только при изменениях, окно «Отклонить», публикация результатов по зачётам."""

from django.test import override_settings
from django.urls import reverse

from apps.conduct import services
from apps.conduct.tests.test_conduct import ConductBase
from apps.entries.tests.test_entries import TMP


@override_settings(MEDIA_ROOT=TMP)
class RouteScreenTests(ConductBase):
    def url(self, tab):
        return reverse("conduct:program", args=[self.t.pk, self.p1.pk]) + f"?tab={tab}"

    def test_publish_button_only_when_order_changed(self):
        """П. 19: «Опубликовать новую версию» — только после изменения порядка."""
        self.client.force_login(self.judge)
        services.draw(self.judge, self.p1.pk)
        self.assertContains(self.client.get(self.url("order")), "Опубликовать протокол")
        services.publish_start_list(self.judge, self.p1.pk)
        r = self.client.get(self.url("order"))
        self.assertContains(r, "изменений нет")
        self.assertNotContains(r, "Опубликовать новую версию")
        a, b = self.p1.slots.order_by("position")
        services.move(self.judge, b.pk, 1)
        self.assertContains(self.client.get(self.url("order")), "Опубликовать новую версию")

    def test_route_menu_and_tabs(self):
        """Пп. 21–23: без «Панели турнира» в меню; вкладки Заявки · Порядок · Протокол · Результаты."""
        self.client.force_login(self.judge)
        r = self.client.get(self.url("order"))
        self.assertNotContains(r, "Панель турнира")
        for label in ("Заявки", "Порядок", "Протокол", "Результаты"):
            self.assertContains(r, f">{label}</a>")

    def test_publish_results_stays_on_results_tab(self):
        """Пп. 32–33: после публикации — вкладка «Результаты»; без изменений кнопки нет."""
        self.client.force_login(self.judge)
        services.draw(self.judge, self.p1.pk)
        services.save_result(self.judge, self.e2.pk, {"penalties": "0"}, "1", "")
        r = self.client.get(self.url("results"))
        self.assertContains(r, ">Опубликовать</button>")
        resp = self.client.post(reverse("conduct:action", args=[self.t.pk, self.p1.pk, "publish_results"]), {"tclass": self.c_open.pk})
        self.assertIn("tab=results", resp["Location"])
        r = self.client.get(self.url("results"))
        self.assertContains(r, "Без изменений")
        services.save_result(self.judge, self.e2.pk, {"penalties": "4"}, "1", "")
        r = self.client.get(self.url("results"))
        self.assertContains(r, "есть исправление")
        self.assertContains(r, ">Обновить</button>")

    def test_reject_sheet_and_guest(self):
        """П. 18: окно «Отклонить заявку» с частыми причинами; гостю экран закрыт."""
        url = self.url("entries")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.judge)
        r = self.client.get(reverse("entries:manage", args=[self.t.pk]))
        self.assertContains(r, f'data-sheet-open="rej{self.e2.pk}"')
        self.assertContains(r, "Не подходит по году рождения")
        self.assertContains(r, "Отклонить заявку")
