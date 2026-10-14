from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache


def privacy(request):
    return render(request, "legal/privacy.html", {"doc": "privacy"})


def consent(request):
    return render(request, "legal/consent.html", {"doc": "consent"})


def publication_consent(request):
    return render(request, "legal/publication_consent.html", {"doc": "publication"})


@never_cache
def health_live(request):
    return JsonResponse({"status": "ok"})


@never_cache
def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
