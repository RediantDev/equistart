from django.contrib import admin

from .models import Application, ChangeProposal, Entry


class EntryInline(admin.TabularInline):
    model = Entry
    extra = 0
    fields = ("program_name", "class_name", "decision", "admission")
    readonly_fields = fields


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("pk", "tournament", "submitted_by", "created_at", "late")
    inlines = [EntryInline]


admin.site.register(ChangeProposal)
