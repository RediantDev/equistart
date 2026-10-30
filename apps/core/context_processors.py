from django.conf import settings


def site(request):
    unread = 0
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        from .models import Notification

        unread = Notification.objects.filter(user=user, read_at__isnull=True).count()
    return {
        "SITE_NAME": settings.SITE_NAME,
        "OPERATOR": settings.OPERATOR,
        "LEGAL_DOCS_VERSION": settings.LEGAL_DOCS_VERSION,
        "UNREAD_NOTIFICATIONS": unread,
        "STAND_BANNER": settings.STAND_BANNER,
        "SUPPORT_TELEGRAM": _site_settings().telegram,
        "SITE_SETTINGS": _site_settings(),
        "ADMIN_OVERRIDE": _admin_override(request),
    }


def _site_settings():
    from .models import SiteSettings

    return SiteSettings.get()


def _admin_override(request) -> bool:
    """ADM-06: администратор открыл чужой турнир — на экранах турнира плашка «Вы работаете как администратор»."""
    user = getattr(request, "user", None)
    if not (user is not None and user.is_authenticated and getattr(user, "is_admin", False)):
        return False
    match = getattr(request, "resolver_match", None)
    if not match or not request.path.startswith("/organizer/t/") or "pk" not in match.kwargs:
        return False
    from apps.tournaments.services import real_roles_of
    from apps.tournaments.models import Tournament

    t = Tournament.objects.filter(pk=match.kwargs["pk"]).only("pk", "owner_id").first()
    return bool(t and not real_roles_of(user, t))
