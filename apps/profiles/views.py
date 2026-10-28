import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.models import User
from apps.core.models import audit

from . import services
from .forms import AthleteForm, DocumentForm, HorseForm, PhoneForm
from .models import COLOR_SHORT, SEX_SHORT, Athlete, AthleteDelegate, Horse, HorseAccess, HorseDocument, Offer


# ---------------- спортсмены ----------------

def _last_first(u):
    return " ".join(p for p in (u.last_name, u.first_name) if p) or u.phone


def _self_word(a):
    """«управляет сам/сама» — по отчеству; если его нет, по имени управляющего."""
    mid = (a.middle_name or "").lower()
    if mid.endswith(("вна", "чна", "кызы")):
        return "сама"
    if mid.endswith(("вич", "ич", "оглы")):
        return "сам"
    return ""


def _athlete_meta(a):
    """Строка под именем (макет S04B): разряд · клуб · год; без даты/разряда — пометка, заполняются в профиле."""
    parts = [a.rank_short, a.club, str(a.birth_year) if a.birth_year else ""]
    missing = [w for w, ok in (("год", a.birth_date), ("разряд", a.rank)) if not ok]
    if len(missing) == 2:
        parts.append("год и разряд не указаны")
    elif missing:
        parts.append(f"{missing[0]} не указан")
    return " · ".join(p for p in parts if p)


@login_required
def athletes(request):
    """Спортсмены по группам (макет S04B): «Это я», «Мои», «Мне доверили»."""
    me, mine, trusted = [], [], []
    for a in services.athletes_for_entry(request.user).select_related("manager"):
        row = {"a": a, "manage": a.manager_id == request.user.pk, "own": a.linked_user_id == request.user.pk,
               "ini": (a.last_name[:1] + a.first_name[:1]).upper(), "meta": _athlete_meta(a)}
        if row["own"]:
            me.append(row)
        elif row["manage"]:
            mine.append(row)
        else:
            if a.manager_id == a.linked_user_id:
                who = "управляет " + (_self_word(a) or "сам спортсмен")
            else:
                who = "управляет " + _last_first(a.manager)
            row["meta"] = " · ".join(p for p in (row["meta"], who) if p)
            trusted.append(row)
    return render(request, "profiles/athletes.html", {
        "me": me, "mine": mine, "trusted": trusted, "count": len(me) + len(mine) + len(trusted),
    })


@login_required
@require_http_methods(["GET", "POST"])
def athlete_edit(request, pk=None):
    athlete = get_object_or_404(Athlete, pk=pk) if pk else None
    if athlete and not services.can_manage_athlete(request.user, athlete):
        raise PermissionDenied
    myself = request.GET.get("me") == "1" or request.POST.get("me") == "1"
    initial = {}
    if not athlete and myself:
        if Athlete.objects.filter(linked_user=request.user).exists():
            messages.info(request, "Ваш профиль спортсмена уже создан.")
            return redirect("profiles:athletes")
        initial = {"last_name": request.user.last_name, "first_name": request.user.first_name, "middle_name": request.user.middle_name}
    form = AthleteForm(request.POST or None, instance=athlete, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            a = form.save(commit=False)
            if not athlete:
                a.manager = request.user
                if myself:
                    a.linked_user = request.user
            if not a.consent_confirmed_at:
                a.consent_confirmed_at = timezone.now()  # тест 4: отдельной галочки нет, согласие даётся при регистрации
            a.save()
            audit(request.user, "athlete.save", a, created=not athlete)
        messages.success(request, f"Спортсмен «{a.full_name}» сохранён.")
        nxt = _back_to(request)
        if nxt:
            return redirect(nxt if athlete else _with_param(nxt, "athlete", a.pk))
        return redirect("profiles:athlete", pk=a.pk)
    return render(request, "profiles/athlete_form.html", {"form": form, "athlete": athlete, "myself": myself, "next": _back_to(request)})


@login_required
def athlete_detail(request, pk):
    a = get_object_or_404(Athlete.objects.select_related("manager", "linked_user"), pk=pk)
    if not services.can_view_athlete(request.user, a):
        raise PermissionDenied
    manage = services.can_manage_athlete(request.user, a)
    offers = a.offers.filter(state=Offer.State.PENDING) if manage else []
    return render(
        request, "profiles/athlete_detail.html",
        {"a": a, "manage": manage, "delegates": a.delegates.select_related("user") if manage else [], "offers": offers, "form": PhoneForm()},
    )


@login_required
@require_POST
def athlete_action(request, pk, action):
    a = get_object_or_404(Athlete, pk=pk)
    if not services.can_manage_athlete(request.user, a):
        raise PermissionDenied
    if action in ("delegate", "link", "transfer"):
        form = PhoneForm(request.POST)
        if not form.is_valid():
            messages.error(request, "; ".join(form.errors["phone"]))
            return redirect("profiles:athlete", pk=a.pk)
        phone = form.cleaned_data["phone"]
        if action == "delegate":
            coach = User.objects.filter(phone=phone, is_active=True).first()
            if not coach:
                messages.error(request, "Пользователь с этим номером не зарегистрирован. Попросите тренера зарегистрироваться.")
            elif coach.pk == request.user.pk:
                messages.error(request, "Вы и так управляете этим спортсменом.")
            else:
                AthleteDelegate.objects.get_or_create(athlete=a, user=coach, defaults={"granted_by": request.user})
                audit(request.user, "athlete.delegate", a, user=coach.pk)
                messages.success(request, f"{coach.short_name} может подавать заявки за спортсмена.")
        else:
            kind = Offer.Kind.ATHLETE_LINK if action == "link" else Offer.Kind.ATHLETE_TRANSFER
            if kind == Offer.Kind.ATHLETE_LINK and a.linked_user_id:
                messages.error(request, "Запись уже связана с аккаунтом спортсмена.")
            elif phone == request.user.phone and kind == Offer.Kind.ATHLETE_TRANSFER:
                messages.error(request, "Вы и так управляете этим спортсменом.")
            else:
                try:
                    with transaction.atomic():
                        Offer.objects.create(kind=kind, athlete=a, phone=phone, created_by=request.user)
                except IntegrityError:
                    messages.error(request, "Такое предложение уже отправлено.")
                else:
                    audit(request.user, f"offer.{kind}", a, phone=phone)
                    messages.success(request, "Предложение отправлено. Оно появится в кабинете у владельца номера.")
    elif action == "undelegate":
        AthleteDelegate.objects.filter(athlete=a, pk=request.POST.get("delegate")).delete()
        audit(request.user, "athlete.undelegate", a)
        messages.success(request, "Разрешение отозвано.")
    elif action == "cancel_offer":
        Offer.objects.filter(athlete=a, pk=request.POST.get("offer"), state=Offer.State.PENDING).update(state=Offer.State.CANCELLED)
        messages.success(request, "Предложение отменено.")
    else:
        raise Http404
    return redirect("profiles:athlete", pk=a.pk)


def _back_to(request):
    """Адрес возврата (например, в заявку), если он безопасный."""
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return nxt
    return ""


def _with_param(url, key, value):
    base, _, frag = url.partition("#")
    base += ("&" if "?" in base else "?") + f"{key}={value}"
    return base + "#add"


# ---------------- лошади ----------------

@login_required
def horses(request):
    """Мои лошади (макет R2_05): свои и с доступом, чипы-фильтры, поиск по мере ввода.
    Входящие приглашения к чужой лошади — карточкой сверху с «Принять / Отклонить» (R2_06C)."""
    items = list(Horse.objects.filter(manager=request.user, archived=False, added_by_judge=False).order_by("name", "birth_year"))
    shared = list(Horse.objects.filter(accesses__user=request.user, archived=False).exclude(manager=request.user)
                  .select_related("manager").order_by("name", "birth_year"))
    for h in shared:
        h.manager_name = _last_first(h.manager)
        h.short_meta = " · ".join(p for p in (str(h.birth_year), SEX_SHORT.get(h.sex, ""), COLOR_SHORT.get(h.color, "")) if p)
    invites = list(Offer.objects.filter(phone=request.user.phone, state=Offer.State.PENDING, kind=Offer.Kind.HORSE_ACCESS, horse__archived=False)
                   .select_related("horse", "created_by"))
    for o in invites:
        o.from_name = _last_first(o.created_by)
    everything = items + shared
    return render(request, "profiles/horses.html", {
        "horses": items, "shared": shared, "invites": invites, "q": request.GET.get("q", "")[:100],
        "total": len(everything), "ponies": sum(1 for h in everything if h.is_pony),
    })


@login_required
@require_http_methods(["GET", "POST"])
def horse_edit(request, pk=None):
    horse = get_object_or_404(Horse, pk=pk) if pk else None
    if horse and not services.can_manage_horse(request.user, horse):
        raise PermissionDenied
    form = HorseForm(request.POST or None, instance=horse)
    similar = []
    if request.method == "POST" and form.is_valid():
        if not horse and "confirm_similar" not in request.POST:
            # одинаковые клички — обычное дело; предупреждаем, только если совпал и год рождения (вероятный дубль)
            similar = list(Horse.objects.filter(name__iexact=form.cleaned_data["name"].strip(), birth_year=form.cleaned_data["birth_year"],
                                                archived=False)[:5])
        if not similar:
            try:
                with transaction.atomic():
                    h = form.save(commit=False)
                    if not horse:
                        h.manager = request.user
                    h.save()
                    audit(request.user, "horse.save", h, created=not horse)
            except IntegrityError:
                form.add_error("fksr_number", "Лошадь с таким номером ФКСР уже есть в сервисе.")
            else:
                messages.success(request, f"Лошадь «{h.name}» сохранена. Внутренний код: {h.code}.")
                nxt = _back_to(request)
                if nxt and not horse:
                    return redirect(_with_param(nxt, "horse", h.pk))
                return redirect("profiles:horse", pk=h.pk)
    return render(request, "profiles/horse_form.html", {"form": form, "horse": horse, "similar": similar, "next": _back_to(request)})


DOC_SOON_DAYS = 42  # «Истекает» — за шесть недель до окончания срока (подсказка интерфейса, не норма)
HORSE_TABS = ("info", "docs", "access")


def _mask_phone(phone):
    """+79161234545 → +7 916 ***-**-45 (макет R2_06A)."""
    if phone and phone.startswith("+7") and len(phone) == 12:
        return f"+7 {phone[2:5]} ***-**-{phone[-2:]}"
    return phone


def _open_tournaments_for(user):
    """Турниры с открытым приёмом заявок (для кнопки «Подать заявку с …»)."""
    from apps.tournaments.models import Tournament

    qs = Tournament.objects.filter(publication=Tournament.Publication.PUBLISHED, pub_deadline__gte=timezone.localdate() - datetime.timedelta(days=1))
    return [t for t in qs.order_by("pub_starts_on")[:50] if t.public_entries_open()]


@login_required
def horse_detail(request, pk):
    """Профиль лошади. У управляющего — вкладки Сведения / Документы / Доступ (R2_06A, S08A);
    у получателя доступа — только сведения, «Подать заявку» и «Отказаться от доступа» (R2_06D)."""
    h = get_object_or_404(Horse.objects.select_related("manager"), pk=pk)
    manage = services.can_manage_horse(request.user, h)
    docs_visible = services.can_view_horse_documents(request.user, h)
    my_access = None if manage else HorseAccess.objects.filter(horse=h, user=request.user).first()
    tabs = [t for t in HORSE_TABS if t == "info" or (t == "docs" and docs_visible) or (t == "access" and manage)]
    tab = request.GET.get("tab") if request.GET.get("tab") in tabs else "info"
    documents = list(h.documents.all()) if docs_visible else []
    today = timezone.localdate()
    for d in documents:
        if not d.valid_until:
            d.status = ""
        elif d.valid_until < today:
            d.status = "expired"
        elif (d.valid_until - today).days <= DOC_SOON_DAYS:
            d.status = "soon"
        else:
            d.status = "ok"
    ctx = {
        "h": h, "manage": manage, "docs_visible": docs_visible, "documents": documents, "tabs": tabs, "tab": tab,
        "my_access": my_access, "manager_name": _last_first(h.manager), "form": PhoneForm(),
    }
    if manage:
        accesses = list(h.accesses.select_related("user"))
        for a in accesses:
            a.name = _last_first(a.user)
            a.ini = ((a.user.last_name[:1] + a.user.first_name[:1]) or "?").upper()
        pending = list(h.offers.filter(state=Offer.State.PENDING, kind=Offer.Kind.HORSE_ACCESS))
        busy = {o.phone for o in pending} | {a.user.phone for a in accesses}
        # Отклонённые приглашения — за последние 30 дней, если тому же номеру не отправлено новое
        declined, seen = [], set()
        for o in h.offers.filter(state=Offer.State.DECLINED, kind=Offer.Kind.HORSE_ACCESS,
                                 responded_at__gte=timezone.now() - datetime.timedelta(days=30)).order_by("-responded_at"):
            if o.phone not in busy and o.phone not in seen:
                seen.add(o.phone)
                declined.append(o)
        for o in pending + declined:
            o.masked = _mask_phone(o.phone)
        ctx.update({
            "doc_form": DocumentForm(),
            "offers": h.offers.filter(state=Offer.State.PENDING, kind=Offer.Kind.HORSE_TRANSFER),
            "access_offers": pending, "accesses": accesses, "declined": declined,
            "access_count": len(pending) + len(accesses),
        })
    if my_access:
        opened = _open_tournaments_for(request.user)
        if len(opened) == 1:
            ctx["apply_url"] = reverse("entries:apply", args=[opened[0].pk]) + "?" + urlencode({"horse": h.pk})
        elif opened:
            ctx["apply_url"] = reverse("core:calendar") + "?open=1"
    return render(request, "profiles/horse_detail.html", ctx)


@login_required
@require_POST
def horse_action(request, pk, action):
    h = get_object_or_404(Horse, pk=pk)
    if action == "leave":
        # Получатель доступа сам отказывается от него
        n, _ = HorseAccess.objects.filter(horse=h, user=request.user).delete()
        if not n:
            raise PermissionDenied
        audit(request.user, "horse.access_leave", h)
        messages.success(request, f"Вы отказались от доступа к лошади «{h.name}».")
        return redirect("profiles:horses")
    if not services.can_manage_horse(request.user, h):
        raise PermissionDenied
    if action == "grant":
        form = PhoneForm(request.POST)
        if not form.is_valid():
            messages.error(request, "; ".join(form.errors["phone"]))
        elif form.cleaned_data["phone"] == request.user.phone:
            messages.error(request, "Вы управляете этой лошадью — доступ вам не нужен.")
        elif HorseAccess.objects.filter(horse=h, user__phone=form.cleaned_data["phone"]).exists():
            messages.error(request, "У этого человека уже есть доступ.")
        else:
            try:
                with transaction.atomic():
                    Offer.objects.create(kind=Offer.Kind.HORSE_ACCESS, horse=h, phone=form.cleaned_data["phone"], created_by=request.user)
            except IntegrityError:
                messages.error(request, "Приглашение на этот номер уже отправлено.")
            else:
                audit(request.user, "offer.horse_access", h, phone=form.cleaned_data["phone"])
                messages.success(request, "Приглашение отправлено. Доступ появится, когда получатель его примет.")
    elif action == "revoke":
        n, _ = HorseAccess.objects.filter(horse=h, pk=request.POST.get("access")).delete()
        if n:
            audit(request.user, "horse.access_revoke", h)
            messages.success(request, "Доступ отозван. Уже поданные заявки сохраняются.")
    elif action == "transfer":
        form = PhoneForm(request.POST)
        if not form.is_valid():
            messages.error(request, "; ".join(form.errors["phone"]))
        elif form.cleaned_data["phone"] == request.user.phone:
            messages.error(request, "Вы и так управляете этой лошадью.")
        else:
            try:
                with transaction.atomic():
                    Offer.objects.create(kind=Offer.Kind.HORSE_TRANSFER, horse=h, phone=form.cleaned_data["phone"], created_by=request.user)
            except IntegrityError:
                messages.error(request, "Такое предложение уже отправлено.")
            else:
                audit(request.user, "offer.horse_transfer", h, phone=form.cleaned_data["phone"])
                messages.success(request, "Предложение передачи отправлено. До принятия его можно отменить.")
    elif action == "cancel_offer":
        Offer.objects.filter(horse=h, pk=request.POST.get("offer"), state=Offer.State.PENDING).update(state=Offer.State.CANCELLED)
        messages.success(request, "Предложение отменено.")
    elif action == "upload":
        form = DocumentForm(request.POST, request.FILES)
        if form.is_valid():
            d = form.save(commit=False)
            d.horse, d.uploaded_by, d.content_type = h, request.user, form.content_type
            d.save()
            audit(request.user, "horse.document", h, document=d.pk, kind=d.kind)
            messages.success(request, "Документ загружен.")
        else:
            # Тест 5, п. 16.2: понятно, какое поле не заполнено
            labels = {"file": "Файл", "kind": "Тип", "title": "Название", "valid_until": "Действует до"}
            for name, errs in form.errors.items():
                msg = "; ".join(errs)
                if name == "file" and "Обязательное" in msg:
                    msg = "выберите файл документа"
                messages.error(request, f"{labels.get(name, 'Документ')}: {msg}")
    elif action == "delete_doc":
        HorseDocument.objects.filter(horse=h, pk=request.POST.get("document")).delete()
        audit(request.user, "horse.document_delete", h)
        messages.success(request, "Документ удалён.")
    else:
        raise Http404
    tab = "docs" if action in ("upload", "delete_doc") else "access"
    return redirect(reverse("profiles:horse", args=[h.pk]) + f"?tab={tab}")


def _document_or_403(request, pk, doc_id):
    h = get_object_or_404(Horse, pk=pk)
    if not services.can_view_horse_documents(request.user, h):
        raise PermissionDenied
    return h, get_object_or_404(HorseDocument, pk=doc_id, horse=h)


@login_required
@xframe_options_sameorigin  # тест 5.1: превью внутри страницы просмотра документа
def horse_document(request, pk, doc_id):
    h, d = _document_or_403(request, pk, doc_id)
    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/heic": "heic"}.get(d.content_type, "bin")
    download = request.GET.get("download") == "1"
    resp = FileResponse(d.file.open("rb"), content_type=d.content_type, as_attachment=download, filename=f"{h.code}-{d.kind}.{ext}")
    resp["X-Content-Type-Options"] = "nosniff"
    if d.content_type != "application/pdf":
        # Встроенный просмотрщик PDF в браузерах не работает в «песочнице»; тип PDF проверен по сигнатуре при загрузке
        resp["Content-Security-Policy"] = "sandbox"
    resp["Cache-Control"] = "private, no-store"
    return resp


@login_required
def horse_document_view(request, pk, doc_id):
    """Страница документа: шапка с возвратом к лошади, сведения, превью и «Скачать» (тест 5.1)."""
    h, d = _document_or_403(request, pk, doc_id)
    return render(request, "profiles/horse_document.html", {
        "h": h, "d": d, "is_pdf": d.content_type == "application/pdf", "is_image": d.content_type.startswith("image/"),
        "manage": services.can_manage_horse(request.user, h),
    })


@login_required
def horse_search(request):
    """Поиск по всему сервису нужен только для выбора лошади в заявке (есть ?next=).
    Без next открыть можно лишь свои профили — это «Мои лошади» с тем же запросом (тест 5.1)."""
    q = request.GET.get("q", "")
    nxt = _back_to(request)
    if not nxt:
        url = reverse("profiles:horses")
        return redirect(url + ("?" + urlencode({"q": q}) if q else ""))
    mode = request.GET.get("mode", "auto")
    results = services.search_horses(q, mode)
    return render(request, "profiles/horse_search.html", {"q": q, "mode": mode, "results": results, "next": nxt})


# ---------------- входящие предложения ----------------

@login_required
def offers(request):
    incoming = Offer.objects.filter(phone=request.user.phone, state=Offer.State.PENDING).select_related("athlete", "horse", "created_by")
    return render(request, "profiles/offers.html", {"incoming": incoming})


@login_required
@require_POST
def offer_respond(request, offer_id):
    accept = request.POST.get("decision") == "accept"
    back = _back_to(request) or reverse("profiles:offers")  # из «Моих лошадей» — обратно туда (R2_06C)
    try:
        offer = services.respond_offer(request.user, offer_id, accept)
    except Offer.DoesNotExist:
        messages.error(request, "Предложение уже неактуально.")
    else:
        if offer.state == Offer.State.CANCELLED:
            messages.error(request, "Предложение уже неактуально.")
            return redirect(back)
        if offer.kind == Offer.Kind.HORSE_ACCESS and offer.horse_id:
            messages.success(request, f"Доступ к лошади «{offer.horse.name}» принят." if accept else "Приглашение отклонено.")
        else:
            messages.success(request, "Принято." if accept else "Отклонено.")
    return redirect(back)
