import re

from django import forms
from django.core.validators import validate_email

from apps.core.models import SiteSettings


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = ["moderator_emails", "support_telegram", "footer_phone", "footer_email"]
        widgets = {"moderator_emails": forms.Textarea(attrs={"rows": 3, "placeholder": "ivan@example.ru"})}

    def clean_moderator_emails(self):
        raw = self.cleaned_data.get("moderator_emails", "")
        emails = [a for a in (x.strip() for x in re.split(r"[\s,;]+", raw)) if a]
        for a in emails:
            try:
                validate_email(a)
            except forms.ValidationError:
                raise forms.ValidationError(f"«{a}» — не похоже на email.")
        return "\n".join(emails)

    def clean_support_telegram(self):
        v = (self.cleaned_data.get("support_telegram") or "").strip()
        v = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", v).lstrip("@")
        if v and not re.fullmatch(r"[A-Za-z0-9_]{5,32}", v):
            raise forms.ValidationError("Имя в Telegram — от 5 до 32 латинских букв, цифр или «_», например @startlist_help.")
        return f"@{v}" if v else ""
