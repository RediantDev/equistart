"""Тестовые данные для стенда: аккаунты всех ролей, опубликованный турнир, заявки, денники, протокол.

    python manage.py seed_demo --password 'Пароль-для-всех'

Повторный запуск ничего не дублирует. Только для тестового стенда — не запускать на рабочем сайте с реальными людьми.
"""

import datetime

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.conduct import services as cs
from apps.entries import services as es
from apps.entries.models import Application
from apps.profiles.models import Athlete, Horse
from apps.stables import services as ss
from apps.stables.models import Service, StableCategory
from apps.tournaments import services as ts
from apps.tournaments.models import ProgramItem, TeamMember, Tournament, TournamentClass, Venue

PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

ACCOUNTS = [
    ("+79990000001", "Волков", "Андрей", {"is_organizer": True}, "Организатор турнира"),
    ("+79990000002", "Иванов", "Иван", {"is_moderator": True}, "Модератор"),
    ("+79990000003", "Соколова", "Мария", {"is_coach": True}, "Тренер: ведёт спортсменов и лошадей"),
    ("+79990000004", "Смирнова", "Лиза", {"is_athlete": True}, "Спортсмен, связан с профилем у тренера"),
    ("+79990000005", "Петрова", "Ольга", {}, "Судья турнира"),
    ("+79990000006", "Кузнецов", "Павел", {}, "Второй судья турнира"),
    ("+79990000007", "Морозова", "Наталья", {}, "Конюшня и услуги"),
]


class Command(BaseCommand):
    help = "Тестовые данные для стенда (аккаунты всех ролей, турнир, заявки, денники)."

    def add_arguments(self, parser):
        parser.add_argument("--password", required=True, help="пароль для всех тестовых аккаунтов")

    @transaction.atomic
    def handle(self, password, **kw):
        if len(password) < 8:
            raise CommandError("Пароль — не короче 8 символов.")
        if Tournament.objects.filter(title__startswith="Кубок «Белая роща»").exists():
            self.stdout.write("Тестовые данные уже есть — ничего не меняю.")
            return self.print_accounts()
        now = timezone.now()
        u = {}
        for phone, last, first, flags, _ in ACCOUNTS:
            user = User.objects.filter(phone=phone).first() or User.objects.create_user(phone, password, last_name=last, first_name=first)
            for k, v in flags.items():
                setattr(user, k, v)
            user.set_password(password)
            user.phone_verified_at = now
            user.save()
            u[phone[-1]] = user
        org, mod, coach, kid, judge, judge2, groom = (u[str(i)] for i in range(1, 8))

        start = timezone.localdate() + datetime.timedelta(days=21)
        while start.weekday() != 5:
            start += datetime.timedelta(days=1)
        end = start + datetime.timedelta(days=1)
        venue = Venue.objects.create(name="КСК «Белая роща»", region="mos", settlement="д. Берёзки", address="ул. Полевая, 14",
                                     timezone="Europe/Moscow", created_by=org, confirmed=True)
        t = Tournament.objects.create(owner=org, title="Кубок «Белая роща» по конкуру, 3-й этап", starts_on=start, ends_on=end, venue=venue,
                                      timezone="Europe/Moscow", organizer_name="КСК «Белая роща»", contact="+7 999 000-00-01",
                                      entry_deadline=start - datetime.timedelta(days=4),
                                      cancel_terms="Отмена выступления — до окончания приёма заявок.")
        t.regulation.save("polozhenie.pdf", ContentFile(PDF))
        progs = [
            (start, datetime.time(10, 0), "jumping", "Маршрут №1 · 60 см", "jumping_clear", ["penalties"], [("Дети до 14 лет", 0, 14), ("Любители", 18, None)]),
            (start, datetime.time(11, 30), "jumping", "Маршрут №2 · 80 см", "jumping_clear_time", ["penalties", "time"], [("Любители", 18, None), ("Открытый", None, None)]),
            (end, datetime.time(10, 0), "jumping", "Маршрут №3 · 100 см", "jumping_jumpoff", ["penalties", "time", "jumpoff_penalties", "jumpoff_time"], [("Открытый", None, None)]),
            (end, datetime.time(13, 0), "dressage", "Езда «Предварительный приз. Дети»", "dressage_test", ["percent"], [("Дети", 10, 14)]),
        ]
        classes = {}
        for i, (day, tm, disc, name, ptype, fields, cls) in enumerate(progs):
            p = ProgramItem.objects.create(tournament=t, day=day, start_time=tm, discipline=disc, name=name, program_type=ptype, result_fields=fields, position=i, slot_minutes=3)
            for j, (cname, amin, amax) in enumerate(cls):
                classes[(i, cname)] = TournamentClass.objects.create(program=p, name=cname, min_age=amin, max_age=amax, position=j)
        for user, role in ((judge, "judge"), (judge2, "judge"), (groom, "stable")):
            TeamMember.objects.create(tournament=t, phone=user.phone, user=user, role=role, state="accepted", invited_by=org)
        t.min_rest_minutes = 20
        t.save()
        ts.submit(org, t.pk, t.revision)
        t.refresh_from_db()
        ts.approve(mod, t.pk, t.revision)
        t.refresh_from_db()

        y = start.year
        people = [("Смирнова", "Лиза", y - 13, "2y", kid), ("Орлов", "Дмитрий", 1996, "2", None), ("Белова", "Анна", 2001, "3", None),
                  ("Петров", "Иван", 1990, "1", None), ("Ким", "Алина", y - 12, "none", None)]
        athletes = [Athlete.objects.create(last_name=l, first_name=f, birth_date=datetime.date(by, 3, 15), rank=r, club="КСК «Белая роща»",
                                           region="Московская область", manager=coach, linked_user=lu, consent_confirmed_at=now)
                    for l, f, by, r, lu in people]
        horses = [Horse.objects.create(name=n, birth_year=b, color=c, sex=s, breed=br, manager=coach)
                  for n, b, c, s, br in (("Барон", 2014, "bay", "gelding", "ганноверская"), ("Искра", 2016, "chestnut", "mare", "тракененская"),
                                         ("Гранит", 2012, "grey", "gelding", "орловская"), ("Вихрь", 2015, "black", "stallion", "русская верховая"),
                                         ("Ладога", 2013, "bay", "mare", "буденновская"))]
        lisa, dima, anna, ivan, alina = athletes
        baron, iskra, granit, vihr, ladoga = horses
        app = es.submit(coach, t.pk, [
            es.Line(lisa, baron, classes[(0, "Дети до 14 лет")]),
            es.Line(alina, iskra, classes[(0, "Дети до 14 лет")]),
            es.Line(dima, granit, classes[(1, "Любители")]),
            es.Line(anna, vihr, classes[(1, "Любители")]),
            es.Line(ivan, ladoga, classes[(1, "Открытый")]),
            es.Line(ivan, granit, classes[(2, "Открытый")]),
        ], "demo-app-1", contact_name="Соколова Мария", contact_phone=coach.phone)
        entries = list(app.entries.order_by("pk"))
        es.decide(judge, t.pk, [(e.pk, e.revision) for e in entries[:5]], "accept")
        # шестая остаётся «на рассмотрении» — чтобы было что решить судье

        cat = StableCategory.objects.create(tournament=t, name="Денник в конюшне", available_from=start - datetime.timedelta(days=1),
                                            available_to=end + datetime.timedelta(days=1), capacity=12, checkin_time=datetime.time(12, 0),
                                            checkout_time=datetime.time(18, 0), conditions="Первая подстилка (опилки) включена.")
        StableCategory.objects.create(tournament=t, name="Летний денник", available_from=start - datetime.timedelta(days=1),
                                      available_to=end + datetime.timedelta(days=1), capacity=20)
        sawdust = Service.objects.create(tournament=t, name="Опилки", unit="мешок", limit=200)
        Service.objects.create(tournament=t, name="Сено", unit="тюк")
        Service.objects.create(tournament=t, name="Электричество для коневоза", unit="сутки", recipient="trailer")
        b1 = ss.book(coach, cat.pk, baron, cat.available_from, cat.available_to, "demo-b1", required=True, contact_phone=coach.phone)
        ss.decide(groom, b1.pk, True)
        ss.assign_box(groom, b1.pk, "А-3")
        ss.book(coach, cat.pk, granit, cat.available_from, cat.available_to, "demo-b2")
        ss.order(coach, sawdust.pk, 4, "demo-o1", horse=baron)


        p1 = t.programs.get(position=0)
        p2 = t.programs.get(position=1)
        cs.draw(judge, p1.pk)
        cs.draw(judge, p2.pk)
        cs.publish_start_list(judge, p2.pk)
        self.stdout.write(self.style.SUCCESS("Тестовые данные созданы."))
        self.print_accounts()

    def print_accounts(self):
        self.stdout.write("\nТестовые аккаунты (пароль — тот, что указан при запуске):")
        for phone, last, first, _, role in ACCOUNTS:
            self.stdout.write(f"  {phone}  {last} {first} — {role}")
