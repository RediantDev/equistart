"""Экраны по утверждённым макетам 28.10.2026: S04B, R2_05, R2_06A–D, R2_07A/B, S08A."""
import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.entries.tests.test_entries import PDF, TMP, Base, mkuser
from apps.profiles.models import AthleteDelegate, Athlete, Horse, HorseAccess, HorseDocument, Offer


@override_settings(MEDIA_ROOT=TMP)
class AthletesScreenTests(Base):
    def test_groups_and_missing_year_rank(self):
        AthleteDelegate.objects.create(athlete=self.a2, user=self.other, granted_by=self.coach)
        Athlete.objects.create(last_name="Ким", first_name="Алина", manager=self.other, linked_user=self.other, rank="2", birth_date=datetime.date(2005, 1, 1))
        Athlete.objects.create(last_name="Петров", first_name="Пётр", manager=self.other, birth_date=datetime.date(2001, 1, 1), rank="none")
        self.client.force_login(self.other)
        page = self.client.get(reverse("profiles:athletes")).content.decode()
        me, mine, trusted = page.index("Это я"), page.index(">Мои<"), page.index(">Мне доверили<")
        self.assertLess(me, page.index("Ким Алина"))
        self.assertLess(mine, page.index("Петров Пётр"))
        self.assertLess(trusted, page.index("Орлов Дима"))
        # a2 без разряда; год указан
        self.assertIn("разряд не указан", page)
        self.assertIn("управляет Соколова Мария", page)

    def test_no_year_no_rank(self):
        Athlete.objects.create(last_name="Без", first_name="Даты", manager=self.coach)
        self.client.force_login(self.coach)
        self.assertContains(self.client.get(reverse("profiles:athletes")), "год и разряд не указаны")

    def test_guest_and_foreign(self):
        self.assertEqual(self.client.get(reverse("profiles:athletes")).status_code, 302)
        self.client.force_login(mkuser("+79990001790"))
        self.assertNotContains(self.client.get(reverse("profiles:athletes")), "Орлов")

    def test_detail_edit_is_pencil_for_manager_only(self):
        """Тест 5.2, п. 50: «Изменить» — карандаш справа от имени; делегату, чужому и гостю — нет."""
        url = reverse("profiles:athlete", args=[self.a1.pk])
        edit = reverse("profiles:athlete_edit", args=[self.a1.pk])
        self.client.force_login(self.coach)
        page = self.client.get(url).content.decode()
        self.assertIn(f'class="ps-ib ps-ib--o" href="{edit}" aria-label="Изменить сведения спортсмена"', page)
        self.assertNotIn(">Изменить</a>", page)
        AthleteDelegate.objects.create(athlete=self.a1, user=self.other, granted_by=self.coach)
        self.client.force_login(self.other)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, edit)
        self.client.force_login(mkuser("+79990001791"))
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)


@override_settings(MEDIA_ROOT=TMP)
class MyHorsesScreenTests(Base):
    def test_chips_counts_and_shared_label(self):
        HorseAccess.objects.create(horse=self.h2, user=self.coach, granted_by=self.other)
        Horse.objects.create(name="Пуговка", birth_year=2016, color="bay", sex="mare", breed="уэльский", manager=self.coach, is_pony=True)
        self.client.force_login(self.coach)
        page = self.client.get(reverse("profiles:horses")).content.decode()
        # Тест 5.2, п. 54: без счётчиков после слов
        self.assertIn('data-f="all" aria-pressed="true">Все</button>', page)
        self.assertIn('data-f="mine" aria-pressed="false">Мои</button>', page)
        self.assertIn('data-f="shared" aria-pressed="false">С доступом</button>', page)
        self.assertIn('data-f="pony" aria-pressed="false">Пони</button>', page)
        self.assertIn('<h1 class="h1 ps-h1">Мои лошади</h1>', page)
        self.assertIn("управляет Тест Юзер", page)
        self.assertIn('data-horse-filter', page)
        # документы в списке не показываем
        self.assertNotIn("Документ", page)

    def test_incoming_invite_card_accept_returns_to_list(self):
        o = Offer.objects.create(kind=Offer.Kind.HORSE_ACCESS, horse=self.h1, phone=self.other.phone, created_by=self.coach)
        self.client.force_login(self.other)
        page = self.client.get(reverse("profiles:horses")).content.decode()
        self.assertIn("Соколова Мария открывает вам доступ к лошади Барон", page)
        self.assertIn(reverse("profiles:offer_respond", args=[o.pk]), page)
        r = self.client.post(reverse("profiles:offer_respond", args=[o.pk]), {"decision": "accept", "next": reverse("profiles:horses")})
        self.assertRedirects(r, reverse("profiles:horses"))
        self.assertTrue(HorseAccess.objects.filter(horse=self.h1, user=self.other).exists())
        self.assertNotIn("открывает вам доступ", self.client.get(reverse("profiles:horses")).content.decode())

    def test_respond_external_next_ignored_and_foreign_forbidden(self):
        o = Offer.objects.create(kind=Offer.Kind.HORSE_ACCESS, horse=self.h1, phone=self.other.phone, created_by=self.coach)
        stranger = mkuser("+79990001791")
        self.client.force_login(stranger)
        self.assertNotContains(self.client.get(reverse("profiles:horses")), "открывает вам доступ")
        r = self.client.post(reverse("profiles:offer_respond", args=[o.pk]), {"decision": "accept", "next": reverse("profiles:horses")})
        self.assertEqual(r.status_code, 403)
        self.client.force_login(self.other)
        r = self.client.post(reverse("profiles:offer_respond", args=[o.pk]), {"decision": "decline", "next": "https://evil.example/"})
        self.assertRedirects(r, reverse("profiles:offers"))
        self.client.logout()
        r = self.client.post(reverse("profiles:offer_respond", args=[o.pk]), {"decision": "accept"})
        self.assertIn("login", r["Location"])


@override_settings(MEDIA_ROOT=TMP)
class HorseTabsTests(Base):
    def url(self, tab=""):
        return reverse("profiles:horse", args=[self.h1.pk]) + (f"?tab={tab}" if tab else "")

    def test_access_tab_groups(self):
        HorseAccess.objects.create(horse=self.h1, user=self.other, granted_by=self.coach)
        Offer.objects.create(kind=Offer.Kind.HORSE_ACCESS, horse=self.h1, phone="+79161234545", created_by=self.coach)
        Offer.objects.create(kind=Offer.Kind.HORSE_ACCESS, horse=self.h1, phone="+79031111112", created_by=self.coach,
                             state=Offer.State.DECLINED, responded_at=timezone.now())
        self.client.force_login(self.coach)
        page = self.client.get(self.url("access")).content.decode()
        self.assertIn(">Ожидает подтверждения</h3>", page)
        self.assertIn("+7 916 ***-**-45", page)
        self.assertIn(">Есть доступ</h3>", page)
        self.assertIn("Закрыть доступ", page)
        self.assertIn(">Отклонено</h3>", page)
        self.assertIn("+7 903 ***-**-12", page)
        self.assertIn('aria-current="page">Доступ</a>', page)
        self.assertNotIn("Доступ 2", page)
        self.assertIn("Отправить приглашение", page)

    def test_actions_return_to_tab(self):
        self.client.force_login(self.coach)
        r = self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "grant"]), {"phone": "+79990001004"})
        self.assertRedirects(r, self.url("access"))
        r = self.client.post(reverse("profiles:horse_action", args=[self.h1.pk, "upload"]),
                             {"kind": "passport", "file": SimpleUploadedFile("d.pdf", PDF)})
        self.assertRedirects(r, self.url("docs"))

    def test_docs_tiles_status(self):
        today = timezone.localdate()
        for title, days in (("Старый", -1), ("Скоро", 20), ("Долгий", 200)):
            HorseDocument.objects.create(horse=self.h1, kind="other", title=title, file=SimpleUploadedFile("d.pdf", PDF), content_type="application/pdf",
                                         uploaded_by=self.coach, valid_until=today + datetime.timedelta(days=days))
        self.client.force_login(self.coach)
        page = self.client.get(self.url("docs")).content.decode()
        self.assertIn("Срок истёк", page)
        self.assertIn("Истекает", page)
        self.assertIn("Действует", page)
        # Тест 5.2, п. 54: ни во вкладке, ни в заголовке нет числа документов
        self.assertIn('aria-current="page">Документы</a>', page)
        self.assertIn('<h2 class="h3 ps-h2">Документы</h2>', page)
        self.assertNotIn("Документы 3", page)
        self.assertIn("Сохранить документ", page)

    def test_foreign_and_guest_get_info_only(self):
        HorseAccess.objects.create(horse=self.h1, user=self.other, granted_by=self.coach)
        Offer.objects.create(kind=Offer.Kind.HORSE_ACCESS, horse=self.h1, phone="+79161234545", created_by=self.coach)
        stranger = mkuser("+79990001792")
        for u in (stranger, self.other):
            self.client.force_login(u)
            for tab in ("access", "docs"):
                page = self.client.get(self.url(tab)).content.decode()
                self.assertNotIn("***-**-45", page)
                self.assertNotIn("Ожидает подтверждения", page)
                self.assertNotIn("Сохранить документ", page)
                self.assertNotIn('class="ps-seg"', page)
        self.client.logout()
        self.assertIn("login", self.client.get(self.url("access"))["Location"])

    def test_recipient_view_apply_and_lock(self):
        HorseAccess.objects.create(horse=self.h1, user=self.other, granted_by=self.coach)
        self.client.force_login(self.other)
        page = self.client.get(self.url()).content.decode()
        self.assertIn("Управляет Соколова Мария", page)
        self.assertIn("Документы видят создатель, организатор и судьи турнира", page)
        self.assertIn("Отказаться от доступа", page)
        self.assertTrue(self.t.public_entries_open())
        self.assertIn("Подать заявку с лошадью Барон", page)
        self.assertIn(reverse("entries:apply", args=[self.t.pk]) + f"?horse={self.h1.pk}", page)
        self.assertNotIn("Владелец", page)

    def test_recipient_no_apply_when_no_open_tournaments(self):
        from apps.tournaments.models import Tournament

        Tournament.objects.update(pub_deadline=timezone.localdate() - datetime.timedelta(days=5))
        HorseAccess.objects.create(horse=self.h1, user=self.other, granted_by=self.coach)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(self.url()), "Подать заявку с")


@override_settings(MEDIA_ROOT=TMP)
class HorseFormScreenTests(Base):
    def data(self, **kw):
        d = {"name": "Гром", "birth_year": 2015, "color": "grey", "sex": "stallion", "breed": "орловская"}
        d.update(kw)
        return d

    def test_steps_markup(self):
        self.client.force_login(self.coach)
        page = self.client.get(reverse("profiles:horse_new")).content.decode()
        self.assertIn("data-steps", page)
        self.assertIn("Дополнительно", page)
        self.assertIn("Пропустить", page)
        self.assertIn('data-extra="birthplace" data-off', page)
        # редактирование — одна страница без шагов
        page = self.client.get(reverse("profiles:horse_edit", args=[self.h1.pk])).content.decode()
        self.assertNotIn("data-steps", page)
        self.assertNotIn("Пропустить", page)

    def test_pony_switch_and_extras(self):
        self.client.force_login(self.coach)
        self.client.post(reverse("profiles:horse_new"), self.data(is_pony="on", height_cm=130, sire="Гранит", owner_name="Иванов"))
        h = Horse.objects.get(name="Гром")
        self.assertTrue(h.is_pony)
        self.assertEqual((h.sire, h.owner_name, h.height_cm), ("Гранит", "Иванов", 130))
        self.client.post(reverse("profiles:horse_edit", args=[h.pk]), self.data(is_pony="false", name="Гром"))
        h.refresh_from_db()
        self.assertFalse(h.is_pony)

    def test_next_returns_to_entry(self):
        self.client.force_login(self.coach)
        nxt = reverse("entries:apply", args=[self.t.pk])
        r = self.client.post(reverse("profiles:horse_new"), {**self.data(), "next": nxt})
        h = Horse.objects.get(name="Гром")
        self.assertTrue(r["Location"].startswith(nxt + f"?horse={h.pk}"))

    def test_guest_and_foreign(self):
        self.assertIn("login", self.client.get(reverse("profiles:horse_new"))["Location"])
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("profiles:horse_edit", args=[self.h1.pk]), self.data()).status_code, 403)


class FksrSixDigitsTests(TestCase):
    """Решение 26.10.2026: номер ФКСР — ровно 6 цифр."""

    def test_validation(self):
        from apps.profiles.forms import AthleteForm
        from apps.profiles.models import fksr_valid

        self.assertTrue(fksr_valid(""))
        self.assertTrue(fksr_valid("000 700"))
        self.assertFalse(fksr_valid("12345"))
        self.assertFalse(fksr_valid("12345A"))
        f = AthleteForm({"last_name": "Ким", "first_name": "Алина", "birth_date": "2012-03-04", "rank": "none", "fksr_number": "123"})
        self.assertIn("fksr_number", f.errors)
