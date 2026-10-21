from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.models import audit

from . import dressage as D
from .forms import DressageTestForm
from .models import DressageTest


def _require_creator(user):
    if not (user.is_moderator or user.can_create_tournament):
        raise PermissionDenied


def _visible(user):
    return DressageTest.objects.filter(Q(is_official=True) | Q(owner=user))


@login_required
def test_list(request):
    _require_creator(request.user)
    tests = _visible(request.user).filter(is_active=True)
    return render(request, "sport/test_list.html", {"official": [t for t in tests if t.is_official], "own": [t for t in tests if not t.is_official]})


@login_required
def test_detail(request, pk):
    _require_creator(request.user)
    test = get_object_or_404(_visible(request.user), pk=pk)
    parts = [{"p": p, "items": D.items_of(p), "max": D.part_max(p)} for p in test.parts]
    can_edit = (test.owner_id == request.user.pk and not test.is_official) or (test.is_official and request.user.is_moderator)
    return render(request, "sport/test_detail.html", {"test": test, "parts": parts, "can_edit": can_edit,
                                                      "scheme": D.PENALTY_SCHEMES.get(test.penalty_scheme, {}).get("label", "")})


@login_required
def test_edit(request, pk=None):
    _require_creator(request.user)
    test = None
    if pk:
        test = get_object_or_404(DressageTest, pk=pk)
        if not ((test.owner_id == request.user.pk and not test.is_official) or (test.is_official and request.user.is_moderator)):
            raise PermissionDenied
    initial = {}
    src_id = request.GET.get("from")
    if not test and src_id:
        src = _visible(request.user).filter(pk=src_id).first()
        if src:
            test = DressageTest(**{f: getattr(src, f) for f in ("short", "level", "kind", "arena", "duration_min", "parts", "penalty_scheme",
                                                                "tie_rule", "min_horse_age", "source")})
            test.name = f"{src.name} (копия)"
            test._state.adding = True
            initial["name"] = test.name
    form = DressageTestForm(request.POST or None, instance=test, initial=initial)
    back = request.GET.get("t") or request.POST.get("t")
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if not obj.pk:
            obj.owner = request.user
            obj.is_official = False
        obj.save()
        audit(request.user, "dressage_test.save", obj, name=obj.name)
        messages.success(request, f"Езда «{obj.name}» сохранена." + (" Выберите её в карточке программы." if back else ""))
        if back and back.isdigit():
            return redirect("tournaments:step", pk=int(back), step=2)
        return redirect("sport:test_detail", pk=obj.pk)
    return render(request, "sport/test_edit.html", {"form": form, "test": test if test and test.pk else None, "back": back})
