"""Бизнес-логика клубов: проверки прав на сервере, модерация, связь с местом проведения."""

import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.core.models import audit, notify
from apps.tournaments.models import Venue
from apps.tournaments.regions import MULTI_ZONE, REGION_TZ

from .models import Club, ClubMember, ClubNews

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


def is_owner(user, club) -> bool:
    return user.is_authenticated and club.manager_id == user.pk


def can_manage(user, club) -> bool:
    if not user.is_authenticated or club.merged_into_id:
        return False
    return club.manager_id == user.pk or club.members.filter(user=user, state=ClubMember.State.ACCEPTED).exists()


def club_of(user):
    """Клуб, которым управляет пользователь (владелец или управляющий); объединённые не считаются."""
    if not user.is_authenticated:
        return None
    return (Club.objects.filter(merged_into__isnull=True)
            .filter(Q(manager=user) | Q(members__user=user, members__state=ClubMember.State.ACCEPTED)).distinct().first())


def managers(club):
    return [club.manager] + [m.user for m in club.members.filter(state=ClubMember.State.ACCEPTED).select_related("user")]


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
        if club_of(user):
            raise ValueError("Вы уже управляете клубом. Один аккаунт ведёт один клуб.")
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
    notify(managers(club), f"Клуб «{club.name}» подтверждён и опубликован. Организаторы могут выбрать его местом проведения.",
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
    notify(managers(club), f"Профиль клуба «{club.name}» возвращён с замечаниями: {comment}", reverse("clubs:mine"), "club")
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
    notify(managers(n.club), f"Новость «{n.title}» скрыта модератором: {reason}", reverse("clubs:mine"), "club")
    return n


# ---------------- управляющие ----------------

@transaction.atomic
def invite_manager(owner, club_id, raw_phone) -> ClubMember:
    from apps.accounts.models import User
    from apps.accounts.phone import normalize_phone

    club = Club.objects.select_for_update().get(pk=club_id)
    if not is_owner(owner, club):
        raise PermissionDenied
    phone = normalize_phone(raw_phone)
    if phone == owner.phone:
        raise ValueError("Это ваш номер — вы уже владелец клуба.")
    m = club.members.filter(phone=phone).first()
    if m and m.state != ClubMember.State.DECLINED:
        raise ValueError("Этот номер уже приглашён." if m.state == ClubMember.State.PENDING else "Этот человек уже управляет клубом.")
    if m:
        m.state, m.user, m.invited_by, m.responded_at = ClubMember.State.PENDING, None, owner, None
        m.save()
    else:
        m = ClubMember.objects.create(club=club, phone=phone, invited_by=owner)
    audit(owner, "club.member.invite", club, phone=phone)
    u = User.objects.filter(phone=phone).first()
    if u:
        notify([u], f"{owner.short_name} приглашает вас управлять клубом «{club.name}»", reverse("tournaments:invitations"), "club")
    return m


@transaction.atomic
def respond(user, member_id, accept: bool) -> ClubMember:
    m = ClubMember.objects.select_for_update().select_related("club").get(pk=member_id, state=ClubMember.State.PENDING)
    if m.phone != user.phone:
        raise PermissionDenied
    if accept:
        other = club_of(user)
        if other and other.pk != m.club_id:
            raise ValueError(f"Вы уже управляете клубом «{other.name}». Один аккаунт ведёт один клуб.")
        if not user.is_club:
            user.add_category("club")
            user.save(update_fields=["is_club"])
    m.state = ClubMember.State.ACCEPTED if accept else ClubMember.State.DECLINED
    m.user = user if accept else None
    m.responded_at = timezone.now()
    m.save()
    audit(user, "club.member.accept" if accept else "club.member.decline", m.club)
    notify([m.club.manager], f"{user.short_name} {'принял(а)' if accept else 'отклонил(а)'} приглашение управлять клубом", reverse("clubs:team"), "club")
    return m


@transaction.atomic
def remove_manager(user, member_id) -> None:
    m = ClubMember.objects.select_for_update().select_related("club").get(pk=member_id)
    # владелец убирает любого; управляющий может уйти сам
    if not (is_owner(user, m.club) or (m.user_id == user.pk)):
        raise PermissionDenied
    audit(user, "club.member.remove", m.club, phone=m.phone)
    if m.user_id and m.user_id != user.pk:
        notify([m.user], f"Вы больше не управляете клубом «{m.club.name}»", "", "club")
    m.delete()


@transaction.atomic
def transfer(owner, club_id, member_id) -> Club:
    """Передать владение управляющему; прежний владелец остаётся управляющим."""
    club = Club.objects.select_for_update().get(pk=club_id)
    if not is_owner(owner, club):
        raise PermissionDenied
    m = club.members.select_for_update().get(pk=member_id, state=ClubMember.State.ACCEPTED)
    new_owner = m.user
    m.user, m.phone = owner, owner.phone
    m.save(update_fields=["user", "phone"])
    club.manager = new_owner
    club.save(update_fields=["manager", "updated_at"])
    audit(owner, "club.transfer", club, to=new_owner.pk)
    notify([new_owner], f"Вы теперь владелец клуба «{club.name}»", reverse("clubs:team"), "club")
    return club


# ---------------- дубли ----------------

@transaction.atomic
def merge(moderator, dup_id, main_id) -> Club:
    """Модератор объединяет дубль с основным клубом: турниры, новости и фото переходят к основному,
    дубль скрывается из каталога и со списка мест, его страница ведёт на основной клуб."""
    from apps.tournaments.models import Tournament

    if not moderator.is_moderator:
        raise PermissionDenied
    if dup_id == main_id:
        raise ValueError("Выберите другой клуб.")
    dup, main = (Club.objects.select_for_update().get(pk=x) for x in (dup_id, main_id))
    if main.merged_into_id:
        raise ValueError("Основной клуб сам объединён с другим — выберите действующий.")
    if dup.merged_into_id:
        raise ValueError("Этот клуб уже объединён.")
    if not main.is_public:
        raise ValueError("Объединять можно только с подтверждённым клубом.")
    moved = 0
    if dup.venue_id:
        if main.venue_id:
            moved = Tournament.objects.filter(venue_id=dup.venue_id).update(venue_id=main.venue_id)
        Venue.objects.filter(pk=dup.venue_id).update(confirmed=False)
    dup.news.update(club=main)
    dup.photos.update(club=main)
    dup.merged_into, dup.merged_at = main, timezone.now()
    dup.status = Club.Status.CONFIRMED if dup.confirmed_at else dup.status
    dup.save(update_fields=["merged_into", "merged_at", "status", "updated_at"])
    audit(moderator, "club.merge", dup, into=main.pk, tournaments=moved)
    notify(managers(dup), f"Модератор объединил клуб «{dup.name}» с «{main.public_name}» как дубль. "
                          f"Чтобы управлять им, попросите владельца «{main.public_name}» пригласить вас.", reverse("clubs:detail", args=[main.pk]), "club")
    notify(managers(main), f"К клубу «{main.public_name}» присоединён дубль «{dup.name}»: его турниры, новости и фото теперь у вас.",
           reverse("clubs:mine"), "club")
    return dup
