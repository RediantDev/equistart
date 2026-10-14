from django.urls import path

from . import views

app_name = "conduct"

urlpatterns = [
    path("organizer/t/<int:pk>/conduct/", views.overview, name="overview"),
    path("organizer/t/<int:pk>/conduct/settings/", views.settings_save, name="settings"),
    path("organizer/t/<int:pk>/conduct/finish/", views.finish, name="finish"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/", views.program, name="program"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/live/", views.live, name="live"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/live/save/", views.live_save, name="live_save"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/<str:action>/", views.program_action, name="action"),
    path("t/<int:pk>/start/<int:program_id>.xlsx", views.startlist_xlsx, name="startlist_xlsx"),
    path("t/<int:pk>/results/<int:class_id>.xlsx", views.results_xlsx, name="results_xlsx"),
]
