from django.urls import path

from onboarding import views

urlpatterns = [
    path("", views.welcome, name="welcome"),
    path("path/", views.path_choice, name="path_choice"),
    path("manual/", views.manual_start, name="manual_start"),
    path("upload/", views.upload, name="upload"),
    path("extract/", views.extract, name="extract"),
    path("step/<slug:step_id>/", views.step, name="step"),
    path("review/", views.review, name="review"),
    path("summary/", views.summary, name="summary"),
    path("summary.json", views.summary_json, name="summary_json"),
    path("reset/", views.reset, name="reset"),
]
