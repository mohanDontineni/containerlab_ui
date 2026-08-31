import uuid
import ipaddress
from pathlib import Path
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler
from . import models, serializers
from .permissions import ProjectAccess, project_role
from .runtime import ClabernetesAdapter
from .tasks import execute_operation, reconcile_deployment
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
    @action(detail=True,methods=["post"])
    def deploy(self,request,pk=None):
        lab=self.get_object()
        if project_role(request.user,lab.project) not in ("administrator","editor"):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).select_related("deployment").first()
        if existing:
            if existing.state in ("accepted","scheduled"):
                execute_operation.delay(str(existing.id))
            return Response({"deployment":serializers.DeploymentSerializer(existing.deployment).data,"operation":serializers.OperationSerializer(existing).data},status=202)
        with transaction.atomic():
            # Lock only the lab row. PostgreSQL rejects FOR UPDATE across the nullable
            # current_draft outer join, while SQLite silently permits it.
            lab=models.Lab.objects.select_for_update().get(pk=lab.pk)
            revision=lab.current_draft
            if not revision or not revision.nodes.exists(): return Response({"error":{"code":"empty_topology","details":"Save at least one device before deployment."}},status=422)
            errors=ClabernetesAdapter.validate_topology(revision)
            if errors: return Response({"error":{"code":"topology_not_deployable","details":errors}},status=422)
            revision.immutable=True; revision.save(update_fields=["immutable","updated_at"])
            lab.current_draft=None; lab.save(update_fields=["current_draft","updated_at"])
            deployment_id=uuid.uuid4()
            deployment=models.LabDeployment.objects.create(id=deployment_id,revision=revision,namespace=f"clab-{deployment_id.hex[:20]}",
                cluster_identity="kubernetes-admin@kubernetes",runtime_version="0.8.0",observed_state=models.LabDeployment.State.PENDING)
            job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type="deploy_lab",target_id=deployment.id,idempotency_key=key,state="scheduled")
            transaction.on_commit(lambda: execute_operation.delay(str(job.id)))
        return Response({"deployment":serializers.DeploymentSerializer(deployment).data,"operation":serializers.OperationSerializer(job).data},status=202)

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
    def _require_operator(self,deployment):
        if project_role(self.request.user,deployment.revision.lab.project) not in ("administrator","editor"):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
    @action(detail=True,methods=["get"])
    def runtime(self,request,pk=None):
        deployment=self.get_object()
        return Response({"deployment":serializers.DeploymentSerializer(deployment).data,
            "devices":serializers.DeviceInstanceSerializer(deployment.devices.select_related("lab_node__template_version"),many=True).data,
            "operations":serializers.OperationSerializer(deployment.operations.order_by("-created_at")[:20],many=True).data})
    @action(detail=True,methods=["post"])
    def refresh(self,request,pk=None):
        deployment=self.get_object()
        self._require_operator(deployment)
        reconcile_deployment.delay(str(deployment.id))
        return Response({"state":"scheduled"},status=202)
    @action(detail=True,methods=["post"])
    def operations(self,request,pk=None):
        deployment=self.get_object(); op=request.data.get("operation")
        self._require_operator(deployment)
        if op not in ("deploy_lab","stop_lab","delete_runtime"): return Response({"error":{"code":"unsupported_operation"}},status=422)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        try:
            with transaction.atomic(): job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type=op,target_id=deployment.id,idempotency_key=key)
        except Exception:
            job=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
            if not job: return Response({"error":{"code":"operation_conflict"}},status=409)
        execute_operation.delay(str(job.id)); return Response(serializers.OperationSerializer(job).data,status=202)
    @action(detail=True,methods=["post"])
    def diagnostics(self,request,pk=None):
        deployment=self.get_object(); self._require_operator(deployment)
        try: target=str(ipaddress.ip_address(request.data.get("target","")))
        except ValueError: return Response({"error":{"code":"invalid_target","details":"Enter a valid IPv4 or IPv6 address."}},status=422)
        try: node_id=uuid.UUID(str(request.data.get("node_id")))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_node"}},status=422)
        if not deployment.revision.nodes.filter(id=node_id).exists(): return Response({"error":{"code":"invalid_node"}},status=422)
        count=request.data.get("count",3); timeout=request.data.get("timeout",2)
        if not isinstance(count,int) or not 1<=count<=5 or not isinstance(timeout,int) or not 1<=timeout<=5:
            return Response({"error":{"code":"invalid_bounds","details":"Count and timeout must be between 1 and 5."}},status=422)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        job=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if not job:
            try: job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type="ping",target_id=deployment.id,
                idempotency_key=key,state="scheduled",request_payload={"node_id":str(node_id),"target":target,"count":count,"timeout":timeout})
            except IntegrityError:
                return Response({"error":{"code":"diagnostic_in_progress","details":"Wait for the active diagnostic to finish."}},status=409)
        if job.state in ("accepted","scheduled"): execute_operation.delay(str(job.id))
        return Response(serializers.OperationSerializer(job).data,status=202)
