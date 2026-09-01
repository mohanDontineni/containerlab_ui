from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import render
from .models import Lab, LabDeployment, OperationJob, Project
from .operation_presenters import present
from .quotas import normalized_quotas, project_usage
def platform_health():
    try: return {"metrics":cache.get("studio:platform:metrics"),"runtime":cache.get("studio:platform:runtime"),
        "registry":cache.get("studio:platform:registry")}
    except Exception: return {"metrics":None,"runtime":None,"registry":None}
@login_required
def dashboard(request):
    project_filter = Q(revision__lab__project__owner=request.user) | Q(revision__lab__project__memberships__user=request.user)
    deployments = LabDeployment.objects.filter(project_filter).select_related("revision__lab").distinct().order_by("-updated_at")[:8]
    visible_projects = Project.objects.filter(Q(owner=request.user) | Q(memberships__user=request.user),deleted_at__isnull=True).distinct()
    all_deployments = LabDeployment.objects.filter(project_filter).distinct()
    summary = {
        "active": all_deployments.filter(observed_state="running").count(),
        "deploying": all_deployments.filter(observed_state__in=("pending", "deploying")).count(),
        "stopped": all_deployments.filter(observed_state__in=("stopped", "removed")).count(),
        "degraded": all_deployments.filter(observed_state="degraded").count(),
        "failed": all_deployments.filter(observed_state="failed").count(),
        "labs": Lab.objects.filter(project__in=visible_projects,deleted_at__isnull=True).count(),
        "projects": visible_projects.count(),
    }
    summary["attention"]=summary["degraded"]+summary["failed"]
    quota_projects=[]
    for project in visible_projects.order_by("name")[:6]:
        quota_projects.append({"project":project,"limits":normalized_quotas(project),"usage":project_usage(project)})
    operations=OperationJob.objects.filter(owner=request.user).select_related("deployment__revision__lab").order_by("-created_at")
    return render(request, "studio/dashboard.html", {
        "deployments": deployments,
        "operations": operations[:6],
        "failures": [present(job) for job in operations.filter(state="failed")[:3]],
        "quota_projects":quota_projects,
        "summary": summary,
        "platform_health":platform_health(),
    })
