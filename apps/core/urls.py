from django.urls import path

from apps.tournaments.views import calendar

from . import views

app_name = "core"

urlpatterns = [
    path("", calendar, name="calendar"),
    path("privacy/", views.privacy, name="privacy"),
    path("consent/", views.consent, name="consent"),
    path("publication-consent/", views.publication_consent, name="publication_consent"),
    path("health/live", views.health_live, name="health_live"),
    path("health/ready", views.health_ready, name="health_ready"),
]
