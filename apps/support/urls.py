from django.urls import path

from . import views

app_name = "support"

urlpatterns = [
    path("support/", views.my_list, name="my"),
    path("support/new/", views.new, name="new"),
    path("support/<int:pk>/", views.detail, name="detail"),
    path("support/g/<str:token>/", views.guest, name="guest"),
    path("support/file/<int:pk>/", views.attachment, name="file"),
    path("help/", views.faq, name="faq"),
]
