import uuid
import ipaddress
import hashlib
import json
import difflib
from pathlib import Path
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count,Q
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
from .bundles import BundleError, LabBundleParser, export_lab_bundle, import_lab_bundle, inspect_lab_bundle
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
    return models.Project.objects.filter(Q(owner=user)|Q(memberships__user=user),deleted_at__isnull=True).distinct()

class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class=serializers.ProjectSerializer; permission_classes=[ProjectAccess]
    def get_queryset(self): return visible_projects(self.request.user)
    def perform_create(self,serializer): serializer.save(owner=self.request.user)
    def perform_update(self,serializer):
        project=serializer.instance
        if project_role(self.request.user,project)!=models.ProjectMembership.Role.ADMIN:
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        before={key:getattr(project,key) for key in ("name","description","tags")};updated=serializer.save()
        changed=[key for key,value in before.items() if value!=getattr(updated,key)]
        models.AuditEvent.objects.create(actor=self.request.user,project=updated,action="project.metadata_updated",target_type="Project",target_id=updated.id,
            correlation_id=getattr(self.request,"correlation_id",""),metadata={"changed_fields":changed})
    def destroy(self,request,*args,**kwargs):
        return Response({"error":{"code":"guarded_delete_required","details":"Preview and confirm project retirement using the guarded operation."}},status=405)
    @staticmethod
    def _retirement_preview(project):
        active_states=(models.LabDeployment.State.PENDING,models.LabDeployment.State.DEPLOYING,models.LabDeployment.State.RUNNING,
            models.LabDeployment.State.DEGRADED,models.LabDeployment.State.DELETING)
        active_labs=project.labs.filter(deleted_at__isnull=True).count()
        active_images=project.image_artifacts.filter(deleted_at__isnull=True).count()
        active_uploads=project.upload_sessions.filter(status=models.UploadSession.Status.ACTIVE,expires_at__gt=timezone.now()).count()
        deployments=models.LabDeployment.objects.filter(revision__lab__project=project)
        active_deployments=deployments.filter(observed_state__in=active_states).count()
        active_operations=models.OperationJob.objects.filter(Q(deployment__revision__lab__project=project)|Q(target_id__in=project.labs.values("id"))|
            Q(target_id__in=project.image_artifacts.values("id"))|Q(target_id__in=project.upload_sessions.values("id")),
            state__in=("accepted","scheduled","started")).distinct().count()
        blockers=[]
        for count,label in ((active_labs,"active lab"),(active_images,"active image artifact"),(active_uploads,"active upload"),
            (active_deployments,"active deployment"),(active_operations,"active operation")):
            if count: blockers.append(f"{count} {label}{'s' if count!=1 else ''}")
        return {"project_id":str(project.id),"name":project.name,"updated_at":project.updated_at.isoformat(),"can_retire":not blockers,"blockers":blockers,
            "references":{"active_labs":active_labs,"active_images":active_images,"active_uploads":active_uploads,
                "active_deployments":active_deployments,"active_operations":active_operations,"members":project.memberships.count(),
                "historical_deployments":deployments.count()},
            "impact":["Remove the project from dashboards, libraries, selectors, and collaboration access.",
                "Preserve memberships, deleted labs and images, deployments, configurations, operations, and audit history.",
                "Allow the owner to reuse this project name."]}
    @action(detail=True,methods=["get"],url_path="retirement-preview")
    def retirement_preview(self,request,pk=None):
        project=self.get_object()
        if project_role(request.user,project)!=models.ProjectMembership.Role.ADMIN:
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        response=Response(self._retirement_preview(project));response["Cache-Control"]="no-store";return response
    @action(detail=True,methods=["post"],url_path="retire")
    def retire(self,request,pk=None):
        project=models.Project.objects.filter(Q(owner=request.user)|Q(memberships__user=request.user),pk=pk).distinct().first()
        if not project: return Response({"error":{"code":"not_found"}},status=404)
        if project_role(request.user,project)!=models.ProjectMembership.Role.ADMIN:
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.operation_type!="retire_project" or existing.target_id!=project.id: return Response({"error":{"code":"idempotency_conflict"}},status=409)
            return Response(existing.result_payload)
        expected=request.headers.get("X-Expected-Updated-At")
        if not expected: return Response({"error":{"code":"expected_updated_at_required"}},status=400)
        if project.deleted_at: return Response({"error":{"code":"project_already_retired"}},status=410)
        try:
            with transaction.atomic():
                locked=models.Project.objects.select_for_update().get(pk=project.pk)
                if locked.updated_at.isoformat()!=expected:
                    return Response({"error":{"code":"project_changed","details":"The project changed after retirement was previewed.",
                        "updated_at":locked.updated_at.isoformat()}},status=409)
                preview=self._retirement_preview(locked)
                if not preview["can_retire"]: return Response({"error":{"code":"project_retirement_blocked","details":preview["blockers"],
                    "references":preview["references"]}},status=409)
                locked.deleted_at=timezone.now();locked.save(update_fields=["deleted_at","updated_at"])
                result={"project_id":str(locked.id),"retired_at":locked.deleted_at.isoformat(),"preserved":preview["references"]}
                job=models.OperationJob.objects.create(owner=request.user,operation_type="retire_project",target_id=locked.id,idempotency_key=key,
                    state="succeeded",progress=100,request_payload={"expected_updated_at":expected},result_payload=result)
                result["operation_id"]=str(job.id);job.result_payload=result;job.save(update_fields=["result_payload","updated_at"])
                models.AuditEvent.objects.create(actor=request.user,project=locked,action="project.retired",target_type="Project",target_id=locked.id,
                    correlation_id=getattr(request,"correlation_id",""),metadata={"operation":str(job.id),"preserved":preview["references"]})
        except IntegrityError: return Response({"error":{"code":"operation_conflict"}},status=409)
        return Response(result)
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
    def get_queryset(self): return models.Lab.objects.filter(project__in=visible_projects(self.request.user),deleted_at__isnull=True).select_related("project")
    def perform_create(self,serializer):
        project=serializer.validated_data["project"]
        if project_role(self.request.user,project) not in ("administrator","editor"): from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        with transaction.atomic():
            project=models.Project.objects.select_for_update().get(pk=project.pk)
            used=project.labs.filter(deleted_at__isnull=True).count();limit=normalized_quotas(project)["max_labs"]
            if used>=limit: raise ProjectQuotaExceeded("labs",limit,used)
            serializer.save(project=project)
    def perform_update(self,serializer):
        lab=serializer.instance
        if project_role(self.request.user,lab.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        before={key:getattr(lab,key) for key in ("name","description","tags")};updated=serializer.save()
        changed=[key for key,value in before.items() if value!=getattr(updated,key)]
        models.AuditEvent.objects.create(actor=self.request.user,project=updated.project,action="lab.metadata_updated",target_type="Lab",target_id=updated.id,
            correlation_id=getattr(self.request,"correlation_id",""),metadata={"changed_fields":changed})
    def destroy(self,request,*args,**kwargs):
        return Response({"error":{"code":"guarded_delete_required","details":"Preview and confirm lab deletion using the guarded delete operation."}},status=405)
    def _deletion_preview(self,lab):
        deployments=models.LabDeployment.objects.filter(revision__lab=lab)
        active_states=(models.LabDeployment.State.PENDING,models.LabDeployment.State.DEPLOYING,models.LabDeployment.State.RUNNING,
            models.LabDeployment.State.DEGRADED,models.LabDeployment.State.DELETING)
        active_deployments=deployments.filter(observed_state__in=active_states).count()
        active_operations=models.OperationJob.objects.filter(deployment__revision__lab=lab,state__in=("accepted","scheduled","started")).count()
        blockers=[]
        if active_deployments: blockers.append(f"{active_deployments} active deployment{'s' if active_deployments!=1 else ''}")
        if active_operations: blockers.append(f"{active_operations} active operation{'s' if active_operations!=1 else ''}")
        return {"lab_id":str(lab.id),"name":lab.name,"project_id":str(lab.project_id),"updated_at":lab.updated_at.isoformat(),
            "references":{"revisions":lab.revisions.count(),"deployments":deployments.count(),"active_deployments":active_deployments,
                "active_operations":active_operations},"can_delete":not blockers,"blockers":blockers,
            "impact":["Remove the lab from the library and topology workspace.","Release one lab from the project quota.",
                "Preserve immutable revisions, deployment records, operations, and audit history.","Allow this lab name to be reused in the project."]}
    @action(detail=True,methods=["get"],url_path="delete-preview")
    def delete_preview(self,request,pk=None):
        lab=self.get_object()
        if project_role(request.user,lab.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        response=Response(self._deletion_preview(lab));response["Cache-Control"]="no-store";return response
    @action(detail=True,methods=["post"],url_path="delete")
    def delete_lab(self,request,pk=None):
        lab=models.Lab.objects.filter(pk=pk,project__in=visible_projects(request.user)).select_related("project").first()
        if not lab: return Response({"error":{"code":"not_found"}},status=404)
        if project_role(request.user,lab.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.operation_type!="delete_lab_record" or existing.target_id!=lab.id: return Response({"error":{"code":"idempotency_conflict"}},status=409)
            return Response(existing.result_payload)
        expected=request.headers.get("X-Expected-Updated-At")
        if not expected: return Response({"error":{"code":"expected_updated_at_required"}},status=400)
        if lab.deleted_at: return Response({"error":{"code":"lab_already_deleted"}},status=410)
        try:
            with transaction.atomic():
                locked=models.Lab.objects.select_for_update().get(pk=lab.pk)
                if locked.updated_at.isoformat()!=expected:
                    return Response({"error":{"code":"lab_changed","details":"The lab changed after deletion was previewed.","updated_at":locked.updated_at.isoformat()}},status=409)
                preview=self._deletion_preview(locked)
                if not preview["can_delete"]: return Response({"error":{"code":"lab_delete_blocked","details":preview["blockers"],"references":preview["references"]}},status=409)
                locked.deleted_at=timezone.now();locked.save(update_fields=["deleted_at","updated_at"])
                result={"lab_id":str(locked.id),"deleted_at":locked.deleted_at.isoformat(),"released_quota":{"labs":1},
                    "preserved":{"revisions":preview["references"]["revisions"],"deployments":preview["references"]["deployments"]}}
                job=models.OperationJob.objects.create(owner=request.user,operation_type="delete_lab_record",target_id=locked.id,idempotency_key=key,
                    state="succeeded",progress=100,request_payload={"expected_updated_at":expected},result_payload=result)
                result["operation_id"]=str(job.id);job.result_payload=result;job.save(update_fields=["result_payload","updated_at"])
                models.AuditEvent.objects.create(actor=request.user,project=locked.project,action="lab.deleted",target_type="Lab",target_id=locked.id,
                    correlation_id=getattr(request,"correlation_id",""),metadata={"operation":str(job.id),"revisions":preview["references"]["revisions"],
                        "deployments":preview["references"]["deployments"],"released_lab_quota":1})
        except IntegrityError: return Response({"error":{"code":"operation_conflict"}},status=409)
        return Response(result)
    @action(detail=True,methods=["post"],url_path="clone")
    def clone_lab(self,request,pk=None):
        source=self.get_object()
        if project_role(request.user,source.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        name=str(request.data.get("name","")).strip()
        if not name or len(name)>120:
            return Response({"error":{"code":"invalid_lab_name","details":"Name must contain 1 to 120 characters."}},status=422)
        bundle=export_lab_bundle(source)
        with transaction.atomic():
            project=models.Project.objects.select_for_update().get(pk=source.project_id)
            used=project.labs.filter(deleted_at__isnull=True).count();limit=normalized_quotas(project)["max_labs"]
            if used>=limit: return Response(quota_exceeded("labs",limit,used),status=409)
            if project.labs.filter(name=name,deleted_at__isnull=True).exists():
                return Response({"error":{"code":"lab_name_conflict","details":"A lab with this name already exists in the project."}},status=409)
            clone=models.Lab.objects.create(project=project,name=name,description=source.description,tags=source.tags)
            try: revision=import_lab_bundle(clone,request.user,bundle)
            except BundleError as exc:
                transaction.set_rollback(True)
                return Response({"error":{"code":"lab_clone_failed","details":str(exc)}},status=422)
            models.AuditEvent.objects.create(actor=request.user,project=project,action="lab.cloned",target_type="Lab",target_id=clone.id,
                correlation_id=getattr(request,"correlation_id",""),metadata={"source_lab":str(source.id),"revision":str(revision.id),
                    "node_count":revision.nodes.count(),"link_count":revision.links.count()})
        clone.refresh_from_db()
        payload=serializers.LabSerializer(clone).data
        payload.update({"workspace_url":f"/labs/{clone.id}/topology/","node_count":revision.nodes.count(),"link_count":revision.links.count()})
        return Response(payload,status=201)
    @action(detail=True,methods=["get"],url_path="revisions")
    def revisions(self,request,pk=None):
        lab=self.get_object()
        rows=lab.revisions.annotate(node_count=Count("nodes",distinct=True),link_count=Count("links",distinct=True),
            deployment_count=Count("deployments",distinct=True)).order_by("-revision_number")
        return Response({"current_draft":str(lab.current_draft_id) if lab.current_draft_id else None,"revisions":[{
            "id":str(row.id),"revision_number":row.revision_number,"edit_version":row.edit_version,"immutable":row.immutable,
            "topology_checksum":row.topology_checksum,"node_count":row.node_count,"link_count":row.link_count,
            "deployment_count":row.deployment_count,"created_at":row.created_at,"is_current_draft":row.id==lab.current_draft_id,
        } for row in rows]})
    @action(detail=True,methods=["post"],url_path=r"revisions/(?P<revision_id>[^/.]+)/restore")
    def restore_revision(self,request,pk=None,revision_id=None):
        lab=self.get_object()
        if project_role(request.user,lab.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        try: source_id=uuid.UUID(str(revision_id))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_revision"}},status=422)
        source=lab.revisions.filter(pk=source_id).first()
        if not source: return Response({"error":{"code":"revision_not_found"}},status=404)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.operation_type!="restore_revision" or existing.target_id!=source.id:
                return Response({"error":{"code":"idempotency_conflict"}},status=409)
            restored=lab.revisions.filter(pk=existing.result_payload.get("revision_id")).first()
            if not restored: return Response({"error":{"code":"restore_result_unavailable"}},status=409)
            return Response({"revision_id":str(restored.id),"revision_number":restored.revision_number,"edit_version":restored.edit_version,
                "node_count":restored.nodes.count(),"link_count":restored.links.count(),"operation_id":str(existing.id)},status=200)
        expected=request.data.get("expected_current_draft")
        expected=str(expected) if expected else None
        bundle=export_lab_bundle(lab,source)
        try:
            with transaction.atomic():
                locked=models.Lab.objects.select_for_update().get(pk=lab.pk)
                actual=str(locked.current_draft_id) if locked.current_draft_id else None
                if actual!=expected:
                    return Response({"error":{"code":"draft_changed","details":"The active draft changed while revision history was open.",
                        "current_draft":actual}},status=409)
                job=models.OperationJob.objects.create(owner=request.user,operation_type="restore_revision",target_id=source.id,
                    idempotency_key=key,state="started",progress=25,request_payload={"lab_id":str(lab.id),"expected_current_draft":expected})
                restored=import_lab_bundle(locked,request.user,bundle)
                job.state="succeeded";job.progress=100;job.result_payload={"revision_id":str(restored.id),"source_revision_id":str(source.id)}
                job.save(update_fields=["state","progress","result_payload","updated_at"])
                models.AuditEvent.objects.create(actor=request.user,project=lab.project,action="lab.revision_restored",target_type="LabRevision",
                    target_id=restored.id,correlation_id=getattr(request,"correlation_id",""),metadata={"lab":str(lab.id),
                        "source_revision":str(source.id),"source_revision_number":source.revision_number,"operation":str(job.id),
                        "node_count":restored.nodes.count(),"link_count":restored.links.count()})
        except BundleError as exc:
            return Response({"error":{"code":"revision_restore_failed","details":str(exc)}},status=422)
        except IntegrityError:
            return Response({"error":{"code":"operation_conflict"}},status=409)
        return Response({"revision_id":str(restored.id),"revision_number":restored.revision_number,"edit_version":restored.edit_version,
            "node_count":restored.nodes.count(),"link_count":restored.links.count(),"operation_id":str(job.id)},status=201)
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
    @action(detail=True,methods=["post"],url_path="import-preview",parser_classes=[LabBundleParser,JSONParser])
    def import_preview(self,request,pk=None):
        lab=self.get_object()
        if project_role(request.user,lab.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        try: _,preview=inspect_lab_bundle(lab,request.data)
        except BundleError as exc: return Response({"error":{"code":"invalid_lab_bundle","details":str(exc)}},status=422)
        preview["expected_current_draft"]=str(lab.current_draft_id) if lab.current_draft_id else None
        models.AuditEvent.objects.create(actor=request.user,project=lab.project,action="lab.import_previewed",target_type="Lab",target_id=lab.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={key:preview[key] for key in ("checksum","node_count","link_count","configured_node_count")})
        response=Response(preview);response["Cache-Control"]="no-store";response["X-Content-Type-Options"]="nosniff";return response
    @action(detail=True,methods=["post"],url_path="import",parser_classes=[LabBundleParser,JSONParser])
    def import_bundle(self,request,pk=None):
        lab=self.get_object()
        if project_role(request.user,lab.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        expected_header=request.headers.get("X-Expected-Draft")
        if expected_header is None: return Response({"error":{"code":"expected_draft_required"}},status=400)
        expected=None if expected_header.lower()=="none" else expected_header
        if expected:
            try: expected=str(uuid.UUID(expected))
            except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_expected_draft"}},status=422)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.operation_type!="import_lab" or existing.target_id!=lab.id: return Response({"error":{"code":"idempotency_conflict"}},status=409)
            return Response(existing.result_payload,status=200)
        raw=request.data
        try:
            with transaction.atomic():
                locked=models.Lab.objects.select_for_update().get(pk=lab.id);actual=str(locked.current_draft_id) if locked.current_draft_id else None
                if actual!=expected: return Response({"error":{"code":"draft_changed","details":"The active draft changed after the backup preview.","current_draft":actual}},status=409)
                _,preview=inspect_lab_bundle(locked,raw)
                job=models.OperationJob.objects.create(owner=request.user,operation_type="import_lab",target_id=lab.id,idempotency_key=key,state="started",progress=25,
                    request_payload={"lab_id":str(lab.id),"expected_current_draft":expected,"bundle_checksum":preview["checksum"]})
                revision=import_lab_bundle(locked,request.user,raw)
                result={"revision_id":str(revision.id),"revision_number":revision.revision_number,"edit_version":revision.edit_version,
                    "node_count":revision.nodes.count(),"link_count":revision.links.count(),"operation_id":str(job.id),"bundle_checksum":preview["checksum"]}
                job.state="succeeded";job.progress=100;job.result_payload=result;job.save(update_fields=["state","progress","result_payload","updated_at"])
        except BundleError as exc: return Response({"error":{"code":"invalid_lab_bundle","details":str(exc)}},status=422)
        models.AuditEvent.objects.create(actor=request.user,project=lab.project,action="lab.imported",target_type="Lab",target_id=lab.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"revision":str(revision.id),"bytes":len(raw),"checksum":preview["checksum"],"operation":str(job.id)})
        return Response(result,status=201)
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
    def get_queryset(self): return models.ImageArtifact.objects.filter(project__in=visible_projects(self.request.user)).select_related("project").order_by("-created_at")
    def list(self,request,*args,**kwargs):
        queryset=self.filter_queryset(self.get_queryset().filter(deleted_at__isnull=True))
        page=self.paginate_queryset(queryset)
        if page is not None: return self.get_paginated_response(self.get_serializer(page,many=True).data)
        return Response(self.get_serializer(queryset,many=True).data)
    def retrieve(self,request,*args,**kwargs):
        artifact=self.get_object()
        if artifact.deleted_at: return Response({"error":{"code":"image_not_found"}},status=404)
        return Response(self.get_serializer(artifact).data)
    @staticmethod
    def _deletion_preview(artifact):
        publications=artifact.published_images.count();builds=artifact.builds.count()
        revisions=models.LabNode.objects.filter(published_image__artifact=artifact).values("revision_id").distinct().count()
        active_jobs=models.OperationJob.objects.filter(target_id=artifact.id,state__in=("accepted","scheduled","started")).exclude(operation_type="delete_image").count()
        blockers=[]
        if publications: blockers.append(f"{publications} published image record{'s' if publications!=1 else ''}")
        if builds: blockers.append(f"{builds} retained build record{'s' if builds!=1 else ''}")
        if revisions: blockers.append(f"{revisions} lab revision{'s' if revisions!=1 else ''}")
        if active_jobs: blockers.append(f"{active_jobs} active operation{'s' if active_jobs!=1 else ''}")
        return {"artifact_id":str(artifact.id),"name":artifact.original_filename,"checksum":artifact.checksum,"byte_size":artifact.byte_size,
            "source_type":artifact.source_type,"references":{"publications":publications,"builds":builds,"lab_revisions":revisions,"active_operations":active_jobs},
            "can_delete":not blockers,"blockers":blockers,"impact":["Remove the quarantined artifact file when it is owned by Studio",
                "Release the artifact's project storage quota","Retain upload, operation, and audit provenance","Prevent this artifact from appearing in image and topology libraries"]}
    @action(detail=True,methods=["get"],url_path="delete-preview")
    def delete_preview(self,request,pk=None):
        artifact=self.get_object()
        if project_role(request.user,artifact.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        if artifact.deleted_at: return Response({"error":{"code":"image_already_deleted"}},status=410)
        response=Response(self._deletion_preview(artifact));response["Cache-Control"]="no-store";response["X-Content-Type-Options"]="nosniff";return response
    @action(detail=True,methods=["post"],url_path="delete")
    def delete_artifact(self,request,pk=None):
        artifact=self.get_object()
        if project_role(request.user,artifact.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        expected=request.headers.get("X-Expected-Checksum")
        if expected!=artifact.checksum: return Response({"error":{"code":"image_changed","details":"Refresh the deletion preview before confirming."}},status=409)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.operation_type!="delete_image" or existing.target_id!=artifact.id: return Response({"error":{"code":"idempotency_conflict"}},status=409)
            return Response(existing.result_payload,status=200)
        if artifact.deleted_at: return Response({"error":{"code":"image_already_deleted"}},status=410)
        with transaction.atomic():
            artifact=models.ImageArtifact.objects.select_for_update().get(pk=artifact.id)
            preview=self._deletion_preview(artifact)
            if not preview["can_delete"]: return Response({"error":{"code":"image_in_use","details":preview["blockers"],"references":preview["references"]}},status=409)
            job=models.OperationJob.objects.create(owner=request.user,operation_type="delete_image",target_id=artifact.id,idempotency_key=key,
                state="started",progress=25,request_payload={"checksum":artifact.checksum})
            removed_storage=False
            if artifact.source_type==models.ImageArtifact.Source.UPLOAD and artifact.storage_reference:
                root=Path(settings.MEDIA_ROOT).resolve();candidate=Path(artifact.storage_reference).resolve()
                if candidate.is_relative_to(root) and candidate.is_file(): candidate.unlink();removed_storage=True
            deleted_at=timezone.now();artifact.deleted_at=deleted_at;artifact.storage_reference=""
            artifact.inspection_result={**artifact.inspection_result,"deleted":True,"deleted_at":deleted_at.isoformat()}
            artifact.save(update_fields=["deleted_at","storage_reference","inspection_result","updated_at"])
            result={"artifact_id":str(artifact.id),"deleted_at":deleted_at.isoformat(),"checksum":artifact.checksum,"byte_size":artifact.byte_size,
                "storage_removed":removed_storage,"released_bytes":artifact.byte_size}
            job.state="succeeded";job.progress=100;job.result_payload=result;job.save(update_fields=["state","progress","result_payload","updated_at"])
            models.AuditEvent.objects.create(actor=request.user,project=artifact.project,action="image.deleted",target_type="ImageArtifact",
                target_id=artifact.id,correlation_id=getattr(request,"correlation_id",""),metadata={"checksum":artifact.checksum,
                    "byte_size":artifact.byte_size,"storage_removed":removed_storage,"operation":str(job.id)})
        return Response(result,status=200)
    @action(detail=True,methods=["post"])
    def publish(self,request,pk=None):
        artifact=self.get_object()
        if project_role(request.user,artifact.project) not in (models.ProjectMembership.Role.ADMIN,models.ProjectMembership.Role.EDITOR):
            from rest_framework.exceptions import PermissionDenied; raise PermissionDenied()
        if artifact.deleted_at: return Response({"error":{"code":"image_not_found"}},status=404)
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
        nodes={node.name:node for node in deployment.revision.nodes.select_related("template_version")}
        response=Response([{"id":str(row.id),"name":row.name,"version":row.version,"checksum":row.checksum,
            "byte_size":event_by_target[row.id].metadata.get("byte_size",0),"created_at":row.created_at,
            "device":event_by_target[row.id].metadata.get("device","") ,
            "restorable":bool(nodes.get(event_by_target[row.id].metadata.get("device","")) and
                nodes[event_by_target[row.id].metadata["device"]].template_version.launch_profile.get("startup_config_target")),
            "download":f"/api/v1/deployments/{deployment.id}/configurations/{row.id}/download/"} for row in rows])
        response["Cache-Control"]="no-store";response["X-Content-Type-Options"]="nosniff";return response

    def _collected_configuration(self,deployment,configuration_id):
        try: configuration_uuid=uuid.UUID(str(configuration_id))
        except (ValueError,TypeError,AttributeError): return None,None
        configuration=models.ConfigurationVersion.objects.filter(id=configuration_uuid,project=deployment.revision.lab.project).first()
        event=models.AuditEvent.objects.filter(action="configuration.collected",target_id=configuration_uuid,
            metadata__deployment=str(deployment.id)).order_by("-occurred_at").first()
        return (configuration,event) if configuration and event else (None,None)
    @action(detail=True,methods=["get"],url_path=r"configurations/(?P<configuration_id>[^/.]+)/download")
    def download_configuration(self,request,pk=None,configuration_id=None):
        deployment=self.get_object();self._require_operator(deployment)
        configuration,evidence=self._collected_configuration(deployment,configuration_id)
        if not configuration or not evidence: return Response({"error":{"code":"configuration_not_found"}},status=404)
        payload=decrypt_configuration(configuration.encrypted_content).encode("utf-8")
        models.AuditEvent.objects.create(actor=request.user,project=configuration.project,action="configuration.downloaded",
            target_type="ConfigurationVersion",target_id=configuration.id,correlation_id=getattr(request,"correlation_id",""),
            metadata={"deployment":str(deployment.id),"version":configuration.version,"checksum":configuration.checksum,"byte_size":len(payload)})
        response=HttpResponse(payload,content_type="text/plain; charset=utf-8")
        response["Content-Disposition"]=f'attachment; filename="{slugify(configuration.name)[:80] or "configuration"}-v{configuration.version}.txt"'
        response["Cache-Control"]="no-store";response["X-Content-Type-Options"]="nosniff"
        return response

    @action(detail=True,methods=["post"],url_path="configuration-compare")
    def compare_configurations(self,request,pk=None):
        deployment=self.get_object();self._require_operator(deployment)
        left,left_event=self._collected_configuration(deployment,request.data.get("left_id"))
        right,right_event=self._collected_configuration(deployment,request.data.get("right_id"))
        if not left or not right: return Response({"error":{"code":"configuration_not_found"}},status=404)
        left_device=left_event.metadata.get("device","");right_device=right_event.metadata.get("device","")
        if left_device!=right_device:
            return Response({"error":{"code":"configuration_device_mismatch","details":"Compare versions collected from the same device."}},status=422)
        left_text=decrypt_configuration(left.encrypted_content);right_text=decrypt_configuration(right.encrypted_content)
        diff="".join(difflib.unified_diff(left_text.splitlines(keepends=True),right_text.splitlines(keepends=True),
            fromfile=f"{left_device} v{left.version}",tofile=f"{right_device} v{right.version}",n=3))
        encoded=diff.encode("utf-8");truncated=len(encoded)>256*1024
        if truncated: diff=encoded[:256*1024].decode("utf-8","ignore")+"\n… diff truncated at 256 KiB …\n"
        models.AuditEvent.objects.create(actor=request.user,project=left.project,action="configuration.compared",
            target_type="LabDeployment",target_id=deployment.id,correlation_id=getattr(request,"correlation_id",""),
            metadata={"device":left_device,"left":str(left.id),"right":str(right.id),"left_checksum":left.checksum,
                "right_checksum":right.checksum,"changed":left.checksum!=right.checksum,"truncated":truncated})
        response=Response({"device":left_device,"left":{"id":str(left.id),"version":left.version,"checksum":left.checksum},
            "right":{"id":str(right.id),"version":right.version,"checksum":right.checksum},"changed":left.checksum!=right.checksum,
            "diff":diff,"truncated":truncated})
        response["Cache-Control"]="no-store";response["Pragma"]="no-cache";response["X-Content-Type-Options"]="nosniff";return response

    @action(detail=True,methods=["get"],url_path=r"configurations/(?P<configuration_id>[^/.]+)/restore-preview")
    def configuration_restore_preview(self,request,pk=None,configuration_id=None):
        deployment=self.get_object();self._require_operator(deployment)
        configuration,event=self._collected_configuration(deployment,configuration_id)
        if not configuration: return Response({"error":{"code":"configuration_not_found"}},status=404)
        device_name=event.metadata.get("device","")
        node=deployment.revision.nodes.select_related("template_version","startup_configuration").filter(name=device_name).first()
        if not node: return Response({"error":{"code":"configuration_device_not_found"}},status=409)
        if not node.template_version.launch_profile.get("startup_config_target"):
            return Response({"error":{"code":"configuration_restore_unsupported","details":"This template cannot deliver a startup configuration."}},status=422)
        lab=deployment.revision.lab
        response=Response({"configuration_id":str(configuration.id),"device":device_name,"version":configuration.version,
            "checksum":configuration.checksum,"source_revision":deployment.revision.revision_number,
            "current_startup_checksum":node.startup_configuration.checksum if node.startup_configuration_id else None,
            "expected_current_draft":str(lab.current_draft_id) if lab.current_draft_id else None,
            "running_deployment_unchanged":True,"requires_deploy":True,
            "impact":["Create a new editable draft from the deployed immutable revision",
                f"Pin collected version {configuration.version} as {device_name}'s startup configuration",
                "Leave the current running deployment and collected history unchanged",
                "Apply the restored configuration only when the new draft is explicitly deployed"]})
        response["Cache-Control"]="no-store";response["X-Content-Type-Options"]="nosniff";return response

    @action(detail=True,methods=["post"],url_path=r"configurations/(?P<configuration_id>[^/.]+)/restore")
    def restore_configuration(self,request,pk=None,configuration_id=None):
        deployment=self.get_object();self._require_operator(deployment)
        configuration,event=self._collected_configuration(deployment,configuration_id)
        if not configuration: return Response({"error":{"code":"configuration_not_found"}},status=404)
        device_name=event.metadata.get("device","")
        source_node=deployment.revision.nodes.select_related("template_version").filter(name=device_name).first()
        if not source_node: return Response({"error":{"code":"configuration_device_not_found"}},status=409)
        if not source_node.template_version.launch_profile.get("startup_config_target"):
            return Response({"error":{"code":"configuration_restore_unsupported","details":"This template cannot deliver a startup configuration."}},status=422)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        expected_header=request.headers.get("X-Expected-Draft")
        if expected_header is None: return Response({"error":{"code":"expected_draft_required"}},status=400)
        expected=None if expected_header.lower()=="none" else expected_header
        if expected:
            try: expected=str(uuid.UUID(expected))
            except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_expected_draft"}},status=422)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.operation_type!="restore_configuration" or existing.target_id!=configuration.id or existing.deployment_id!=deployment.id:
                return Response({"error":{"code":"idempotency_conflict"}},status=409)
            return Response(existing.result_payload,status=200)
        try:
            with transaction.atomic():
                lab=models.Lab.objects.select_for_update().get(pk=deployment.revision.lab_id)
                actual=str(lab.current_draft_id) if lab.current_draft_id else None
                if actual!=expected: return Response({"error":{"code":"draft_changed","details":"The active draft changed after restore preview.","current_draft":actual}},status=409)
                job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type="restore_configuration",
                    target_id=configuration.id,idempotency_key=key,state="started",progress=25,
                    request_payload={"configuration_id":str(configuration.id),"device":device_name,"expected_current_draft":expected})
                bundle=export_lab_bundle(lab,deployment.revision)
                target_document=next(item for item in bundle["topology"]["nodes"] if item["name"]==device_name)
                target_document["startupConfiguration"]=decrypt_configuration(configuration.encrypted_content)
                restored=import_lab_bundle(lab,request.user,bundle)
                result={"revision_id":str(restored.id),"revision_number":restored.revision_number,"configuration_id":str(configuration.id),
                    "device":device_name,"checksum":configuration.checksum,"workspace_url":f"/labs/{lab.id}/topology/","operation_id":str(job.id)}
                job.state="succeeded";job.progress=100;job.result_payload=result;job.save(update_fields=["state","progress","result_payload","updated_at"])
                models.AuditEvent.objects.create(actor=request.user,project=lab.project,action="configuration.restore_draft_created",
                    target_type="LabRevision",target_id=restored.id,correlation_id=getattr(request,"correlation_id",""),
                    metadata={"deployment":str(deployment.id),"source_revision":str(deployment.revision_id),"device":device_name,
                        "configuration":str(configuration.id),"checksum":configuration.checksum,"operation":str(job.id)})
        except BundleError as exc: return Response({"error":{"code":"configuration_restore_failed","details":str(exc)}},status=422)
        except IntegrityError: return Response({"error":{"code":"operation_conflict"}},status=409)
        return Response(result,status=201)
    @action(detail=True,methods=["get"])
    def runtime(self,request,pk=None):
        deployment=self.get_object()
        conditions=deployment.resource_identities.get("link_conditions",{})
        links=deployment.revision.links.select_related("endpoint_a__node","endpoint_b__node")
        response=Response({"deployment":serializers.DeploymentSerializer(deployment).data,
            "devices":serializers.DeviceInstanceSerializer(deployment.devices.select_related("lab_node__template_version").prefetch_related("lab_node__interfaces"),many=True).data,
            "links":[{"id":str(link.id),"label":link.label,"endpoint_a":{"node":link.endpoint_a.node.name,"interface":link.endpoint_a.name},
                "endpoint_b":{"node":link.endpoint_b.node.name,"interface":link.endpoint_b.name},"condition":conditions.get(str(link.id),{"active":False,
                    "disabled":False,"latency_ms":0,"jitter_ms":0,"loss_percent":0,"corruption_percent":0,"rate_kbps":0})} for link in links],
            "operations":serializers.OperationSerializer(deployment.operations.order_by("-created_at")[:20],many=True).data})
        response["Cache-Control"]="no-store";response["X-Content-Type-Options"]="nosniff";return response
    @action(detail=True,methods=["get"],url_path="redeploy-preview")
    def redeploy_preview(self,request,pk=None):
        deployment=self.get_object()
        devices=deployment.devices.all();conditions=deployment.resource_identities.get("link_conditions",{})
        active=deployment.operations.filter(state__in=("accepted","scheduled","started")).order_by("created_at").first()
        return Response({"action":"redeploy_lab","deployment_id":str(deployment.id),"lab":deployment.revision.lab.name,
            "revision":deployment.revision.revision_number,"namespace":deployment.namespace,"observed_state":deployment.observed_state,
            "runtime_exists":deployment.observed_state not in (models.LabDeployment.State.STOPPED,models.LabDeployment.State.PENDING),
            "devices":{"total":devices.count(),"ready":devices.filter(observed_readiness="ready").count()},
            "links":deployment.revision.links.count(),"active_link_conditions":sum(1 for value in conditions.values() if value.get("active")),
            "blocked_by":{"job_id":str(active.id),"operation":active.operation_type} if active else None,
            "impact":["Recreate runtime resources from the pinned immutable revision",
                "Preserve the project, lab, revision, startup configurations, and collected history",
                "End active consoles and packet captures while device compute is replaced"]})
    def _removal_preview(self,deployment):
        active=deployment.operations.filter(state__in=("accepted","scheduled","started")).order_by("created_at").first()
        active_consoles=models.ConsoleSession.objects.filter(device__deployment=deployment,revoked_at__isnull=True,expires_at__gt=timezone.now()).count()
        active_captures=models.CaptureSession.objects.filter(deployment=deployment,status__in=("pending","scheduled","capturing")).count()
        return {"action":"delete_runtime","deployment_id":str(deployment.id),"lab":deployment.revision.lab.name,
            "revision":deployment.revision.revision_number,"namespace":deployment.namespace,"observed_state":deployment.observed_state,
            "updated_at":deployment.updated_at.isoformat(),"already_removed":bool(deployment.removed_at),
            "can_remove":not active and not active_captures and not deployment.removed_at,
            "blocked_by":{"job_id":str(active.id),"operation":active.operation_type} if active else None,
            "references":{"devices":deployment.devices.count(),"active_consoles":active_consoles,"active_captures":active_captures,
                "retained_artifacts":deployment.artifacts.count(),"operations":deployment.operations.count()},
            "impact":["Delete the dedicated Kubernetes namespace and all Clabernetes runtime resources.",
                "Revoke active browser consoles and release device compute.",
                "Preserve the project, lab, immutable revision, configurations, captures, artifacts, operations, and audit history.",
                "Mark this deployment record as permanently removed; deploy the saved lab again to create a new runtime."]}
    @action(detail=True,methods=["get"],url_path="removal-preview")
    def removal_preview(self,request,pk=None):
        deployment=self.get_object();self._require_operator(deployment)
        response=Response(self._removal_preview(deployment));response["Cache-Control"]="no-store";return response
    @action(detail=True,methods=["post"],url_path="remove")
    def remove_runtime(self,request,pk=None):
        deployment=self.get_object();self._require_operator(deployment)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.deployment_id!=deployment.id or existing.operation_type!="delete_runtime":
                return Response({"error":{"code":"idempotency_conflict"}},status=409)
            if existing.state in ("accepted","scheduled"): execute_operation.delay(str(existing.id))
            return Response(serializers.OperationSerializer(existing).data,status=202)
        expected=request.headers.get("X-Expected-Updated-At")
        if not expected: return Response({"error":{"code":"expected_updated_at_required"}},status=400)
        with transaction.atomic():
            locked=models.LabDeployment.objects.select_for_update().get(pk=deployment.pk)
            if locked.removed_at: return Response({"error":{"code":"runtime_already_removed"}},status=410)
            if locked.updated_at.isoformat()!=expected:
                return Response({"error":{"code":"deployment_changed","details":"The runtime changed after removal was previewed.",
                    "updated_at":locked.updated_at.isoformat()}},status=409)
            preview=self._removal_preview(locked)
            if preview["blocked_by"] or preview["references"]["active_captures"]:
                return Response({"error":{"code":"runtime_removal_blocked","details":preview["blocked_by"] or
                    f'{preview["references"]["active_captures"]} packet capture(s) are active.',"references":preview["references"]}},status=409)
            locked.observed_state=models.LabDeployment.State.DELETING;locked.requested_desired_state="removed"
            locked.save(update_fields=["observed_state","requested_desired_state","updated_at"])
            job=models.OperationJob.objects.create(deployment=locked,owner=request.user,operation_type="delete_runtime",target_id=locked.id,
                idempotency_key=key,state="scheduled",request_payload={"expected_updated_at":expected,"namespace":locked.namespace})
            models.AuditEvent.objects.create(actor=request.user,project=locked.revision.lab.project,action="deployment.removal_scheduled",
                target_type="LabDeployment",target_id=locked.id,correlation_id=getattr(request,"correlation_id",""),
                metadata={"revision":locked.revision.revision_number,"namespace":locked.namespace,"operation":str(job.id)})
            transaction.on_commit(lambda:execute_operation.delay(str(job.id)))
        return Response(serializers.OperationSerializer(job).data,status=202)
    @action(detail=True,methods=["post"])
    def refresh(self,request,pk=None):
        deployment=self.get_object()
        self._require_operator(deployment)
        if deployment.removed_at: return Response({"error":{"code":"runtime_removed","details":"Removed deployments are retained as history and are not reconciled."}},status=409)
        reconcile_deployment.delay(str(deployment.id))
        return Response({"state":"scheduled"},status=202)
    @action(detail=True,methods=["post"])
    def operations(self,request,pk=None):
        deployment=self.get_object(); op=request.data.get("operation")
        self._require_operator(deployment)
        if op not in ("deploy_lab","redeploy_lab","stop_lab"): return Response({"error":{"code":"unsupported_operation"}},status=422)
        if deployment.removed_at: return Response({"error":{"code":"runtime_removed","details":"Deploy the saved lab to create a new runtime."}},status=409)
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.deployment_id!=deployment.id or existing.operation_type!=op: return Response({"error":{"code":"idempotency_conflict"}},status=409)
            if existing.state in ("accepted","scheduled"): execute_operation.delay(str(existing.id))
            return Response(serializers.OperationSerializer(existing).data,status=202)
        active=deployment.operations.filter(state__in=("accepted","scheduled","started")).first()
        if active: return Response({"error":{"code":"operation_in_progress","details":f"{active.operation_type} is already in progress."}},status=409)
        with transaction.atomic(): job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type=op,target_id=deployment.id,idempotency_key=key,state="scheduled")
        models.AuditEvent.objects.create(actor=request.user,project=deployment.revision.lab.project,action=f"deployment.{op.removesuffix('_lab')}_scheduled",
            target_type="LabDeployment",target_id=deployment.id,correlation_id=getattr(request,"correlation_id",""),metadata={"revision":deployment.revision.revision_number,"namespace":deployment.namespace})
        execute_operation.delay(str(job.id)); return Response(serializers.OperationSerializer(job).data,status=202)
    @action(detail=True,methods=["post"],url_path="device-operations")
    def device_operations(self,request,pk=None):
        deployment=self.get_object(); self._require_operator(deployment)
        operation=str(request.data.get("operation",""))
        if operation not in ("restart_device","stop_device","start_device","suspend_device","resume_device","collect_configuration","get_device_logs"):
            return Response({"error":{"code":"unsupported_operation","details":"Supported device operations are start, stop, restart, suspend, resume, configuration collection, and bounded runtime logs."}},status=422)
        try: device_id=uuid.UUID(str(request.data.get("device_id")))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_device"}},status=422)
        operation_payload={"device_id":str(device_id)}
        if operation=="get_device_logs":
            source=str(request.data.get("source","appliance"));tail=request.data.get("tail",200)
            if source not in ("appliance","launcher") or not isinstance(tail,int) or isinstance(tail,bool) or not 20<=tail<=1000:
                return Response({"error":{"code":"invalid_log_request","details":"Choose appliance or launcher and request 20-1000 lines."}},status=422)
            operation_payload.update({"source":source,"tail":tail})
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        existing=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if existing:
            if existing.target_id!=device_id or existing.operation_type!=operation or existing.request_payload!=operation_payload: return Response({"error":{"code":"idempotency_conflict"}},status=409)
            if existing.state in ("accepted","scheduled"): execute_operation.delay(str(existing.id))
            return Response(serializers.OperationSerializer(existing).data,status=202)
        with transaction.atomic():
            device=deployment.devices.select_for_update().select_related("lab_node").filter(id=device_id).first()
            if not device: return Response({"error":{"code":"invalid_device"}},status=422)
            desired_state=device.runtime_resources.get("manual_desired_state")
            desired_suspended=desired_state=="suspended";desired_stopped=desired_state=="stopped"
            if operation=="resume_device":
                if not desired_suspended or not device.runtime_resources.get("pod"): return Response({"error":{"code":"device_not_suspended"}},status=409)
            elif operation=="start_device":
                if not desired_stopped: return Response({"error":{"code":"device_not_stopped"}},status=409)
            elif desired_stopped:
                return Response({"error":{"code":"device_stopped","details":"Start the device before running another operation."}},status=409)
            elif operation=="get_device_logs":
                if not device.runtime_resources.get("pod"): return Response({"error":{"code":"device_runtime_unavailable"}},status=409)
            elif device.observed_readiness!="ready" or not device.runtime_resources.get("pod"):
                return Response({"error":{"code":"device_not_ready"}},status=409)
            if operation=="collect_configuration" and not device.lab_node.template_version.launch_profile.get("configuration_collect_command"):
                return Response({"error":{"code":"configuration_collection_unsupported","details":"This device template has no verified runtime collector."}},status=422)
            if models.OperationJob.objects.filter(target_id=device.id,state__in=("accepted","scheduled","started")).exists():
                return Response({"error":{"code":"device_operation_in_progress"}},status=409)
            job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type=operation,
                target_id=device.id,idempotency_key=key,state="scheduled",request_payload=operation_payload)
            audit_action="device.logs_requested" if operation=="get_device_logs" else f"device.{operation.removesuffix('_device')}"
            metadata={"operation_job":str(job.id),"node":device.lab_node.name}
            if operation=="get_device_logs": metadata.update({"source":source,"tail":tail})
            models.AuditEvent.objects.create(actor=request.user,project=deployment.revision.lab.project,action=audit_action,
                target_type="DeviceInstance",target_id=device.id,correlation_id=getattr(request,"correlation_id",""),metadata=metadata)
            transaction.on_commit(lambda: execute_operation.delay(str(job.id)))
        return Response(serializers.OperationSerializer(job).data,status=202)
    @action(detail=True,methods=["post"])
    def diagnostics(self,request,pk=None):
        deployment=self.get_object(); self._require_operator(deployment)
        operation=str(request.data.get("operation","ping"))
        if operation not in ("ping","traceroute"):
            return Response({"error":{"code":"unsupported_diagnostic","details":"Choose ping or traceroute."}},status=422)
        try: target=str(ipaddress.ip_address(request.data.get("target","")))
        except ValueError: return Response({"error":{"code":"invalid_target","details":"Enter a valid IPv4 or IPv6 address."}},status=422)
        try: node_id=uuid.UUID(str(request.data.get("node_id")))
        except (ValueError,TypeError,AttributeError): return Response({"error":{"code":"invalid_node"}},status=422)
        node=deployment.revision.nodes.filter(id=node_id).first()
        if not node: return Response({"error":{"code":"invalid_node"}},status=422)
        device=deployment.devices.filter(lab_node=node).first()
        if not device or device.observed_readiness!="ready" or not device.runtime_resources.get("pod"):
            return Response({"error":{"code":"device_not_ready","details":"Choose a ready device with active compute."}},status=409)
        timeout=request.data.get("timeout",2)
        if not isinstance(timeout,int) or isinstance(timeout,bool) or not 1<=timeout<=5:
            return Response({"error":{"code":"invalid_bounds","details":"Timeout must be between 1 and 5 seconds."}},status=422)
        if operation=="ping":
            count=request.data.get("count",3)
            if not isinstance(count,int) or isinstance(count,bool) or not 1<=count<=5:
                return Response({"error":{"code":"invalid_bounds","details":"Ping count must be between 1 and 5."}},status=422)
            payload={"node_id":str(node_id),"target":target,"count":count,"timeout":timeout}
        else:
            max_hops=request.data.get("max_hops",20);probes=request.data.get("probes",1)
            if (not isinstance(max_hops,int) or isinstance(max_hops,bool) or not 3<=max_hops<=30 or
                not isinstance(probes,int) or isinstance(probes,bool) or not 1<=probes<=3):
                return Response({"error":{"code":"invalid_bounds","details":"Traceroute allows 3-30 hops and 1-3 probes per hop."}},status=422)
            payload={"node_id":str(node_id),"target":target,"max_hops":max_hops,"timeout":timeout,"probes":probes}
        key=request.headers.get("Idempotency-Key")
        if not key: return Response({"error":{"code":"idempotency_key_required"}},status=400)
        job=models.OperationJob.objects.filter(owner=request.user,idempotency_key=key).first()
        if job:
            if job.deployment_id!=deployment.id or job.operation_type!=operation or job.request_payload!=payload:
                return Response({"error":{"code":"idempotency_conflict"}},status=409)
        else:
            try:
                job=models.OperationJob.objects.create(deployment=deployment,owner=request.user,operation_type=operation,target_id=deployment.id,
                    idempotency_key=key,state="scheduled",request_payload=payload)
            except IntegrityError:
                return Response({"error":{"code":"diagnostic_in_progress","details":"Wait for the active diagnostic to finish."}},status=409)
            models.AuditEvent.objects.create(actor=request.user,project=deployment.revision.lab.project,action="diagnostic.scheduled",
                target_type="LabDeployment",target_id=deployment.id,correlation_id=getattr(request,"correlation_id",""),
                metadata={"operation":operation,"node":node.name,"target":target,**({"count":payload["count"]} if operation=="ping" else
                    {"max_hops":payload["max_hops"],"probes":payload["probes"]}),"timeout":timeout,"operation_job":str(job.id)})
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
                    "disabled":False,"latency_ms":0,"jitter_ms":0,"loss_percent":0,"corruption_percent":0,"rate_kbps":0})} for link in links])
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
        corruption=request.data.get("corruption_percent",0)
        if isinstance(corruption,bool) or not isinstance(corruption,(int,float)) or corruption<0 or corruption>100:
            return Response({"error":{"code":"invalid_link_condition","details":"corruption_percent must be between 0 and 100."}},status=422)
        if values["jitter_ms"] and not values["latency_ms"]:
            return Response({"error":{"code":"invalid_link_condition","details":"Jitter requires non-zero latency."}},status=422)
        if values["rate_kbps"] and values["rate_kbps"]<64:
            return Response({"error":{"code":"invalid_link_condition","details":"Rate must be zero or at least 64 Kbit/s."}},status=422)
        condition={"active":disabled or bool(values["latency_ms"] or loss or corruption or values["rate_kbps"]),"disabled":disabled,
            "latency_ms":values["latency_ms"],"jitter_ms":values["jitter_ms"],"loss_percent":float(loss),
            "corruption_percent":float(corruption),"rate_kbps":values["rate_kbps"]}
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
