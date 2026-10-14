from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    path("my/money/", views.my, name="my"),
    path("organizer/t/<int:pk>/cash/", views.cash, name="cash"),
    path("organizer/t/<int:pk>/cash/payer/<int:user_id>/", views.payer, name="payer"),
    path("organizer/t/<int:pk>/cash/refunds/", views.refunds, name="refunds"),
    path("organizer/t/<int:pk>/cash/export.xlsx", views.export, name="export"),
    path("organizer/t/<int:pk>/cash/do/<str:action>/", views.action, name="action"),
]
