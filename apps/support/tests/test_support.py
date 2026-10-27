import datetime
import shutil
import tempfile

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog, Notification
from apps.support.models import Ticket
from apps.tournaments.models import TeamMember, Tournament

TMP_MEDIA = tempfile.mkdtemp()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40


def user(phone, **extra):
    data = {"last_name": "Тестов", "first_name": "Иван"}
    data.update(extra)
    return User.objects.create_user(phone, "Konkur-2026!", **data)


@override_settings(MEDIA_ROOT=TMP_MEDIA, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   SUPPORT_MAX_PER_HOUR_GUEST=3, SUPPORT_MAX_PER_HOUR_USER=10)
class SupportTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.u = user("+79990000201", email="u@example.test")
        self.other = user("+79990000202")
        self.mod = user("+79990000203", is_moderator=True, email="mod@example.test")
        self.org = user("+79990000204", is_organizer=True)
        self.judge = user("+79990000205", is_judge=True)
        today = timezone.localdate()
        self.t = Tournament.objects.create(owner=self.org, title="Кубок", pub_title="Кубок", publication=Tournament.Publication.PUBLISHED,
                                           pub_starts_on=today, pub_ends_on=today, pub_timezone="UTC", starts_on=today, ends_on=today)
        TeamMember.objects.create(tournament=self.t, user=self.judge, phone=self.judge.phone, role=TeamMember.Role.JUDGE,
                                  state=TeamMember.State.ACCEPTED, invited_by=self.org)

    def guest_post(self, **kw):
        data = {"topic": "login", "text": "Не могу войти", "name": "Мария", "phone": "+7 900 000-11-22",
                "email": "g@example.test", "consent": "on"}
        data.update(kw)
        return self.client.post(reverse("support:new"), data)

    # ---- гость ----
    def test_guest_can_write_and_open_by_link(self):
        r = self.guest_post()
        t = Ticket.objects.get()
        self.assertIsNone(t.author)
        self.assertRedirects(r, reverse("support:guest", args=[t.token]))
        self.assertContains(self.client.get(reverse("support:guest", args=[t.token])), "Не могу войти")
        self.assertEqual(self.client.get(reverse("support:guest", args=["wrong-token"])).status_code, 404)
        self.assertTrue(AuditLog.objects.filter(action="support.create", target_id=t.pk).exists())

    def test_guest_needs_consent_and_contacts(self):
        r = self.guest_post(consent="", email="")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Ticket.objects.exists())

    def test_guest_rate_limit(self):
        for _ in range(3):
            self.guest_post()
        r = self.guest_post()
        self.assertContains(r, "Слишком много обращений")
        self.assertEqual(Ticket.objects.count(), 3)

    def test_text_is_escaped(self):
        self.guest_post(text="<script>alert(1)</script>")
        t = Ticket.objects.get()
        r = self.client.get(reverse("support:guest", args=[t.token]))
        self.assertNotContains(r, "<script>alert(1)</script>")
        self.assertContains(r, "&lt;script&gt;")

    # ---- пользователь ----
    def test_user_sees_only_own(self):
        self.client.force_login(self.u)
        self.client.post(reverse("support:new"), {"topic": "bug", "text": "Кнопка не работает", "page": "/t/1/"})
        t = Ticket.objects.get()
        self.assertEqual(t.author, self.u)
        self.assertContains(self.client.get(reverse("support:my")), "Кнопка не работает")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("support:detail", args=[t.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("support:my")), "Кнопка не работает")
        self.client.force_login(self.mod)
        self.assertRedirects(self.client.get(reverse("support:detail", args=[t.pk])), reverse("moderation:support_detail", args=[t.pk]))

    def test_foreign_page_is_not_stored(self):
        self.client.force_login(self.u)
        self.client.post(reverse("support:new"), {"topic": "bug", "text": "x", "page": "https://evil.example/x"})
        self.assertEqual(Ticket.objects.get().page, "")

    def test_staff_reply_notifies_and_mails_then_user_reopens(self):
        t = Ticket.objects.create(author=self.u, topic="other", text="Вопрос")
        self.client.force_login(self.mod)
        self.client.post(reverse("moderation:support_detail", args=[t.pk]), {"text": "Ответ поддержки", "status": "resolved"})
        t.refresh_from_db()
        self.assertEqual(t.status, Ticket.Status.RESOLVED)
        self.assertTrue(Notification.objects.filter(user=self.u, kind="support.answer").exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Ответ поддержки", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["u@example.test"])
        self.client.force_login(self.u)
        self.client.post(reverse("support:detail", args=[t.pk]), {"text": "Не помогло"})
        t.refresh_from_db()
        self.assertEqual(t.status, Ticket.Status.IN_WORK)
        self.assertEqual(t.messages.count(), 2)

    def test_queue_only_for_moderator(self):
        self.client.force_login(self.u)
        self.assertEqual(self.client.get(reverse("moderation:support")).status_code, 403)
        self.client.force_login(self.mod)
        self.assertEqual(self.client.get(reverse("moderation:support")).status_code, 200)
        self.assertEqual(self.client.get(reverse("moderation:panel")).status_code, 200)

    # ---- вложения ----
    def test_attachment_access(self):
        self.client.force_login(self.u)
        self.client.post(reverse("support:new"), {"topic": "bug", "text": "Скрин", "attachments": [SimpleUploadedFile("a.png", PNG, "image/png")]})
        a = Ticket.objects.get().attachments.get()
        url = reverse("support:file", args=[a.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.mod)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_attachment_must_be_image(self):
        self.client.force_login(self.u)
        r = self.client.post(reverse("support:new"), {"topic": "bug", "text": "x", "attachments": [SimpleUploadedFile("a.png", b"<html>", "image/png")]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Ticket.objects.exists())

    # ---- «Идёт турнир» ----
    def test_urgent_for_team_on_tournament_day(self):
        self.client.force_login(self.judge)
        self.client.post(reverse("support:new"), {"topic": "bug", "text": "Не сохраняется", "tournament": self.t.pk, "urgent": "on"})
        t = Ticket.objects.get()
        self.assertTrue(t.urgent)
        self.assertTrue(Notification.objects.filter(user=self.mod, kind="support.urgent").exists())
        self.assertTrue(any("mod@example.test" in m.to for m in mail.outbox))

    def test_urgent_not_for_outsider(self):
        self.client.force_login(self.u)
        self.client.post(reverse("support:new"), {"topic": "bug", "text": "x", "tournament": self.t.pk, "urgent": "on"})
        self.assertFalse(Ticket.objects.get().urgent)

    def test_urgent_not_on_other_days(self):
        self.t.pub_starts_on = self.t.pub_ends_on = timezone.localdate() + datetime.timedelta(days=5)
        self.t.save()
        self.client.force_login(self.judge)
        self.client.post(reverse("support:new"), {"topic": "bug", "text": "x", "tournament": self.t.pk, "urgent": "on"})
        self.assertFalse(Ticket.objects.get().urgent)

    # ---- ошибка в результатах (SUP-10) ----
    def test_results_need_tournament_and_transfer_to_judges(self):
        self.client.force_login(self.u)
        r = self.client.post(reverse("support:new"), {"topic": "results", "text": "Неверное место"})
        self.assertEqual(r.status_code, 200)
        self.client.post(reverse("support:new"), {"topic": "results", "text": "Неверное место", "tournament": self.t.pk})
        t = Ticket.objects.get()
        self.client.force_login(self.mod)
        self.client.post(reverse("moderation:support_detail", args=[t.pk]), {"action": "transfer"})
        t.refresh_from_db()
        self.assertIsNotNone(t.transferred_at)
        for who in (self.org, self.judge):
            self.assertTrue(Notification.objects.filter(user=who, kind="support.transfer").exists())
        self.assertFalse(Notification.objects.filter(user=self.u, kind="support.transfer").exists())

    # ---- страницы ----
    def test_faq_and_menu(self):
        self.assertEqual(self.client.get(reverse("support:faq")).status_code, 200)
        r = self.client.get("/")
        self.assertContains(r, "mnav")
        self.assertContains(r, "Поддержка")
        self.client.force_login(self.org)
        r = self.client.get("/")
        self.assertContains(r, "Мои соревнования")
        self.assertContains(r, "Кабинет")
