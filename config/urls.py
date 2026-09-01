from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from studio.views import dashboard
from studio import portal_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/password_change/",RedirectView.as_view(pattern_name="portal-settings",permanent=False)),
    path("accounts/password_change/done/",RedirectView.as_view(pattern_name="portal-settings",permanent=False)),
    path("accounts/", include("django.contrib.auth.urls")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("api/v1/", include("studio.urls")), path("", dashboard, name="dashboard"),
    path("projects/", portal_views.projects, name="portal-projects"),
    path("projects/new/", portal_views.project_create, name="portal-project-create"),
    path("projects/<uuid:project_id>/edit/", portal_views.project_edit, name="portal-project-edit"),
    path("projects/<uuid:project_id>/", portal_views.project_detail, name="portal-project-detail"),
    path("labs/", portal_views.labs, name="portal-labs"),
    path("labs/new/", portal_views.lab_create, name="portal-lab-create"),
    path("labs/folders/new/", portal_views.lab_folder_create, name="portal-lab-folder-create"),
    path("labs/folders/<uuid:folder_id>/edit/", portal_views.lab_folder_edit, name="portal-lab-folder-edit"),
    path("labs/folders/<uuid:folder_id>/delete/", portal_views.lab_folder_delete, name="portal-lab-folder-delete"),
    path("labs/<uuid:lab_id>/edit/", portal_views.lab_edit, name="portal-lab-edit"),
    path("labs/<uuid:lab_id>/workspace/", portal_views.topology_workspace, name="topology-workspace"),
    path("users/", portal_views.platform_users, name="portal-users"),
    path("users/<uuid:user_id>/status/", portal_views.platform_user_status, name="portal-user-status"),
    path("users/<uuid:user_id>/password-reset/", portal_views.platform_user_password_reset, name="portal-user-password-reset"),
    path("api/v1/topology/templates/", portal_views.topology_catalog, name="topology-catalog"),
    path("api/v1/labs/<uuid:lab_id>/topology/images/", portal_views.topology_images, name="topology-images"),
    path("api/v1/labs/<uuid:lab_id>/topology/edit-lease/", portal_views.topology_edit_lease, name="topology-edit-lease"),
    path("api/v1/labs/<uuid:lab_id>/topology/", portal_views.topology_document, name="topology-document"),
    path("deployments/", portal_views.deployments, name="portal-deployments"),
    path("deployments/<uuid:deployment_id>/", portal_views.deployment_detail, name="portal-deployment-detail"),
    path("images/", portal_views.images, name="portal-images"),
    path("images/upload/", portal_views.image_upload, name="portal-image-upload"),
    path("images/register/", portal_views.image_register, name="portal-image-register"),
    path("images/credentials/", portal_views.image_credentials, name="portal-image-credentials"),
    path("images/credentials/new/", portal_views.image_credential_manage, name="portal-image-credential-create"),
    path("images/credentials/<uuid:credential_id>/edit/", portal_views.image_credential_manage, name="portal-image-credential-edit"),
    path("images/credentials/<uuid:credential_id>/deactivate/", portal_views.image_credential_deactivate, name="portal-image-credential-deactivate"),
    path("device-templates/", portal_views.templates, name="portal-templates"),
    path("device-templates/new/", portal_views.template_manage, name="portal-template-create"),
    path("device-templates/<uuid:template_id>/", portal_views.template_manage, name="portal-template-detail"),
    path("operations/", portal_views.operations, name="portal-operations"),
    path("audit/", portal_views.audit_trail, name="portal-audit"),
    path("settings/", portal_views.settings_view, name="portal-settings"),
]
