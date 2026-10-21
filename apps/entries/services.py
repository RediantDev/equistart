"""Правила заявок: подача, решения, изменения, отмена.

Каждая позиция заявки независима: отказ по одной не меняет остальные.
"""

import hashlib
import json
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import audit, notify
from apps.profiles.models import RANK_SHORT, Athlete, Horse, rank_at_least
from apps.profiles.services import athletes_for_entry
from apps.tournaments.models import TeamMember, Tournament, TournamentClass
from apps.tournaments.services import roles_of

from .models import Application, ChangeProposal, Entry

JUDGE_ROLES = {TeamMember.Role.ORGANIZER, TeamMember.Role.JUDGE}


class Conflict(Exception):
    pass


def can_judge(user, tournament) -> bool:
    return bool(roles_of(user, tournament) & JUDGE_ROLES)


def can_view_application(user, app) -> bool:
    if not user.is_authenticated:
        return False
    return app.submitted_by_id == user.pk or bool(roles_of(user, app.tournament))


def require_judge(user, tournament):
    if not can_judge(user, tournament):
        raise PermissionDenied


# ---------------- проверка строки заявки ----------------

@dataclass
class Line:
    athlete: Athlete
    horse: Horse
    tclass: TournamentClass
    height: int | None = None  # для маршрута с несколькими высотами
    confirmed: bool = False  # участник подтвердил условие «не выполнявшие норматив …»

    def key(self):
        return (self.athlete.pk, self.horse.pk, self.tclass.pk, self.height)


def athlete_age(athlete, tournament):
    if not athlete.birth_date or not tournament.pub_starts_on:
        return None
    # Возраст в конном спорте определяется по году рождения
    return tournament.pub_starts_on.year - athlete.birth_date.year


def class_reasons(athlete, horse, c: TournamentClass, year: int, height: int | None = None, confirmed: bool = True) -> list[str]:
    """Почему пара не подходит в зачёт (APP-02). Пустой список — подходит."""
    out = []
    if c.merged_into_id:
        out.append(f"зачёт объединён с «{c.merged_into.name}»")
    p = c.program
    if p.heights and len(p.heights) > 1:
        if height is None:
            out.append("выберите высоту")
        elif height not in p.heights:
            out.append(f"в маршруте нет высоты {height} см")
        elif c.height and c.height != height:
            out.append(f"зачёт для высоты {c.height} см")
    if c.birth_year_from or c.birth_year_to:
        if not athlete.birth_date:
            out.append("нужна дата рождения всадника")
        else:
            y = athlete.birth_date.year
            if (c.birth_year_from and y < c.birth_year_from) or (c.birth_year_to and y > c.birth_year_to):
                out.append(f"нужен {c.conditions.split(',')[0]}")
    if c.rank and c.rank_rule:
        r = athlete.rank_for(p.discipline)
        if c.rank_rule == TournamentClass.RankRule.MIN and not rank_at_least(r, c.rank):
            out.append(f"нужен разряд не ниже {RANK_SHORT[c.rank]}" + ("" if r else " (в профиле разряд не указан)"))
        if c.rank_rule == TournamentClass.RankRule.NEVER:
            if r and rank_at_least(r, c.rank):
                out.append(f"у всадника разряд {RANK_SHORT[r]} — зачёт для не выполнявших норматив {RANK_SHORT[c.rank]}")
            elif not confirmed:
                out.append(f"подтвердите, что всадник не выполнял норматив {RANK_SHORT[c.rank]} и выше")
    age = year - horse.birth_year
    if c.horse_age_min and age < c.horse_age_min:
        out.append(f"лошади {age} лет, нужно от {c.horse_age_min}")
    if c.horse_age_max and age > c.horse_age_max:
        out.append(f"лошади {age} лет, нужно до {c.horse_age_max}")
    if c.horse_kind == TournamentClass.HorseKind.PONY and not horse.is_pony:
        out.append("зачёт для пони")
    if c.horse_kind == TournamentClass.HorseKind.HORSE and horse.is_pony:
        out.append("зачёт для лошадей, не пони")
    if c.height_min or c.height_max:
        if not horse.height_cm:
            out.append("укажите рост лошади в холке в её профиле")
        elif (c.height_min and horse.height_cm < c.height_min) or (c.height_max and horse.height_cm > c.height_max):
            out.append(f"рост в холке {horse.height_cm} см не подходит")
    return out


def check_line(user, tournament, line: Line, late=False, allowed_athletes=None) -> list[str]:
    errors = []
    c = line.tclass
    if c.program.tournament_id != tournament.pk:
        return ["Зачёт не относится к этому турниру."]
    allowed = allowed_athletes if allowed_athletes is not None else set(athletes_for_entry(user).values_list("pk", flat=True))
    a = line.athlete
    if a.pk not in allowed:
        errors.append(f"Нет права подавать заявку за спортсмена «{a.public_name}».")
    if not a.consent_confirmed_at:
        errors.append(f"«{a.public_name}»: не подтверждено согласие на обработку и публикацию.")
    year = (tournament.pub_starts_on or tournament.starts_on or timezone.localdate()).year
    for r in class_reasons(a, line.horse, c, year, line.height, line.confirmed):
        errors.append(f"«{a.public_name} / {line.horse.name}» — зачёт «{c.label}»: {r}.")
    if Entry.objects.filter(tclass=c, athlete=line.athlete, horse=line.horse, height=line.height, decision__in=Entry.ACTIVE).exists():
        errors.append(f"«{line.athlete.public_name}» на «{line.horse.name}» уже заявлен(а) в этот зачёт.")
    return errors


def tournament_open_for(user, tournament, late) -> str | None:
    if tournament.publication != Tournament.Publication.PUBLISHED:
        return "Турнир не принимает заявки."
    if late:
        if not can_judge(user, tournament):
            return "Добавлять участников после срока может только судья или организатор турнира."
        return None
    if not tournament.public_entries_open():
        return "Приём заявок закрыт."
    return None


def content_hash(lines) -> str:
    raw = json.dumps(sorted([list(map(str, l.key())) for l in lines]), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


@transaction.atomic
def submit(user, tournament_id, lines: list[Line], key: str, contact_name="", contact_phone="", comment="", late=False) -> Application:
    t = Tournament.objects.select_for_update(of=("self",)).get(pk=tournament_id)
    h = content_hash(lines)
    existing = Application.objects.filter(idempotency_key=key).first()
    if existing:
        if existing.submitted_by_id == user.pk and existing.content_hash == h:
            return existing
        raise Conflict("Эта заявка уже отправлена с другим составом.")
    reason = tournament_open_for(user, t, late)
    if reason:
        raise ValueError([reason])
    if not lines:
        raise ValueError(["Добавьте хотя бы одно выступление."])
    allowed = set(athletes_for_entry(user).values_list("pk", flat=True))
    errors, seen = [], set()
    for l in lines:
        if l.key() in seen:
            errors.append(f"Выступление «{l.athlete.public_name} / {l.horse.name}» добавлено дважды.")
        seen.add(l.key())
        errors += check_line(user, t, l, late=late, allowed_athletes=allowed)
    if errors:
        raise ValueError(errors)
    app = Application.objects.create(
        tournament=t, submitted_by=user, contact_name=contact_name or user.full_name, contact_phone=contact_phone or user.phone,
        comment=comment, late=late, idempotency_key=key, content_hash=h,
    )
    for l in lines:
        e = Entry.objects.create(
            application=app, tournament=t, program=l.tclass.program, tclass=l.tclass, athlete=l.athlete, horse=l.horse,
            athlete_snapshot=l.athlete.private_snapshot(l.tclass.program.discipline), horse_snapshot=l.horse.public_snapshot(),
            height=l.height, conditions_confirmed=l.confirmed,
            program_name=l.tclass.program.name, class_name=l.tclass.name, day=l.tclass.program.day,
            decision=Entry.Decision.ACCEPTED if late else Entry.Decision.PENDING,
            decided_by=user if late else None, decided_at=timezone.now() if late else None,
        )
    audit(user, "application.submit", app, tournament=t.pk, entries=len(lines), late=late)
    judges = _judges(t)
    notify(judges, f"Новая заявка №{app.pk} на «{t.pub_title}»: выступлений {len(lines)}", reverse("entries:manage", args=[t.pk]), "application")
    return app


def _judges(t):
    from apps.accounts.models import User

    ids = set(TeamMember.objects.filter(tournament=t, state=TeamMember.State.ACCEPTED, role__in=JUDGE_ROLES).values_list("user_id", flat=True))
    ids.add(t.owner_id)
    return list(User.objects.filter(pk__in=ids))


def _recipients(entry):
    """Кому сообщать о решении: подавшему заявку и связанному спортсмену."""
    users = [entry.application.submitted_by]
    if entry.athlete.linked_user_id:
        users.append(entry.athlete.linked_user)
    return users


# ---------------- решения судьи ----------------

ACTIONS = {
    "accept": "Принять",
    "reject": "Отклонить",
    "cancel": "Отменить (судья)",
}


@dataclass
class RowResult:
    entry_id: int
    ok: bool
    text: str


def decide(user, tournament_id, items: list[tuple[int, str]], action: str, reason: str = "") -> list[RowResult]:
    """Решение по выбранным позициям. items — пары (id, revision). Каждая строка — своя транзакция."""
    t = Tournament.objects.get(pk=tournament_id)
    require_judge(user, t)
    if action not in ACTIONS:
        raise ValueError("Неизвестное действие")
    if action in ("reject", "cancel") and not reason.strip():
        raise ValueError("Укажите причину — её увидит подавший заявку.")
    results = []
    for entry_id, rev in items:
        try:
            with transaction.atomic():
                e = Entry.objects.select_for_update(of=("self",)).select_related("application", "athlete").get(pk=entry_id, tournament=t)
                if str(e.revision) != str(rev):
                    raise Conflict("данные изменились, обновите страницу")
                text = _apply_action(user, e, action, reason)
                e.revision += 1
                e.save()
                audit(user, f"entry.{action}", e, reason=reason or "", decision=e.decision)
                notify(_recipients(e), f"«{t.pub_title}», {e.program_name}: {e.athlete_snapshot.get('name')} / {e.horse_snapshot.get('name')} — {text.lower()}",
                       reverse("entries:application", args=[e.application_id]), "decision")
            results.append(RowResult(entry_id, True, text))
        except (Conflict, ValueError) as ex:
            results.append(RowResult(entry_id, False, str(ex)))
        except Entry.DoesNotExist:
            results.append(RowResult(entry_id, False, "позиция не найдена в этом турнире"))
    return results


def _apply_action(user, e, action, reason):
    now = timezone.now()
    if e.decision == Entry.Decision.CANCELLED:
        raise ValueError("позиция отменена — обычным решением её не восстановить")
    if e.is_started and action in ("reject", "cancel"):
        raise ValueError("выступление уже начато")
    if action == "accept":
        if e.decision == Entry.Decision.ACCEPTED:
            raise ValueError("уже принято")
        e.decision, e.decided_by, e.decided_at, e.decision_reason = Entry.Decision.ACCEPTED, user, now, ""
        return "Принято"
    if action == "reject":
        e.decision, e.decided_by, e.decided_at, e.decision_reason = Entry.Decision.REJECTED, user, now, reason.strip()
        return "Отклонено"
    if action == "cancel":
        e.decision, e.cancelled_by, e.cancelled_at, e.cancel_reason = Entry.Decision.CANCELLED, user, now, reason.strip()
        return "Отменено судьёй"
    raise ValueError("Неизвестное действие")


# ---------------- отмена представителем ----------------

def cancel_by_representative(user, application_id, items: list[tuple[int, str]]) -> list[RowResult]:
    app = Application.objects.select_related("tournament").get(pk=application_id)
    if app.submitted_by_id != user.pk:
        raise PermissionDenied
    results = []
    for entry_id, rev in items:
        try:
            with transaction.atomic():
                e = Entry.objects.select_for_update(of=("self",)).get(pk=entry_id, application=app)
                if str(e.revision) != str(rev):
                    raise Conflict("данные изменились, обновите страницу")
                if not e.is_active:
                    raise ValueError("уже не действует")
                if e.is_started:
                    raise ValueError("выступление уже начато")
                e.decision, e.cancelled_by, e.cancelled_at = Entry.Decision.CANCELLED, user, timezone.now()
                e.revision += 1
                e.save()
                ChangeProposal.objects.filter(entry=e, state=ChangeProposal.State.PENDING).update(state=ChangeProposal.State.WITHDRAWN)
                audit(user, "entry.cancel_by_representative", e)
            results.append(RowResult(entry_id, True, "Отменено"))
        except (Conflict, ValueError) as ex:
            results.append(RowResult(entry_id, False, str(ex)))
        except Entry.DoesNotExist:
            results.append(RowResult(entry_id, False, "позиция не найдена в этой заявке"))
    if any(r.ok for r in results):
        notify(_judges(app.tournament), f"Заявка №{app.pk}: представитель отменил выступлений — {sum(r.ok for r in results)}", reverse("entries:manage", args=[app.tournament_id]), "cancel")
    return results


# ---------------- предложения изменений ----------------

@transaction.atomic
def propose(user, entry_id, athlete=None, horse=None, tclass=None) -> ChangeProposal:
    e = Entry.objects.select_for_update(of=("self",)).select_related("application", "tournament").get(pk=entry_id)
    if e.application.submitted_by_id != user.pk:
        raise PermissionDenied
    if not e.is_active or e.is_started:
        raise ValueError("Это выступление уже нельзя изменить.")
    if athlete and athlete.pk == e.athlete_id:
        athlete = None
    if horse and horse.pk == e.horse_id:
        horse = None
    if tclass and tclass.pk == e.tclass_id:
        tclass = None
    if not (athlete or horse or tclass):
        raise ValueError("Ничего не изменено — выберите другого спортсмена, лошадь или зачёт.")
    if ChangeProposal.objects.filter(entry=e, state=ChangeProposal.State.PENDING).exists():
        raise ValueError("По этому выступлению уже есть предложение на рассмотрении.")
    height = e.height if (tclass or e.tclass).program_id == e.program_id else None
    line = Line(athlete or e.athlete, horse or e.horse, tclass or e.tclass, height, confirmed=e.conditions_confirmed)
    errors = check_line(user, e.tournament, line)
    if errors:
        raise ValueError(errors[0])
    p = ChangeProposal.objects.create(entry=e, athlete=athlete, horse=horse, tclass=tclass, created_by=user)
    audit(user, "proposal.create", e, proposal=p.pk, text=p.describe())
    notify(_judges(e.tournament), f"Предложено изменение в заявке №{e.application_id}: {p.describe()}", reverse("entries:application", args=[e.application_id]), "proposal")
    return p


@transaction.atomic
def decide_proposal(user, proposal_id, accept: bool, reason: str = "") -> ChangeProposal:
    p = ChangeProposal.objects.select_for_update().select_related("entry").get(pk=proposal_id)
    e = Entry.objects.select_for_update(of=("self",)).select_related("tournament", "application").get(pk=p.entry_id)
    require_judge(user, e.tournament)
    if p.state != ChangeProposal.State.PENDING:
        raise Conflict("Решение по предложению уже принято.")
    now = timezone.now()
    if accept:
        if not e.is_active or e.is_started:
            raise ValueError("Выступление уже нельзя изменить.")
        new_class = p.tclass or e.tclass
        height = e.height if new_class.program_id == e.program_id else None
        line = Line(p.athlete or e.athlete, p.horse or e.horse, new_class, height, confirmed=e.conditions_confirmed)
        allowed = set(athletes_for_entry(e.application.submitted_by).values_list("pk", flat=True))
        errors = check_line(e.application.submitted_by, e.tournament, line, allowed_athletes=allowed)
        if errors:
            raise ValueError(errors[0])
        if p.athlete_id:
            e.athlete, e.athlete_snapshot = p.athlete, p.athlete.private_snapshot(new_class.program.discipline)
        if p.horse_id:
            e.horse, e.horse_snapshot = p.horse, p.horse.public_snapshot()
        if p.tclass_id:
            e.tclass, e.program = new_class, new_class.program
            e.program_name, e.class_name, e.day = new_class.program.name, new_class.name, new_class.program.day
        e.revision += 1
        e.save()
        p.state = ChangeProposal.State.ACCEPTED
    else:
        p.state = ChangeProposal.State.REJECTED
        p.reason = reason.strip()
    p.decided_by, p.decided_at = user, now
    p.save()
    audit(user, "proposal.accept" if accept else "proposal.reject", e, proposal=p.pk, text=p.describe(), reason=p.reason)
    notify(_recipients(e), f"Изменение в заявке №{e.application_id} {'принято' if accept else 'отклонено'}: {p.describe()}", reverse("entries:application", args=[e.application_id]), "proposal")
    return p


def summarize(entries) -> list[tuple[str, int, str]]:
    """Сводка состояний без смешивания в один ложный статус."""
    counts = {}
    for e in entries:
        counts[e.decision] = counts.get(e.decision, 0) + 1
    colors = {"pending": "warning", "accepted": "success", "rejected": "danger", "cancelled": ""}
    return [(Entry.Decision(k).label, v, colors[k]) for k, v in counts.items()]
