from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password-reset/", views.password_reset, name="password_reset"),
    path("password-reset/confirm/", views.password_reset_confirm, name="password_reset_confirm"),
    path("cabinet/", views.cabinet, name="cabinet"),
    path("verify-phone/", views.verify_phone, name="verify_phone"),
    path("cabinet/password/", views.change_password, name="password"),
    path("cabinet/add-role/", views.add_role, name="add_role"),
    path("profile/", views.profile, name="profile"),
    path("profile/phone/", views.change_phone, name="phone"),
    path("profile/remove-role/", views.remove_role, name="remove_role"),
]
