from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .models import User
from .phone import normalize_phone


class UserCreationAdminForm(forms.ModelForm):
    password1 = forms.CharField(label="Пароль", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Пароль ещё раз", widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ("phone", "last_name", "first_name")

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])

    def clean_password2(self):
        p1, p2 = self.cleaned_data.get("password1"), self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Пароли не совпадают.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class UserChangeAdminForm(forms.ModelForm):
    password = ReadOnlyPasswordHashField(label="Пароль")

    class Meta:
        model = User
        fields = "__all__"

    def clean_phone(self):
        return normalize_phone(self.cleaned_data["phone"])


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserChangeAdminForm
    add_form = UserCreationAdminForm
    list_display = ("phone", "full_name", "is_athlete", "is_coach", "is_organizer", "is_moderator", "is_active", "date_joined")
    list_filter = ("is_athlete", "is_coach", "is_organizer", "is_moderator", "is_active", "is_staff")
    search_fields = ("phone", "last_name", "first_name", "email")
    ordering = ("-date_joined",)
    readonly_fields = ("date_joined", "last_login", "pd_consent_at", "pd_consent_version")
    fieldsets = (
        (None, {"fields": ("phone", "password", "phone_verified_at")}),
        ("Личные данные", {"fields": ("last_name", "first_name", "middle_name", "email")}),
        ("Категории", {"fields": ("is_athlete", "is_coach", "is_organizer", "current_mode")}),
        ("Служебные права", {"fields": ("is_moderator", "is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Согласие и даты", {"fields": ("pd_consent_at", "pd_consent_version", "date_joined", "last_login")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "last_name", "first_name", "password1", "password2")}),)

    @admin.display(description="ФИО")
    def full_name(self, obj):
        return obj.full_name
