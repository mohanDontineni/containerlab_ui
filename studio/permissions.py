from rest_framework.permissions import BasePermission
from .models import ProjectMembership

def project_role(user, project):
    if not user.is_authenticated: return None
    if user.is_superuser or project.owner_id == user.id: return ProjectMembership.Role.ADMIN
    return ProjectMembership.objects.filter(project=project, user=user).values_list("role", flat=True).first()

class ProjectAccess(BasePermission):
    def has_object_permission(self, request, view, obj):
        project = obj if obj.__class__.__name__ == "Project" else getattr(obj, "project", None)
        role = project_role(request.user, project) if project else None
        return bool(role) and (request.method in ("GET", "HEAD", "OPTIONS") or role in ("administrator", "editor"))

