from django.urls import path

from . import views

app_name = "entries"

urlpatterns = [
    path("t/<int:pk>/apply/", views.apply, name="apply"),
    path("my/applications/", views.my_applications, name="mine"),
    path("my/starts/", views.my_starts, name="my_starts"),
    path("applications/<int:pk>/", views.application, name="application"),
    path("applications/<int:pk>/cancel/", views.application_cancel, name="cancel"),
    path("applications/<int:pk>/contacts/", views.application_contacts, name="contacts"),
    path("entries/<int:pk>/propose/", views.propose, name="propose"),
    path("proposals/<int:pk>/decide/", views.proposal_decide, name="proposal_decide"),
    path("organizer/t/<int:pk>/entries/", views.manage, name="manage"),
    path("organizer/t/<int:pk>/entries/decide/", views.manage_decide, name="decide"),
]
