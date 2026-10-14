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
    }
