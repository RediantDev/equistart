from django import forms

from .models import Service, StableCategory

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class CategoryForm(forms.ModelForm):
    class Meta:
        model = StableCategory
        fields = ["name", "available_from", "available_to", "capacity", "checkin_time", "checkout_time", "conditions", "is_active"]
        widgets = {"available_from": DATE, "available_to": DATE, "checkin_time": TIME, "checkout_time": TIME, "conditions": forms.Textarea(attrs={"rows": 3})}
        help_texts = {"available_from": "Может быть раньше начала турнира.", "available_to": "Может быть позже окончания турнира.",
                      "capacity": "Сколько денников этой категории всего."}

    def clean(self):
        d = super().clean()
        a, b = d.get("available_from"), d.get("available_to")
        if a and b and b <= a:
            self.add_error("available_to", "Последний выезд — позже первого заезда.")
        if d.get("capacity") == 0:
            self.add_error("capacity", "Вместимость — хотя бы 1.")
        return d


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = ["name", "unit", "recipient", "available_from", "available_to", "limit", "is_active"]
        widgets = {"available_from": DATE, "available_to": DATE}
        help_texts = {"limit": "Пусто — без ограничения."}

    def clean(self):
        d = super().clean()
        a, b = d.get("available_from"), d.get("available_to")
        if a and b and b < a:
            self.add_error("available_to", "Дата «по» не раньше даты «с».")
        return d
