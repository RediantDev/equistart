import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.phone import normalize_phone
from apps.profiles.models import Athlete, Horse
from apps.profiles import services as pservices
from apps.profiles.services import athletes_for_entry
from apps.tournaments.models import Tournament, TournamentClass
from apps.tournaments import services as tservices
from apps.tournaments.services import roles_of

from . import services
from .models import Application, ChangeProposal, Entry
from .services import Conflict, Line

# ---------------- черновик заявки (в базе) ----------------

def _cart(request, t, late):
    from .models import ApplicationDraft

    d = ApplicationDraft.objects.filter(user=request.user, tournament=t, late=late).first()
    cart = dict(d.data) if d else {}
    if "pairs" not in cart:
        cart = {"pairs": [], "lines": [], "horses": [], "key": uuid.uuid4().hex}
    return cart


def _save_cart(request, t, late, cart):
    from .models import ApplicationDraft

    if not cart["pairs"] and not cart["lines"] and not cart["horses"]:
        ApplicationDraft.objects.filter(user=request.user, tournament=t, late=late).delete()
        return
    ApplicationDraft.objects.update_or_create(user=request.user, tournament=t, late=late, defaults={"data": cart})


def _drop_cart(request, t, late):
    from .models import ApplicationDraft

    ApplicationDraft.objects.filter(user=request.user, tournament=t, late=late).delete()


def _load_lines(cart):
    ids_a = {r["a"] for r in cart["lines"]}
    ids_h = {r["h"] for r in cart["lines"]}
    ids_c = {r["c"] for r in cart["lines"]}
    athletes = Athlete.objects.in_bulk(ids_a)
    horses = Horse.objects.in_bulk(ids_h)
    classes = TournamentClass.objects.select_related("program", "merged_into").in_bulk(ids_c)
    out = []
    for raw in cart["lines"]:
        a, h, c = athletes.get(raw["a"]), horses.get(raw["h"]), classes.get(raw["c"])
        if a and h and c:
            out.append(Line(a, h, c, raw.get("ht"), bool(raw.get("cf"))))
    return out


def _programs(t):
    return list(t.programs.all().prefetch_related("classes__merged_into").order_by("day", "start_time", "position", "id"))


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_choice(raw):
    """Значение выбора «зачёт|высота» из списка у маршрута."""
    if not raw:
        return None, None
    cid, _, ht = str(raw).partition("|")
    return _int(cid), _int(ht)


@never_cache  # «Назад» в браузере не показывает устаревшую форму без новой лошади
@login_required
@require_http_methods(["GET", "POST"])
def apply(request, pk):
    """APP-01…04: заявка строится из пар «всадник + лошадь»; у пары — выступления (маршрут/езда + ровно один зачёт).
    Раскрыта одна пара; маршруты для неё выбираются сразу списком и добавляются одним нажатием."""
    from apps.sport import rules

    t = get_object_or_404(Tournament, pk=pk)
    late = request.GET.get("late") == "1" or request.POST.get("late") == "1"
    reason = services.tournament_open_for(request.user, t, late)
    if reason:
        messages.error(request, reason)
        return redirect("tournaments:public", pk=t.pk)
    cart = _cart(request, t, late)
    athletes = list(athletes_for_entry(request.user))
    by_a = {a.pk: a for a in athletes}
    horse_ids = set(pservices.horses_for_entry(request.user).values_list("pk", flat=True)) | set(cart["horses"])
    horses = list(Horse.objects.filter(pk__in=horse_ids).order_by("name"))
    by_h = {h.pk: h for h in horses}
    programs = _programs(t)
    classes = {c.pk: c for p in programs for c in p.classes.all()}
    year = (t.pub_starts_on or t.starts_on).year
    errors = []
    pair_errors = []  # ошибки выбора зачётов показываются внутри карточки пары (её обновляет HTMX)
    open_pair = request.GET.get("open") or request.POST.get("open") or ""
    # Запрос HTMX из самой страницы (не hx-boost): сообщение не нужно — изменение видно в карточке пары
    partial = request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"

    def note(text):
        if not partial:
            messages.success(request, text)

    def back(pair=None):
        url = _apply_url(t, late)
        if pair:
            url += ("&" if "?" in url else "?") + f"open={pair[0]}-{pair[1]}#pair-{pair[0]}-{pair[1]}"
        return redirect(url)

    if request.method == "POST":
        action = request.POST.get("action") or ("pick" if request.POST.get("pick_horse") else "")
        if request.POST.get("remove") is not None:
            action = "remove"
        if action == "pick":
            hid = _int(request.POST.get("horse"))
            if hid and Horse.objects.filter(pk=hid, archived=False).exists() and hid not in cart["horses"]:
                cart["horses"].append(hid)
                _save_cart(request, t, late, cart)
                note("Лошадь добавлена в список выбора. Она видна только в этой заявке.")
            return back()
        if action == "pair_add":
            a, h = by_a.get(_int(request.POST.get("athlete"))), by_h.get(_int(request.POST.get("horse")))
            if not a or not h:
                errors.append("Выберите всадника и лошадь.")
            elif [a.pk, h.pk] in cart["pairs"]:
                errors.append("Такая пара уже есть в заявке — выберите для неё маршруты ниже.")
                open_pair = f"{a.pk}-{h.pk}"
            else:
                cart["pairs"].append([a.pk, h.pk])
                _save_cart(request, t, late, cart)
                return back((a.pk, h.pk))
        if action == "pair_remove":
            pair = [_int(request.POST.get("a")), _int(request.POST.get("h"))]
            if pair in cart["pairs"]:
                cart["pairs"].remove(pair)
                cart["lines"] = [r for r in cart["lines"] if [r["a"], r["h"]] != pair]
                _save_cart(request, t, late, cart)
            return back()
        if action == "remove":
            # Тест 5: строку убираем по ключу «пара + зачёт + высота», а не по номеру в списке —
            # номер устаревает, когда карточки пар обновляются по отдельности.
            raw_val = str(request.POST.get("remove") or request.POST.get("index") or "")
            raw = None
            if "|" in raw_val:
                cid, ht = _parse_choice(raw_val)
                key = (_int(request.POST.get("a")), _int(request.POST.get("h")), cid, ht)
                for r in cart["lines"]:
                    if (r["a"], r["h"], r["c"], r.get("ht")) == key:
                        raw = r
                        break
                if raw:
                    cart["lines"].remove(raw)
            else:
                try:
                    raw = cart["lines"].pop(int(raw_val))
                except (ValueError, IndexError):
                    raw = None
            _save_cart(request, t, late, cart)
            pair = (raw["a"], raw["h"]) if raw else (_int(request.POST.get("a")), _int(request.POST.get("h")))
            return back(pair if all(pair) else None)
        if action in ("add", "add_many"):
            a, h = by_a.get(_int(request.POST.get("a"))), by_h.get(_int(request.POST.get("h")))
            picks = []
            if action == "add":  # совместимость: один зачёт
                cid, ht = _int(request.POST.get("tclass")), _int(request.POST.get("height"))
                picks.append((cid, ht, request.POST.get("confirm") == "1"))
            else:
                for p in programs:
                    chosen = [v for v in request.POST.getlist(f"sel_{p.pk}") if v]
                    multi = p.heights and len(p.heights) > 1
                    if len(chosen) > 1 and not multi:
                        pair_errors.append(f"{p.name}: в одном маршруте пара выступает в одном зачёте — оставьте один.")
                        continue
                    for raw in chosen:  # тест 4.2: в маршруте с несколькими высотами — можно несколько высот
                        cid, ht = _parse_choice(raw)
                        if cid:
                            picks.append((cid, ht, request.POST.get(f"confirm_{p.pk}") == "1"))
            if not (a and h):
                errors.append("Пара не найдена — добавьте её заново.")
            elif not picks and not pair_errors:
                pair_errors.append("Выберите зачёт хотя бы в одном маршруте или езде.")
            else:
                added = []
                existing = {(r["a"], r["h"], r["c"], r.get("ht")) for r in cart["lines"]}
                for cid, ht, confirmed in picks:
                    c = classes.get(cid)
                    if not c:
                        pair_errors.append("Зачёт не найден — обновите страницу.")
                        continue
                    if not (c.program.heights and len(c.program.heights) > 1):
                        ht = None
                    elif c.height and ht is None:
                        ht = c.height
                    line = Line(a, h, c, ht, confirmed)
                    errs = services.check_line(request.user, t, line, late=late, allowed_athletes=set(by_a))
                    if line.key() in existing:
                        errs.append("уже есть в заявке")
                    if errs:
                        pair_errors.extend(f"{c.program.name}, {c.label}: {e}" for e in errs)
                        continue
                    cart["lines"].append({"a": a.pk, "h": h.pk, "c": c.pk, "ht": ht, "cf": confirmed})
                    existing.add(line.key())
                    added.append(f"{c.program.name} — {c.label}" + (f" · {ht} см" if ht and not c.height else ""))
                if added:
                    _save_cart(request, t, late, cart)
                    note(f"{a.public_name} / {h.name}: добавлено {len(added)} — " + "; ".join(added) + ".")
                if not errors and not pair_errors:
                    # тест 4.2: после «Добавить выбранные» выбор сворачивается, видна сводка пары
                    url = _apply_url(t, late)
                    return redirect(url + ("&" if "?" in url else "?") + f"open=-#pair-{a.pk}-{h.pk}")
                open_pair = f"{a.pk}-{h.pk}"
            if pair_errors and a and h:
                open_pair = f"{a.pk}-{h.pk}"
        if action == "submit":
            lines = _load_lines(cart)
            try:
                phone = normalize_phone(request.POST.get("contact_phone") or request.user.phone)
            except ValidationError as e:
                # тест первых пользователей: неверный телефон не подменяем молча телефоном из профиля
                errors.append("Телефон для связи: " + e.messages[0])
                phone = None
            try:
                if not phone:
                    raise ValueError(errors)
                app = services.submit(
                    request.user, t.pk, lines, cart["key"], contact_name=request.POST.get("contact_name", "").strip(),
                    contact_phone=phone, comment=request.POST.get("comment", "").strip(), late=late,
                )
            except ValueError as e:
                errors = e.args[0] if isinstance(e.args[0], list) else [str(e)]
            except Conflict as e:
                messages.error(request, str(e))
                _drop_cart(request, t, late)
                return redirect(_apply_url(t, late))
            else:
                _drop_cart(request, t, late)
                messages.success(request, f"Заявка №{app.pk} отправлена. Решение по каждому выступлению появится здесь и в уведомлениях.")
                return redirect("entries:application", pk=app.pk)

    lines = _load_lines(cart)
    pair_objs = []
    extra_h = Horse.objects.in_bulk({h for _, h in cart["pairs"]} - set(by_h))
    for i_a, i_h in cart["pairs"]:
        a, h = by_a.get(i_a), by_h.get(i_h) or extra_h.get(i_h)
        if a and h:
            pair_objs.append((a, h))
    if not open_pair and pair_objs:
        # по умолчанию раскрыта первая пара без выступлений, иначе — последняя добавленная
        empty = [(a, h) for a, h in pair_objs if not any(l.athlete.pk == a.pk and l.horse.pk == h.pk for l in lines)]
        a, h = (empty or pair_objs[-1:])[0]
        open_pair = f"{a.pk}-{h.pk}"
    pairs = []
    for a, h in pair_objs:
        mine = [(i, l) for i, l in enumerate(lines) if l.athlete.pk == a.pk and l.horse.pk == h.pk]
        mine_keys = {(l.tclass.pk, l.height) for _, l in mine}
        is_open = open_pair == f"{a.pk}-{h.pk}"
        progs = []
        # Сводка свёрнутой пары: маршрут → выбранные зачёты
        by_p = {}
        for _, l in mine:
            by_p.setdefault(l.tclass.program_id, {"p": l.tclass.program, "lines": []})["lines"].append(l)
        summary = [by_p[p.pk] for p in programs if p.pk in by_p] + [g for pid, g in by_p.items() if pid not in {p.pk for p in programs}]
        if is_open:
            for p in programs:
                opts = []
                multi = p.heights and len(p.heights) > 1
                for c in p.classes.all():
                    if c.merged_into_id:
                        continue
                    hts = [c.height] if (multi and c.height) else (p.heights if multi else [None])
                    for ht in hts:
                        why = services.class_reasons(a, h, c, year, ht, True)
                        why = [w for w in why if w != "выберите высоту"]
                        label = c.label + (f" · {ht} см" if ht and not c.height else "")
                        opts.append({"value": f"{c.pk}|{ht}" if ht else str(c.pk), "label": label, "why": why, "conditions": c.conditions,
                                     "added": (c.pk, ht) in mine_keys, "remove": f"{c.pk}|{ht if ht else ''}"})
                shown = {(o["value"].partition("|")[0], o["value"].partition("|")[2]) for o in opts if o["added"]}
                # строки, которых нет среди вариантов (например, зачёт объединили), — отдельными плашками
                added = [(f"{l.tclass.pk}|{l.height or ''}", l) for _, l in mine if l.tclass.program_id == p.pk
                         and (str(l.tclass.pk), str(l.height) if l.height else "") not in shown]
                # Тест 5: разряд указан в профиле — условие «не выполнявшие норматив» решается по нему, без галочки
                progs.append({"p": p, "opts": opts, "added": added,
                              "never": not a.rank and any(c.rank_rule == "never" and c.rank for c in p.classes.all() if not c.merged_into_id),
                              "has_ok": any(o["added"] or not o["why"] for o in opts),
                              "need_birth": any("нужна дата рождения всадника" in (o["why"] or []) for o in opts)})
        pairs.append({"a": a, "h": h, "programs": progs, "count": len(mine), "open": is_open, "mine": mine, "summary": summary,
                      "errors": pair_errors if is_open else []})
    warnings = rules.entry_warnings(t, lines)
    return render(
        request, "entries/apply.html",
        {
            "t": t, "s": t.snapshot, "late": late, "pairs": pairs, "lines": lines, "errors": errors, "warnings": warnings,
            "athletes": athletes, "horses": horses, "key": cart["key"], "apply_url": _apply_url(t, late),
            # тест первых пользователей: единственный всадник/лошадь и только что созданные выбраны сразу
            "sel_a": _int(request.GET.get("athlete")) or (athletes[0].pk if len(athletes) == 1 else None),
            "sel_h": _int(request.GET.get("horse")) or (horses[0].pk if len(horses) == 1 else None),
        },
    )


def _class_choices(t):
    classes = TournamentClass.objects.filter(program__tournament=t, merged_into__isnull=True).select_related("program").order_by(
        "program__day", "program__start_time", "program__position", "height", "position")
    groups = {}
    for c in classes:
        label = f"{c.program.day:%d.%m} · {c.program.name}"
        groups.setdefault(label, []).append((c.pk, f"{c.label}{' · ' + c.conditions if c.conditions else ''}"))
    return list(groups.items()), {c.pk: c for c in classes}


def _apply_url(t, late):
    return reverse("entries:apply", args=[t.pk]) + ("?late=1" if late else "")


# ---------------- мои заявки ----------------

@login_required
def my_applications(request):
    """Тест 5.1: «Мои заявки» объединены с «Мои соревнования» — старая ссылка ведёт туда (поиск переносится)."""
    url = reverse("entries:competitions")
    q = request.GET.get("q", "").strip()
    if q:
        from urllib.parse import urlencode

        url += "?" + urlencode({"q": q[:100]})
    return redirect(url)


@login_required
def my_starts(request):
    """Тест 5.1: «Мои выступления» — часть «Мои соревнования» (связанный спортсмен видит там только свои позиции)."""
    return redirect("entries:competitions")


@login_required
def application(request, pk):
    app = get_object_or_404(Application.objects.select_related("tournament", "submitted_by"), pk=pk)
    if not services.can_view_application(request.user, app):
        raise PermissionDenied
    owner = app.submitted_by_id == request.user.pk
    judge = services.can_judge(request.user, app.tournament)
    entries = list(app.entries.select_related("tclass", "athlete", "horse", "program").prefetch_related("proposals")
                   .order_by("day", "program__start_time", "program__position", "program_id", "athlete_snapshot__name", "pk"))
    for e in entries:
        e.pending_proposal = next((p for p in e.proposals.all() if p.state == ChangeProposal.State.PENDING), None)
        e.cancellable = owner and e.is_active and not e.is_started
    # Тест 5.1: выступления по дням соревнований; макет 28.10 (R2_03B): строка «Всадник — Лошадь», внутри дня — по времени
    days = {}
    for e in entries:
        days.setdefault(e.day, []).append(e)
    groups = [{"day": d, "entries": items, "count": len(items)} for d, items in days.items()]
    can_edit = any(e.cancellable for e in entries)
    # Режим правки (макет R2_03B): отметить выступления к отмене и решить судьбу денников/услуг — одной формой.
    # Работает и без JS: «Изменить заявку» — ссылка на ?edit=1, переключатели — label + checkbox/radio.
    edit = can_edit and request.GET.get("edit") == "1"
    bookings, orders = (_edit_stables(request.user, app, entries) if edit else ([], []))
    return render(request, "entries/application.html", {
        "app": app, "t": app.tournament, "entries": entries, "groups": groups, "owner": owner, "judge": judge,
        "summary": services.summarize(entries), "show_contacts": owner or judge,
        "can_edit": can_edit, "edit": edit, "bookings": bookings, "orders": orders,
    })


def _stables_for_horses(user, app, horses):
    """Действующие брони денников и заказы услуг пользователя на этом турнире для указанных лошадей."""
    from apps.stables.models import Booking, ServiceOrder

    bookings = list(Booking.objects.filter(tournament=app.tournament_id, horse_id__in=horses, requested_by=user, state__in=Booking.HOLDING)
                    .select_related("category").order_by("arrive", "pk"))
    orders = list(ServiceOrder.objects.filter(tournament=app.tournament_id, horse_id__in=horses, ordered_by=user,
                                              state=ServiceOrder.State.ORDERED, delivered=0).select_related("service").order_by("for_date", "pk"))
    return bookings, orders


def _edit_stables(user, app, entries):
    """Режим правки: денники и услуги лошадей, у которых на турнире не останется выступлений, если отменить
    их выступления в этой заявке. У каждой записи — `need`: какие выступления должны быть отмечены, чтобы
    строка показалась (пусто — у лошади выступлений уже нет). Окончательно проверяет `_cancel_stables`."""
    cancellable = {e.pk for e in entries if e.cancellable}
    horses = {e.horse_id for e in entries}
    blocked = set(Entry.objects.filter(tournament=app.tournament_id, horse_id__in=horses, decision__in=Entry.ACTIVE)
                  .exclude(pk__in=cancellable).values_list("horse_id", flat=True))
    bookings, orders = _stables_for_horses(user, app, horses - blocked)
    need = {}
    for e in entries:
        if e.pk in cancellable:
            need.setdefault(e.horse_id, []).append(e.pk)
    for x in bookings + orders:
        x.need = ",".join(str(i) for i in need.get(x.horse_id, []))
    return bookings, orders


def _related_stables(user, app, entries):
    """Брони денников и заказы услуг пользователя на лошадей, у которых после отмены не останется
    действующих выступлений на этом турнире (тест 5.1): их предлагаем отменить вместе с выступлениями."""
    cancelling = {e.pk for e in entries}
    horses = {e.horse_id for e in entries}
    still = set(Entry.objects.filter(tournament=app.tournament_id, horse_id__in=horses, decision__in=Entry.ACTIVE)
                .exclude(pk__in=cancelling).values_list("horse_id", flat=True))
    return _stables_for_horses(user, app, horses - still)


def _cancel_stables(request, app, booking_ids, order_ids):
    """Отмена выбранных броней и заказов теми же правилами, что и на экране денников: только свои,
    только для лошадей без действующих выступлений на турнире (проверяется заново после отмены выступлений)."""
    from apps.stables import services as sservices
    from apps.stables.models import Booking, ServiceOrder

    if not booking_ids and not order_ids:
        return
    horses_left = set(Entry.objects.filter(tournament=app.tournament_id, decision__in=Entry.ACTIVE).values_list("horse_id", flat=True))
    done, failed = [], []
    for b in Booking.objects.filter(pk__in=booking_ids, tournament=app.tournament_id, requested_by=request.user).exclude(horse_id__in=horses_left):
        try:
            sservices.cancel_booking(request.user, b.pk, "Отменено вместе с выступлениями")
            done.append(f"денник «{b.horse_name}»")
        except sservices.Conflict:
            pass  # уже отменена — повтор запроса ничего не меняет
        except ValueError as ex:
            failed.append(f"денник «{b.horse_name}»: {ex}")
    for o in ServiceOrder.objects.filter(pk__in=order_ids, tournament=app.tournament_id, ordered_by=request.user).exclude(horse_id__in=horses_left).select_related("service"):
        try:
            sservices.cancel_order(request.user, o.pk, "Отменено вместе с выступлениями")
            done.append(f"{o.service.name} × {o.quantity} ({o.recipient_label})")
        except sservices.Conflict:
            pass
        except ValueError as ex:
            failed.append(f"{o.service.name} ({o.recipient_label}): {ex}")
    if done:
        messages.success(request, "Отменено также: " + ", ".join(done) + ".")
    for text in failed:
        messages.error(request, "Не отменено — " + text)


@login_required
@require_http_methods(["GET", "POST"])
def application_cancel(request, pk):
    app = get_object_or_404(Application, pk=pk)
    if request.method == "GET":
        # Тест 5.1: экран подтверждения открыт POST-запросом; «Назад», обновление страницы или восстановление
        # вкладки в телефоне повторяют адрес GET-запросом — раньше это был пустой ответ 405 (белый экран).
        return redirect("entries:application", pk=app.pk)
    if app.submitted_by_id != request.user.pk:
        raise PermissionDenied
    ids = [i for i in request.POST.getlist("entry") if i.isdigit()]
    if request.POST.get("edit") == "1":
        return _cancel_from_edit(request, app, ids)
    if request.POST.get("confirm") != "1":
        entries = list(app.entries.filter(pk__in=ids, decision__in=Entry.ACTIVE).order_by("day", "athlete_snapshot__name", "pk"))
        if not entries:
            messages.error(request, "Выберите выступления для отмены.")
            return redirect("entries:application", pk=app.pk)
        bookings, orders = _related_stables(request.user, app, entries)
        return render(request, "entries/cancel_confirm.html", {"app": app, "entries": entries, "bookings": bookings, "orders": orders})
    items = [(int(i), request.POST.get(f"rev-{i}")) for i in ids]
    results = services.cancel_by_representative(request.user, app.pk, items)
    _report(request, results)
    _cancel_stables(request, app, [int(i) for i in request.POST.getlist("booking") if i.isdigit()],
                    [int(i) for i in request.POST.getlist("order") if i.isdigit()])
    return redirect("entries:application", pk=app.pk)


def _cancel_from_edit(request, app, ids):
    """Отправка режима правки (макет R2_03B): отмеченные выступления и выбранные «Отменить» денники/услуги —
    одним запросом. Позиции берутся только из этой заявки; уже отменённые пропускаются молча, поэтому
    повтор запроса ничего не меняет и не пугает ошибкой. Денник/услуга отменяются, только если выбрано
    «Отменить» и у лошади не осталось выступлений (проверяет `_cancel_stables`)."""
    active = dict(app.entries.filter(pk__in=ids, decision__in=Entry.ACTIVE).values_list("pk", "revision"))
    items = [(pk, request.POST.get(f"rev-{pk}") or rev) for pk, rev in active.items()]
    booking_ids = [int(k[8:]) for k, v in request.POST.items() if k.startswith("booking-") and k[8:].isdigit() and v == "cancel"]
    order_ids = [int(k[6:]) for k, v in request.POST.items() if k.startswith("order-") and k[6:].isdigit() and v == "cancel"]
    if not items and not booking_ids and not order_ids:
        if not ids:
            messages.error(request, "Отметьте выступления, которые нужно отменить.")
            return redirect(reverse("entries:application", args=[app.pk]) + "?edit=1")
        return redirect("entries:application", pk=app.pk)
    if items:
        _report(request, services.cancel_by_representative(request.user, app.pk, items))
    _cancel_stables(request, app, booking_ids, order_ids)
    return redirect("entries:application", pk=app.pk)


@login_required
@require_POST
def application_contacts(request, pk):
    app = get_object_or_404(Application, pk=pk)
    if app.submitted_by_id != request.user.pk:
        raise PermissionDenied
    try:
        app.contact_phone = normalize_phone(request.POST.get("contact_phone", ""))
    except Exception:
        messages.error(request, "Проверьте номер телефона.")
        return redirect("entries:application", pk=app.pk)
    app.contact_name = request.POST.get("contact_name", "").strip()[:200]
    app.comment = request.POST.get("comment", "").strip()
    app.revision += 1
    app.save()
    from apps.core.models import audit

    audit(request.user, "application.contacts", app)
    messages.success(request, "Контакты и комментарий обновлены.")
    return redirect("entries:application", pk=app.pk)


@login_required
@require_http_methods(["GET", "POST"])
def propose(request, pk):
    e = get_object_or_404(Entry.objects.select_related("application", "tournament", "tclass"), pk=pk)
    if e.application.submitted_by_id != request.user.pk:
        raise PermissionDenied
    athletes = list(athletes_for_entry(request.user))
    horse_ids = set(pservices.horses_for_entry(request.user).values_list("pk", flat=True)) | {e.horse_id}
    horses = list(Horse.objects.filter(pk__in=horse_ids).order_by("name"))
    class_groups, classes = _class_choices(e.tournament)
    if request.method == "POST":
        a = next((x for x in athletes if str(x.pk) == request.POST.get("athlete")), None)
        h = next((x for x in horses if str(x.pk) == request.POST.get("horse")), None)
        c = classes.get(int(request.POST["tclass"])) if request.POST.get("tclass", "").isdigit() else None
        try:
            services.propose(request.user, e.pk, athlete=a, horse=h, tclass=c)
        except ValueError as ex:
            messages.error(request, str(ex))
        else:
            messages.success(request, "Предложение отправлено судье. До решения действуют прежние сведения.")
            return redirect("entries:application", pk=e.application_id)
    return render(request, "entries/propose.html", {"e": e, "athletes": athletes, "horses": horses, "class_groups": class_groups})


@login_required
@require_POST
def proposal_decide(request, pk):
    p = get_object_or_404(ChangeProposal.objects.select_related("entry"), pk=pk)
    try:
        services.decide_proposal(request.user, p.pk, request.POST.get("decision") == "accept", request.POST.get("reason", ""))
    except (ValueError, Conflict) as ex:
        messages.error(request, str(ex))
    else:
        messages.success(request, "Решение по предложению сохранено.")
    return redirect(request.POST.get("next") or reverse("entries:application", args=[p.entry.application_id]))


# ---------------- рассмотрение (судья / организатор) ----------------

def _manage_qs(t, f):
    qs = Entry.objects.filter(tournament=t)
    if f.get("q"):
        q = f["q"]
        qs = qs.filter(Q(athlete_snapshot__name__icontains=q) | Q(horse_snapshot__name__icontains=q) | Q(application__contact_name__icontains=q))
    if f.get("decision"):
        qs = qs.filter(decision=f["decision"])
    if str(f.get("program", "")).isdigit():
        qs = qs.filter(program_id=int(f["program"]))
    return qs


PAGE_SIZE = 50


TABS = [("pending", "На рассмотрении"), ("accepted", "Принятые"), ("rejected", "Отклонённые"), ("cancelled", "Отменённые")]


@login_required
def manage(request, pk):
    """Экран заявок судьи (тест 4): вкладки по решению, внутри — маршрут → зачёт → пары; принять / отклонить в строке."""
    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    from django.db.models import Count

    counts = dict(Entry.objects.filter(tournament=t).order_by().values("decision").annotate(n=Count("id")).values_list("decision", "n"))
    tab = request.GET.get("tab") or request.GET.get("decision") or ("pending" if counts.get("pending") else "accepted")
    if tab not in dict(TABS):
        tab = "pending"
    f = {"q": request.GET.get("q", ""), "program": request.GET.get("program", ""), "decision": tab}
    qs = (_manage_qs(t, f).select_related("application", "tclass", "program").prefetch_related("proposals")
          .order_by("day", "program__start_time", "program__position", "program_id", "tclass__position", "tclass_id", "athlete_snapshot__name", "pk"))
    groups, by_prog = [], {}
    for e in qs:
        e.pending_proposal = next((p for p in e.proposals.all() if p.state == ChangeProposal.State.PENDING), None)
        g = by_prog.get(e.program_id)
        if g is None:
            g = by_prog[e.program_id] = {"p": e.program, "classes": [], "by_class": {}, "n": 0}
            groups.append(g)
        c = g["by_class"].get(e.tclass_id)
        if c is None:
            c = g["by_class"][e.tclass_id] = {"c": e.tclass, "entries": []}
            g["classes"].append(c)
        c["entries"].append(e)
        g["n"] += 1
    params = "&".join(f"{k}={v}" for k, v in (("q", f["q"]), ("program", f["program"])) if v)
    return render(request, "entries/manage.html", {
        "t": t, "groups": groups, "tab": tab, "tabs": [(k, l, counts.get(k, 0)) for k, l in TABS], "f": f, "params": params,
        "programs": t.programs.all().order_by("day", "start_time", "position"), "total": sum(g["n"] for g in groups),
        "is_manager": tservices.can_manage(request.user, t),
    })


@login_required
@require_http_methods(["GET", "POST"])
def manage_decide(request, pk):
    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    if request.method == "GET":  # «Назад» с экрана подтверждения — не пустой ответ 405, а список заявок
        return redirect("entries:manage", pk=t.pk)
    action = request.POST.get("action", "")
    back = request.POST.get("next") or reverse("entries:manage", args=[t.pk])
    if request.POST.get("scope") == "filter":
        # «Применить ко всем по фильтру»: состав берётся заново на сервере, без передачи сотен полей
        f = {k: request.POST.get(f"f_{k}", "") for k in ("q", "decision", "program")}
        qs = _manage_qs(t, f).exclude(decision=Entry.Decision.CANCELLED)
        ids = [str(i) for i in qs.values_list("pk", flat=True)]
        revs = dict(qs.values_list("pk", "revision"))
    elif request.POST.get("items"):
        ids = [c.partition(":")[0] for c in request.POST["items"].split(",") if c.partition(":")[0].isdigit()]
        revs = None
    else:
        ids = [i for i in request.POST.getlist("entry") if i.isdigit()]
        revs = None
    if not ids:
        messages.error(request, "Выберите строки — отметьте их галочкой или нажмите «Выбрать все по фильтру».")
        return redirect(back)
    if request.POST.get("confirm") != "1":
        entries = list(Entry.objects.filter(tournament=t, pk__in=ids).order_by("day", "program_id", "pk"))
        # состав и версии строк уходят одним полем «id:версия,…» — без лимита на число полей формы
        items = ",".join(f"{e.pk}:{request.POST.get(f'rev-{e.pk}') or (revs or {}).get(e.pk) or e.revision}" for e in entries)
        return render(request, "entries/decide_confirm.html", {"t": t, "entries": entries[:100], "more": max(0, len(entries) - 100),
                                                              "count": len(entries), "items": items, "action": action,
                                                              "label": services.ACTIONS.get(action, ""), "next": back})
    try:
        if request.POST.get("items"):
            items = []
            for chunk in request.POST["items"].split(","):
                i, _, rev = chunk.partition(":")
                if i.isdigit():
                    items.append((int(i), rev))
        else:
            items = [(int(i), request.POST.get(f"rev-{i}") or (revs or {}).get(int(i))) for i in ids]
        results = services.decide(request.user, t.pk, items, action, request.POST.get("reason", ""))
    except ValueError as ex:
        messages.error(request, str(ex))
        return redirect(back)
    _report(request, results)
    return redirect(back)


def _report(request, results):
    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]
    if len(results) == 1 and ok:
        e = Entry.objects.filter(pk=ok[0].entry_id).first()
        who = f": {e.athlete_snapshot.get('name')} / {e.horse_snapshot.get('name')} — {e.program_name} · {e.class_name}" if e else ""
        messages.success(request, f"{ok[0].text}{who}.")
    elif ok:
        messages.success(request, f"Выполнено для {len(ok)} из {len(results)}.")
    reasons = {}
    for r in bad:
        reasons.setdefault(r.text, []).append(r.entry_id)
    for text, ids in reasons.items():
        tail = ", ".join(f"#{i}" for i in ids[:5]) + (f" и ещё {len(ids) - 5}" if len(ids) > 5 else "")
        messages.error(request, f"Не выполнено для {len(ids)}: {text} ({tail}).")


# ---------------- быстрое добавление пары судьёй (тест 4) ----------------

@login_required
def quick_add(request, pk):
    from .forms import QuickAddForm

    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    nxt = request.GET.get("next") or request.POST.get("next") or ""
    nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else ""
    program = request.GET.get("program", "")
    initial = {}
    if program.isdigit():
        initial["classes"] = [str(c) for c in TournamentClass.objects.filter(program_id=int(program), program__tournament=t,
                                                                            merged_into__isnull=True).values_list("pk", flat=True)[:1]]
    form = QuickAddForm(t, request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            entries = services.quick_add(request.user, t.pk, form.cleaned_data)
        except ValueError as e:
            messages.error(request, str(e))
        else:
            e = entries[0]
            messages.success(request, f"Добавлено: {e.athlete_snapshot.get('name')} / {e.horse_snapshot.get('name')} — "
                                      + ", ".join(f"{x.program_name} · {x.class_name}" for x in entries) + ". Выступления приняты.")
            if "more" in request.POST:
                return redirect(reverse("entries:quick_add", args=[t.pk]) + (f"?next={nxt}" if nxt else ""))
            return redirect(nxt or reverse("entries:manage", args=[t.pk]) + "?tab=accepted")
    groups = {}
    for k, c in form.class_objs.items():
        groups.setdefault(c.program, []).append((k, c))
    selected = form["classes"].value() or []
    return render(request, "entries/quick_add.html", {"t": t, "form": form, "groups": list(groups.items()), "selected": [str(x) for x in selected], "next": nxt})


@login_required
def quick_search(request, pk):
    """Поиск спортсмена или лошади по всей базе для быстрого добавления (только судьям турнира)."""
    from django.http import JsonResponse

    from apps.profiles.models import Athlete, Horse

    t = get_object_or_404(Tournament, pk=pk)
    services.require_judge(request.user, t)
    q = (request.GET.get("q") or "").strip()
    if len(q) < 2:
        return JsonResponse({"items": []})
    words = q.split()
    if request.GET.get("kind") == "horse":
        qs = Horse.objects.filter(archived=False, name__icontains=q)[:10]
        items = [{"id": h.pk, "title": h.name, "sub": h.summary()} for h in qs]
    else:
        qs = Athlete.objects.filter(archived=False)
        for w in words:
            qs = qs.filter(Q(last_name__icontains=w) | Q(first_name__icontains=w))
        items = [{"id": a.pk, "title": a.full_name, "sub": " · ".join(x for x in (str(a.birth_year or ""), a.rank_short, a.club) if x)}
                 for a in qs.order_by("last_name", "first_name")[:10]]
    return JsonResponse({"items": items})


def _norm(text):
    """Строка для поиска на странице: нижний регистр, «ё» = «е»."""
    return " ".join(str(text or "").lower().replace("ё", "е").split())


@login_required
def my_competitions(request):
    """«Мои соревнования» (вариант А, решение 27.10.2026; тест 5.1): всё про старты в одном месте.

    Вкладки «Предстоящие» и «Прошедшие» — карточка на турнир: мои заявки, выступления, куда заявил тренер,
    брони денников и услуги по лошадям. Поиск и фильтр по решению — на этой же странице, без запросов.
    Связанный спортсмен видит в чужой заявке только свои выступления.
    """
    from django.utils import timezone

    from apps.stables.models import Booking, ServiceOrder

    tab = request.GET.get("tab", "upcoming")
    if tab not in ("upcoming", "past"):
        tab = "upcoming"  # тест 5.1: вкладка «Заявки» убрана — она повторяла «Предстоящие»
    today = timezone.localdate()
    user = request.user
    own = Entry.objects.filter(application__submitted_by=user)
    mine_as_athlete = Entry.objects.filter(athlete__linked_user=user).exclude(application__submitted_by=user)
    entries = list((own | mine_as_athlete).select_related("tournament__venue", "application", "program")
                   .order_by("day", "program__start_time", "program__position", "program_id", "athlete_snapshot__name", "pk"))
    bookings = list(Booking.objects.filter(requested_by=user).exclude(state=Booking.State.CANCELLED)
                    .select_related("tournament__venue", "category").order_by("arrive", "pk"))
    orders = list(ServiceOrder.objects.filter(ordered_by=user).exclude(state=ServiceOrder.State.CANCELLED)
                  .select_related("tournament__venue", "service").order_by("for_date", "pk"))

    cards = {}

    def card(t):
        return cards.setdefault(t.pk, {"t": t, "entries": [], "apps": {}, "stable": {}, "n_orders": 0, "n_bookings": 0})

    def stable_group(c, key, label):
        return c["stable"].setdefault(key, {"label": label, "bookings": [], "orders": []})

    for e in entries:
        c = card(e.tournament)
        e.by_coach = e.application.submitted_by_id != user.pk
        c["entries"].append(e)
        if not e.by_coach:
            c["apps"][e.application_id] = e.application
    for b in bookings:
        c = card(b.tournament)
        stable_group(c, ("h", b.horse_id), b.horse_name)["bookings"].append(b)
        c["n_bookings"] += 1
    for o in orders:
        c = card(o.tournament)
        key = ("h", o.horse_id) if o.horse_id else ("r", o.recipient_label)
        stable_group(c, key, o.recipient_label)["orders"].append(o)
        c["n_orders"] += 1
    upcoming, past = [], []
    for c in cards.values():
        t = c["t"]
        end = t.pub_ends_on or t.ends_on
        c["apps"] = sorted(c["apps"].values(), key=lambda a: a.pk)
        days = {}
        for e in c["entries"]:
            days.setdefault(e.day, []).append(e)
        c["days"] = [{"day": d, "entries": es} for d, es in days.items()]
        c["n_entries"] = sum(1 for e in c["entries"] if e.is_active)
        c["statuses"] = " ".join(sorted({e.decision for e in c["entries"]}))
        c["search"] = _norm(" ".join([t.pub_title or t.title, t.venue.name if t.venue else ""]
                                     + [e.athlete_snapshot.get("name", "") + " " + e.horse_snapshot.get("name", "") for e in c["entries"]]
                                     + [g["label"] for g in c["stable"].values()]))
        c["stable"] = list(c["stable"].values())
        (past if end and end < today else upcoming).append(c)
    upcoming.sort(key=lambda c: c["t"].pub_starts_on or c["t"].starts_on)
    past.sort(key=lambda c: c["t"].pub_starts_on or c["t"].starts_on, reverse=True)
    for e in entries:
        e.search = _norm(e.athlete_snapshot.get("name", "") + " " + e.horse_snapshot.get("name", ""))
    return render(request, "entries/my_competitions.html", {
        "tab": tab, "cards": past if tab == "past" else upcoming,
        "counts": {"upcoming": len(upcoming), "past": len(past)}, "q": request.GET.get("q", "").strip()[:100],
        "status_filters": [("", "Все"), ("pending", "На рассмотрении"), ("accepted", "Принято"), ("rejected", "Отклонено")],
    })
