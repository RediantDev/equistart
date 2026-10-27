"""Правила турнира: права, готовность к проверке, публикация, отмена."""

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.models import audit

from .disciplines import DISCIPLINE_NAMES
from .models import TeamMember, Tournament
from .regions import tz_label


class Conflict(Exception):
    """Данные изменились с момента открытия страницы."""


# ---------------- права ----------------

def roles_of(user, tournament) -> set[str]:
    if not user.is_authenticated:
        return set()
    roles = set(
        TeamMember.objects.filter(tournament=tournament, user=user, state=TeamMember.State.ACCEPTED).values_list("role", flat=True)
    )
    if tournament.owner_id == user.pk:
        roles.add(TeamMember.Role.ORGANIZER)
    return roles


def can_manage(user, tournament) -> bool:
    """Полное управление: создатель или принятый организатор."""
    return TeamMember.Role.ORGANIZER in roles_of(user, tournament)


def can_view_internal(user, tournament) -> bool:
    """Внутренние сведения турнира видит команда и модератор."""
    return bool(roles_of(user, tournament)) or getattr(user, "is_moderator", False)


def require_manage(user, tournament):
    if not can_manage(user, tournament):
        raise PermissionDenied


def check_revision(tournament, posted) -> None:
    try:
        posted = int(posted)
    except (TypeError, ValueError):
        raise Conflict
    if posted != tournament.revision:
        raise Conflict


# ---------------- готовность ----------------

@dataclass
class Problem:
    step: int
    text: str


def problems(t: Tournament) -> list[Problem]:
    """Что мешает отправить турнир на проверку. Пустой список — готово."""
    out = []
    if not t.title.strip():
        out.append(Problem(1, "Укажите название соревнования."))
    if not t.starts_on:
        out.append(Problem(1, "Укажите дату проведения."))
    if not t.venue_id:
        out.append(Problem(1, "Укажите место проведения."))
    if not t.contact.strip():
        out.append(Problem(1, "Укажите телефон для участников — он будет на странице турнира."))
    if not t.timezone:
        out.append(Problem(1, "Не определён часовой пояс места проведения."))
    programs = list(t.programs.all().prefetch_related("classes"))
    if not programs:
        out.append(Problem(2, "Добавьте хотя бы один маршрут или езду."))
    days = set(t.days())
    for p in programs:
        if p.day not in days:
            out.append(Problem(2, f"«{p.name}»: день {p.day:%d.%m.%Y} вне дат соревнования — выберите новый день."))
    for p in programs:
        if not p.classes.all():
            out.append(Problem(2, f"«{p.name}»: добавьте хотя бы один зачёт."))
        if p.is_dressage and not p.test_snapshot:
            out.append(Problem(2, f"«{p.name}»: выберите езду."))
        if p.is_jumping and not p.heights and not p.route.manual:
            out.append(Problem(2, f"«{p.name}»: укажите высоту."))
        if p.heights and len(p.heights) > 1:
            for c in p.classes.all():
                if c.height and c.height not in p.heights:
                    out.append(Problem(2, f"«{p.name}», зачёт «{c.name}»: высоты {c.height} см больше нет в маршруте."))
    if not t.entry_deadline:
        out.append(Problem(3, "Укажите последний день подачи заявок."))
    elif t.ends_on and t.entry_deadline > t.ends_on:
        out.append(Problem(3, "Последний день подачи заявок не может быть позже окончания соревнования."))
    if not t.regulation:
        out.append(Problem(3, "Загрузите положение о соревновании (PDF)."))
    return out


def first_invalid_step(t: Tournament) -> int | None:
    steps = [p.step for p in problems(t)]
    return min(steps) if steps else None


# ---------------- снимок публикации ----------------

def build_snapshot(t: Tournament) -> dict:
    programs = []
    for p in t.programs.all().prefetch_related("classes"):
        test = p.test_snapshot or {}
        programs.append(
            {
                "day": p.day.isoformat(),
                "start_time": p.start_time.strftime("%H:%M") if p.start_time else "",
                "discipline": p.discipline,
                "discipline_name": DISCIPLINE_NAMES.get(p.discipline, ""),
                "name": p.name,
                "program_type": p.program_type,
                "program_type_name": p.program_type_name,
                "variant": p.variant,
                "table": p.table,
                "heights": p.heights,
                "heights_label": p.heights_label,
                "article": p.article_label,
                "test": {"name": test.get("name"), "level": test.get("level"), "arena": test.get("arena")} if test else None,
                "classes": [
                    {"name": c.name, "label": c.label, "height": c.height, "conditions": c.conditions, "note": c.note}
                    for c in p.classes.all()
                ],
            }
        )
    return {
        "title": t.title,
        "starts_on": t.starts_on.isoformat(),
        "ends_on": (t.ends_on or t.starts_on).isoformat(),
        "venue": t.venue.public_dict() if t.venue else None,
        "timezone": t.timezone,
        "timezone_label": tz_label(t.timezone),
        "contact_name": t.contact_name,
        "contact": t.contact,
        "level": t.level,
        "level_name": t.get_level_display(),
        "classification": t.classification,
        "entry_deadline": t.entry_deadline.isoformat() if t.entry_deadline else None,
        "entry_deadline_time": t.entry_deadline_time.strftime("%H:%M") if t.entry_deadline_time else "",
        "cancel_terms": t.cancel_terms,
        "has_regulation": bool(t.regulation),
        "disciplines": list(dict.fromkeys(p["discipline"] for p in programs)),
        "programs": programs,
    }


# ---------------- действия ----------------

@transaction.atomic
def submit(user, tournament_id: int, revision) -> Tournament:
    t = Tournament.objects.select_for_update().get(pk=tournament_id)
    require_manage(user, t)
    check_revision(t, revision)
    if t.status not in (Tournament.Status.DRAFT, Tournament.Status.RETURNED):
        raise Conflict
    found = problems(t)
    if found:
        raise ValueError(found)
    t.status = Tournament.Status.REVIEW
    t.submitted_at = timezone.now()
    t.revision += 1
    t.save()
    audit(user, "tournament.submit", t, revision=t.revision)
    return t


@transaction.atomic
def approve(moderator, tournament_id: int, revision, confirm_venue: bool = False) -> Tournament:
    if not moderator.is_moderator:
        raise PermissionDenied
    t = Tournament.objects.select_for_update().get(pk=tournament_id)
    check_revision(t, revision)
    if t.status != Tournament.Status.REVIEW:
        raise Conflict
    snap = build_snapshot(t)
    t.snapshot = snap
    t.snapshot_version += 1
    t.published_at = timezone.now()
    t.published_regulation = t.regulation.name if t.regulation else ""
    t.pub_title = t.title
    t.pub_starts_on = t.starts_on
    t.pub_ends_on = t.ends_on or t.starts_on
    t.pub_region = t.venue.region if t.venue else ""
    t.pub_disciplines = snap["disciplines"]
    t.pub_deadline = t.entry_deadline
    t.pub_deadline_time = t.entry_deadline_time
    t.pub_timezone = t.timezone
    if t.publication == Tournament.Publication.NONE:
        t.publication = Tournament.Publication.PUBLISHED
    t.status = Tournament.Status.APPROVED
    t.moderation_comment = ""
    t.reviewed_by = moderator
    t.reviewed_at = timezone.now()
    t.revision += 1
    t.save()
    if confirm_venue and t.venue and not t.venue.confirmed:
        t.venue.confirmed = True
        t.venue.save(update_fields=["confirmed"])
        audit(moderator, "venue.confirm", t.venue)
    audit(moderator, "tournament.approve", t, snapshot_version=t.snapshot_version)
    return t


@transaction.atomic
def return_for_fixes(moderator, tournament_id: int, revision, comment: str) -> Tournament:
    if not moderator.is_moderator:
        raise PermissionDenied
    comment = (comment or "").strip()
    if not comment:
        raise ValueError("Напишите, что нужно исправить.")
    t = Tournament.objects.select_for_update().get(pk=tournament_id)
    check_revision(t, revision)
    if t.status != Tournament.Status.REVIEW:
        raise Conflict
    t.status = Tournament.Status.RETURNED
    t.moderation_comment = comment
    t.reviewed_by = moderator
    t.reviewed_at = timezone.now()
    t.revision += 1
    t.save()
    audit(moderator, "tournament.return", t, comment=comment)
    return t


@transaction.atomic
def reject(moderator, tournament_id: int, revision, comment: str) -> Tournament:
    """Тест 4.2: модератор отклоняет соревнование — окончательно, без повторной отправки. Только для неопубликованных."""
    if not moderator.is_moderator:
        raise PermissionDenied
    comment = (comment or "").strip()
    if not comment:
        raise ValueError("Напишите причину отклонения — её увидит организатор.")
    t = Tournament.objects.select_for_update().get(pk=tournament_id)
    check_revision(t, revision)
    if t.status != Tournament.Status.REVIEW:
        raise Conflict
    if t.publication != Tournament.Publication.NONE:
        raise ValueError("Турнир уже опубликован — отклонить можно только новый. Верните изменения с замечаниями.")
    t.status = Tournament.Status.REJECTED
    t.moderation_comment = comment
    t.reviewed_by = moderator
    t.reviewed_at = timezone.now()
    t.revision += 1
    t.save()
    audit(moderator, "tournament.reject", t, comment=comment)
    from apps.core.models import notify

    notify([t.owner], f"Соревнование «{t.title}» отклонено модератором: {comment}", reverse("tournaments:step", args=[t.pk, 4]), "moderation")
    return t


@transaction.atomic
def cancel(user, tournament_id: int, revision, reason: str) -> Tournament:
    t = Tournament.objects.select_for_update().get(pk=tournament_id)
    require_manage(user, t)
    check_revision(t, revision)
    if t.publication != Tournament.Publication.PUBLISHED:
        raise Conflict
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Укажите причину отмены — её увидят участники.")
    t.publication = Tournament.Publication.CANCELLED
    t.cancelled_reason = reason
    t.revision += 1
    t.save()
    audit(user, "tournament.cancel", t, reason=reason)
    return t


@transaction.atomic
def delete_draft(user, tournament_id: int, revision) -> None:
    t = Tournament.objects.select_for_update().get(pk=tournament_id)
    require_manage(user, t)
    check_revision(t, revision)
    if t.is_published or t.status == Tournament.Status.REVIEW:
        raise Conflict
    audit(user, "tournament.delete", t, title=t.title)
    t.delete()
