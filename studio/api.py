import uuid
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from . import models, serializers
from .permissions import ProjectAccess, project_role
from .tasks import execute_operation
from .uploads import UploadError, append_chunk, finalize

def exception_handler(exc,context):
    response=drf_exception_handler(exc,context)
    if response is not None: response.data={"error":{"code":getattr(exc,"default_code","validation_error"),"details":response.data}}
    return response

def visible_projects(user):
    return models.Project.objects.filter(Q(owner=user)|Q(memberships__user=user)).distinct()

class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class=serializers.ProjectSerializer; permission_classes=[ProjectAccess]
    def get_queryset(self): return visible_projects(self.request.user)
    def perform_create(self,serializer): serializer.save(owner=self.request.user)

class LabViewSet(viewsets.ModelViewSet):
    serializer_class=serializers.LabSerializer; permission_classes=[ProjectAccess]
    def get_queryset(self): return models.Lab.objects.filter(project__in=visible_projects(self.request.user)).select_related("project")
    def perform_create(self,serializer):
        project=serializer.validated_data["project"]
        if project_role(self.request.user,project) not in ("administrator","editor"): from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        serializer.save()

class UploadViewSet(viewsets.ModelViewSet):
    serializer_class=serializers.UploadSessionSerializer
    def get_queryset(self): return models.UploadSession.objects.filter(owner=self.request.user,project__in=visible_projects(self.request.user))
    def perform_create(self,serializer):
        size=serializer.validated_data["expected_size"]
        if size>settings.MAX_UPLOAD_BYTES: from rest_framework.exceptions import ValidationError; raise ValidationError({"expected_size":"Exceeds configured limit"})
        uid=uuid.uuid4(); destination=Path(settings.MEDIA_ROOT)/"quarantine"/str(uid)
        serializer.save(id=uid,owner=self.request.user,artifact_destination=str(destination),expires_at=timezone.now()+timezone.timedelta(hours=24))
    @action(detail=True,methods=["put"],url_path="chunks")
    def chunk(self,request,pk=None):
        session=self.get_object()
        try: written=append_chunk(session,request.user,int(request.headers.get("Upload-Offset","-1")),request.stream)
        except UploadError as e: return Response({"error":{"code":"upload_conflict","details":str(e)}},status=status.HTTP_409_CONFLICT)
        return Response({"received":written,"offset":session.received_bytes},status=204,headers={"Upload-Offset":str(session.received_bytes)})
    @action(detail=True,methods=["post"])
    def complete(self,request,pk=None):
        try: artifact=finalize(self.get_object(),request.user)
        except UploadError as e: return Response({"error":{"code":"upload_invalid","details":str(e)}},status=422)
        return Response(serializers.ImageArtifactSerializer(artifact).data,status=201)
    @action(detail=True,methods=["post"])
    def cancel(self,request,pk=None):
        session=self.get_object(); session.status=models.UploadSession.Status.CANCELLED; session.save(update_fields=["status","updated_at"]); return Response(status=204)

class DeploymentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=serializers.DeploymentSerializer
    def get_queryset(self): return models.LabDeployment.objects.filter(revision__lab__project__in=visible_projects(self.request.user))
    @action(detail=True,methods=["post"])
    def operations(self,request,pk=None):
        deployment=self.get_object(); op=request.data.get("operation")
        if op not in ("deploy_lab","stop_lab","delete_runtime"): return Response({"error":{"code":"unsupported_operation"}},status=422)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        try:
            with transaction.atomic(): job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type=op,target_id=deployment.id,idempotency_key=key)
        except Exception:
            job=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
            if not job: return Response({"error":{"code":"operation_conflict"}},status=409)
        execute_operation.delay(str(job.id)); return Response(serializers.OperationSerializer(job).data,status=202)

