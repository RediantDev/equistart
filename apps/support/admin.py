from django.contrib import admin

from .models import Faq, Ticket, TicketMessage


@admin.register(Faq)
class FaqAdmin(admin.ModelAdmin):
    list_display = ("question", "section", "order", "published")
    list_filter = ("section", "published")
    list_editable = ("order", "published")
    search_fields = ("question", "answer")


class MessageInline(admin.TabularInline):
    model = TicketMessage
    extra = 0
    readonly_fields = ("author", "is_staff", "text", "created_at")
    can_delete = False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    """Работать с обращениями удобнее в «Пульт → Поддержка»; здесь — просмотр и поиск."""

    list_display = ("id", "topic", "status", "urgent", "created_at")
    list_filter = ("status", "topic", "urgent")
    search_fields = ("id", "text", "name", "email")
    readonly_fields = [f.name for f in Ticket._meta.fields]
    inlines = [MessageInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # SUP-04: история обращения не удаляется
