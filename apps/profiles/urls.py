from django.urls import path

from . import views

app_name = "profiles"

urlpatterns = [
    path("athletes/", views.athletes, name="athletes"),
    path("athletes/new/", views.athlete_edit, name="athlete_new"),
    path("athletes/<int:pk>/", views.athlete_detail, name="athlete"),
    path("athletes/<int:pk>/edit/", views.athlete_edit, name="athlete_edit"),
    path("athletes/<int:pk>/<str:action>/", views.athlete_action, name="athlete_action"),
    path("horses/", views.horses, name="horses"),
    path("horses/new/", views.horse_edit, name="horse_new"),
    path("horses/search/", views.horse_search, name="horse_search"),
    path("horses/<int:pk>/", views.horse_detail, name="horse"),
    path("horses/<int:pk>/edit/", views.horse_edit, name="horse_edit"),
    path("horses/<int:pk>/documents/<int:doc_id>/", views.horse_document, name="horse_document"),
    path("horses/<int:pk>/<str:action>/", views.horse_action, name="horse_action"),
    path("cabinet/offers/", views.offers, name="offers"),
    path("cabinet/offers/<int:offer_id>/", views.offer_respond, name="offer_respond"),
]
