from django.urls import path

from onboarding import views

urlpatterns = [
    # Chat is the front door.
    path("", views.chat_home, name="chat_home"),
    path("chat/api/", views.chat_api, name="chat_api"),
    path("chat/upload/", views.chat_upload, name="chat_upload"),

    # Summary / export (retained from the original flow).
    path("summary/", views.summary, name="summary"),
    path("summary.json", views.summary_json, name="summary_json"),
    path("reset/", views.reset, name="reset"),

    # Legacy step flow — kept for reference/fallback, no longer the entry point.
    path("welcome/", views.welcome, name="welcome"),
    path("path/", views.path_choice, name="path_choice"),
    path("manual/", views.manual_start, name="manual_start"),
    path("upload/", views.upload, name="upload"),
    path("extract/", views.extract, name="extract"),
    path("step/<slug:step_id>/", views.step, name="step"),
    path("review/", views.review, name="review"),
]
