from django.urls import path

from . import judging, views

app_name = "conduct"

urlpatterns = [
    path("organizer/t/<int:pk>/conduct/", views.overview, name="overview"),
    path("organizer/t/<int:pk>/conduct/settings/", views.settings_save, name="settings"),
    path("organizer/t/<int:pk>/conduct/finish/", views.finish, name="finish"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/", views.program, name="program"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/live/", views.live, name="live"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/live/save/", views.live_save, name="live_save"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/course/", judging.course_save, name="course"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/field/", judging.jump_live, name="jump_live"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/field/save/", judging.jump_save, name="jump_save"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/panel/", judging.panel, name="panel"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/judge/<str:letter>/", judging.judge_sheet, name="judge_sheet"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/judge/<str:letter>/mark/", judging.judge_mark, name="judge_mark"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/judge/<str:letter>/sign/", judging.judge_sign, name="judge_sign"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/flags/", judging.flags_save, name="flags"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/scorer/", judging.scorer, name="scorer"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/scorer/<str:action>/", judging.scorer_action, name="scorer_action"),
    path("organizer/t/<int:pk>/conduct/p/<int:program_id>/<str:action>/", views.program_action, name="action"),
    path("t/<int:pk>/start/<int:program_id>.xlsx", views.startlist_xlsx, name="startlist_xlsx"),
    path("t/<int:pk>/results/<int:class_id>.xlsx", views.results_xlsx, name="results_xlsx"),
    path("entries/<int:entry_id>/sheets/", judging.my_sheets, name="my_sheets"),
]
