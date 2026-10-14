from django.conf import settings


def site(request):
    return {"SITE_NAME": settings.SITE_NAME, "OPERATOR": settings.OPERATOR, "LEGAL_DOCS_VERSION": settings.LEGAL_DOCS_VERSION}
