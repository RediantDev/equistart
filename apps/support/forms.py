from django import forms

from apps.accounts.phone import normalize_phone

from . import services
from .models import TOPIC_LINKS, Topic


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiImageField(forms.FileField):
    widget = MultiFileInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        files = [f for f in files if f]
        if len(files) > services.MAX_ATTACHMENTS:
            raise forms.ValidationError(f"Не больше {services.MAX_ATTACHMENTS} изображений.")
        out = []
        for f in files:
            try:
                out.append(services.check_image(f))
            except services.SupportError as e:
                raise forms.ValidationError(str(e))
        return out


class _Choice(forms.ModelChoiceField):
    def __init__(self, *a, label_fn=str, **kw):
        self._label_fn = label_fn
        super().__init__(*a, **kw)

    def label_from_instance(self, obj):
        return self._label_fn(obj)


def _t_label(t):
    d = t.pub_starts_on.strftime("%d.%m.%Y") if t.pub_starts_on else ""
    return f"{t.pub_title or t.title} · {d}"


class TicketForm(forms.Form):
    topic = forms.ChoiceField(label="Тема", choices=[("", "— выберите тему —")] + list(Topic.choices))
    tournament = _Choice(label="Турнир", queryset=None, required=False, empty_label="— не про турнир —", label_fn=_t_label)
    urgent = forms.BooleanField(label="Идёт турнир — срочно", required=False,
                                help_text="Для организатора и судей в дни турнира: обращение встанет первым в очереди.")
    athlete = _Choice(label="Спортсмен", queryset=None, required=False, empty_label="— не выбран —")
    horse = _Choice(label="Лошадь", queryset=None, required=False, empty_label="— не выбрана —")
    club = _Choice(label="Клуб", queryset=None, required=False, empty_label="— не выбран —",
                   label_fn=lambda c: f"{c.public_name} · {c.public_settlement}")
    text = forms.CharField(label="Опишите вопрос", max_length=5000, widget=forms.Textarea(attrs={"rows": 6}),
                           help_text="Что случилось, на какой странице, что вы ожидали увидеть.")
    attachments = MultiImageField(label="Скриншоты", required=False,
                                  help_text="До 3 изображений JPEG или PNG, до 10 МБ каждое. Видят только вы и поддержка.")
    name = forms.CharField(label="Ваше имя", max_length=200, required=False)
    phone = forms.CharField(label="Телефон", max_length=30, required=False, widget=forms.TextInput(attrs={"type": "tel"}),
                            help_text="С кодом страны: +7…, +375…")
    email = forms.EmailField(label="Email для ответа", required=False)
    consent = forms.BooleanField(label="Согласен на обработку указанных контактов для ответа на обращение", required=False)
    page = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, user, *args, live=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.clubs.models import Club
        from apps.profiles.models import Athlete, Horse
        from apps.tournaments.models import Tournament

        self.user = user
        self.is_guest = not getattr(user, "is_authenticated", False)
        self.live = live or []
        self.fields["tournament"].queryset = Tournament.objects.filter(
            publication__in=[Tournament.Publication.PUBLISHED, Tournament.Publication.FINISHED, Tournament.Publication.CANCELLED]
        ).order_by("-pub_starts_on")
        self.fields["club"].queryset = Club.objects.filter(confirmed_at__isnull=False, merged_into__isnull=True).order_by("pub_name")
        if self.is_guest:
            for f in ("athlete", "horse", "urgent"):
                del self.fields[f]
            for f in ("name", "phone", "email"):
                self.fields[f].required = True
            self.fields["consent"].required = True
        else:
            for f in ("name", "phone", "email", "consent"):
                del self.fields[f]
            self.fields["athlete"].queryset = (Athlete.objects.filter(manager=user) | Athlete.objects.filter(linked_user=user)).distinct().order_by("last_name", "first_name")
            self.fields["horse"].queryset = Horse.objects.filter(manager=user).order_by("name")
            if not self.live:
                del self.fields["urgent"]

    def clean_phone(self):
        raw = self.cleaned_data.get("phone", "")
        if not raw:
            return ""
        try:
            return normalize_phone(raw)
        except Exception:
            raise forms.ValidationError("Проверьте номер: с «+» и кодом страны, например +7 900 000-00-00.")

    def clean(self):
        c = super().clean()
        topic = c.get("topic")
        if topic:
            allowed = set(TOPIC_LINKS.get(topic, []))
            for f in ("tournament", "athlete", "horse", "club"):
                if f not in allowed and f in c:
                    c[f] = None  # поле этой теме не нужно — не сохраняем случайный выбор
        if topic == Topic.RESULTS and not c.get("tournament"):
            self.add_error("tournament", "Выберите турнир, в результатах которого ошибка.")
        if c.get("urgent"):
            t = c.get("tournament")
            if not t or t not in self.live:
                self.add_error("urgent", "Отметка «Идёт турнир» — только для турнира, который проходит сегодня, и только для его команды.")
        return c
