from django import forms

from apps.tournaments.models import Tournament

from . import services
from .models import Club, ClubNews


class ClubForm(forms.ModelForm):
    class Meta:
        model = Club
        fields = ["name", "description", "region", "settlement", "address", "phone", "email", "site", "socials", "logo"]
        widgets = {"description": forms.Textarea(attrs={"rows": 5}), "logo": forms.FileInput(attrs={"accept": "image/jpeg,image/png"})}
        help_texts = {"name": "Как клуб называют участники: «КСК «Пегас»».", "address": "Улица, дом или ориентир — для навигатора.",
                      "description": "Пара абзацев: площадки, манежи, чем занимаетесь. Видно всем после подтверждения.",
                      "logo": "JPEG или PNG до 20 МБ.",
                      # тест судьи и клуба: контакты публичны — предупреждаем; без запрещённых в РФ соцсетей в подсказке
                      "socials": "Ссылки через пробел, например VK и Telegram. Будут видны на странице клуба.",
                      "phone": "Будет виден всем на странице клуба.", "email": "Будет виден всем на странице клуба."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["region"].choices = [("", "— выберите —")] + list(self.fields["region"].choices)[1:]
        self.fields["phone"].widget.input_type = "tel"

    submitting = False

    def clean(self):
        c = super().clean()
        if self.submitting and not (c.get("phone") or c.get("email") or c.get("site")):
            self.add_error("phone", "Укажите хотя бы один контакт: телефон, email или сайт — без него не отправить на проверку.")
        return c

    def clean_logo(self):
        f = self.cleaned_data.get("logo")
        if f and hasattr(f, "size") and f is not getattr(self.instance, "logo", None) and not getattr(f, "_committed", False):
            services.check_image(f)
        return f


class NewsForm(forms.ModelForm):
    photos = forms.FileField(label="Фото", required=False, widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png"}),
                             help_text="JPEG или PNG до 20 МБ. Можно добавить ещё после сохранения.")

    class Meta:
        model = ClubNews
        fields = ["title", "text", "tournament"]
        widgets = {"text": forms.Textarea(attrs={"rows": 6})}
        labels = {"tournament": "О каком турнире новость"}

    def __init__(self, club, *args, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Tournament.objects.none()
        if club.venue_id:
            qs = Tournament.objects.filter(venue_id=club.venue_id, publication__in=["published", "finished"])
        qs = qs | Tournament.objects.filter(owner=club.manager, publication__in=["published", "finished"])
        self.fields["tournament"].queryset = qs.distinct().order_by("-starts_on")
        self.fields["tournament"].required = False
        self.fields["tournament"].empty_label = "— не о турнире —"
        self.fields["tournament"].help_text = "Если новость о турнире на вашей площадке — выберите его: в новости появится ссылка на турнир."  # тест 5, п. 25.1

    def clean_photos(self):
        f = self.cleaned_data.get("photos")
        if f:
            services.check_image(f)
        return f
