from django.contrib import admin

from .models import ProgramItem, TeamMember, Tournament, TournamentClass, Venue


class ProgramInline(admin.TabularInline):
    model = ProgramItem
    extra = 0
    fields = ("day", "start_time", "discipline", "name", "program_type", "limit")


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "starts_on", "status", "publication", "updated_at")
    list_filter = ("status", "publication")
    search_fields = ("title", "owner__phone", "owner__last_name")
    readonly_fields = ("snapshot", "snapshot_version", "published_at", "revision", "created_at", "updated_at")
    inlines = [ProgramInline]


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ("name", "settlement", "region", "timezone", "confirmed", "created_by")
    list_filter = ("confirmed", "region")
    search_fields = ("name", "settlement", "address")


@admin.register(TeamMember)
class TeamMemberAdmin(admin.ModelAdmin):
    list_display = ("tournament", "phone", "role", "state", "user", "created_at")
    list_filter = ("role", "state")


admin.site.register(TournamentClass)
