import datetime
import re
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.phone import normalize_phone
from apps.profiles.models import Horse
from apps.tournaments.models import TeamMember, Tournament
from apps.tournaments.services import can_manage

from . import services
from .forms import CategoryForm, ServiceForm
from .models import Booking, Service, ServiceOrder, StableCategory

PUBLIC = ["published", "finished", "cancelled"]


def _date(raw):
    try:
        return datetime.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _staff_tournament(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    services.require_stable(request.user, t)
    return t


def _my_horses(user):
    return Horse.objects.filter(manager=user, archived=False, added_by_judge=False).order_by("name")


# ---------------- представитель ----------------

class _FieldError(ValueError):
    """Ошибка, которую показываем у конкретного поля формы брони."""

    def __init__(self, field, msg):
        self.field = field
        super().__init__(msg)


def _book_horses(user, t, staff):
    horses = list(_my_horses(user))
    if staff:
        # тест 4.2: организатор и конюшня видят лошадей всех заявок турнира и могут ввести лошадь вручную
        from apps.entries.models import Entry

        ids = set(Entry.objects.filter(tournament=t).exclude(decision="cancelled").values_list("horse_id", flat=True))
        ids |= set(t.bookings.values_list("horse_id", flat=True))
        have = {h.pk for h in horses}
        horses += [h for h in Horse.objects.filter(pk__in=ids - have).order_by("name")]
    return horses


def _service_rows(svcs, horses, post=None, errors=None):
    """Строки корзины услуг: для услуги «на лошадь» — количество по каждой лошади, иначе одно поле."""
    post = post or {}
    errors = errors or {}
    out = []
    for s in svcs:
        if s.recipient == Service.Recipient.HORSE:
            rows = [{"name": f"q-{s.pk}-{h.pk}", "label": h.name, "value": post.get(f"q-{s.pk}-{h.pk}", "")} for h in horses]
        else:
            rows = [{"name": f"q-{s.pk}", "label": "Количество", "value": post.get(f"q-{s.pk}", "")}]
        out.append({"s": s, "rows": rows, "trailer": post.get(f"trailer-{s.pk}", ""), "date": post.get(f"date-{s.pk}", ""),
                    "errors": errors.get(s.pk, [])})
    return out


def _contacts(t):
    """Кому звонить по денникам: конюшня из команды турнира, иначе — контакт турнира."""
    # Решение 27.10.2026: участнику — один ответственный (назначает организатор), иначе — первый принятый из «Конюшни»
    out = []
    stable = TeamMember.objects.filter(tournament=t, state=TeamMember.State.ACCEPTED, role=TeamMember.Role.STABLE).select_related("user")
    m = stable.filter(stable_contact=True).first() or stable.order_by("created_at").first()
    if m:
        out.append({"name": (m.user.full_name if m.user else "") or "Конюшня турнира", "role": m.title or "Ответственный за денники", "phone": m.phone})
    if not out:
        snap = t.snapshot or {}
        name, phone = snap.get("contact_name") or t.contact_name, normalize_phone(snap.get("contact") or t.contact or "") or ""
        if name or phone:
            out.append({"name": name or "Организатор", "role": "Контакт турнира", "phone": phone})
    return out


def _render_book(request, t, form=None, preview=None, errors=None, field_errors=None, order_post=None, order_errors=None, status=200):
    cats = list(t.stable_categories.filter(is_active=True))
    svcs = list(t.services.filter(is_active=True))
    staff = services.can_stable(request.user, t)
    horses = _book_horses(request.user, t, staff)
    if form is None:
        form = {"category": request.GET.get("category", ""), "horses": [], "arrive": "", "depart": "", "required": False, "contact_phone": "",
                "comment": "", "key": uuid.uuid4().hex, "add_new": False, "new_horse": "", "new_owner": ""}
    for c in cats:
        c.free_total = services.free_for(c, c.available_from, c.available_to)
    for h in horses:
        h.checked = str(h.pk) in form["horses"]
    order_errors = order_errors or {}
    my_bookings = list(Booking.objects.filter(tournament=t, requested_by=request.user).select_related("category").order_by("arrive", "pk"))
    my_orders = list(ServiceOrder.objects.filter(tournament=t, ordered_by=request.user).select_related("service").order_by("-created_at"))
    if not staff:
        return _render_cart(request, t, cats, svcs, horses, my_bookings, my_orders, post=order_post, status=status,
                            errors=list(errors or []) + list((field_errors or {}).values()) + [m for v in order_errors.values() for m in v])
    return render(request, "stables/book_staff.html", {
        "t": t, "staff": staff, "cats": cats, "svcs": svcs, "horses": horses, "form": form, "preview": preview, "errors": errors or [],
        "fe": field_errors or {}, "open": services.booking_open(t), "okey": (order_post or {}).get("key") or uuid.uuid4().hex,
        "svc_rows": _service_rows(svcs, horses, order_post, order_errors), "order_general": order_errors.get(None, []),
        "my_bookings": my_bookings, "my_orders": my_orders, "all_cats": list(t.stable_categories.all()),
        "date_min": min((c.available_from for c in cats), default=None), "date_max": max((c.available_to for c in cats), default=None),
        "contacts": _contacts(t) if (my_bookings or my_orders) else [],
        "here": reverse("stables:book", args=[t.pk]),
    }, status=status)


# ---------------- экран «Денники и услуги» (макет 10B: лента ночей по каждой лошади) ----------------

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _plural(n, one, few, many):
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def period_label(arrive, depart):
    """«20–23 ноября · 3 ночи»: даты заезда и выезда."""
    n = (depart - arrive).days
    if arrive.month == depart.month:
        dates = f"{arrive.day}–{depart.day} {MONTHS[depart.month - 1]}"
    else:
        dates = f"{arrive.day} {MONTHS[arrive.month - 1]} – {depart.day} {MONTHS[depart.month - 1]}"
    return f"{dates} · {n} {_plural(n, 'ночь', 'ночи', 'ночей')}"


def _contiguous(nights):
    return all((b - a).days == 1 for a, b in zip(nights, nights[1:]))


def _render_cart(request, t, cats, svcs, horses, my_bookings, my_orders, post=None, errors=None, status=200):
    """Карточка на каждую лошадь: лента ночей (union периодов активных категорий), счётчики услуг «на лошадь»,
    её брони и заказы со статусами."""
    post = post or {}
    days = sorted({d for c in cats for d in c.nights()})
    horse_svcs = [s for s in svcs if s.recipient == Service.Recipient.HORSE]
    other_svcs = [s for s in svcs if s.recipient != Service.Recipient.HORSE]
    default_cat = str(cats[0].pk) if cats else ""
    for b in my_bookings:
        b.period = period_label(b.arrive, b.depart)
    cards, shown = [], set()
    for h in horses:
        bks = [b for b in my_bookings if b.horse_id == h.pk]
        busy = {d for b in bks if b.is_holding for d in services._days(b.arrive, b.depart)}
        chosen = set(post.getlist(f"n-{h.pk}")) if post else set()
        cat = post.get(f"c-{h.pk}", default_cat) if post else default_cat
        nights = [{"d": d, "iso": d.isoformat(), "wd": f"{WEEKDAYS[d.weekday()]} → {WEEKDAYS[(d.weekday() + 1) % 7]}",
                   "busy": d in busy, "on": d.isoformat() in chosen and d not in busy,
                   "cats": " ".join(str(c.pk) for c in cats if c.available_from <= d < c.available_to)} for d in days]
        on = [n["d"] for n in nights if n["on"]]
        holding = [b for b in bks if b.is_holding]
        empty = "; ".join(b.period for b in holding) if holding else "без денника"
        summary = period_label(on[0], on[-1] + datetime.timedelta(days=1)) if on else empty
        counters = [{"s": s, "name": f"q-{s.pk}-{h.pk}", "value": post.get(f"q-{s.pk}-{h.pk}", "") if post else ""} for s in horse_svcs]
        cards.append({"h": h, "nights": nights, "cat": cat, "bookings": bks, "summary": summary, "empty": empty, "selected": bool(on),
                      "orders": [o for o in my_orders if o.horse_id == h.pk], "counters": counters})
        shown.add(h.pk)
    others = [{"s": s, "name": f"q-{s.pk}", "value": post.get(f"q-{s.pk}", "") if post else "", "trailer": post.get(f"trailer-{s.pk}", "") if post else ""}
              for s in other_svcs]
    cols = min(len(days), 4) or 1
    return render(request, "stables/book.html", {
        "t": t, "cats": cats, "cards": cards, "others": others, "open": services.booking_open(t), "errors": errors or [],
        "key": post.get("key") if post else uuid.uuid4().hex,
        "extra_bookings": [b for b in my_bookings if b.horse_id not in shown],
        "extra_orders": [o for o in my_orders if o.horse_id not in shown],
        "cols": cols, "cols_wide": min(len(days), 7) or 1,
        "required": post.get("required") == "on" if post else False, "contact_phone": post.get("contact_phone", "") if post else "",
        "comment": post.get("comment", "") if post else "",
        "contacts": _contacts(t) if (my_bookings or my_orders) else [], "here": reverse("stables:book", args=[t.pk]),
        "units": {s.pk: s.unit for s in svcs},
    }, status=status)


_FIELD = re.compile(r"^(?:n|c)-(\d+)$|^q-\d+-(\d+)$")


def _book_cart(request, t):
    """Отправка экрана «Денники и услуги»: брони и услуги для нескольких лошадей одной кнопкой."""
    staff = services.can_stable(request.user, t)
    horses = _book_horses(request.user, t, staff)
    allowed = {h.pk for h in horses}
    for name in request.POST:
        m = _FIELD.match(name)
        if m and any(v.strip() for v in request.POST.getlist(name)):
            if int(m.group(1) or m.group(2)) not in allowed:
                raise PermissionDenied  # чужая лошадь
    cats = {str(c.pk): c for c in t.stable_categories.filter(is_active=True)}
    errors, bookings, lines = [], [], []
    for h in horses:
        raw = request.POST.getlist(f"n-{h.pk}")
        nights = sorted({d for d in (_date(x) for x in raw) if d})
        if len(nights) != len(set(raw)):
            errors.append(f"«{h.name}»: неверная дата — обновите страницу.")
            continue
        if not nights:
            continue
        c = cats.get(request.POST.get(f"c-{h.pk}", "")) or (next(iter(cats.values())) if len(cats) == 1 else None)
        if c is None:
            errors.append(f"«{h.name}»: выберите категорию денника.")
        elif not _contiguous(nights):
            errors.append(f"«{h.name}»: ночи должны идти подряд — одна бронь на один период.")
        else:
            bookings.append({"category": c.pk, "horse": h, "arrive": nights[0], "depart": nights[-1] + datetime.timedelta(days=1)})
    for s in t.services.filter(is_active=True):
        if s.recipient == Service.Recipient.HORSE:
            for h in horses:
                q = request.POST.get(f"q-{s.pk}-{h.pk}", "").strip()
                if q and q != "0":
                    lines.append({"service": s.pk, "quantity": q, "horse": h})
        else:
            q = request.POST.get(f"q-{s.pk}", "").strip()
            if q and q != "0":
                lines.append({"service": s.pk, "quantity": q, "trailer": request.POST.get(f"trailer-{s.pk}", "")})
    phone = ""
    if request.POST.get("contact_phone", "").strip():
        phone = normalize_phone(request.POST["contact_phone"])
        if not phone:
            errors.append("Телефон: мобильный номер с кодом страны, например +7 999 123-45-67.")
    if not errors:
        try:
            made, orders = services.submit_cart(request.user, t.pk, bookings, lines, request.POST.get("key", ""),
                                                request.POST.get("required") == "on", phone, request.POST.get("comment", ""))
            parts = []
            if made:
                parts.append("брони отправлены: " + ", ".join(f"«{b.horse_name}»" for b in made) + " — конюшня подтвердит их и назначит номера денников")
            if orders:
                parts.append("услуги заказаны: " + ", ".join(f"{o.service.name} × {o.quantity} ({o.recipient_label})" for o in orders))
            text = "; ".join(parts) or "готово"
            messages.success(request, text[:1].upper() + text[1:] + ".")
            return redirect(reverse("stables:book", args=[t.pk]))
        except services.OrderErrors as e:
            errors += [m for _, m in e.items]
        except (ValueError, services.Conflict) as e:
            errors.append(str(e))
    svcs = list(t.services.filter(is_active=True))
    my_bookings = list(Booking.objects.filter(tournament=t, requested_by=request.user).select_related("category").order_by("arrive", "pk"))
    my_orders = list(ServiceOrder.objects.filter(tournament=t, ordered_by=request.user).select_related("service").order_by("-created_at"))
    return _render_cart(request, t, list(cats.values()), svcs, horses, my_bookings, my_orders, post=request.POST, errors=errors)


@login_required
@require_http_methods(["GET", "POST"])
def book(request, pk):
    """Денники и услуги турнира для представителя: бронь на одну или несколько лошадей, проверка до отправки (LOD-02)."""
    t = get_object_or_404(Tournament, pk=pk, publication__in=PUBLIC)
    if request.method == "GET":
        return _render_book(request, t)
    if request.POST.get("mode") == "cart":
        return _book_cart(request, t)
    cats = list(t.stable_categories.filter(is_active=True))
    staff = services.can_stable(request.user, t)
    horses = _book_horses(request.user, t, staff)
    form = {"category": request.POST.get("category", ""), "horses": request.POST.getlist("horse"),
            "arrive": request.POST.get("arrive", ""), "depart": request.POST.get("depart", ""), "required": request.POST.get("required") == "on",
            "contact_phone": request.POST.get("contact_phone", ""), "comment": request.POST.get("comment", ""), "key": request.POST.get("key") or uuid.uuid4().hex,
            "add_new": staff and request.POST.get("add_new") == "on",
            "new_horse": request.POST.get("new_horse", "").strip(), "new_owner": request.POST.get("new_owner", "").strip()}
    preview, errors, fe = None, [], {}
    confirm = request.POST.get("confirm") == "1"
    try:
        c = next((x for x in cats if str(x.pk) == form["category"]), None)
        if not c:
            raise _FieldError("category", "Выберите категорию денника.")
        chosen = [h for h in horses if str(h.pk) in form["horses"]]
        if len(chosen) != len(set(form["horses"])):
            raise PermissionDenied  # лошадь не из доступного списка
        if form["add_new"]:
            if not form["new_horse"]:
                raise _FieldError("new_horse", "Впишите кличку лошади.")
            chosen.append(Horse(name=form["new_horse"]))
        if not chosen:
            raise _FieldError("horse", "Выберите лошадь." if staff else "Отметьте хотя бы одну свою лошадь.")
        arrive, depart = _date(form["arrive"]), _date(form["depart"])
        try:
            services.check_period(c, arrive, depart)
        except ValueError as e:
            raise _FieldError("dates", str(e))
        phone = ""
        if form["contact_phone"].strip():
            phone = normalize_phone(form["contact_phone"])
            if not phone:
                raise _FieldError("contact_phone", "Телефон: мобильный номер с кодом страны, например +7 999 123-45-67.")
        free = services.free_for(c, arrive, depart)
        if free < len(chosen):
            raise _FieldError("dates", f"Свободно денников этой категории на эти даты: {free}, выбрано лошадей: {len(chosen)}.")
        if confirm:
            comment = (form["comment"] + (f" Владелец/контакт: {form['new_owner']}" if form["add_new"] and form["new_owner"] else "")).strip()
            with transaction.atomic():
                if form["add_new"]:
                    # вписанную вручную лошадь создаём один раз на ключ формы: повтор отправки не плодит лошадей
                    existing = Booking.objects.filter(idempotency_key__startswith=f"{form['key'][:40]}:", horse__added_by_judge=True,
                                                      requested_by=request.user).select_related("horse").first()
                    nh = existing.horse if existing else Horse.objects.create(
                        name=form["new_horse"][:100], birth_year=0, color="", sex="", breed="", owner_name=form["new_owner"][:200],
                        manager=request.user, added_by_judge=True)
                    chosen = [h for h in chosen if h.pk] + [nh]
                made = services.book_many(request.user, c.pk, chosen, arrive, depart, form["key"], form["required"], phone, comment)
            names = ", ".join(f"«{b.horse_name}»" for b in made)
            if staff:
                messages.success(request, f"Брони созданы: {names}. Подтвердите их и назначьте номера денников.")
                return redirect(reverse("stables:manage", args=[t.pk]) + "?tab=bookings")
            messages.success(request, f"Брони отправлены: {names}. Конюшня подтвердит их и назначит номера денников — статус ниже.")
            return redirect(reverse("stables:book", args=[t.pk]))
        preview = {"c": c, "horses": chosen, "arrive": arrive, "depart": depart, "nights": (depart - arrive).days, "free": free}
    except _FieldError as e:
        fe[e.field] = str(e)
    except (ValueError, services.Conflict) as e:
        errors.append(str(e))
    return _render_book(request, t, form=form, preview=preview, errors=errors, field_errors=fe)


@login_required
@require_POST
def order(request, pk):
    """Корзина услуг: все позиции одной кнопкой; после заказа — тот же экран со списком своих заказов."""
    t = get_object_or_404(Tournament, pk=pk, publication__in=PUBLIC)
    staff = services.can_stable(request.user, t)
    horses = {str(h.pk): h for h in _book_horses(request.user, t, staff)}
    lines = []
    for s in t.services.filter(is_active=True):
        date = _date(request.POST.get(f"date-{s.pk}"))
        if s.recipient == Service.Recipient.HORSE:
            for hid, h in horses.items():
                q = request.POST.get(f"q-{s.pk}-{hid}", "").strip()
                if q:
                    lines.append({"service": s.pk, "quantity": q, "horse": h, "for_date": date})
        else:
            q = request.POST.get(f"q-{s.pk}", "").strip()
            if q:
                lines.append({"service": s.pk, "quantity": q, "trailer": request.POST.get(f"trailer-{s.pk}", ""), "for_date": date})
    try:
        made = services.order_many(request.user, t.pk, lines, request.POST.get("key", ""), comment=request.POST.get("comment", ""))
        messages.success(request, "Заказ оформлен: " + ", ".join(f"{o.service.name} × {o.quantity} ({o.recipient_label})" for o in made) + ".")
        return redirect(reverse("stables:book", args=[t.pk]))
    except services.OrderErrors as e:
        errs = {}
        for sid, msg in e.items:
            errs.setdefault(sid, []).append(msg)
    except (ValueError, services.Conflict) as e:
        errs = {None: [str(e)]}
    return _render_book(request, t, order_post=request.POST, order_errors=errs, status=200)


@login_required
@require_POST
def my_edit(request, booking_id):
    """Изменение своей брони (категория и даты), пока она на рассмотрении."""
    b = get_object_or_404(Booking, pk=booking_id)
    if b.requested_by_id != request.user.pk:
        raise PermissionDenied
    back = reverse("stables:book", args=[b.tournament_id])  # тест 5.1: экран открывается сверху, итог — в сообщении
    arrive, depart = _date(request.POST.get("arrive")), _date(request.POST.get("depart"))
    try:
        services.update_booking(request.user, b.pk, request.POST.get("category") or 0, arrive, depart, request.POST.get("revision"))
        messages.success(request, f"Бронь «{b.horse_name}» изменена. Она снова ждёт подтверждения конюшни.")
    except (ValueError, services.Conflict) as e:
        messages.error(request, str(e))
    return redirect(back)


@login_required
def my(request):
    """Все брони и заказы пользователя, сгруппированные по турнирам; управление — на экране турнира."""
    bookings = Booking.objects.filter(requested_by=request.user).select_related("tournament", "category").order_by("arrive", "pk")
    orders = ServiceOrder.objects.filter(ordered_by=request.user).select_related("tournament", "service").order_by("-created_at")
    groups = {}
    for b in bookings:
        groups.setdefault(b.tournament_id, {"t": b.tournament, "bookings": [], "orders": []})["bookings"].append(b)
    for o in orders:
        groups.setdefault(o.tournament_id, {"t": o.tournament, "bookings": [], "orders": []})["orders"].append(o)
    today = timezone.localdate()

    def sort_key(g):
        start = g["t"].pub_starts_on or datetime.date.max
        return (start < today, abs((start - today).days) if start != datetime.date.max else 10**6)

    return render(request, "stables/my.html", {"groups": sorted(groups.values(), key=sort_key)})


@login_required
@require_POST
def my_cancel(request, kind, obj_id):
    try:
        if kind == "booking":
            services.cancel_booking(request.user, obj_id, request.POST.get("reason", ""))
            messages.success(request, "Бронь отменена.")
        elif kind == "order":
            services.cancel_order(request.user, obj_id)
            messages.success(request, "Заказ отменён.")
        else:
            raise Http404
    except (Booking.DoesNotExist, ServiceOrder.DoesNotExist):
        raise Http404
    except (ValueError, services.Conflict) as e:
        messages.error(request, str(e))
    nxt = request.POST.get("next", "")
    ok = url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure())
    return redirect(nxt if ok and nxt.startswith("/") else reverse("stables:my"))


# ---------------- конюшня ----------------

TABS = [("bookings", "Брони"), ("grid", "Занятость"), ("orders", "Заказы услуг"), ("setup", "Категории и услуги")]


@login_required
def manage(request, pk):
    t = _staff_tournament(request, pk)
    tab = request.GET.get("tab", "bookings")
    if tab not in dict(TABS):
        tab = "bookings"
    ctx = {"t": t, "tab": tab, "tabs": TABS, "manage": can_manage(request.user, t)}
    cats = list(t.stable_categories.all())
    ctx["cats"] = cats
    ctx["svcs"] = list(t.services.all())
    if tab == "bookings":
        pending = Booking.objects.filter(tournament=t, state=Booking.State.PENDING).count()
        nobox = Booking.objects.filter(tournament=t, state=Booking.State.CONFIRMED, box_number="").count()
        # по умолчанию — то, что требует действия: сначала подтверждение, затем назначение номеров
        state = request.GET.get("state") or ("pending" if pending or not nobox else "nobox")
        qs = Booking.objects.filter(tournament=t).select_related("category", "requested_by", "horse")
        if state == "nobox":
            qs = qs.filter(state=Booking.State.CONFIRMED, box_number="")
        elif state in Booking.State.values:
            qs = qs.filter(state=state)
        ctx.update({"bookings": qs, "state": state, "states": Booking.State.choices, "pending": pending, "nobox": nobox})
    elif tab == "grid":
        grid = []
        for c in cats:
            occ = services.occupancy(c)
            nights = c.nights()
            grid.append({"c": c, "cells": [(d, occ.get(d, 0), c.capacity) for d in nights]})
        ctx["grid"] = grid
    elif tab == "orders":
        show = request.GET.get("show", "open")
        qs = ServiceOrder.objects.filter(tournament=t).select_related("service", "ordered_by").prefetch_related("deliveries")
        if show == "open":
            qs = [o for o in qs.filter(state=ServiceOrder.State.ORDERED) if o.delivered < o.quantity]
        ctx.update({"orders": qs, "show": show})
    else:
        for c in cats:
            c.used = sum(1 for b in c.bookings.all() if b.is_holding)
        for s in ctx["svcs"]:
            s.used = services.ordered_total(s)
    return render(request, "stables/manage.html", ctx)


@login_required
@require_POST
def manage_action(request, pk, action):
    t = _staff_tournament(request, pk)
    nxt = request.POST.get("next", "")
    back = nxt if nxt.startswith("/") else reverse("stables:manage", args=[t.pk])
    try:
        if action in ("confirm", "reject"):
            b = get_object_or_404(Booking, pk=request.POST.get("booking"), tournament=t)
            services.decide(request.user, b.pk, action == "confirm", request.POST.get("reason", ""), request.POST.get("revision"))
            messages.success(request, "Бронь подтверждена." if action == "confirm" else "Бронь отклонена.")
        elif action == "box":
            b = get_object_or_404(Booking, pk=request.POST.get("booking"), tournament=t)
            services.assign_box(request.user, b.pk, request.POST.get("box", ""))
            messages.success(request, "Номер денника сохранён.")
        elif action == "cancel":
            b = get_object_or_404(Booking, pk=request.POST.get("booking"), tournament=t)
            services.cancel_booking(request.user, b.pk, request.POST.get("reason", ""))
            messages.success(request, "Бронь отменена.")
        elif action == "deliver":
            o = get_object_or_404(ServiceOrder, pk=request.POST.get("order"), tournament=t)
            services.deliver(request.user, o.pk, request.POST.get("quantity"), request.POST.get("note", ""))
            messages.success(request, "Выдача отмечена.")
        else:
            raise Http404
    except (ValueError, services.Conflict) as e:
        messages.error(request, str(e))
    return redirect(back)


@login_required
@require_http_methods(["GET", "POST"])
def category_edit(request, pk, cat_id=None):
    t = _staff_tournament(request, pk)
    c = get_object_or_404(StableCategory, pk=cat_id, tournament=t) if cat_id else None
    initial = {} if c else {"available_from": t.starts_on and t.starts_on - datetime.timedelta(days=1), "available_to": t.ends_on and t.ends_on + datetime.timedelta(days=1)}
    form = CategoryForm(request.POST or None, instance=c, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = {f: form.cleaned_data[f] for f in CategoryForm.Meta.fields}
        try:
            services.save_category(request.user, t, data, c)
            messages.success(request, "Категория сохранена.")
            return redirect(reverse("stables:manage", args=[t.pk]) + "?tab=setup")
        except ValueError as e:
            form.add_error(None, str(e))
    return render(request, "stables/form.html", {"t": t, "form": form, "title": "Категория денников" if not c else c.name})


@login_required
@require_http_methods(["GET", "POST"])
def service_edit(request, pk, svc_id=None):
    t = _staff_tournament(request, pk)
    s = get_object_or_404(Service, pk=svc_id, tournament=t) if svc_id else None
    form = ServiceForm(request.POST or None, instance=s)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if s and obj.limit is not None and obj.limit < services.ordered_total(s):
            form.add_error("limit", "Лимит меньше уже заказанного количества.")
        else:
            obj.tournament = t
            obj.save()
            from apps.core.models import audit

            audit(request.user, "service.save", obj, name=obj.name)
            messages.success(request, "Услуга сохранена.")
            return redirect(reverse("stables:manage", args=[t.pk]) + "?tab=setup")
    return render(request, "stables/form.html", {"t": t, "form": form, "title": "Услуга" if not s else s.name})
