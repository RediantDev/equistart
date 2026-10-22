from django.contrib import admin
from django.urls import include, path

from apps.tournaments.urls import moderation_patterns

admin.site.site_header = "EquiStart — управление"
admin.site.site_title = "EquiStart"
admin.site.index_title = "Модерация и администрирование"

urlpatterns = [
    path("manage/", admin.site.urls),
    path("moderation/", include(moderation_patterns)),
    path("", include("apps.clubs.urls")),
    path("", include("apps.tournaments.urls")),
    path("", include("apps.profiles.urls")),
    path("", include("apps.entries.urls")),
    path("", include("apps.conduct.urls")),
    path("", include("apps.sport.urls")),
    path("", include("apps.stables.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.core.urls")),
]

# Страницы ошибок на русском (ТЗ UI-03)
handler400 = "apps.core.errors.bad_request"
handler403 = "apps.core.errors.permission_denied"
handler404 = "apps.core.errors.page_not_found"
handler500 = "apps.core.errors.server_error"
