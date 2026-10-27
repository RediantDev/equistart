from django.contrib import admin

from .models import Athlete, Horse, HorseDocument, Offer


@admin.register(Athlete)
class AthleteAdmin(admin.ModelAdmin):
    list_display = ("full_name", "birth_date", "rank_jumping", "rank_dressage", "club", "manager", "linked_user")
    search_fields = ("last_name", "first_name", "fksr_number", "club")


@admin.register(Horse)
class HorseAdmin(admin.ModelAdmin):
    list_display = ("name", "birth_year", "breed", "fksr_number", "code", "manager", "archived")
    search_fields = ("name", "fksr_number", "code")
    list_filter = ("archived",)


admin.site.register(HorseDocument)
admin.site.register(Offer)
