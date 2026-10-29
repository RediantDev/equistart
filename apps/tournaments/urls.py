from django.urls import path

from . import moderation, views

app_name = "tournaments"

urlpatterns = [
    path("t/<int:pk>/", views.public_detail, name="public"),
    path("t/<int:pk>/regulation.pdf", views.public_regulation, name="public_regulation"),
    path("t/<int:pk>/regulation/", views.public_regulation_page, name="public_regulation_page"),
    path("organizer/", views.my_tournaments, name="mine"),
    path("organizer/new/", views.create, name="create"),
    path("organizer/t/<int:pk>/", views.hub, name="hub"),
    path("organizer/t/<int:pk>/step/<int:step>/", views.step, name="step"),
    path("organizer/t/<int:pk>/programs/add/", views.program_save, name="program_add"),
    path("organizer/t/<int:pk>/programs/<int:item_id>/", views.program_edit, name="program_edit"),
    path("organizer/t/<int:pk>/programs/<int:item_id>/save/", views.program_save, name="program_save"),
    path("organizer/t/<int:pk>/programs/<int:item_id>/delete/", views.program_delete, name="program_delete"),
    path("organizer/t/<int:pk>/programs/<int:program_id>/classes/add/", views.class_save, name="class_add"),
    path("organizer/t/<int:pk>/programs/<int:program_id>/classes/from-template/", views.class_from_template, name="class_from_template"),
    path("organizer/t/<int:pk>/programs/<int:program_id>/classes/<int:class_id>/", views.class_edit, name="class_edit"),
    path("organizer/t/<int:pk>/programs/<int:program_id>/classes/<int:class_id>/save/", views.class_save, name="class_save"),
    path("organizer/t/<int:pk>/programs/<int:program_id>/classes/<int:class_id>/delete/", views.class_delete, name="class_delete"),
    path("organizer/t/<int:pk>/delete/", views.delete, name="delete"),
    path("organizer/t/<int:pk>/withdraw/", views.withdraw, name="withdraw"),
    path("organizer/t/<int:pk>/cancel/", views.cancel, name="cancel"),
    path("organizer/t/<int:pk>/regulation.pdf", views.regulation_working, name="regulation_working"),
    path("organizer/t/<int:pk>/team/", views.team, name="team"),
    path("organizer/t/<int:pk>/team/<int:member_id>/revoke/", views.team_revoke, name="team_revoke"),
    path("organizer/t/<int:pk>/team/<int:member_id>/stable-contact/", views.team_stable_contact, name="team_stable_contact"),
    path("cabinet/invitations/", views.invitations, name="invitations"),
    path("cabinet/invitations/<int:member_id>/", views.invitation_respond, name="invitation_respond"),
]

def _club_mod(name):
    def view(request, *a, **kw):
        from apps.clubs import views as cv

        return getattr(cv, name)(request, *a, **kw)
    return view


def _support_mod(name):
    def view(request, *a, **kw):
        from apps.support import views as sv

        return getattr(sv, name)(request, *a, **kw)
    return view


moderation_patterns = (
    [
        path("", moderation.queue, name="queue"),
        path("panel/", moderation.panel, name="panel"),
        path("moderators/", moderation.moderators, name="moderators"),
        path("t/<int:pk>/", moderation.detail, name="detail"),
        path("t/<int:pk>/decide/", moderation.decide, name="decide"),
        path("clubs/", _club_mod("mod_queue"), name="clubs"),
        path("clubs/<int:pk>/", _club_mod("mod_detail"), name="club"),
        path("support/", _support_mod("mod_queue"), name="support"),
        path("support/<int:pk>/", _support_mod("mod_detail"), name="support_detail"),
    ],
    "moderation",
)
