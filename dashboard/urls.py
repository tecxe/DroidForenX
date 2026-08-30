from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="dashboard/login.html"),
         name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.home, name="home"),
    path("case/<int:case_pk>/", views.case_detail, name="case"),
    path("case/<int:case_pk>/acquire/", views.acquire_view, name="acquire"),
    path("case/<int:case_pk>/report/", views.report_generate, name="report_generate"),
    path("evidence/<int:evidence_pk>/", views.evidence_detail, name="evidence"),
    path("artifact/<int:artifact_pk>/", views.artifact_detail, name="artifact"),
    path("search/", views.search_view, name="search"),
    path("report/<int:report_pk>/download/", views.report_download,
         name="report_download"),
    path("audit/", views.audit_view, name="audit"),
]
