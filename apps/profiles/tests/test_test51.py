"""Тест 5.1 заказчика: спортсмены и лошади на телефоне."""
import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.entries import services as eservices
from apps.entries.tests.test_entries import PDF, TMP, Base
from apps.profiles import services
from apps.profiles.forms import AthleteForm, HorseForm
from apps.profiles.models import Athlete, Horse, HorseDocument, Offer


@override_settings(MEDIA_ROOT=TMP)
class AthleteFormTests(Base):
    def test_birth_date_and_rank_required(self):
        """Решение 28.10.2026: дата рождения и разряд обязательны; «б/р» — допустимый выбор."""
        self.client.force_login(self.coach)
        r = self.client.post(reverse("profiles:athlete_new"), {"last_name": "Ким", "first_name": "Алина"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.context["form"].errors), {"birth_date", "rank"})
        self.assertFalse(Athlete.objects.filter(last_name="Ким").exists())
        r = self.client.post(reverse("profiles:athlete_new"), {"last_name": "Ким", "first_name": "Алина", "birth_date": "2012-03-04", "rank": "none"})
        a = Athlete.objects.get(last_name="Ким")
        self.assertRedirects(r, reverse("profiles:athlete", args=[a.pk]))
        self.assertEqual(a.rank_short, "б/р")

    def test_old_record_without_rank_still_valid_in_db(self):
        # a2 создан без разряда: в списках и карточке он есть, в заявку его можно выбрать
        self.assertEqual(self.a2.rank, "")
        self.client.force_login(self.coach)
        self.assertContains(self.client.get(reverse("profiles:athletes")), "Орлов")
        self.assertEqual(self.client.get(reverse("profiles:athlete", args=[self.a2.pk])).status_code, 200)
        self.assertIn(self.a2, list(services.athletes_for_entry(self.coach)))
        # при редактировании потребуется дописать разряд
        f = AthleteForm({"last_name": "Орлов", "first_name": "Дима", "birth_date": "1996-01-01"}, instance=self.a2)
        self.assertIn("rank", f.errors)

    def test_no_verbose_help_texts(self):
        self.client.force_login(self.coach)
        page = self.client.get(reverse("profiles:athlete_new")).content.decode()
        for text in ("Один для конкура", "Один номер — один спортсмен", "регистрацией FEI", "Только подпись в протоколе", "Обязательно только ФИО"):
            self.assertNotIn(text, page)
        self.assertIn("— выберите —", page)

    def test_guest_and_foreign(self):
        self.assertEqual(self.client.get(reverse("profiles:athlete_new")).status_code, 302)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("profiles:athlete_edit", args=[self.a1.pk]),
                                          {"last_name": "Х", "first_name": "Х", "birth_date": "2010-01-01", "rank": "none"}).status_code, 403)


@override_settings(MEDIA_ROOT=TMP)
class HorseFormTests(Base):
    def data(self, **kw):
        d = {"name": "Гром", "birth_year": 2015, "color": "grey", "sex": "stallion", "breed": "орловская"}
        d.update(kw)
        return d

    def test_birth_year_realistic(self):
        this = timezone.localdate().year
        self.client.force_login(self.coach)
        r = self.client.post(reverse("profiles:horse_new"), self.data(birth_year=this - 36))
        self.assertContains(r, f"укажите год от {this - 35} до {this}")
        r = self.client.post(reverse("profiles:horse_new"), self.data(birth_year=this + 1))
        self.assertContains(r, "из будущего")
        self.assertFalse(Horse.objects.filter(name="Гром").exists())
        r = self.client.post(reverse("profiles:horse_new"), self.data(birth_year=this - 35))
        self.assertEqual(r.status_code, 302)

    def test_min_max_attrs_and_no_verbose_help(self):
        this = timezone.localdate().year
        self.client.force_login(self.coach)
        page = self.client.get(reverse("profiles:horse_new")).content.decode()
        self.assertIn(f'min="{this - 35}"', page)
        self.assertIn(f'max="{this}"', page)
        self.assertNotIn("мы предложим найденный профиль", page)
        self.assertNotIn("Нужен для зачётов", page)
        self.assertIn('data-show-when="is_pony:checked"', page)
        self.assertEqual(HorseForm().fields["owner_name"].help_text, "Не публикуется.")


@override_settings(MEDIA_ROOT=TMP)
class HorseSearchTests(Base):
    def setUp(self):
        super().setUp()
        self.h3 = Horse.objects.create(name="Гранд Ёлка", birth_year=2012, color="bay", sex="mare", breed="x", manager=self.other, fksr_number="777001")

    def test_prefix_not_contains(self):
        self.assertIn(self.h1, list(services.search_horses("бар", "auto")))
        self.assertNotIn(self.h1, list(services.search_horses("арон", "auto")))
        # начало любого слова, ё = е, регистр не важен
        self.assertIn(self.h3, list(services.search_horses("елк", "auto")))
        self.assertIn(self.h3, list(services.search_horses("ЁЛ", "auto")))
        self.assertIn(self.h3, list(services.search_horses("гра", "auto")))
        self.assertNotIn(self.h3, list(services.search_horses("лка", "auto")))
        self.assertIn(self.h3, list(services.search_horses("777001", "auto")))

    def test_with_next_results_selectable(self):
        self.client.force_login(self.coach)
        nxt = reverse("entries:apply", args=[self.t.pk])
        r = self.client.get(reverse("profiles:horse_search"), {"q": "лад", "next": nxt})
        self.assertContains(r, "Ладога")
        self.assertContains(r, f'action="{nxt}"')
        self.assertContains(r, 'name="pick_horse"')

    def test_without_next_redirects_to_my_horses(self):
        self.client.force_login(self.coach)
        r = self.client.get(reverse("profiles:horse_search"), {"q": "лад"})
        self.assertRedirects(r, reverse("profiles:horses") + "?q=%D0%BB%D0%B0%D0%B4")
        # внешний next не принимается
        r = self.client.get(reverse("profiles:horse_search"), {"q": "лад", "next": "https://evil.example/"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r["Location"].startswith(reverse("profiles:horses")))

    def test_guest(self):
        r = self.client.get(reverse("profiles:horse_search"), {"q": "лад"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])
        self.assertEqual(self.client.get(reverse("profiles:horses")).status_code, 302)

    def test_my_horses_only_own_clickable_live_filter(self):
        self.client.force_login(self.coach)
        r = self.client.get(reverse("profiles:horses"), {"q": "бар"})
        page = r.content.decode()
        self.assertIn(reverse("profiles:horse", args=[self.h1.pk]), page)
        self.assertNotIn("Ладога", page)
        self.assertNotIn("Гранд", page)
        self.assertIn('value="бар"', page)
        self.assertIn("data-horse-filter", page)
        self.assertNotIn(">Найти</button>", page)


@override_settings(MEDIA_ROOT=TMP)
class HorseDetailTests(Base):
    def test_edit_icon_hint_and_pending_inside_transfer(self):
        Offer.objects.create(kind=Offer.Kind.HORSE_TRANSFER, horse=self.h1, phone=self.other.phone, created_by=self.coach)
        self.client.force_login(self.coach)
        page = self.client.get(reverse("profiles:horse", args=[self.h1.pk])).content.decode()
        self.assertIn('aria-label="Изменить лошадь"', page)
        # макет R2_06A: подсказка — во вкладке «Документы», передача управления — во вкладке «Доступ»
        self.assertIn('class="pf-hint', self.client.get(reverse("profiles:horse", args=[self.h1.pk]), {"tab": "docs"}).content.decode())
        page = self.client.get(reverse("profiles:horse", args=[self.h1.pk]), {"tab": "access"}).content.decode()
        transfer = page[page.index("Передать управление"):]
        self.assertIn("Ожидает ответа", transfer)
        self.assertIn("Передача на", transfer)
        self.assertIn("Отменить", transfer)
        self.assertEqual(page.count("Ожидает ответа"), 1)

    def test_foreign_has_no_edit(self):
        self.client.force_login(self.other)
        for tab in ("", "access", "docs"):
            page = self.client.get(reverse("profiles:horse", args=[self.h1.pk]), {"tab": tab}).content.decode()
            self.assertNotIn('aria-label="Изменить лошадь"', page)
            self.assertNotIn("Передать управление", page)
            self.assertNotIn("Пригласить", page)


@override_settings(MEDIA_ROOT=TMP)
class DocumentViewerTests(Base):
    def setUp(self):
        super().setUp()
        self.doc = HorseDocument.objects.create(horse=self.h1, kind="passport", file=SimpleUploadedFile("d.pdf", PDF), content_type="application/pdf",
                                                uploaded_by=self.coach, valid_until=datetime.date(2030, 1, 31))
        self.view_url = reverse("profiles:horse_document_view", args=[self.h1.pk, self.doc.pk])
        self.file_url = reverse("profiles:horse_document", args=[self.h1.pk, self.doc.pk])

    def test_owner_sees_viewer(self):
        self.client.force_login(self.coach)
        r = self.client.get(self.view_url)
        self.assertContains(r, "← Барон")
        self.assertContains(r, "Ветеринарный паспорт")
        self.assertContains(r, "31.01.2030")
        self.assertContains(r, f'<iframe class="pf-doc__frame" src="{self.file_url}"')
        self.assertContains(r, "Скачать")
        # макет S08A: документы — во вкладке «Документы» профиля лошади
        detail = self.client.get(reverse("profiles:horse", args=[self.h1.pk]), {"tab": "docs"})
        self.assertContains(detail, f'href="{self.view_url}"')
        f = self.client.get(self.file_url)
        self.assertEqual(f.status_code, 200)
        self.assertEqual(f["X-Frame-Options"], "SAMEORIGIN")
        self.assertTrue(f["Content-Disposition"].startswith("inline"))
        self.assertTrue(self.client.get(self.file_url, {"download": "1"})["Content-Disposition"].startswith("attachment"))

    def test_image_preview(self):
        img = HorseDocument.objects.create(horse=self.h1, kind="other", file=SimpleUploadedFile("a.jpg", b"\xff\xd8\xff" + b"0" * 20),
                                           content_type="image/jpeg", uploaded_by=self.coach)
        self.client.force_login(self.coach)
        r = self.client.get(reverse("profiles:horse_document_view", args=[self.h1.pk, img.pk]))
        self.assertContains(r, '<img class="pf-doc__img"')

    def test_tournament_judge_after_entry(self):
        self.client.force_login(self.judge)
        self.assertEqual(self.client.get(self.view_url).status_code, 403)
        eservices.submit(self.coach, self.t.pk, [eservices.Line(self.a2, self.h1, self.c_open)], "k-doc")
        self.assertTrue(services.can_view_horse_documents(self.judge, self.h1))
        self.assertEqual(self.client.get(self.view_url).status_code, 200)

    def test_other_user_and_guest(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.view_url).status_code, 403)
        self.assertEqual(self.client.get(self.file_url).status_code, 403)
        # документ чужой лошади через свою — 404
        own = HorseDocument.objects.create(horse=self.h2, kind="passport", file=SimpleUploadedFile("e.pdf", PDF), content_type="application/pdf", uploaded_by=self.other)
        self.assertEqual(self.client.get(reverse("profiles:horse_document_view", args=[self.h2.pk, self.doc.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("profiles:horse_document_view", args=[self.h2.pk, own.pk])).status_code, 200)
        self.client.logout()
        r = self.client.get(self.view_url)
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])
