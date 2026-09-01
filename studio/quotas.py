from django.db.models import Count,Max,Sum
from django.utils import timezone
from rest_framework.exceptions import APIException
from .models import ImageArtifact, LabDeployment, LabNode, ProjectMembership, UploadSession

DEFAULT_PROJECT_QUOTAS={
    "max_labs":50,
    "max_nodes_per_lab":100,
    "max_running_deployments":5,
    "max_image_bytes":100*1024**3,
    "max_members":25,
}

QUOTA_BOUNDS={
    "max_labs":(1,500),
    "max_nodes_per_lab":(1,250),
    "max_running_deployments":(1,100),
    "max_image_bytes":(1024**3,10*1024**4),
    "max_members":(1,500),
}

def normalized_quotas(project):
    configured=project.quotas if isinstance(project.quotas,dict) else {}
    return {key:configured.get(key,default) for key,default in DEFAULT_PROJECT_QUOTAS.items()}

def validate_quotas(payload,current=None):
    if not isinstance(payload,dict): raise ValueError("Quota settings must be an object")
    unknown=set(payload)-set(DEFAULT_PROJECT_QUOTAS)
    if unknown: raise ValueError(f"Unknown quota setting: {sorted(unknown)[0]}")
    values={**DEFAULT_PROJECT_QUOTAS,**(current or {}),**payload}
    for key,(minimum,maximum) in QUOTA_BOUNDS.items():
        value=values[key]
        if isinstance(value,bool) or not isinstance(value,int) or not minimum<=value<=maximum:
            raise ValueError(f"{key} must be an integer between {minimum} and {maximum}")
    return values

def project_usage(project):
    image_bytes=ImageArtifact.objects.filter(project=project,deleted_at__isnull=True).aggregate(total=Sum("byte_size"))["total"] or 0
    reserved_upload_bytes=UploadSession.objects.filter(project=project,status=UploadSession.Status.ACTIVE,expires_at__gt=timezone.now()).aggregate(total=Sum("expected_size"))["total"] or 0
    largest_draft_nodes=LabNode.objects.filter(revision__draft_for_labs__project=project).values("revision_id").annotate(total=Count("id")).aggregate(maximum=Max("total"))["maximum"] or 0
    running=LabDeployment.objects.filter(revision__lab__project=project,observed_state__in=(
        LabDeployment.State.PENDING,LabDeployment.State.DEPLOYING,LabDeployment.State.RUNNING,LabDeployment.State.DEGRADED)).count()
    return {"labs":project.labs.count(),"members":ProjectMembership.objects.filter(project=project).count()+1,
        "running_deployments":running,"image_bytes":image_bytes,"reserved_upload_bytes":reserved_upload_bytes,"largest_draft_nodes":largest_draft_nodes}

def quota_exceeded(code,limit,used,requested=1):
    return {"error":{"code":"project_quota_exceeded","details":{"resource":code,"limit":limit,"used":used,"requested":requested}}}

class ProjectQuotaExceeded(APIException):
    status_code=409
    default_code="project_quota_exceeded"
    default_detail="Project quota exceeded"
    def __init__(self,resource,limit,used,requested=1):
        super().__init__({"resource":resource,"limit":limit,"used":used,"requested":requested})
