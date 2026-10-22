"""Бизнес-логика клубов: проверки прав на сервере, модерация, связь с местом проведения."""

import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.models import audit, notify
from apps.tournaments.models import Venue
from apps.tournaments.regions import MULTI_ZONE, REGION_TZ

from .models import Club, ClubNews

IMG_MAX = 10 * 1024 * 1024
IMG_SIGNATURES = (b"\x89PNG\r", b"\xff\xd8\xff")


def check_image(f):
    """JPEG или PNG до 10 МБ — по содержимому, а не по расширению."""
    if f.size > IMG_MAX:
        raise ValidationError("Файл больше 10 МБ.")
    head = f.read(5)
    f.seek(0)
    if not any(head.startswith(s) for s in IMG_SIGNATURES):
        raise ValidationError("Нужна картинка JPEG или PNG.")
    return f


def can_manage(user, club) -> bool:
    return user.is_authenticated and club.manager_id == user.pk


def require_manage(user, club):
    if not can_manage(user, club):
        raise PermissionDenied


def can_view(user, club) -> bool:
    return club.is_public or can_manage(user, club) or (user.is_authenticated and user.is_moderator)


def missing(club) -> list[str]:
    out = []
    for f, label in (("name", "название"), ("region", "регион"), ("settlement", "населённый пункт"), ("address", "адрес")):
        if not getattr(club, f):
            out.append(f"Укажите {label}.")
    if not (club.phone or club.email or club.site):
        out.append("Укажите хотя бы один контакт: телефон, email или сайт.")
    return out


def _moderators():
    from apps.accounts.models import User

    return list(User.objects.filter(is_moderator=True, is_active=True))


@transaction.atomic
def save(user, club, changed_fields=()) -> Club:
    """Сохранить профиль. Существенная правка подтверждённого клуба (название, адрес) отправляет его на повторную проверку;
    до решения модератора публика видит прежние название и адрес (CLB-02)."""
    if club.pk:
        require_manage(user, club)
    else:
        if not user.is_club:
            raise PermissionDenied
        club.manager = user
    resubmit = club.is_public and club.substantial_changed()
    club.revision += 1
    if resubmit:
        club.status, club.submitted_at = Club.Status.REVIEW, timezone.now()
    club.save()
    audit(user, "club.save", club, changed=list(changed_fields), resubmit=resubmit)
    if resubmit:
        notify(_moderators(), f"Клуб «{club.public_name}» изменил название или адрес — нужна повторная проверка",
               reverse("moderation:club", args=[club.pk]), "club")
    return club


@transaction.atomic
def submit(user, club_id) -> Club:
    club = Club.objects.select_for_update().get(pk=club_id)
    require_manage(user, club)
    if club.status == Club.Status.REVIEW:
        raise ValueError("Профиль уже на проверке.")
    if club.status == Club.Status.CONFIRMED:
        raise ValueError("Профиль уже подтверждён.")
    probs = missing(club)
    if probs:
        raise ValueError(" ".join(probs))
    club.status, club.submitted_at = Club.Status.REVIEW, timezone.now()
    club.save(update_fields=["status", "submitted_at", "updated_at"])
    audit(user, "club.submit", club)
    notify(_moderators(), f"Новый клуб на проверке: «{club.name}», {club.settlement}", reverse("moderation:club", args=[club.pk]), "club")
    return club


def _norm(s):
    s = re.sub(r"(кск|кк|ксш|конный|клуб|конноспортивный|комплекс|школа|центр|[«»\"'.,-])", " ", (s or "").lower())
    return " ".join(s.split())


def similar(club) -> list[Club]:
    """Похожие клубы в том же населённом пункте — предупреждение модератору (CLB-02)."""
    key = _norm(club.name)
    out = []
    for c in Club.objects.filter(settlement__iexact=club.settlement.strip()).exclude(pk=club.pk):
        k = _norm(c.name)
        if key and k and (key in k or k in key):
            out.append(c)
    return out


def _tz(region):
    if region in MULTI_ZONE:
        return MULTI_ZONE[region][0][0]
    return REGION_TZ.get(region, "Europe/Moscow")


@transaction.atomic
def confirm(moderator, club_id) -> Club:
    club = Club.objects.select_for_update().get(pk=club_id)
    if not moderator.is_moderator:
        raise PermissionDenied
    if club.status != Club.Status.REVIEW:
        raise ValueError("Подтвердить можно только профиль на проверке.")
    now = timezone.now()
    club.status, club.moderator_comment = Club.Status.CONFIRMED, ""
    club.confirmed_at = club.confirmed_at or now
    club.pub_name, club.pub_address, club.pub_settlement, club.pub_region = club.name, club.address, club.settlement, club.region
    # клуб становится местом проведения: организаторы выбирают его из списка (CLB-06)
    v = club.venue
    if v is None:
        v = Venue(created_by=club.manager)
    v.name, v.region, v.settlement, v.address, v.timezone, v.confirmed = club.name, club.region, club.settlement, club.address, _tz(club.region), True
    v.save()
    club.venue = v
    club.save()
    audit(moderator, "club.confirm", club)
    notify([club.manager], f"Клуб «{club.name}» подтверждён и опубликован. Организаторы могут выбрать его местом проведения.",
           reverse("clubs:detail", args=[club.pk]), "club")
    return club


@transaction.atomic
def return_for_fixes(moderator, club_id, comment) -> Club:
    club = Club.objects.select_for_update().get(pk=club_id)
    if not moderator.is_moderator:
        raise PermissionDenied
    comment = (comment or "").strip()
    if not comment:
        raise ValueError("Напишите, что исправить, — клуб увидит замечания.")
    if club.status != Club.Status.REVIEW:
        raise ValueError("Вернуть можно только профиль на проверке.")
    club.status, club.moderator_comment = Club.Status.RETURNED, comment
    club.save(update_fields=["status", "moderator_comment", "updated_at"])
    audit(moderator, "club.return", club, comment=comment)
    notify([club.manager], f"Профиль клуба «{club.name}» возвращён с замечаниями: {comment}", reverse("clubs:mine"), "club")
    return club


@transaction.atomic
def hide_news(moderator, news_id, reason) -> ClubNews:
    n = ClubNews.objects.select_for_update().select_related("club").get(pk=news_id)
    if not moderator.is_moderator:
        raise PermissionDenied
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Укажите причину — клуб её увидит.")
    n.hidden, n.hidden_reason = True, reason[:300]
    n.save(update_fields=["hidden", "hidden_reason", "updated_at"])
    audit(moderator, "club.news.hide", n.club, news=n.pk, reason=reason)
    notify([n.club.manager], f"Новость «{n.title}» скрыта модератором: {reason}", reverse("clubs:mine"), "club")
    return n
