from django.urls import path

from . import views

app_name = "clubs"
urlpatterns = [
    path("clubs/", views.club_list, name="list"),
    path("clubs/<int:pk>/", views.detail, name="detail"),
    path("clubs/file/<str:kind>/<int:pk>/", views.file, name="file"),
    path("my/club/", views.mine, name="mine"),
    path("my/club/photos/add/", views.photo_add, name="photo_add"),
    path("my/club/photos/<int:pk>/delete/", views.photo_delete, name="photo_delete"),
    path("my/club/team/", views.team, name="team"),
    path("my/club/invite/<int:pk>/", views.invite_respond, name="invite_respond"),
    path("my/club/news/new/", views.news_edit, name="news_new"),
    path("my/club/news/<int:pk>/", views.news_edit, name="news_edit"),
    path("my/club/news/<int:pk>/delete/", views.news_delete, name="news_delete"),
]
