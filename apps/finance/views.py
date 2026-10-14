import datetime
import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core import xlsx
from apps.tournaments.models import Tournament
from apps.tournaments.services import can_manage

from . import services
from .models import Charge, Payment, Refund

User = get_user_model()


def _tournament(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    services.require_cash(request.user, t)
    return t


def _date(raw):
    try:
        return datetime.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _charges(t, **kw):
    return Charge.objects.filter(tournament=t, **kw).select_related("payer", "entry").prefetch_related("adjustments", "allocations__payment", "refunds")


@login_required
def cash(request, pk):
    t = _tournament(request, pk)
    q = request.GET.get("q", "").strip()
    direction = request.GET.get("direction", "")
    only = request.GET.get("only", "")
    qs = _charges(t)
    if direction in Charge.Direction.values:
        qs = qs.filter(direction=direction)
    charges = list(qs)
    by_dir = {d: services.totals([c for c in charges if c.direction == d]) for d in Charge.Direction.values}
    payers = {}
    for c in charges:
        payers.setdefault(c.payer_id, {"user": c.payer, "charges": []})["charges"].append(c)
    rows = []
    for pid, d in payers.items():
        tt = services.totals(d["charges"])
        hay = " ".join([d["user"].full_name] + [c.subtitle for c in d["charges"]]).lower()
        if q and q.lower() not in hay:
            continue
        if only == "debt" and not tt["debt"]:
            continue
        if only == "refund" and not (tt["overpaid"] or tt["refund_pending"]):
            continue
        rows.append({"user": d["user"], "t": tt, "count": len(d["charges"]), "cancelled": sum(1 for c in d["charges"] if c.source_cancelled and c.balance > 0)})
    rows.sort(key=lambda r: (-r["t"]["debt"], r["user"].full_name))
    payments = Payment.objects.filter(tournament=t, voided_at__isnull=True)
    methods = {m: sum(p.amount_kopecks for p in payments if p.method == m) for m in Payment.Method.values}
    return render(request, "finance/cash.html", {
        "t": t, "rows": rows, "total": services.totals(charges), "by_dir": [(Charge.Direction(d).label, v) for d, v in by_dir.items()],
        "methods": [(Payment.Method(m).label, v) for m, v in methods.items()],
        "open_refunds": Refund.objects.filter(charge__tournament=t, state=Refund.State.OPEN).count(),
        "f": {"q": q, "direction": direction, "only": only}, "directions": Charge.Direction.choices, "manage": can_manage(request.user, t),
    })


@login_required
def payer(request, pk, user_id):
    t = _tournament(request, pk)
    u = get_object_or_404(User, pk=user_id)
    charges = list(_charges(t, payer=u))
    if not charges:
        raise Http404
    payments = Payment.objects.filter(tournament=t, payer=u).prefetch_related("allocations__charge").select_related("recorded_by", "voided_by")
    return render(request, "finance/payer.html", {
        "t": t, "payer": u, "charges": charges, "total": services.totals(charges), "payments": payments,
        "methods": Payment.Method.choices, "today": timezone.localdate(), "key": uuid.uuid4().hex,
        "refunds": Refund.objects.filter(charge__in=charges).select_related("charge", "created_by", "done_by"),
    })


@login_required
def refunds(request, pk):
    t = _tournament(request, pk)
    state = request.GET.get("state", "open")
    qs = Refund.objects.filter(charge__tournament=t).select_related("charge__payer", "created_by", "done_by")
    if state in Refund.State.values:
        qs = qs.filter(state=state)
    return render(request, "finance/refunds.html", {"t": t, "refunds": qs, "state": state, "states": Refund.State.choices,
                                                    "methods": Payment.Method.choices, "today": timezone.localdate()})


@login_required
@require_POST
def action(request, pk, action):
    t = _tournament(request, pk)
    nxt = request.POST.get("next", "")
    back = nxt if nxt.startswith("/") else reverse("finance:cash", args=[t.pk])
    try:
        if action == "pay":
            payer_id = int(request.POST["payer"])
            p = services.record_payment(
                request.user, t, payer_id, services.parse_rub(request.POST.get("amount")), request.POST.get("method", ""),
                _date(request.POST.get("paid_on")), [int(x) for x in request.POST.getlist("charges")],
                request.POST.get("key") or uuid.uuid4().hex, request.POST.get("note", ""),
            )
            messages.success(request, f"Оплата {services.rub(p.amount_kopecks)} отмечена.")
        elif action == "void":
            services.void_payment(request.user, int(request.POST["payment"]), request.POST.get("reason", ""))
            messages.success(request, "Отметка об оплате аннулирована.")
        elif action == "adjust":
            services.adjust(request.user, int(request.POST["charge"]), services.parse_rub(request.POST.get("amount")), request.POST.get("reason", ""))
            messages.success(request, "Корректировка сохранена.")
        elif action == "write_off":
            c = get_object_or_404(Charge, pk=request.POST.get("charge"), tournament=t)
            services.adjust(request.user, c.pk, c.net_paid if c.net_paid > 0 else 0, request.POST.get("reason") or "Списание остатка по отменённой позиции")
            messages.success(request, "Остаток списан.")
        elif action == "refund":
            services.create_refund(request.user, int(request.POST["charge"]), services.parse_rub(request.POST.get("amount")), request.POST.get("reason", ""))
            messages.success(request, "Задача возврата создана.")
        elif action == "refund_done":
            services.complete_refund(request.user, int(request.POST["refund"]), request.POST.get("method", ""), _date(request.POST.get("done_on")))
            messages.success(request, "Возврат отмечен как выданный.")
        elif action == "refund_cancel":
            services.cancel_refund(request.user, int(request.POST["refund"]), request.POST.get("reason", ""))
            messages.success(request, "Задача возврата отменена.")
        else:
            raise Http404
    except (ValueError, KeyError) as e:
        messages.error(request, str(e) if str(e) and not isinstance(e, KeyError) else "Проверьте данные формы.")
    except (Charge.DoesNotExist, Payment.DoesNotExist, Refund.DoesNotExist):
        raise Http404
    return redirect(back)


@login_required
def export(request, pk):
    t = _tournament(request, pk)
    rows = [["Плательщик", "Направление", "Позиция", "Спортсмен / лошадь", "Начислено", "Оплачено", "Возвращено", "Остаток", "Отметка"]]
    for c in _charges(t).order_by("payer__last_name", "created_at"):
        rows.append([c.payer.full_name, c.get_direction_display(), c.title, c.subtitle, c.amount / 100, c.paid / 100, c.refunded / 100,
                     c.balance / 100, ("Отменено: " + c.source_note) if c.source_cancelled else ""])
    rows += [[], ["Оплаты"], ["Дата", "Плательщик", "Сумма", "Способ", "Отметил", "Примечание", "Аннулировано"]]
    for p in Payment.objects.filter(tournament=t).select_related("payer", "recorded_by").order_by("paid_on", "id"):
        rows.append([p.paid_on.strftime("%d.%m.%Y"), p.payer.full_name, p.amount_kopecks / 100, p.get_method_display(), p.recorded_by.full_name, p.note,
                     p.void_reason if p.is_void else ""])
    resp = HttpResponse(xlsx.build("Касса", rows), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="kassa-{t.pk}.xlsx"'
    return resp


@login_required
def my(request):
    """Начисления представителя: по турнирам, с оплатами и возвратами (FIN-03)."""
    qs = Charge.objects.filter(payer=request.user).select_related("tournament").prefetch_related("adjustments", "allocations__payment", "refunds")
    tid = request.GET.get("t")
    if tid and tid.isdigit():
        qs = qs.filter(tournament_id=int(tid))
    groups = {}
    for c in qs.order_by("-tournament__starts_on", "created_at"):
        groups.setdefault(c.tournament_id, {"t": c.tournament, "charges": []})["charges"].append(c)
    out = []
    for g in groups.values():
        g["total"] = services.totals(g["charges"])
        g["payments"] = Payment.objects.filter(tournament=g["t"], payer=request.user)
        out.append(g)
    return render(request, "finance/my.html", {"groups": out, "filtered": bool(tid)})
