from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("cabinet/", views.cabinet, name="cabinet"),
    path("cabinet/mode/", views.switch_mode, name="switch_mode"),
    path("verify-phone/", views.verify_phone, name="verify_phone"),
    path("cabinet/password/", views.change_password, name="password"),
    path("cabinet/add-role/", views.add_role, name="add_role"),
]
