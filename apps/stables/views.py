import datetime
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.phone import normalize_phone
from apps.profiles.models import Horse
from apps.tournaments.models import Tournament
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
    return Horse.objects.filter(manager=user, archived=False).order_by("name")


# ---------------- представитель ----------------

@login_required
@require_http_methods(["GET", "POST"])
def book(request, pk):
    """Денники и услуги турнира для представителя: выбор категории и периода, сумма до заказа (LOD-02)."""
    t = get_object_or_404(Tournament, pk=pk, publication__in=PUBLIC)
    cats = list(t.stable_categories.filter(is_active=True))
    svcs = list(t.services.filter(is_active=True))
    horses = list(_my_horses(request.user))
    preview, errors = None, []
    form = {"category": request.POST.get("category") or request.GET.get("category", ""), "horse": request.POST.get("horse", ""),
            "arrive": request.POST.get("arrive", ""), "depart": request.POST.get("depart", ""), "required": request.POST.get("required") == "on",
            "contact_phone": request.POST.get("contact_phone", ""), "comment": request.POST.get("comment", ""), "key": request.POST.get("key") or uuid.uuid4().hex}
    if request.method == "POST":
        try:
            c = next((x for x in cats if str(x.pk) == form["category"]), None)
            h = next((x for x in horses if str(x.pk) == form["horse"]), None)
            if not c:
                raise ValueError("Выберите категорию денника.")
            if not h:
                raise ValueError("Выберите свою лошадь.")
            arrive, depart = _date(form["arrive"]), _date(form["depart"])
            services.check_period(c, arrive, depart)
            phone = ""
            if form["contact_phone"].strip():
                phone = normalize_phone(form["contact_phone"])
                if not phone:
                    raise ValueError("Телефон: мобильный номер России, Казахстана или Беларуси.")
            if request.POST.get("confirm") == "1":
                services.book(request.user, c.pk, h, arrive, depart, form["key"], form["required"], phone, form["comment"])
                messages.success(request, "Бронь отправлена. Конюшня подтвердит её и назначит номер денника.")
                return redirect("stables:my")
            free = services.free_for(c, arrive, depart)
            if free < 1:
                raise ValueError("На эти даты свободных денников этой категории нет.")
            preview = {"c": c, "h": h, "arrive": arrive, "depart": depart, "nights": (depart - arrive).days, "free": free}
        except services.Conflict as e:
            errors.append(str(e))
        except ValueError as e:
            errors.append(str(e))
    for c in cats:
        c.free_total = services.free_for(c, c.available_from, c.available_to)
    return render(request, "stables/book.html", {"t": t, "cats": cats, "svcs": svcs, "horses": horses, "form": form, "preview": preview,
                                                 "errors": errors, "open": services.booking_open(t), "okey": uuid.uuid4().hex})


@login_required
@require_POST
def order(request, pk):
    t = get_object_or_404(Tournament, pk=pk, publication__in=PUBLIC)
    s = get_object_or_404(Service, pk=request.POST.get("service"), tournament=t)
    horse = None
    if request.POST.get("horse"):
        horse = get_object_or_404(Horse, pk=request.POST["horse"], manager=request.user)
    try:
        services.order(request.user, s.pk, request.POST.get("quantity"), request.POST.get("key") or uuid.uuid4().hex, horse=horse,
                       trailer=request.POST.get("trailer", ""), for_date=_date(request.POST.get("for_date")), comment=request.POST.get("comment", ""))
        messages.success(request, f"Заказ «{s.name}» оформлен. Оплата — в кассе турнира.")
        return redirect("stables:my")
    except (ValueError, services.Conflict) as e:
        messages.error(request, str(e))
    return redirect(reverse("stables:book", args=[t.pk]) + "#services")


@login_required
def my(request):
    bookings = Booking.objects.filter(requested_by=request.user).select_related("tournament", "category").order_by("-arrive")
    orders = ServiceOrder.objects.filter(ordered_by=request.user).select_related("tournament", "service").order_by("-created_at")
    return render(request, "stables/my.html", {"bookings": bookings, "orders": orders})


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
    return redirect(nxt if nxt.startswith("/") else reverse("stables:my"))


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
        state = request.GET.get("state", "pending")
        qs = Booking.objects.filter(tournament=t).select_related("category", "requested_by", "horse")
        if state in Booking.State.values:
            qs = qs.filter(state=state)
        ctx.update({"bookings": qs, "state": state, "states": Booking.State.choices,
                    "pending": Booking.objects.filter(tournament=t, state=Booking.State.PENDING).count()})
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
