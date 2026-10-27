from django.urls import path

from . import views

app_name = "stables"

urlpatterns = [
    path("t/<int:pk>/stables/", views.book, name="book"),
    path("t/<int:pk>/stables/order/", views.order, name="order"),
    path("my/stables/", views.my, name="my"),
    path("my/stables/<str:kind>/<int:obj_id>/cancel/", views.my_cancel, name="my_cancel"),
    path("organizer/t/<int:pk>/stables/", views.manage, name="manage"),
    path("organizer/t/<int:pk>/stables/do/<str:action>/", views.manage_action, name="action"),
    path("organizer/t/<int:pk>/stables/category/new/", views.category_edit, name="category_new"),
    path("organizer/t/<int:pk>/stables/category/<int:cat_id>/", views.category_edit, name="category_edit"),
    path("organizer/t/<int:pk>/stables/service/new/", views.service_edit, name="service_new"),
    path("organizer/t/<int:pk>/stables/service/<int:svc_id>/", views.service_edit, name="service_edit"),
]
