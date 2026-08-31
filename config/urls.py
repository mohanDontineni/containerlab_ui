from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from studio.views import dashboard
from studio import portal_views

urlpatterns = [
    path("admin/", admin.site.urls), path("accounts/", include("django.contrib.auth.urls")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("api/v1/", include("studio.urls")), path("", dashboard, name="dashboard"),
    path("projects/", portal_views.projects, name="portal-projects"),
    path("projects/new/", portal_views.project_create, name="portal-project-create"),
    path("projects/<uuid:project_id>/", portal_views.project_detail, name="portal-project-detail"),
    path("labs/", portal_views.labs, name="portal-labs"),
    path("labs/new/", portal_views.lab_create, name="portal-lab-create"),
    path("labs/<uuid:lab_id>/workspace/", portal_views.topology_workspace, name="topology-workspace"),
    path("api/v1/topology/templates/", portal_views.topology_catalog, name="topology-catalog"),
    path("api/v1/labs/<uuid:lab_id>/topology/images/", portal_views.topology_images, name="topology-images"),
    path("api/v1/labs/<uuid:lab_id>/topology/", portal_views.topology_document, name="topology-document"),
    path("deployments/", portal_views.deployments, name="portal-deployments"),
    path("images/", portal_views.images, name="portal-images"),
    path("images/register/", portal_views.image_register, name="portal-image-register"),
    path("device-templates/", portal_views.templates, name="portal-templates"),
    path("operations/", portal_views.operations, name="portal-operations"),
    path("settings/", portal_views.settings_view, name="portal-settings"),
]
