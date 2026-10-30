"""Кабинет модератора и администратора (ТЗ 13.3): права, пользователи, дубли, все турниры, журнал, настройки, система."""

import datetime

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog, Notification, OutgoingMail, SiteSettings
from apps.entries import services as eservices
from apps.entries.models import Entry
from apps.entries.tests.test_entries import Base, mkuser
from apps.profiles.models import Athlete, AthleteDelegate, Horse
from apps.support.models import Ticket

MOD_PAGES = ["moderation:panel", "moderation:users", "moderation:duplicates", "moderation:tournaments"]
ADMIN_PAGES = ["moderation:journal", "moderation:settings", "moderation:system", "moderation:moderators"]


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CabinetTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = mkuser("+79990001090", is_moderator=True, is_superuser=True, is_staff=True, last_name="Главный", email="admin@example.test")
        self.a1 = Athlete.objects.create(last_name="Смирнова", first_name="Лиза", birth_date=datetime.date(self.start.year - 12, 5, 1),
                                         manager=self.coach, consent_confirmed_at=timezone.now(), rank="2")
        self.h1 = Horse.objects.create(name="Барон", birth_year=2014, color="bay", sex="gelding", breed="ганноверская", manager=self.coach)

    # ---------------- доступ ----------------
    def test_guest_and_user_have_no_access(self):
        for name in MOD_PAGES + ADMIN_PAGES:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)  # гостя — на вход
        self.client.force_login(self.coach)
        for name in MOD_PAGES + ADMIN_PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.get(reverse("moderation:user", args=[self.coach.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("moderation:export", args=["tournaments"])).status_code, 403)

    def test_moderator_sees_his_sections_but_not_admin_ones(self):
        self.client.force_login(self.mod)
        for name in MOD_PAGES:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
            self.assertContains(r, "mcab__nav")
            self.assertNotContains(r, reverse("moderation:journal"))
        for name in ADMIN_PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.get(reverse("moderation:export", args=["users"])).status_code, 403)
        self.assertEqual(self.client.get(reverse("moderation:export", args=["tickets"])).status_code, 403)

    def test_admin_sees_everything(self):
        self.client.force_login(self.admin)
        for name in MOD_PAGES + ADMIN_PAGES:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)
        r = self.client.get(reverse("moderation:panel"))
        self.assertContains(r, "Администратор")
        for kind in ("tournaments", "users", "tickets"):
            r = self.client.get(reverse("moderation:export", args=[kind]))
            self.assertEqual(r.status_code, 200, kind)
            self.assertTrue(r.content.startswith(b"PK"))

    # ---------------- пользователи ----------------
    def test_phone_only_for_admin(self):
        self.client.force_login(self.mod)
        r = self.client.get(reverse("moderation:users"), {"q": "1003"})
        self.assertNotContains(r, "Соколова")  # модератор не ищет по телефону
        r = self.client.get(reverse("moderation:user", args=[self.coach.pk]))
        self.assertContains(r, "Соколова")
        self.assertNotContains(r, self.coach.phone_display)
        self.client.force_login(self.admin)
        r = self.client.get(reverse("moderation:users"), {"q": "0001003"})
        self.assertContains(r, "Соколова")
        self.assertContains(self.client.get(reverse("moderation:user", args=[self.coach.pk])), self.coach.phone_display)

    def test_block_needs_reason_closes_login_and_unblock(self):
        self.client.force_login(self.coach)
        coach_client = self.client_class()
        coach_client.force_login(self.coach)
        self.client.force_login(self.mod)
        url = reverse("moderation:user", args=[self.coach.pk])
        self.client.post(url, {"action": "block", "reason": "  "})
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.is_active)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, {"action": "block", "reason": "Фальшивые заявки"})
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.is_blocked)
        self.assertEqual(self.coach.blocked_reason, "Фальшивые заявки")
        self.assertEqual(self.coach.blocked_by, self.mod)
        # прежний вход завершён
        self.assertEqual(coach_client.get(reverse("accounts:cabinet")).status_code, 302)
        # новый вход — сообщение о блокировке
        anon = self.client_class()
        r = anon.post(reverse("accounts:login"), {"phone": self.coach.phone, "password": "Konkur-2026!"})
        self.assertContains(r, "Аккаунт заблокирован")
        self.assertTrue(AuditLog.objects.filter(action="user.block", target_id=self.coach.pk).exists())
        self.client.post(url, {"action": "unblock"})
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.is_active)
        self.assertFalse(self.coach.is_blocked)
        self.assertTrue(anon.login(phone=self.coach.phone, password="Konkur-2026!"))

    def test_cannot_block_moderator_or_self(self):
        self.client.force_login(self.mod)
        for target in (self.admin, self.mod):
            self.client.post(reverse("moderation:user", args=[target.pk]), {"action": "block", "reason": "x"})
            target.refresh_from_db()
            self.assertTrue(target.is_active)

    def test_reset_password_admin_only_and_forces_change(self):
        url = reverse("moderation:user", args=[self.coach.pk])
        self.client.force_login(self.mod)
        self.client.post(url, {"action": "reset_password"})
        self.coach.refresh_from_db()
        self.assertFalse(self.coach.must_change_password)
        self.client.force_login(self.admin)
        r = self.client.post(url, {"action": "reset_password"})
        pwd = r.context["temp_password"]
        self.assertTrue(pwd)
        self.assertContains(r, pwd)
        self.coach.refresh_from_db()
        self.assertTrue(self.coach.must_change_password)
        c = self.client_class()
        self.assertTrue(c.login(phone=self.coach.phone, password=pwd))
        self.assertRedirects(c.get(reverse("accounts:cabinet")), reverse("accounts:password"))
        c.post(reverse("accounts:password"), {"old_password": pwd, "new_password1": "Novyi-Parol-77", "new_password2": "Novyi-Parol-77"})
        self.coach.refresh_from_db()
        self.assertFalse(self.coach.must_change_password)
        self.assertEqual(c.get(reverse("accounts:cabinet")).status_code, 200)

    def test_delete_account_admin_only(self):
        url = reverse("moderation:user", args=[self.other.pk])
        self.client.force_login(self.mod)
        self.client.post(url, {"action": "delete", "confirm": "1"})
        self.other.refresh_from_db()
        self.assertIsNone(self.other.deleted_at)
        self.client.force_login(self.admin)
        self.client.post(url, {"action": "delete"})  # без подтверждения
        self.other.refresh_from_db()
        self.assertIsNone(self.other.deleted_at)
        self.client.post(url, {"action": "delete", "confirm": "1"})
        self.other.refresh_from_db()
        self.assertIsNotNone(self.other.deleted_at)
        self.assertFalse(self.other.is_active)
        r = self.client_class().post(reverse("accounts:login"), {"phone": self.other.phone, "password": "Konkur-2026!"})
        self.assertNotContains(r, "Аккаунт заблокирован")

    # ---------------- дубли ----------------
    def test_merge_athletes_moves_entries_and_delegates(self):
        dup = Athlete.objects.create(last_name="смирнова", first_name="Лиза", birth_date=self.a1.birth_date,
                                     manager=self.other, consent_confirmed_at=timezone.now(), fksr_number="123456")
        AthleteDelegate.objects.create(athlete=dup, user=self.judge, granted_by=self.other)
        AthleteDelegate.objects.create(athlete=dup, user=self.coach, granted_by=self.other)
        AthleteDelegate.objects.create(athlete=self.a1, user=self.judge, granted_by=self.coach)
        app = eservices.submit(self.other, self.t.pk, [eservices.Line(dup, self.h1, self.c_open)], "dup-1")
        e = app.entries.get()
        ticket = Ticket.objects.create(author=self.other, topic="duplicate", text="дубль", athlete=dup)
        self.client.force_login(self.mod)
        r = self.client.get(reverse("moderation:duplicates"))
        self.assertContains(r, f'value="{dup.pk}"')
        self.client.post(reverse("moderation:duplicates"), {"kind": "athlete", "main": self.a1.pk, "dup": [dup.pk]})  # без подтверждения
        self.assertFalse(Athlete.objects.get(pk=dup.pk).archived)
        self.client.post(reverse("moderation:duplicates"), {"kind": "athlete", "main": self.a1.pk, "dup": [dup.pk], "confirm": "1"})
        dup.refresh_from_db()
        self.a1.refresh_from_db()
        e.refresh_from_db()
        ticket.refresh_from_db()
        self.assertTrue(dup.archived)
        self.assertEqual(dup.merged_into, self.a1)
        self.assertEqual(e.athlete, self.a1)
        self.assertEqual(ticket.athlete, self.a1)
        self.assertEqual(self.a1.fksr_number, "123456")
        self.assertEqual(set(AthleteDelegate.objects.filter(athlete=self.a1).values_list("user_id", flat=True)), {self.judge.pk, self.coach.pk})
        self.assertFalse(AthleteDelegate.objects.filter(athlete=dup).exists())
        self.assertTrue(Notification.objects.filter(user=self.other, kind="profile.merge").exists())
        self.assertTrue(AuditLog.objects.filter(action="athlete.merge", target_id=self.a1.pk).exists())
        self.assertNotContains(self.client.get(reverse("moderation:duplicates")), f'value="{dup.pk}"')

    def test_merge_horses(self):
        dup = Horse.objects.create(name="БАРОН", birth_year=2014, color="bay", sex="gelding", breed="x", manager=self.other)
        self.client.force_login(self.mod)
        self.client.post(reverse("moderation:duplicates"), {"kind": "horse", "main": self.h1.pk, "dup": [dup.pk], "confirm": "1"})
        dup.refresh_from_db()
        self.assertTrue(dup.archived)
        self.assertEqual(dup.merged_into, self.h1)

    def test_user_cannot_merge(self):
        dup = Horse.objects.create(name="Барон", birth_year=2014, color="bay", sex="gelding", breed="x", manager=self.other)
        self.client.force_login(self.coach)
        r = self.client.post(reverse("moderation:duplicates"), {"kind": "horse", "main": self.h1.pk, "dup": [dup.pk], "confirm": "1"})
        self.assertEqual(r.status_code, 403)
        dup.refresh_from_db()
        self.assertFalse(dup.archived)

    # ---------------- все турниры и полная правка администратором ----------------
    def test_admin_has_full_rights_in_foreign_tournament(self):
        hub = reverse("tournaments:hub", args=[self.t.pk])
        self.client.force_login(self.mod)
        self.assertEqual(self.client.get(hub).status_code, 403)
        self.client.force_login(self.admin)
        r = self.client.get(hub)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Вы работаете как администратор")
        app = eservices.submit(self.coach, self.t.pk, [eservices.Line(self.a1, self.h1, self.c_open)], "adm-1")
        e = app.entries.get()
        self.client.post(reverse("entries:decide", args=[self.t.pk]), {"entry": [e.pk], f"rev-{e.pk}": e.revision, "action": "accept", "confirm": "1"})
        e.refresh_from_db()
        self.assertEqual(e.decision, Entry.Decision.ACCEPTED)
        self.assertTrue(AuditLog.objects.filter(actor=self.admin, data__as_admin=True).exists())
        # в своём турнире плашки нет
        self.client.force_login(self.org)
        self.assertNotContains(self.client.get(hub), "Вы работаете как администратор")

    def test_tournaments_list_filters(self):
        self.client.force_login(self.mod)
        r = self.client.get(reverse("moderation:tournaments"), {"q": "Кубок"})
        self.assertContains(r, "Кубок")
        self.assertNotContains(r, "Открыть как администратор")
        r = self.client.get(reverse("moderation:tournaments"), {"pub": "cancelled"})
        self.assertNotContains(r, "row-card__title")

    # ---------------- журнал, настройки, система ----------------
    def test_journal_filters(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("moderation:journal"), {"target": "tournament", "id": self.t.pk})
        self.assertContains(r, "Опубликован модератором")

    def test_settings_saved_and_used(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("moderation:settings"), {"moderator_emails": "a@example.test, плохой", "support_telegram": "", "footer_phone": "", "footer_email": ""})
        self.assertContains(r, "не похоже на email")
        self.client.post(reverse("moderation:settings"), {"moderator_emails": "a@example.test; b@example.test",
                                                          "support_telegram": "https://t.me/startlist_help", "footer_phone": "+7 900 111-22-33",
                                                          "footer_email": "help@example.test"})
        s = SiteSettings.get()
        self.assertEqual(s.moderator_email_list, ["a@example.test", "b@example.test"])
        self.assertEqual(s.support_telegram, "@startlist_help")
        r = self.client.get(reverse("support:faq"))
        self.assertContains(r, "t.me/startlist_help")
        self.assertContains(r, "+7 900 111-22-33")
        self.assertTrue(AuditLog.objects.filter(action="settings.save").exists())

    def test_system_test_mail(self):
        self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("moderation:system"), {"action": "test_mail"})
        self.assertEqual(mail.outbox[-1].to, ["admin@example.test"])
        self.assertEqual(OutgoingMail.objects.get(kind="system.test").state, OutgoingMail.State.SENT)
        self.assertContains(self.client.get(reverse("moderation:system")), "Проверка почты СтартЛист")

    def test_moderator_appointment_still_owner_only(self):
        self.client.force_login(self.mod)
        r = self.client.post(reverse("moderation:moderators"), {"action": "add", "phone": self.coach.phone})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(User.objects.get(pk=self.coach.pk).is_moderator)
