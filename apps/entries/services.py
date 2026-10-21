"""Правила заявок: подача, решения, изменения, отмена.

Каждая позиция заявки независима: отказ по одной не меняет остальные.
"""

import hashlib
import json
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import audit, notify
from apps.profiles.models import Athlete, Horse
from apps.profiles.services import athletes_for_entry
from apps.tournaments.models import TeamMember, Tournament, TournamentClass
from apps.tournaments.services import roles_of

from .models import Application, ChangeProposal, Entry, EntryPartner

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
    partners: list = field(default_factory=list)  # [(Athlete, Horse)]

    def key(self):
        return (self.athlete.pk, self.horse.pk, self.tclass.pk, tuple((a.pk, h.pk) for a, h in self.partners))


def athlete_age(athlete, tournament):
    if not athlete.birth_date or not tournament.pub_starts_on:
        return None
    # Возраст в конном спорте определяется по году рождения
    return tournament.pub_starts_on.year - athlete.birth_date.year


def check_line(user, tournament, line: Line, late=False, allowed_athletes=None) -> list[str]:
    errors = []
    c = line.tclass
    if c.program.tournament_id != tournament.pk:
        return ["Зачёт не относится к этому турниру."]
    allowed = allowed_athletes if allowed_athletes is not None else set(athletes_for_entry(user).values_list("pk", flat=True))
    everyone = [line.athlete] + [a for a, _ in line.partners]
    for a in everyone:
        if a.pk not in allowed:
            errors.append(f"Нет права подавать заявку за спортсмена «{a.public_name}».")
        if not a.consent_confirmed_at:
            errors.append(f"«{a.public_name}»: не подтверждено согласие на обработку и публикацию.")
        if c.min_age or c.max_age:
            age = athlete_age(a, tournament)
            if age is None:
                errors.append(f"«{a.public_name}»: для зачёта «{c.name}» нужна дата рождения.")
            elif (c.min_age and age < c.min_age) or (c.max_age and age > c.max_age):
                errors.append(f"«{a.public_name}» ({age} лет в {tournament.pub_starts_on.year} г.) не проходит по возрасту в зачёт «{c.name}» ({c.conditions}).")
        if c.rank_requirement and not a.rank_for(c.program.discipline):
            errors.append(f"«{a.public_name}»: для зачёта «{c.name}» нужен разряд ({c.rank_requirement}).")
    if c.composition == TournamentClass.Composition.SINGLE and line.partners:
        errors.append("Зачёт одиночный — партнёры не нужны.")
    if c.composition == TournamentClass.Composition.PAIR and len(line.partners) != 1:
        errors.append("Зачёт парный — укажите одного партнёра с лошадью.")
    if c.composition == TournamentClass.Composition.GROUP and not line.partners:
        errors.append("Зачёт групповой — укажите хотя бы одного партнёра с лошадью.")
    if Entry.objects.filter(tclass=c, athlete=line.athlete, horse=line.horse, decision__in=Entry.ACTIVE).exists():
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


def _capacity_errors(lines) -> list[str]:
    """Лимиты зачёта и программы. Вызывать под блокировкой строк зачётов."""
    errors = []
    need_class, need_program = {}, {}
    for l in lines:
        need_class[l.tclass.pk] = need_class.get(l.tclass.pk, 0) + 1
        need_program[l.tclass.program_id] = need_program.get(l.tclass.program_id, 0) + 1
    classes = {c.pk: c for c in TournamentClass.objects.select_related("program").filter(pk__in=need_class)}
    taken_c = dict(Entry.objects.filter(tclass_id__in=need_class, decision__in=Entry.ACTIVE).values_list("tclass").annotate(n=Count("id")))
    taken_p = dict(Entry.objects.filter(program_id__in=need_program, decision__in=Entry.ACTIVE).values_list("program").annotate(n=Count("id")))
    for cid, n in need_class.items():
        c = classes[cid]
        if c.limit and taken_c.get(cid, 0) + n > c.limit:
            errors.append(f"В зачёте «{c.name}» не осталось мест (лимит {c.limit}).")
    for pid, n in need_program.items():
        p = next(c.program for c in classes.values() if c.program_id == pid)
        if p.limit and taken_p.get(pid, 0) + n > p.limit:
            errors.append(f"В программе «{p.name}» не осталось мест (лимит {p.limit}).")
    return errors


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
    # блокируем зачёты, чтобы два запроса не заняли последнее место дважды
    list(TournamentClass.objects.select_for_update().filter(pk__in={l.tclass.pk for l in lines}).order_by("pk"))
    allowed = set(athletes_for_entry(user).values_list("pk", flat=True))
    errors, seen = [], set()
    for l in lines:
        l.tclass.refresh_from_db()
        if l.key() in seen:
            errors.append(f"Выступление «{l.athlete.public_name} / {l.horse.name}» добавлено дважды.")
        seen.add(l.key())
        errors += check_line(user, t, l, late=late, allowed_athletes=allowed)
    errors += _capacity_errors(lines)
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
            partners_snapshot=[{"athlete": a.private_snapshot(l.tclass.program.discipline), "horse": hh.public_snapshot()} for a, hh in l.partners],
            program_name=l.tclass.program.name, class_name=l.tclass.name, day=l.tclass.program.day,
            decision=Entry.Decision.ACCEPTED if late else Entry.Decision.PENDING,
            decided_by=user if late else None, decided_at=timezone.now() if late else None,
        )
        for i, (a, hh) in enumerate(l.partners, start=1):
            EntryPartner.objects.create(entry=e, athlete=a, horse=hh, position=i)
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
    line = Line(athlete or e.athlete, horse or e.horse, tclass or e.tclass, partners=[(p.athlete, p.horse) for p in e.partners.all()])
    errors = check_line(user, e.tournament, line)
    if tclass:
        errors += _capacity_errors([line])
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
        line = Line(p.athlete or e.athlete, p.horse or e.horse, new_class, partners=[(x.athlete, x.horse) for x in e.partners.all()])
        allowed = set(athletes_for_entry(e.application.submitted_by).values_list("pk", flat=True))
        errors = check_line(e.application.submitted_by, e.tournament, line, allowed_athletes=allowed)
        if p.tclass_id:
            TournamentClass.objects.select_for_update().filter(pk=new_class.pk).exists()
            errors += _capacity_errors([line])
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
