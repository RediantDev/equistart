"""Кабинет модератора (ТЗ 13.3): пользователи, дубли спортсменов и лошадей.

Права проверяются здесь (а не только во view): модератор — блокировка и дубли, администратор — всё остальное.
"""

import secrets
import string
from collections import defaultdict

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import audit, notify


class CabinetError(ValueError):
    pass


def require_moderator(user):
    if not (getattr(user, "is_authenticated", False) and user.is_moderator and user.is_active):
        raise PermissionDenied


def require_admin(user):
    if not getattr(user, "is_admin", False):
        raise PermissionDenied


# ---------------- пользователи (ADM-04) ----------------

def _end_sessions(user_id: int) -> int:
    """Завершает все сессии пользователя на сервере."""
    from django.contrib.sessions.models import Session

    n = 0
    for s in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
        if str(s.get_decoded().get("_auth_user_id")) == str(user_id):
            s.delete()
            n += 1
    return n


@transaction.atomic
def block(moderator, user_id: int, reason: str) -> User:
    require_moderator(moderator)
    reason = " ".join((reason or "").split())
    if not reason:
        raise CabinetError("Укажите причину блокировки — её увидит журнал и другие модераторы.")
    u = User.objects.select_for_update().get(pk=user_id)
    if u.pk == moderator.pk:
        raise CabinetError("Заблокировать себя нельзя.")
    if u.is_moderator or u.is_superuser:
        raise CabinetError("Модератора и администратора заблокировать нельзя. Сначала снимите роль модератора.")
    if u.deleted_at:
        raise CabinetError("Аккаунт удалён.")
    if u.is_blocked:
        raise CabinetError("Аккаунт уже заблокирован.")
    u.is_active = False
    u.blocked_at, u.blocked_reason, u.blocked_by = timezone.now(), reason[:300], moderator
    u.save(update_fields=["is_active", "blocked_at", "blocked_reason", "blocked_by"])
    audit(moderator, "user.block", u, reason=u.blocked_reason)
    transaction.on_commit(lambda: _end_sessions(u.pk))
    return u


@transaction.atomic
def unblock(moderator, user_id: int) -> User:
    require_moderator(moderator)
    u = User.objects.select_for_update().get(pk=user_id)
    if u.deleted_at:
        raise CabinetError("Аккаунт удалён — разблокировать его нельзя.")
    if not u.is_blocked:
        raise CabinetError("Аккаунт не заблокирован.")
    old = u.blocked_reason
    u.is_active = True
    u.blocked_at, u.blocked_reason, u.blocked_by = None, "", None
    u.save(update_fields=["is_active", "blocked_at", "blocked_reason", "blocked_by"])
    audit(moderator, "user.unblock", u, reason_was=old)
    return u


TEMP_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0OoIl1")


def temp_password() -> str:
    return "-".join("".join(secrets.choice(TEMP_ALPHABET) for _ in range(4)) for _ in range(3))


@transaction.atomic
def reset_password(admin, user_id: int) -> str:
    """Администратор создаёт временный пароль; он показывается один раз, пользователь меняет его при входе."""
    require_admin(admin)
    u = User.objects.select_for_update().get(pk=user_id)
    if u.deleted_at:
        raise CabinetError("Аккаунт удалён.")
    pwd = temp_password()
    u.set_password(pwd)
    u.must_change_password = True
    u.save(update_fields=["password", "must_change_password"])
    audit(admin, "user.password_reset", u)
    transaction.on_commit(lambda: _end_sessions(u.pk))
    return pwd


@transaction.atomic
def delete_account(admin, user_id: int) -> User:
    """Удаление по правилам решения 26.10.2026: вход закрыт сразу, данные удаляются через 30 дней,
    опубликованные результаты остаются."""
    require_admin(admin)
    u = User.objects.select_for_update().get(pk=user_id)
    if u.pk == admin.pk or u.is_superuser or u.is_moderator:
        raise CabinetError("Модератора и администратора удалить нельзя. Сначала снимите роль модератора.")
    if u.deleted_at:
        raise CabinetError("Аккаунт уже удалён.")
    u.is_active = False
    u.deleted_at = timezone.now()
    u.save(update_fields=["is_active", "deleted_at"])
    audit(admin, "user.delete", u)
    transaction.on_commit(lambda: _end_sessions(u.pk))
    return u


# ---------------- дубли спортсменов и лошадей (ADM-05) ----------------

def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace("ё", "е").split())


def athlete_duplicates():
    from apps.profiles.models import Athlete

    groups = defaultdict(list)
    for a in Athlete.objects.filter(archived=False).select_related("manager").order_by("created_at"):
        if not a.birth_date:
            continue
        groups[(_norm(a.last_name), _norm(a.first_name), _norm(a.middle_name), a.birth_date.year)].append(a)
    return [g for g in groups.values() if len(g) > 1]


def horse_duplicates():
    from apps.profiles.models import Horse

    groups = defaultdict(list)
    for h in Horse.objects.filter(archived=False).select_related("manager").order_by("created_at"):
        groups[(_norm(h.name), h.birth_year)].append(h)
    return [g for g in groups.values() if len(g) > 1]


def _unique_sets(model, field_name):
    out = []
    for c in model._meta.constraints:
        fields = getattr(c, "fields", None)
        if fields and field_name in fields:
            out.append(tuple(fields))
    for ut in model._meta.unique_together:
        if field_name in ut:
            out.append(tuple(ut))
    return out


def _move_relations(model, dup, main) -> dict:
    """Переносит все ссылки на дубль к основной записи. Строки, которые нарушили бы уникальность
    (то же разрешение у основной записи уже есть), удаляются как лишние."""
    moved = {}
    for rel in model._meta.get_fields(include_hidden=True):
        if not (rel.auto_created and not rel.concrete and (rel.one_to_many or rel.one_to_one)):
            continue
        rm, fname = rel.related_model, rel.field.name
        if rm is model and fname == "merged_into":
            continue
        qs = rm._base_manager.filter(**{fname: dup})
        uniq = _unique_sets(rm, fname)
        if not uniq:
            n = qs.update(**{fname: main})
        else:
            n = 0
            for obj in qs:
                clash = False
                for fields in uniq:
                    look = {}
                    for f in fields:
                        attname = rm._meta.get_field(f).attname
                        look[attname] = main.pk if f == fname else getattr(obj, attname)
                    if rm._base_manager.filter(**look).exclude(pk=obj.pk).exists():
                        clash = True
                        break
                if clash:
                    obj.delete()
                else:
                    setattr(obj, fname, main)
                    obj.save(update_fields=[fname])
                    n += 1
        if n:
            moved[str(rm._meta.verbose_name_plural)] = n
    return moved


@transaction.atomic
def merge(moderator, kind: str, main_id: int, dup_ids: list[int]):
    from apps.profiles.models import Athlete, Horse

    require_moderator(moderator)
    model = {"athlete": Athlete, "horse": Horse}.get(kind)
    if model is None:
        raise CabinetError("Неизвестный вид записи.")
    dup_ids = [int(i) for i in dup_ids if str(i).isdigit() and int(i) != int(main_id)]
    if not dup_ids:
        raise CabinetError("Отметьте хотя бы один дубль, кроме основной записи.")
    rows = {o.pk: o for o in model.objects.select_for_update().filter(pk__in=[main_id, *dup_ids])}
    main = rows.get(int(main_id))
    if main is None or main.archived:
        raise CabinetError("Основная запись не найдена или уже объединена.")
    now = timezone.now()
    report = []
    for did in dup_ids:
        dup = rows.get(did)
        if dup is None or dup.archived:
            raise CabinetError("Одна из записей уже объединена или удалена. Обновите страницу.")
        # сначала дубль уходит из действующих — освобождается его номер ФКСР / FEI
        dup.archived, dup.merged_into, dup.merged_at = True, main, now
        dup.save(update_fields=["archived", "merged_into", "merged_at", "updated_at"])
        update = []
        for f in ("fksr_number", "fei_id"):
            if not getattr(main, f) and getattr(dup, f):
                setattr(main, f, getattr(dup, f))
                update.append(f)
        if kind == "athlete" and not main.linked_user_id and dup.linked_user_id:
            main.linked_user_id = dup.linked_user_id
            update.append("linked_user")
        if update:
            try:
                with transaction.atomic():
                    main.save(update_fields=update + ["updated_at"])
            except IntegrityError:
                raise CabinetError("Номер ФКСР или FEI дубля уже занят другой записью — проверьте записи вручную.")
        moved = _move_relations(model, dup, main)
        audit(moderator, f"{kind}.merge", main, dup=dup.pk, moved=moved)
        audit(moderator, f"{kind}.merged", dup, into=main.pk)
        report.append((dup, moved))
        what = "Спортсмен" if kind == "athlete" else "Лошадь"
        name = main.full_name if kind == "athlete" else main.name
        url = f"/athletes/{main.pk}/" if kind == "athlete" else f"/horses/{main.pk}/"
        notify({main.manager, dup.manager}, f"{what} «{name}»: модератор объединил дубли в одну запись", url, kind="profile.merge")
    return main, report
