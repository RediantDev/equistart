from django.urls import path

from . import views

app_name = "sport"

urlpatterns = [
    path("dressage-tests/", views.test_list, name="test_list"),
    path("dressage-tests/new/", views.test_edit, name="test_new"),
    path("dressage-tests/<int:pk>/", views.test_detail, name="test_detail"),
    path("dressage-tests/<int:pk>/edit/", views.test_edit, name="test_edit"),
]
