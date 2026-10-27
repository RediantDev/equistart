from django.contrib import admin

from .models import ClassTemplate, DressageTest


@admin.register(DressageTest)
class DressageTestAdmin(admin.ModelAdmin):
    list_display = ("name", "short", "level", "kind", "arena", "is_official", "owner", "is_active")
    list_filter = ("is_official", "kind", "arena", "is_active")
    search_fields = ("name", "short", "level")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "short", "level", "kind", "arena", "duration_min", "min_horse_age", "source")}),
        ("Подсчёт", {"fields": ("penalty_scheme", "tie_rule")}),
        ("Лист езды", {"fields": ("parts",), "description": "Части листа в JSON. Удобнее вносить через конструктор езды на сайте."}),
        ("Доступ", {"fields": ("is_official", "owner", "is_active", "created_at", "updated_at")}),
    )


@admin.register(ClassTemplate)
class ClassTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "disciplines", "age_from", "age_to", "rank_rule", "rank", "horse_age_min", "horse_age_max", "horse_kind", "position", "is_active")
    list_editable = ("position", "is_active")
