from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.accounts.models import User
from apps.core.models import audit

from . import services
from .forms import AthleteForm, DocumentForm, HorseForm, PhoneForm
from .models import Athlete, AthleteDelegate, Horse, HorseDocument, Offer


# ---------------- спортсмены ----------------

@login_required
def athletes(request):
    items = services.athletes_for_entry(request.user)
    rows = [
        {"a": a, "manage": a.manager_id == request.user.pk, "own": a.linked_user_id == request.user.pk}
        for a in items.select_related("manager")
    ]
    return render(request, "profiles/athletes.html", {"rows": rows})


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
            if form.cleaned_data.get("consent") and not a.consent_confirmed_at:
                a.consent_confirmed_at = timezone.now()
            a.save()
            audit(request.user, "athlete.save", a, created=not athlete)
        messages.success(request, f"Спортсмен «{a.full_name}» сохранён.")
        return redirect("profiles:athlete", pk=a.pk)
    return render(request, "profiles/athlete_form.html", {"form": form, "athlete": athlete, "myself": myself})


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


# ---------------- лошади ----------------

@login_required
def horses(request):
    items = Horse.objects.filter(manager=request.user, archived=False)
    return render(request, "profiles/horses.html", {"horses": items})


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
            similar = list(Horse.objects.filter(name__iexact=form.cleaned_data["name"].strip(), archived=False)[:5])
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
                return redirect("profiles:horse", pk=h.pk)
    return render(request, "profiles/horse_form.html", {"form": form, "horse": horse, "similar": similar})


@login_required
def horse_detail(request, pk):
    h = get_object_or_404(Horse, pk=pk)
    manage = services.can_manage_horse(request.user, h)
    docs_visible = services.can_view_horse_documents(request.user, h)
    return render(
        request, "profiles/horse_detail.html",
        {
            "h": h, "manage": manage, "docs_visible": docs_visible,
            "documents": h.documents.all() if docs_visible else [],
            "doc_form": DocumentForm() if manage else None,
            "offers": h.offers.filter(state=Offer.State.PENDING) if manage else [],
            "form": PhoneForm(),
        },
    )


@login_required
@require_POST
def horse_action(request, pk, action):
    h = get_object_or_404(Horse, pk=pk)
    if not services.can_manage_horse(request.user, h):
        raise PermissionDenied
    if action == "transfer":
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
            for errs in form.errors.values():
                messages.error(request, "; ".join(errs))
    elif action == "delete_doc":
        HorseDocument.objects.filter(horse=h, pk=request.POST.get("document")).delete()
        audit(request.user, "horse.document_delete", h)
        messages.success(request, "Документ удалён.")
    else:
        raise Http404
    return redirect("profiles:horse", pk=h.pk)


@login_required
def horse_document(request, pk, doc_id):
    h = get_object_or_404(Horse, pk=pk)
    if not services.can_view_horse_documents(request.user, h):
        raise PermissionDenied
    d = get_object_or_404(HorseDocument, pk=doc_id, horse=h)
    ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}.get(d.content_type, "bin")
    resp = FileResponse(d.file.open("rb"), content_type=d.content_type, filename=f"{h.code}-{d.kind}.{ext}")
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Content-Security-Policy"] = "sandbox"
    return resp


@login_required
def horse_search(request):
    q = request.GET.get("q", "")
    mode = request.GET.get("mode", "name")
    results = services.search_horses(q, mode)
    return render(request, "profiles/horse_search.html", {"q": q, "mode": mode, "results": results, "next": request.GET.get("next", "")})


# ---------------- входящие предложения ----------------

@login_required
def offers(request):
    incoming = Offer.objects.filter(phone=request.user.phone, state=Offer.State.PENDING).select_related("athlete", "horse", "created_by")
    return render(request, "profiles/offers.html", {"incoming": incoming})


@login_required
@require_POST
def offer_respond(request, offer_id):
    accept = request.POST.get("decision") == "accept"
    try:
        services.respond_offer(request.user, offer_id, accept)
    except Offer.DoesNotExist:
        messages.error(request, "Предложение уже неактуально.")
    else:
        messages.success(request, "Принято." if accept else "Отклонено.")
    return redirect("profiles:offers")
