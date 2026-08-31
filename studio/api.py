import uuid
import ipaddress
import hashlib
import json
from pathlib import Path
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import BaseParser,JSONParser
from rest_framework.views import exception_handler as drf_exception_handler
from . import models, serializers
from .permissions import ProjectAccess, project_role
from .runtime import ClabernetesAdapter
from .tasks import execute_operation, reconcile_deployment
from .uploads import UploadError, append_chunk, finalize
from .bundles import BundleError, LabBundleParser, export_lab_bundle, import_lab_bundle
from .configurations import decrypt_configuration
from .quotas import ProjectQuotaExceeded,normalized_quotas,project_usage,quota_exceeded,validate_quotas

class OctetStreamParser(BaseParser):
    media_type="application/octet-stream"
    def parse(self,stream,media_type=None,parser_context=None): return stream

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
    @action(detail=True,methods=["get","patch"])
    def quotas(self,request,pk=None):
        project=self.get_object()
        if request.method=="GET": return Response({"limits":normalized_quotas(project),"usage":project_usage(project)})
        if project_role(request.user,project)!=models.ProjectMembership.Role.ADMIN:
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        with transaction.atomic():
            project=models.Project.objects.select_for_update().get(pk=project.pk)
            try: values=validate_quotas(request.data,normalized_quotas(project))
            except ValueError as exc: return Response({"error":{"code":"invalid_project_quotas","details":str(exc)}},status=422)
            usage=project_usage(project)
            occupied={"max_labs":usage["labs"],"max_members":usage["members"],"max_running_deployments":usage["running_deployments"],
                "max_image_bytes":usage["image_bytes"]+usage["reserved_upload_bytes"],"max_nodes_per_lab":usage["largest_draft_nodes"]}
            conflicts={key:{"requested_limit":values[key],"current_usage":used} for key,used in occupied.items() if values[key]<used}
            if conflicts: return Response({"error":{"code":"quota_below_current_usage","details":conflicts}},status=409)
            previous=normalized_quotas(project);project.quotas=values;project.save(update_fields=["quotas","updated_at"])
            models.AuditEvent.objects.create(actor=request.user,project=project,action="project.quotas_changed",target_type="Project",target_id=project.id,
                correlation_id=getattr(request,"correlation_id",""),metadata={"previous":previous,"limits":values})
        return Response({"limits":values,"usage":usage})
    @action(detail=True,methods=["get","post"])
    def members(self,request,pk=None):
        project=self.get_object()
        if request.method=="GET":
            rows=project.memberships.select_related("user").order_by("user__username")
            return Response({"owner":{"id":str(project.owner_id),"username":project.owner.username,"role":"administrator"},"members":serializers.MembershipSerializer(rows,many=True).data})
        if project_role(request.user,project)!=models.ProjectMembership.Role.ADMIN:
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        username=str(request.data.get("username","")).strip()
        role=str(request.data.get("role",models.ProjectMembership.Role.VIEWER))
        if role not in models.ProjectMembership.Role.values: return Response({"error":{"code":"invalid_project_role"}},status=422)
        user=models.User.objects.filter(username__iexact=username,is_active=True).first()
        if not user: return Response({"error":{"code":"user_not_found","details":"No active account has that username."}},status=404)
        if user.id==project.owner_id: return Response({"error":{"code":"owner_membership_immutable"}},status=409)
        with transaction.atomic():
            project=models.Project.objects.select_for_update().get(pk=project.pk)
            membership=models.ProjectMembership.objects.filter(project=project,user=user).first()
            if not membership:
                usage=project.memberships.count()+1;limit=normalized_quotas(project)["max_members"]
                if usage>=limit: return Response(quota_exceeded("members",limit,usage),status=409)
                membership=models.ProjectMembership.objects.create(project=project,user=user,role=role);created=True
            else: membership.role=role;membership.save(update_fields=["role","updated_at"]);created=False
        models.AuditEvent.objects.create(actor=request.user,project=project,action="project.member_added" if created else "project.member_role_changed",
            target_type="ProjectMembership",target_id=membership.id,correlation_id=getattr(request,"correlation_id",""),metadata={"username":user.username,"role":role})
        return Response(serializers.MembershipSerializer(membership).data,status=201 if created else 200)

class MembershipViewSet(viewsets.ModelViewSet):
    serializer_class=serializers.MembershipSerializer
    http_method_names=["get","patch","delete","head","options"]
    def get_queryset(self): return models.ProjectMembership.objects.filter(project__in=visible_projects(self.request.user)).select_related("project","user")
    def _require_admin(self,membership):
        if project_role(self.request.user,membership.project)!=models.ProjectMembership.Role.ADMIN:
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
    def partial_update(self,request,*args,**kwargs):
        membership=self.get_object(); self._require_admin(membership)
        role=str(request.data.get("role",""))
        if role not in models.ProjectMembership.Role.values: return Response({"error":{"code":"invalid_project_role"}},status=422)
        previous=membership.role; membership.role=role; membership.save(update_fields=["role","updated_at"])
        models.AuditEvent.objects.create(actor=request.user,project=membership.project,action="project.member_role_changed",target_type="ProjectMembership",
            target_id=membership.id,correlation_id=getattr(request,"correlation_id",""),metadata={"username":membership.user.username,"previous_role":previous,"role":role})
        return Response(self.get_serializer(membership).data)
    def destroy(self,request,*args,**kwargs):
        membership=self.get_object(); self._require_admin(membership)
        metadata={"username":membership.user.username,"role":membership.role}; project=membership.project; target_id=membership.id
        membership.delete()
        models.AuditEvent.objects.create(actor=request.user,project=project,action="project.member_removed",target_type="ProjectMembership",
            target_id=target_id,correlation_id=getattr(request,"correlation_id",""),metadata=metadata)
        return Response(status=204)

class LabViewSet(viewsets.ModelViewSet):
    serializer_class=serializers.LabSerializer; permission_classes=[ProjectAccess]
    def get_queryset(self): return models.Lab.objects.filter(project__in=visible_projects(self.request.user)).select_related("project")
    def perform_create(self,serializer):
        project=serializer.validated_data["project"]
        if project_role(self.request.user,project) not in ("administrator","editor"): from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        with transaction.atomic():
            project=models.Project.objects.select_for_update().get(pk=project.pk)
            used=project.labs.count();limit=normalized_quotas(project)["max_labs"]
            if used>=limit: raise ProjectQuotaExceeded("labs",limit,used)
            serializer.save(project=project)
    @action(detail=True,methods=["get"],url_path="export")
    def export_bundle(self,request,pk=None):
        lab=self.get_object()
        payload=json.dumps(export_lab_bundle(lab),indent=2,sort_keys=True).encode()
        models.AuditEvent.objects.create(actor=request.user,project=lab.project,action="lab.exported",target_type="Lab",target_id=lab.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"bytes":len(payload),"format":"io.containerlab.studio.lab/v1"})
        response=HttpResponse(payload,content_type="application/vnd.containerlab.studio.lab+json")
        response["Content-Disposition"]=f'attachment; filename="{slugify(lab.name)[:80] or "lab"}.clabstudio.json"'
        response["X-Content-Type-Options"]="nosniff"
        return response
    @action(detail=True,methods=["post"],url_path="import",parser_classes=[LabBundleParser,JSONParser])
    def import_bundle(self,request,pk=None):
        lab=self.get_object()
        if project_role(request.user,lab.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        raw=request.data
        try: revision=import_lab_bundle(lab,request.user,raw)
        except BundleError as exc: return Response({"error":{"code":"invalid_lab_bundle","details":str(exc)}},status=422)
        models.AuditEvent.objects.create(actor=request.user,project=lab.project,action="lab.imported",target_type="Lab",target_id=lab.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"revision":str(revision.id),"bytes":len(raw)})
        return Response({"revision_id":str(revision.id),"revision_number":revision.revision_number,"edit_version":revision.edit_version,
                         "node_count":revision.nodes.count(),"link_count":revision.links.count()},status=201)
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
            project=models.Project.objects.select_for_update().get(pk=lab.project_id)
            usage=project_usage(project);limit=normalized_quotas(project)["max_running_deployments"]
            if usage["running_deployments"]>=limit: return Response(quota_exceeded("running_deployments",limit,usage["running_deployments"]),status=409)
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
        project=serializer.validated_data["project"]
        if project_role(self.request.user,project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        with transaction.atomic():
            project=models.Project.objects.select_for_update().get(pk=project.pk)
            usage=project_usage(project);limit=normalized_quotas(project)["max_image_bytes"]
            if usage["image_bytes"]+usage["reserved_upload_bytes"]+size>limit:
                raise ProjectQuotaExceeded("image_bytes",limit,usage["image_bytes"]+usage["reserved_upload_bytes"],size)
            uid=uuid.uuid4(); destination=Path(settings.MEDIA_ROOT)/"quarantine"/str(uid)
            session=serializer.save(id=uid,project=project,owner=self.request.user,artifact_destination=str(destination),expires_at=timezone.now()+timezone.timedelta(hours=24))
        models.AuditEvent.objects.create(actor=self.request.user,project=project,action="image.upload_created",target_type="UploadSession",target_id=session.id,
            correlation_id=getattr(self.request,"correlation_id",""),metadata={"filename":session.original_filename,"expected_size":size})
    @action(detail=True,methods=["put"],url_path="chunks",parser_classes=[OctetStreamParser])
    def chunk(self,request,pk=None):
        session=self.get_object()
        try: written=append_chunk(session,request.user,int(request.headers.get("Upload-Offset","-1")),request.data)
        except UploadError as e: return Response({"error":{"code":"upload_conflict","details":str(e)}},status=status.HTTP_409_CONFLICT)
        next_offset=int(request.headers.get("Upload-Offset","0"))+written
        return Response(status=204,headers={"Upload-Offset":str(next_offset)})
    @action(detail=True,methods=["post"])
    def complete(self,request,pk=None):
        try: artifact=finalize(self.get_object(),request.user)
        except UploadError as e: return Response({"error":{"code":"upload_invalid","details":str(e)}},status=422)
        models.AuditEvent.objects.create(actor=request.user,project=artifact.project,action="image.inspected",target_type="ImageArtifact",target_id=artifact.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"format":artifact.detected_format,"validation_status":artifact.validation_status,"checksum":artifact.checksum})
        return Response(serializers.ImageArtifactSerializer(artifact).data,status=201)
    @action(detail=True,methods=["post"])
    def cancel(self,request,pk=None):
        session=self.get_object(); Path(session.artifact_destination).unlink(missing_ok=True)
        session.status=models.UploadSession.Status.CANCELLED; session.save(update_fields=["status","updated_at"])
        models.AuditEvent.objects.create(actor=request.user,project=session.project,action="image.upload_cancelled",target_type="UploadSession",target_id=session.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"received_bytes":session.received_bytes})
        return Response(status=204)

class ImageArtifactViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=serializers.ImageArtifactSerializer
    def get_queryset(self): return models.ImageArtifact.objects.filter(project__in=visible_projects(self.request.user)).select_related("project")
    @action(detail=True,methods=["post"])
    def publish(self,request,pk=None):
        artifact=self.get_object()
        if project_role(request.user,artifact.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        if artifact.validation_status!=models.ImageArtifact.Validation.VALIDATED or artifact.detected_format not in ("docker-archive","oci-archive"):
            return Response({"error":{"code":"image_not_publishable","details":"Only validated Docker or OCI archives can be published."}},status=422)
        if not artifact.license_acknowledged: return Response({"error":{"code":"license_acknowledgement_required"}},status=422)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.operation_type!="publish_image" or existing.target_id!=artifact.id: return Response({"error":{"code":"idempotency_conflict"}},status=409)
            if existing.state in ("accepted","scheduled"): execute_operation.delay(str(existing.id))
            return Response(serializers.OperationSerializer(existing).data,status=202)
        published=artifact.published_images.filter(lifecycle_status="ready").first()
        force=request.data.get("force") is True
        if published and not force: return Response(serializers.PublishedImageSerializer(published).data,status=200)
        with transaction.atomic():
            if published:
                published.lifecycle_status="reconciling";published.save(update_fields=["lifecycle_status","updated_at"])
            build_id=uuid.uuid4(); build=models.ImageBuild.objects.create(id=build_id,artifact=artifact,recipe_version="node-containerd-v1",job_identity=f"studio-publish-{build_id.hex[:20]}")
            job=models.OperationJob.objects.create(owner=request.user,operation_type="publish_image",target_id=artifact.id,idempotency_key=key,state="scheduled",request_payload={"build_id":str(build.id),"force":force})
            models.AuditEvent.objects.create(actor=request.user,project=artifact.project,action="image.republication_scheduled" if force else "image.publication_scheduled",target_type="ImageArtifact",target_id=artifact.id,correlation_id=getattr(request,"correlation_id",""),metadata={"build":str(build.id),"operation":str(job.id),"force":force})
            transaction.on_commit(lambda: execute_operation.delay(str(job.id)))
        return Response(serializers.OperationSerializer(job).data,status=202)

class DeploymentViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class=serializers.DeploymentSerializer
    def get_queryset(self): return models.LabDeployment.objects.filter(revision__lab__project__in=visible_projects(self.request.user))
    def _require_operator(self,deployment):
        if project_role(self.request.user,deployment.revision.lab.project) not in ("administrator","editor"):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
    @action(detail=True,methods=["get"],url_path="configurations")
    def configurations(self,request,pk=None):
        deployment=self.get_object();self._require_operator(deployment)
        events=models.AuditEvent.objects.filter(action="configuration.collected",project=deployment.revision.lab.project,
            metadata__deployment=str(deployment.id)).order_by("-occurred_at")[:100]
        event_by_target={event.target_id:event for event in events}
        rows=models.ConfigurationVersion.objects.filter(id__in=event_by_target).order_by("-created_at")
        return Response([{"id":str(row.id),"name":row.name,"version":row.version,"checksum":row.checksum,
            "byte_size":event_by_target[row.id].metadata.get("byte_size",0),"created_at":row.created_at,
            "download":f"/api/v1/deployments/{deployment.id}/configurations/{row.id}/download/"} for row in rows])
    @action(detail=True,methods=["get"],url_path=r"configurations/(?P<configuration_id>[^/.]+)/download")
    def download_configuration(self,request,pk=None,configuration_id=None):
        deployment=self.get_object();self._require_operator(deployment)
        try: configuration_uuid=uuid.UUID(str(configuration_id))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_configuration"}},status=422)
        configuration=models.ConfigurationVersion.objects.filter(id=configuration_uuid,project=deployment.revision.lab.project).first()
        evidence=models.AuditEvent.objects.filter(action="configuration.collected",target_id=configuration_uuid,
            metadata__deployment=str(deployment.id)).exists()
        if not configuration or not evidence: return Response({"error":{"code":"configuration_not_found"}},status=404)
        payload=decrypt_configuration(configuration.encrypted_content).encode("utf-8")
        models.AuditEvent.objects.create(actor=request.user,project=configuration.project,action="configuration.downloaded",
            target_type="ConfigurationVersion",target_id=configuration.id,correlation_id=getattr(request,"correlation_id",""),
            metadata={"deployment":str(deployment.id),"version":configuration.version,"checksum":configuration.checksum,"byte_size":len(payload)})
        response=HttpResponse(payload,content_type="text/plain; charset=utf-8")
        response["Content-Disposition"]=f'attachment; filename="{slugify(configuration.name)[:80] or "configuration"}-v{configuration.version}.txt"'
        response["Cache-Control"]="no-store";response["X-Content-Type-Options"]="nosniff"
        return response
    @action(detail=True,methods=["get"])
    def runtime(self,request,pk=None):
        deployment=self.get_object()
        conditions=deployment.resource_identities.get("link_conditions",{})
        links=deployment.revision.links.select_related("endpoint_a__node","endpoint_b__node")
        return Response({"deployment":serializers.DeploymentSerializer(deployment).data,
            "devices":serializers.DeviceInstanceSerializer(deployment.devices.select_related("lab_node__template_version").prefetch_related("lab_node__interfaces"),many=True).data,
            "links":[{"id":str(link.id),"label":link.label,"endpoint_a":{"node":link.endpoint_a.node.name,"interface":link.endpoint_a.name},
                "endpoint_b":{"node":link.endpoint_b.node.name,"interface":link.endpoint_b.name},"condition":conditions.get(str(link.id),{"active":False,
                    "disabled":False,"latency_ms":0,"jitter_ms":0,"loss_percent":0,"rate_kbps":0})} for link in links],
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
    @action(detail=True,methods=["post"],url_path="device-operations")
    def device_operations(self,request,pk=None):
        deployment=self.get_object(); self._require_operator(deployment)
        operation=str(request.data.get("operation",""))
        if operation not in ("restart_device","collect_configuration"):
            return Response({"error":{"code":"unsupported_operation","details":"Supported device operations are restart and configuration collection."}},status=422)
        try: device_id=uuid.UUID(str(request.data.get("device_id")))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_device"}},status=422)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.target_id!=device_id or existing.operation_type!=operation: return Response({"error":{"code":"idempotency_conflict"}},status=409)
            if existing.state in ("accepted","scheduled"): execute_operation.delay(str(existing.id))
            return Response(serializers.OperationSerializer(existing).data,status=202)
        with transaction.atomic():
            device=deployment.devices.select_for_update().select_related("lab_node").filter(id=device_id).first()
            if not device: return Response({"error":{"code":"invalid_device"}},status=422)
            if device.observed_readiness!="ready" or not device.runtime_resources.get("pod"): return Response({"error":{"code":"device_not_ready"}},status=409)
            if operation=="collect_configuration" and not device.lab_node.template_version.launch_profile.get("configuration_collect_command"):
                return Response({"error":{"code":"configuration_collection_unsupported","details":"This device template has no verified runtime collector."}},status=422)
            if models.OperationJob.objects.filter(target_id=device.id,state__in=("accepted","scheduled","started")).exists():
                return Response({"error":{"code":"device_operation_in_progress"}},status=409)
            job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type=operation,
                target_id=device.id,idempotency_key=key,state="scheduled",request_payload={"device_id":str(device.id)})
            models.AuditEvent.objects.create(actor=request.user,project=deployment.revision.lab.project,action=f"device.{operation.removesuffix('_device')}",
                target_type="DeviceInstance",target_id=device.id,correlation_id=getattr(request,"correlation_id",""),metadata={"operation_job":str(job.id),"node":device.lab_node.name})
            transaction.on_commit(lambda: execute_operation.delay(str(job.id)))
        return Response(serializers.OperationSerializer(job).data,status=202)
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
    @action(detail=True,methods=["get","post"],url_path="link-conditions")
    def link_conditions(self,request,pk=None):
        deployment=self.get_object()
        if request.method=="GET":
            conditions=deployment.resource_identities.get("link_conditions",{})
            links=deployment.revision.links.select_related("endpoint_a__node","endpoint_b__node")
            return Response([{"id":str(link.id),"label":link.label,"endpoint_a":{"node":link.endpoint_a.node.name,"interface":link.endpoint_a.name},
                "endpoint_b":{"node":link.endpoint_b.node.name,"interface":link.endpoint_b.name},"condition":conditions.get(str(link.id),{"active":False,
                    "disabled":False,"latency_ms":0,"jitter_ms":0,"loss_percent":0,"rate_kbps":0})} for link in links])
        self._require_operator(deployment)
        try: link_id=uuid.UUID(str(request.data.get("link_id")))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_link"}},status=422)
        link=deployment.revision.links.filter(id=link_id).first()
        if not link: return Response({"error":{"code":"invalid_link"}},status=422)
        disabled=request.data.get("disabled",False)
        if not isinstance(disabled,bool): return Response({"error":{"code":"invalid_link_condition"}},status=422)
        values={}
        for field,maximum in (("latency_ms",2000),("jitter_ms",1000),("rate_kbps",10_000_000)):
            value=request.data.get(field,0)
            if isinstance(value,bool) or not isinstance(value,int) or value<0 or value>maximum:
                return Response({"error":{"code":"invalid_link_condition","details":f"{field} is outside its supported range."}},status=422)
            values[field]=value
        loss=request.data.get("loss_percent",0)
        if isinstance(loss,bool) or not isinstance(loss,(int,float)) or loss<0 or loss>100:
            return Response({"error":{"code":"invalid_link_condition","details":"loss_percent must be between 0 and 100."}},status=422)
        if values["jitter_ms"] and not values["latency_ms"]:
            return Response({"error":{"code":"invalid_link_condition","details":"Jitter requires non-zero latency."}},status=422)
        if values["rate_kbps"] and values["rate_kbps"]<64:
            return Response({"error":{"code":"invalid_link_condition","details":"Rate must be zero or at least 64 Kbit/s."}},status=422)
        condition={"active":disabled or bool(values["latency_ms"] or loss or values["rate_kbps"]),"disabled":disabled,
            "latency_ms":values["latency_ms"],"jitter_ms":values["jitter_ms"],"loss_percent":float(loss),"rate_kbps":values["rate_kbps"]}
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.operation_type!="set_link_condition" or existing.deployment_id!=deployment.id or existing.target_id!=link.id or existing.request_payload.get("condition")!=condition:
                return Response({"error":{"code":"idempotency_conflict"}},status=409)
            if existing.state in ("accepted","scheduled"): execute_operation.delay(str(existing.id))
            return Response(serializers.OperationSerializer(existing).data,status=202)
        if models.OperationJob.objects.filter(target_id=link.id,state__in=("accepted","scheduled","started")).exists():
            return Response({"error":{"code":"link_operation_in_progress"}},status=409)
        job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type="set_link_condition",target_id=link.id,
            idempotency_key=key,state="scheduled",request_payload={"condition":condition})
        models.AuditEvent.objects.create(actor=request.user,project=deployment.revision.lab.project,action="link.condition_changed",target_type="LabLink",
            target_id=link.id,correlation_id=getattr(request,"correlation_id",""),metadata={"operation_job":str(job.id),"condition":condition})
        transaction.on_commit(lambda: execute_operation.delay(str(job.id)))
        return Response(serializers.OperationSerializer(job).data,status=202)
    @action(detail=True,methods=["get","post"],url_path="captures")
    def captures(self,request,pk=None):
        deployment=self.get_object()
        if request.method=="GET":
            rows=models.CaptureSession.objects.filter(deployment=deployment).select_related("interface__node","owner").order_by("-created_at")[:50]
            return Response([{"id":str(row.id),"device":row.interface.node.name,"interface":row.interface.name,"status":row.status,
                "created_at":row.created_at,"expires_at":row.expires_at,"download":f"/api/v1/deployments/{deployment.id}/captures/{row.id}/download/" if row.status=="complete" else None} for row in rows])
        self._require_operator(deployment)
        try: device_id=uuid.UUID(str(request.data.get("device_id"))); interface_id=uuid.UUID(str(request.data.get("interface_id")))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_capture_target"}},status=422)
        device=deployment.devices.select_related("lab_node").filter(id=device_id).first()
        interface=models.LabInterface.objects.filter(id=interface_id,node__revision=deployment.revision).select_related("node").first()
        if not device or not interface or interface.node_id!=device.lab_node_id: return Response({"error":{"code":"invalid_capture_target"}},status=422)
        if device.observed_readiness!="ready" or not device.runtime_resources.get("pod"): return Response({"error":{"code":"device_not_ready"}},status=409)
        duration=request.data.get("duration",10); packet_limit=request.data.get("packet_limit",500)
        if not isinstance(duration,int) or not 1<=duration<=30 or not isinstance(packet_limit,int) or not 1<=packet_limit<=5000:
            return Response({"error":{"code":"invalid_capture_bounds","details":"Duration must be 1-30 seconds and packet limit 1-5000."}},status=422)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            expected={"duration":duration,"packet_limit":packet_limit,"device_id":str(device.id),"interface_id":str(interface.id)}
            if existing.operation_type!="capture_packets" or existing.deployment_id!=deployment.id or any(existing.request_payload.get(k)!=v for k,v in expected.items()):
                return Response({"error":{"code":"idempotency_conflict"}},status=409)
            if existing.state in ("accepted","scheduled"): execute_operation.delay(str(existing.id))
            return Response(serializers.OperationSerializer(existing).data,status=202)
        try:
            with transaction.atomic():
                capture=models.CaptureSession.objects.create(deployment=deployment,interface=interface,owner=request.user,status="scheduled",
                    expires_at=timezone.now()+timezone.timedelta(hours=24))
                job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type="capture_packets",target_id=capture.id,
                    idempotency_key=key,state="scheduled",request_payload={"duration":duration,"packet_limit":packet_limit,"capture_id":str(capture.id),
                        "device_id":str(device.id),"interface_id":str(interface.id)})
                models.AuditEvent.objects.create(actor=request.user,project=deployment.revision.lab.project,action="capture.started",target_type="CaptureSession",
                    target_id=capture.id,correlation_id=getattr(request,"correlation_id",""),metadata={"node":device.lab_node.name,"interface":interface.name,"duration":duration,"packet_limit":packet_limit})
                transaction.on_commit(lambda: execute_operation.delay(str(job.id)))
        except IntegrityError:
            return Response({"error":{"code":"capture_in_progress","details":"This interface already has an active capture."}},status=409)
        return Response(serializers.OperationSerializer(job).data,status=202)
    @action(detail=True,methods=["get"],url_path=r"captures/(?P<capture_id>[^/.]+)/download")
    def capture_download(self,request,pk=None,capture_id=None):
        deployment=self.get_object()
        try: capture_uuid=uuid.UUID(str(capture_id))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_capture"}},status=404)
        capture=models.CaptureSession.objects.select_related("interface__node").filter(id=capture_uuid,deployment=deployment,status="complete").first()
        if not capture: return Response({"error":{"code":"capture_not_ready"}},status=404)
        root=Path(settings.MEDIA_ROOT).resolve(); path=Path(capture.artifact_reference).resolve()
        if root not in path.parents or not path.is_file(): return Response({"error":{"code":"artifact_unavailable"}},status=410)
        response=FileResponse(path.open("rb"),content_type="application/vnd.tcpdump.pcap",as_attachment=True,
            filename=f"{capture.interface.node.name}-{capture.interface.name}-{capture.id}.pcap")
        response["X-Content-Type-Options"]="nosniff"
        return response
    @action(detail=True,methods=["post"])
    def consoles(self,request,pk=None):
        deployment=self.get_object()
        try: device_id=uuid.UUID(str(request.data.get("device_id")))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_device"}},status=422)
        device=deployment.devices.select_related("lab_node__template_version","deployment__revision__lab__project").filter(id=device_id).first()
        if not device: return Response({"error":{"code":"invalid_device"}},status=422)
        if device.observed_readiness!="ready" or not device.runtime_resources.get("pod"):
            return Response({"error":{"code":"device_not_ready"}},status=409)
        if device.lab_node.template_version.containerlab_kind not in ("linux","bridge"):
            return Response({"error":{"code":"console_unsupported","details":"This template does not have a verified browser-console adapter."}},status=422)
        role=project_role(request.user,deployment.revision.lab.project)
        read_only=role==models.ProjectMembership.Role.VIEWER
        session_id=uuid.uuid4(); browser_key=request.session.session_key
        if not browser_key: request.session.create(); browser_key=request.session.session_key
        token_hash=hashlib.sha256(f"{browser_key}:{session_id}".encode()).hexdigest()
        console=models.ConsoleSession.objects.create(id=session_id,device=device,user=request.user,token_hash=token_hash,
            expires_at=timezone.now()+timezone.timedelta(minutes=15),read_only=read_only)
        models.AuditEvent.objects.create(actor=request.user,project=deployment.revision.lab.project,action="console.authorized",
            target_type="DeviceInstance",target_id=device.id,correlation_id=getattr(request,"correlation_id",""),metadata={"console_session":str(console.id),"read_only":read_only})
        return Response({"id":str(console.id),"websocket":f"/ws/consoles/{console.id}/","expires_at":console.expires_at,"read_only":read_only,"device":{"id":str(device.id),"name":device.lab_node.name}},status=201)
