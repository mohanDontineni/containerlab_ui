from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render
from .models import Lab, LabDeployment, OperationJob, Project
@login_required
def dashboard(request):
    project_filter = Q(revision__lab__project__owner=request.user) | Q(revision__lab__project__memberships__user=request.user)
    deployments = LabDeployment.objects.filter(project_filter).select_related("revision__lab").distinct().order_by("-updated_at")[:8]
    visible_projects = Project.objects.filter(Q(owner=request.user) | Q(memberships__user=request.user),deleted_at__isnull=True).distinct()
    all_deployments = LabDeployment.objects.filter(project_filter).distinct()
    summary = {
        "active": all_deployments.filter(observed_state="running").count(),
        "deploying": all_deployments.filter(observed_state__in=("pending", "deploying")).count(),
        "attention": all_deployments.filter(observed_state__in=("failed", "degraded")).count(),
        "labs": Lab.objects.filter(project__in=visible_projects,deleted_at__isnull=True).count(),
        "projects": visible_projects.count(),
    }
    return render(request, "studio/dashboard.html", {
        "deployments": deployments,
        "operations": OperationJob.objects.filter(owner=request.user).order_by("-created_at")[:6],
        "summary": summary,
    })
