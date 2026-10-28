from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import audit

from .models import Athlete, Horse, Offer


def athletes_for_entry(user):
    """Спортсмены, за которых пользователь может подавать заявки."""
    if not user.is_authenticated:
        return Athlete.objects.none()
    return (
        Athlete.objects.filter(Q(manager=user, added_by_judge=False) | Q(delegates__user=user) | Q(linked_user=user), archived=False)
        .distinct()
        .order_by("last_name", "first_name")
    )


def can_manage_athlete(user, athlete) -> bool:
    return user.is_authenticated and athlete.manager_id == user.pk


def can_enter_athlete(user, athlete) -> bool:
    return athletes_for_entry(user).filter(pk=athlete.pk).exists()


def can_view_athlete(user, athlete) -> bool:
    return can_enter_athlete(user, athlete)


def can_manage_horse(user, horse) -> bool:
    return user.is_authenticated and horse.manager_id == user.pk


def can_view_horse_documents(user, horse) -> bool:
    """Документы лошади: управляющий, а также организатор и судья турнира, куда подана заявка с этой лошадью."""
    if can_manage_horse(user, horse):
        return True
    if not user.is_authenticated:
        return False
    from apps.entries.models import Entry
    from apps.tournaments.models import TeamMember, Tournament

    tournaments = (
        Entry.objects.filter(horse=horse)
        .exclude(decision__in=[Entry.Decision.CANCELLED, Entry.Decision.REJECTED])
        .exclude(tournament__publication__in=[Tournament.Publication.FINISHED, Tournament.Publication.CANCELLED])
        .values_list("tournament_id", flat=True)
    )
    if Tournament.objects.filter(pk__in=tournaments, owner=user).exists():
        return True
    return TeamMember.objects.filter(
        tournament_id__in=tournaments, user=user, state=TeamMember.State.ACCEPTED,
        role__in=[TeamMember.Role.ORGANIZER, TeamMember.Role.JUDGE],
    ).exists()


def search_horses(query: str, mode: str):
    """Поиск: по кличке либо точное совпадение номера ФКСР / внутреннего кода."""
    q = (query or "").strip()
    if not q:
        return Horse.objects.none()
    qs = Horse.objects.filter(archived=False)
    if mode not in ("code", "name"):
        # Тест 5, п. 14.1: кличка или номер — определяем сами. Номер — цифры (ФКСР) или код из латиницы и цифр.
        import re

        mode = "code" if re.fullmatch(r"[\d\s-]+|(?i:eq)[-\s]?[A-Za-z0-9]{6}", q) else "name"
    if mode == "code":
        from .models import normalize_fksr

        norm = normalize_fksr(q)
        return qs.filter(Q(fksr_number=norm) | Q(fei_id=norm) | Q(code=q.upper()) | Q(code="EQ-" + q.upper().replace("EQ", "").strip(" -")))[:20]
    # Тест 5.1: по началу клички или началу любого слова в кличке (не по вхождению); ё = е, регистр не важен
    from django.db.models import Value
    from django.db.models.functions import Lower, Replace

    qn = q.lower().replace("ё", "е")
    nm = Replace(Lower("name"), Value("ё"), Value("е"))
    word = Q(nm__startswith=qn)
    for sep in (" ", "-", "«", "\"", "("):
        word |= Q(nm__contains=sep + qn)
    return qs.annotate(nm=nm).filter(word).order_by("name", "birth_year")[:30]


@transaction.atomic
def respond_offer(user, offer_id: int, accept: bool) -> Offer:
    offer = Offer.objects.select_for_update().get(pk=offer_id, state=Offer.State.PENDING)
    if offer.phone != user.phone:
        raise PermissionDenied
    offer.state = Offer.State.ACCEPTED if accept else Offer.State.DECLINED
    offer.responded_at = timezone.now()
    offer.save()
    if accept:
        if offer.kind == Offer.Kind.ATHLETE_LINK:
            a = Athlete.objects.select_for_update().get(pk=offer.athlete_id)
            a.linked_user = user
            a.save(update_fields=["linked_user", "updated_at"])
            if not user.is_athlete:
                user.is_athlete = True
                user.save(update_fields=["is_athlete"])
            audit(user, "athlete.link", a)
        elif offer.kind == Offer.Kind.ATHLETE_TRANSFER:
            a = Athlete.objects.select_for_update().get(pk=offer.athlete_id)
            prev = a.manager_id
            a.manager = user
            a.save(update_fields=["manager", "updated_at"])
            audit(user, "athlete.transfer", a, previous_manager=prev)
        elif offer.kind == Offer.Kind.HORSE_TRANSFER:
            h = Horse.objects.select_for_update().get(pk=offer.horse_id)
            prev = h.manager_id
            h.manager = user
            h.save(update_fields=["manager", "updated_at"])
            audit(user, "horse.transfer", h, previous_manager=prev)
        # Другие ожидающие передачи того же объекта теряют смысл
        Offer.objects.filter(state=Offer.State.PENDING, kind=offer.kind, athlete=offer.athlete, horse=offer.horse).exclude(pk=offer.pk).update(
            state=Offer.State.CANCELLED
        )
    return offer
