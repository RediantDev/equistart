from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache


def calendar(request):
    # Турниры появятся на этапе 1; пока календарь показывает пустое состояние.
    return render(request, "core/calendar.html", {"tournaments": []})


def privacy(request):
    return render(request, "core/privacy.html")


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
