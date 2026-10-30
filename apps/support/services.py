"""Логика поддержки (ТЗ 13.2). Права проверяются здесь и во view; модератор видит только обращение и связанные объекты."""

import datetime
import hashlib
import logging
import zoneinfo

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.core.models import audit, notify

from .models import Ticket, TicketAttachment, TicketMessage, Topic

log = logging.getLogger(__name__)

ANSWER_WITHIN = datetime.timedelta(days=1)  # SUP-06: срок ответа — 1 рабочий день
ANSWER_WITHIN_TEXT = "1 рабочего дня"
MAX_ATTACHMENTS = 3
ATTACH_MAX = 10 * 1024 * 1024
IMG_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG")


class SupportError(ValueError):
    pass


# ---------------- права ----------------

def can_view(user, ticket, token: str = "") -> bool:
    if token and secrets_equal(token, ticket.token):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    return ticket.author_id == user.pk or user.is_moderator


def secrets_equal(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(str(a), str(b))


# ---------------- «Идёт турнир» (SUP-07) ----------------

def live_tournaments_for(user):
    """Турниры, которые проходят сегодня (по времени места проведения) и где пользователь — организатор или судья."""
    if not getattr(user, "is_authenticated", False):
        return []
    from apps.tournaments.models import TeamMember, Tournament

    member_ids = TeamMember.objects.filter(
        user=user, state=TeamMember.State.ACCEPTED, role__in=[TeamMember.Role.ORGANIZER, TeamMember.Role.JUDGE]
    ).values_list("tournament_id", flat=True)
    today = timezone.localdate()
    qs = Tournament.objects.filter(publication=Tournament.Publication.PUBLISHED,
                                   pub_starts_on__lte=today + datetime.timedelta(days=1),
                                   pub_ends_on__gte=today - datetime.timedelta(days=1))
    qs = qs.filter(owner=user) | qs.filter(pk__in=list(member_ids))
    now = timezone.now()
    out = []
    for t in qs.distinct().order_by("pub_starts_on"):
        try:
            local = now.astimezone(zoneinfo.ZoneInfo(t.pub_timezone)).date() if t.pub_timezone else today
        except Exception:
            local = today
        if t.pub_starts_on <= local <= t.pub_ends_on:
            out.append(t)
    return out


# ---------------- частота (SUP-13) ----------------

def _rate_key(kind, value):
    return "support-rate:%s:%s" % (kind, hashlib.sha256(value.encode()).hexdigest()[:32])


def check_rate(user, ip: str):
    """Гость — не больше 3 обращений в час с одного адреса; аккаунт — не больше 10."""
    if getattr(user, "is_authenticated", False):
        key, limit = _rate_key("user", str(user.pk)), settings.SUPPORT_MAX_PER_HOUR_USER
    else:
        key, limit = _rate_key("ip", ip or "-"), settings.SUPPORT_MAX_PER_HOUR_GUEST
    if cache.get(key, 0) >= limit:
        raise SupportError("Слишком много обращений подряд. Попробуйте через час или напишите в ответ на уже созданное обращение.")
    return key


def _hit(key):
    cache.add(key, 0, 3600)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, 3600)


# ---------------- вложения ----------------

def check_image(f):
    if f.size > ATTACH_MAX:
        raise SupportError("Файл «%s» больше 10 МБ." % f.name)
    head = f.read(5)
    f.seek(0)
    if not any(head.startswith(s) for s in IMG_SIGNATURES):
        raise SupportError("Файл «%s» — не картинка JPEG или PNG." % f.name)
    return f


# ---------------- письма (SUP-05, SUP-07, SUP-14) ----------------

def _abs(url: str) -> str:
    return settings.APP_ORIGIN.rstrip("/") + url


def _mail(to, subject, body, kind="support"):
    from apps.core.mail import queue_mail

    queue_mail(to, subject, body, kind=kind)


def _moderators():
    from apps.accounts.models import User

    return list(User.objects.filter(is_moderator=True, is_active=True))


def moderator_emails(mods=None) -> list[str]:
    """SUP-14: адреса из настроек сервиса (ADM-08) + email каждого модератора; запасной — SUPPORT_ALERT_EMAIL."""
    from apps.core.models import SiteSettings

    mods = _moderators() if mods is None else mods
    return SiteSettings.get().moderator_email_list + [m.email for m in mods] + [settings.SUPPORT_ALERT_EMAIL]


def _snippet(text: str, n: int = 300) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def mail_moderators(t: Ticket, event: str, text: str, mods=None):
    """SUP-14: письмо модератору о новом обращении или новом сообщении пользователя.
    Без телефона, вложений и полного текста — они открываются по ссылке после входа (ACL-03)."""
    author = t.author.full_name if t.author_id else (f"гость {t.name}".strip() if t.name else "гость")
    title = (t.tournament.pub_title or t.tournament.title) if t.tournament_id else ""
    prefix = "СРОЧНО: " if t.urgent else ""
    subject = f"{prefix}{event} — обращение №{t.pk}: {t.get_topic_display()}"
    lines = [f"{event}: обращение №{t.pk}", f"Тема: {t.get_topic_display()}"]
    if t.urgent:
        lines.append(f"Срочно — идёт турнир «{title}»")
    elif title:
        lines.append(f"Турнир: {title}")
    lines += [f"Автор: {author}", "", _snippet(text), "", f"Открыть обращение: {_abs(_mod_url(t))}"]
    _mail(moderator_emails(mods), subject, "\n".join(lines), kind="support.moderator")


# ---------------- действия ----------------

@transaction.atomic
def create(user, data: dict, files, rate_key=None) -> Ticket:
    t = Ticket(
        author=user if getattr(user, "is_authenticated", False) else None,
        name=data.get("name", ""), phone=data.get("phone", ""), email=data.get("email", ""),
        topic=data["topic"], text=data["text"],
        tournament=data.get("tournament"), athlete=data.get("athlete"), horse=data.get("horse"), club=data.get("club"),
        page=(data.get("page") or "")[:300], user_agent=(data.get("user_agent") or "")[:300],
        last_user_at=timezone.now(),
    )
    t.urgent = bool(data.get("urgent") and t.tournament_id and t.tournament in live_tournaments_for(user))
    t.save()
    for f in list(files)[:MAX_ATTACHMENTS]:
        TicketAttachment.objects.create(ticket=t, file=f)
    audit(user, "support.create", t, topic=t.topic, urgent=t.urgent)
    mods = _moderators()
    if t.urgent:
        title = t.tournament.pub_title or t.tournament.title
        notify(mods, f"Срочно — идёт турнир «{title}»: обращение №{t.pk}", _mod_url(t), kind="support.urgent")
    else:
        notify(mods, f"Новое обращение №{t.pk}: {t.get_topic_display()}", _mod_url(t), kind="support.new")
    mail_moderators(t, "Новое обращение", t.text, mods)
    if rate_key:
        _hit(rate_key)
    return t


def _mod_url(t):
    return f"/moderation/support/{t.pk}/"


@transaction.atomic
def user_reply(user, ticket_pk: int, text: str, token: str = "") -> Ticket:
    t = Ticket.objects.select_for_update().get(pk=ticket_pk)
    if not (token and secrets_equal(token, t.token)) and not (getattr(user, "is_authenticated", False) and t.author_id == user.pk):
        raise SupportError("Это не ваше обращение.")
    text = (text or "").strip()
    if not text:
        raise SupportError("Напишите текст ответа.")
    TicketMessage.objects.create(ticket=t, author=t.author, is_staff=False, text=text[:5000])
    old = t.status
    # SUP-04: ответ пользователя в решённое (или ждущее его) обращение возвращает его в работу
    if t.status in (Ticket.Status.RESOLVED, Ticket.Status.WAITING):
        t.status = Ticket.Status.IN_WORK
    t.last_user_at = timezone.now()
    t.save(update_fields=["status", "last_user_at", "updated_at"])
    audit(user, "support.user_reply", t, status_from=old, status_to=t.status)
    mods = _moderators()
    reopened = old == Ticket.Status.RESOLVED
    event = "Обращение открыто снова" if reopened else "Ответ пользователя"
    notify(mods, f"{event} — обращение №{t.pk}", _mod_url(t), kind="support.reply")
    mail_moderators(t, event, text, mods)
    return t


@transaction.atomic
def staff_reply(moderator, ticket_pk: int, text: str, status: str) -> Ticket:
    if not moderator.is_moderator:
        raise SupportError("Нет прав.")
    t = Ticket.objects.select_for_update(of=("self",)).select_related("author").get(pk=ticket_pk)
    text = (text or "").strip()
    if status not in Ticket.Status.values:
        status = t.status
    if not text and status == t.status:
        raise SupportError("Напишите ответ или поменяйте статус.")
    old = t.status
    if text:
        TicketMessage.objects.create(ticket=t, author=moderator, is_staff=True, text=text[:5000])
        t.last_staff_at = timezone.now()
        if status == Ticket.Status.NEW:
            status = Ticket.Status.WAITING
    t.status = status
    t.save(update_fields=["status", "last_staff_at", "updated_at"])
    audit(moderator, "support.staff_reply" if text else "support.status", t, status_from=old, status_to=t.status)
    url = t.get_absolute_url()
    what = "Поддержка ответила" if text else f"Статус обращения: {t.get_status_display().lower()}"
    if t.author_id:
        notify([t.author], f"{what} — обращение №{t.pk}", url, kind="support.answer")
    if text and t.reply_email:
        _mail(t.reply_email, f"Ответ поддержки СтартЛист — обращение №{t.pk}",
              f"Здравствуйте!\n\n{text}\n\nОбращение и переписка: {_abs(url)}\n\n— Поддержка СтартЛист")
    return t


@transaction.atomic
def transfer_to_judges(moderator, ticket_pk: int) -> int:
    """SUP-10: ошибка в результатах — уведомление судьям и организатору турнира. Модератор результаты не меняет."""
    from apps.tournaments.models import TeamMember

    if not moderator.is_moderator:
        raise SupportError("Нет прав.")
    t = Ticket.objects.select_for_update(of=("self",)).select_related("tournament").get(pk=ticket_pk)
    if not t.tournament_id:
        raise SupportError("В обращении не выбран турнир.")
    tr = t.tournament
    users = [tr.owner] + [m.user for m in TeamMember.objects.filter(
        tournament=tr, state=TeamMember.State.ACCEPTED, role__in=[TeamMember.Role.JUDGE, TeamMember.Role.ORGANIZER]
    ).select_related("user")]
    snippet = " ".join(t.text.split())[:180]
    notify(users, f"Обращение об ошибке в результатах (№{t.pk}): {snippet}", f"/organizer/t/{tr.pk}/conduct/", kind="support.transfer")
    t.transferred_at = timezone.now()
    if t.status == Ticket.Status.NEW:
        t.status = Ticket.Status.IN_WORK
    t.save(update_fields=["transferred_at", "status", "updated_at"])
    audit(moderator, "support.transfer", t, tournament=tr.pk, recipients=len({u.pk for u in users}))
    return len({u.pk for u in users})


def is_overdue(t, now=None) -> bool:
    """Ждёт ответа поддержки дольше срока SUP-06."""
    if t.status in (Ticket.Status.RESOLVED, Ticket.Status.WAITING):
        return False
    now = now or timezone.now()
    waiting_since = t.last_user_at or t.created_at
    return (t.last_staff_at is None or t.last_staff_at < waiting_since) and now - waiting_since > ANSWER_WITHIN


ORGANIZER_TOPICS = {Topic.OTHER, Topic.DISPUTE}  # SUP-09: вопросы по условиям турнира решает организатор
