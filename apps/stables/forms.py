from django import forms

from apps.finance.services import parse_rub

from .models import Service, StableCategory

DATE = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
TIME = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class MoneyField(forms.CharField):
    def __init__(self, **kw):
        kw.setdefault("widget", forms.TextInput(attrs={"inputmode": "decimal"}))
        super().__init__(**kw)

    def prepare_value(self, value):
        if isinstance(value, int):
            r, k = divmod(value, 100)
            return f"{r},{k:02d}" if k else str(r)
        return value

    def to_python(self, value):
        value = super().to_python(value)
        try:
            v = parse_rub(value)
        except ValueError as e:
            raise forms.ValidationError(str(e))
        if v < 0:
            raise forms.ValidationError("Цена не может быть отрицательной.")
        return v


class CategoryForm(forms.ModelForm):
    price_kopecks = MoneyField(label="Цена, ₽")

    class Meta:
        model = StableCategory
        fields = ["name", "price_kopecks", "price_mode", "available_from", "available_to", "capacity", "checkin_time", "checkout_time", "conditions", "is_active"]
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
    price_kopecks = MoneyField(label="Цена за единицу, ₽")

    class Meta:
        model = Service
        fields = ["name", "unit", "price_kopecks", "recipient", "available_from", "available_to", "limit", "is_active"]
        widgets = {"available_from": DATE, "available_to": DATE}
        help_texts = {"limit": "Пусто — без ограничения."}

    def clean(self):
        d = super().clean()
        a, b = d.get("available_from"), d.get("available_to")
        if a and b and b < a:
            self.add_error("available_to", "Дата «по» не раньше даты «с».")
        return d
