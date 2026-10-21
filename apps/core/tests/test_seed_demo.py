import shutil
import tempfile
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.entries.models import Entry
from apps.tournaments.models import Tournament

TMP = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=TMP)
class SeedDemoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def test_seed_is_complete_and_idempotent(self):
        call_command("seed_demo", password="Demo-Pass-2026", stdout=StringIO())
        call_command("seed_demo", password="Demo-Pass-2026", stdout=StringIO())
        self.assertEqual(Tournament.objects.count(), 1)
        t = Tournament.objects.get()
        self.assertEqual(t.publication, "published")
        self.assertEqual(Entry.objects.filter(tournament=t).count(), 8)
        self.assertTrue(t.programs.get(discipline="dressage").test_snapshot)
        self.assertTrue(User.objects.get(phone="+79990000005").check_password("Demo-Pass-2026"))
        self.assertEqual(self.client.get(f"/t/{t.pk}/?tab=starts").status_code, 200)
