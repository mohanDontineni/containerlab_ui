import pytest
import uuid
import hashlib
import io
import json
import zipfile
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient
from studio.configurations import decrypt_configuration, decrypt_secret, encrypt_configuration
from studio.models import (AuditEvent, CaptureSession, ConsoleSession, DeploymentSchedule, DeviceInstance, DeviceTemplate, DeviceTemplateVersion, ImageArtifact, ImageBuild, ImageCredentialReference, Lab, LabDeployment,
    ConfigurationVersion, LabArtifact, LabInterface, LabLink, LabNode, LabRevision, OperationJob, Project, ProjectMembership, PublishedImage, UploadSession, User)
from studio.tasks import dispatch_due_schedules, execute_operation, execute_staged_start, reconcile_active_deployments, reconcile_deployment
from studio.quotas import project_usage

def test_web_process_uses_configured_celery_broker():
    assert execute_operation.app.conf.broker_url == settings.CELERY_BROKER_URL
    assert settings.CELERY_BEAT_SCHEDULE["reconcile-active-deployments"]["schedule"]==30.0
    assert settings.CELERY_BEAT_SCHEDULE["dispatch-due-deployment-schedules"]["schedule"]==15.0

def managed_template_payload(**changes):
    payload={"name":"Managed Router","description":"Safe platform-managed launch profile","privileged":False,
        "containerlab_kind":"linux","category":"Routing","icon":"router","interface_prefix":"eth",
        "interface_start":1,"interface_count":4,"management_interface":"eth0","cpu":"500m","memory":"512Mi",
        "console_method":"shell","configuration_profile":"frr","verified":False}
    payload.update(changes);return payload

@pytest.mark.django_db
def test_device_template_creation_is_platform_admin_only_idempotent_and_audited():
    user=User.objects.create_user("template-user",password="long-enough-password")
    admin=User.objects.create_user("template-admin",password="long-enough-password",is_staff=True)
    client=APIClient();client.force_authenticate(user)
    assert client.get("/api/v1/device-templates/").status_code==200
    assert client.post("/api/v1/device-templates/",managed_template_payload(),format="json",HTTP_IDEMPOTENCY_KEY="denied").status_code==403
    client.force_authenticate(admin)
    assert client.post("/api/v1/device-templates/",managed_template_payload(),format="json").status_code==400
    created=client.post("/api/v1/device-templates/",managed_template_payload(),format="json",HTTP_IDEMPOTENCY_KEY="create-template")
    replay=client.post("/api/v1/device-templates/",managed_template_payload(),format="json",HTTP_IDEMPOTENCY_KEY="create-template")
    assert created.status_code==201 and replay.status_code==200 and replay.data==created.data
    template=DeviceTemplate.objects.get(pk=created.data["template_id"]);version=template.active_version
    assert version.version==1 and version.launch_profile["startup_config_target"]=="/etc/frr/frr.conf"
    assert version.interface_rules=={"prefix":"eth","start":1,"count":4,"management":"eth0"}
    assert AuditEvent.objects.filter(action="device_template.created",target_id=template.id).count()==1

@pytest.mark.django_db
def test_device_template_versioning_preserves_pins_and_rejects_stale_or_unsafe_changes():
    admin=User.objects.create_user("template-version-admin",password="long-enough-password",is_staff=True)
    client=APIClient();client.force_authenticate(admin)
    created=client.post("/api/v1/device-templates/",managed_template_payload(name="Versioned Router"),format="json",HTTP_IDEMPOTENCY_KEY="create-versioned")
    template=DeviceTemplate.objects.get(pk=created.data["template_id"]);original=template.active_version
    owner=User.objects.create_user("template-lab-owner",password="long-enough-password");project=Project.objects.create(owner=owner,name="template-project")
    lab=Lab.objects.create(project=project,name="template-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=original)
    endpoint=f"/api/v1/device-templates/{template.id}/versions/"
    invalid=client.post(endpoint,managed_template_payload(name="Versioned Router",management_interface="eth1"),format="json",
        HTTP_IDEMPOTENCY_KEY="invalid-template-version",HTTP_X_EXPECTED_ACTIVE_VERSION=str(original.id))
    assert invalid.status_code==400 and template.versions.count()==1
    stale=client.post(endpoint,managed_template_payload(name="Versioned Router",interface_count=8),format="json",
        HTTP_IDEMPOTENCY_KEY="stale-template-version",HTTP_X_EXPECTED_ACTIVE_VERSION=str(uuid.uuid4()))
    assert stale.status_code==409
    changed=client.post(endpoint,managed_template_payload(name="Versioned Router",interface_count=8,verified=True),format="json",
        HTTP_IDEMPOTENCY_KEY="next-template-version",HTTP_X_EXPECTED_ACTIVE_VERSION=str(original.id))
    replay=client.post(endpoint,managed_template_payload(name="Versioned Router",interface_count=8,verified=True),format="json",
        HTTP_IDEMPOTENCY_KEY="next-template-version",HTTP_X_EXPECTED_ACTIVE_VERSION=str(original.id))
    assert changed.status_code==201 and replay.status_code==200 and replay.data==changed.data
    template.refresh_from_db();node.refresh_from_db()
    assert template.active_version.version==2 and template.active_version.interface_rules["count"]==8
    assert node.template_version_id==original.id and template.versions.count()==2
    assert AuditEvent.objects.filter(action="device_template.version_activated",target_id=template.id).count()==1

@pytest.mark.django_db
def test_periodic_reconciler_queues_only_active_deployments(monkeypatch):
    owner=User.objects.create_user("scheduler-owner",password="long-enough-password");project=Project.objects.create(owner=owner,name="scheduler")
    queued=[];monkeypatch.setattr("studio.tasks.reconcile_deployment.delay",lambda deployment_id:queued.append(deployment_id))
    for index,state in enumerate(("pending","deploying","running","degraded","failed","stopped"),1):
        lab=Lab.objects.create(project=project,name=f"lab-{index}");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum=str(index)*64,immutable=True)
        LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace=f"clab-scheduler-{index}",runtime_version="0.8.0",observed_state=state)
    assert reconcile_active_deployments.run()==4 and len(queued)==4

@pytest.mark.django_db
def test_deployment_schedules_are_authorized_bounded_cancellable_and_audited():
    owner=User.objects.create_user("schedule-owner",password="long-enough-password");viewer=User.objects.create_user("schedule-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="scheduled-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="scheduled-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-schedule-api",runtime_version="0.8.0",observed_state="running")
    endpoint=f"/api/v1/deployments/{deployment.id}/schedules/";execute_at=timezone.now()+timezone.timedelta(minutes=5)
    client=APIClient();client.force_authenticate(viewer)
    assert client.get(endpoint).status_code==200 and client.post(endpoint,{"action":"stop_lab","execute_at":execute_at.isoformat()},format="json").status_code==403
    client.force_authenticate(owner)
    assert client.post(endpoint,{"action":"stop_lab","execute_at":timezone.now().isoformat()},format="json").status_code==422
    created=client.post(endpoint,{"action":"stop_lab","execute_at":execute_at.isoformat()},format="json")
    schedule=DeploymentSchedule.objects.get(pk=created.data["id"]);assert created.status_code==201 and schedule.status=="pending"
    cancel=f"/api/v1/deployments/{deployment.id}/schedules/{schedule.id}/cancel/"
    assert client.post(cancel,{},format="json").status_code==400
    stale=client.post(cancel,{},format="json",HTTP_X_EXPECTED_UPDATED_AT=timezone.now().isoformat());assert stale.status_code==409
    cancelled=client.post(cancel,{},format="json",HTTP_X_EXPECTED_UPDATED_AT=created.data["updated_at"])
    schedule.refresh_from_db();assert cancelled.status_code==200 and schedule.status=="cancelled" and schedule.cancelled_at
    assert AuditEvent.objects.filter(action="deployment.schedule_created",target_id=schedule.id).exists()
    assert AuditEvent.objects.filter(action="deployment.schedule_cancelled",target_id=schedule.id).exists()

@pytest.mark.django_db
def test_due_schedule_dispatches_normal_operation_and_skips_ineligible_state(monkeypatch,django_capture_on_commit_callbacks):
    owner=User.objects.create_user("due-owner",password="long-enough-password");project=Project.objects.create(owner=owner,name="due-project")
    lab=Lab.objects.create(project=project,name="due-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="b"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-due",runtime_version="0.8.0",observed_state="running")
    due=timezone.now()-timezone.timedelta(seconds=1)
    stop=DeploymentSchedule.objects.create(deployment=deployment,created_by=owner,action="stop_lab",execute_at=due)
    start=DeploymentSchedule.objects.create(deployment=deployment,created_by=owner,action="start_lab",execute_at=due)
    queued=[];monkeypatch.setattr("studio.tasks.execute_operation.delay",lambda job_id:queued.append(job_id))
    with django_capture_on_commit_callbacks(execute=True): assert dispatch_due_schedules.run()==1
    stop.refresh_from_db();start.refresh_from_db();assert stop.status=="dispatched" and stop.operation.operation_type=="stop_lab" and queued==[str(stop.operation_id)]
    assert start.status=="skipped" and AuditEvent.objects.filter(action="deployment.schedule_skipped",target_id=start.id,metadata__reason="operation_in_progress").exists()

@pytest.mark.django_db
def test_guessed_project_uuid_is_not_visible():
    owner=User.objects.create_user("owner",password="long-enough-password")
    attacker=User.objects.create_user("attacker",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="secret")
    c=APIClient(); c.force_authenticate(attacker)
    assert c.get(f"/api/v1/projects/{project.id}/").status_code==404
@pytest.mark.django_db
def test_viewer_cannot_modify_project():
    owner=User.objects.create_user("owner",password="long-enough-password")
    viewer=User.objects.create_user("viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="p")
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    c=APIClient(); c.force_authenticate(viewer)
    assert c.patch(f"/api/v1/projects/{project.id}/",{"name":"changed"},format="json").status_code==403

@pytest.mark.django_db
def test_project_member_lifecycle_is_admin_only_and_audited():
    owner=User.objects.create_user("access-owner",password="long-enough-password")
    admin=User.objects.create_user("access-admin",password="long-enough-password")
    editor=User.objects.create_user("access-editor",password="long-enough-password")
    candidate=User.objects.create_user("access-candidate",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="access-project")
    ProjectMembership.objects.create(project=project,user=admin,role="administrator")
    ProjectMembership.objects.create(project=project,user=editor,role="editor")
    client=APIClient();client.force_authenticate(editor)
    endpoint=f"/api/v1/projects/{project.id}/members/"
    assert client.post(endpoint,{"username":candidate.username,"role":"viewer"},format="json").status_code==403
    client.force_authenticate(admin)
    added=client.post(endpoint,{"username":candidate.username,"role":"viewer"},format="json")
    assert added.status_code==201 and added.data["username"]==candidate.username
    membership=ProjectMembership.objects.get(project=project,user=candidate)
    changed=client.patch(f"/api/v1/memberships/{membership.id}/",{"role":"editor"},format="json")
    assert changed.status_code==200 and changed.data["role"]=="editor"
    assert client.delete(f"/api/v1/memberships/{membership.id}/").status_code==204
    assert not ProjectMembership.objects.filter(id=membership.id).exists()
    assert list(AuditEvent.objects.filter(project=project,action__startswith="project.member").values_list("action",flat=True))==[
        "project.member_added","project.member_role_changed","project.member_removed"]

@pytest.mark.django_db
def test_project_membership_prevents_owner_duplication_and_cross_project_mutation():
    owner=User.objects.create_user("protected-owner",password="long-enough-password")
    other_owner=User.objects.create_user("other-owner",password="long-enough-password")
    member=User.objects.create_user("protected-member",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="protected-project")
    other=Project.objects.create(owner=other_owner,name="other-project")
    membership=ProjectMembership.objects.create(project=project,user=member,role="viewer")
    client=APIClient();client.force_authenticate(owner)
    duplicate=client.post(f"/api/v1/projects/{project.id}/members/",{"username":owner.username,"role":"viewer"},format="json")
    assert duplicate.status_code==409
    client.force_authenticate(other_owner)
    assert client.patch(f"/api/v1/memberships/{membership.id}/",{"role":"administrator"},format="json").status_code==404
    assert client.get(f"/api/v1/projects/{other.id}/members/").status_code==200

@pytest.mark.django_db
def test_project_metadata_is_admin_only_audited_and_active_name_unique():
    owner=User.objects.create_user("project-edit-owner",password="long-enough-password")
    editor=User.objects.create_user("project-edit-editor",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Before",description="old",tags=["old"])
    ProjectMembership.objects.create(project=project,user=editor,role="editor")
    client=APIClient();client.force_authenticate(editor)
    assert client.patch(f"/api/v1/projects/{project.id}/",{"name":"Denied"},format="json").status_code==403
    client.force_authenticate(owner)
    changed=client.patch(f"/api/v1/projects/{project.id}/",{"name":"After","description":"new","tags":["network"]},format="json")
    assert changed.status_code==200
    project.refresh_from_db();assert (project.name,project.description,project.tags)==("After","new",["network"])
    event=AuditEvent.objects.get(action="project.metadata_updated",target_id=project.id)
    assert set(event.metadata["changed_fields"])=={"name","description","tags"} and "new" not in str(event.metadata)
    duplicate=client.post("/api/v1/projects/",{"name":"After","description":"","tags":[]},format="json")
    assert duplicate.status_code==400

@pytest.mark.django_db
def test_guarded_project_retirement_blocks_dependencies_then_hides_workspace_preserves_history_and_reuses_name():
    owner=User.objects.create_user("project-retire-owner",password="long-enough-password")
    admin=User.objects.create_user("project-retire-admin",password="long-enough-password")
    editor=User.objects.create_user("project-retire-editor",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Reusable project")
    ProjectMembership.objects.create(project=project,user=admin,role="administrator")
    ProjectMembership.objects.create(project=project,user=editor,role="editor")
    lab=Lab.objects.create(project=project,name="active lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="f"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-project-retire",runtime_version="0.8.0",observed_state="running")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,original_filename="active.bin",detected_format="unknown",byte_size=10,
        checksum="a"*64,storage_reference="",validation_status="unsupported")
    upload=UploadSession.objects.create(owner=owner,project=project,original_filename="active.tar",expected_size=20,
        expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_destination="/tmp/not-written")
    job=OperationJob.objects.create(deployment=deployment,owner=owner,operation_type="diagnostic_ping",target_id=deployment.id,
        idempotency_key="active-project-job",state="started")
    client=APIClient();client.force_authenticate(editor)
    assert client.get(f"/api/v1/projects/{project.id}/retirement-preview/").status_code==403
    client.force_authenticate(admin);blocked=client.get(f"/api/v1/projects/{project.id}/retirement-preview/")
    assert blocked.status_code==200 and not blocked.data["can_retire"]
    assert blocked.data["references"]|{"active_labs":1,"active_images":1,"active_uploads":1,"active_deployments":1,"active_operations":1}==blocked.data["references"]
    lab.deleted_at=timezone.now();lab.save(update_fields=["deleted_at","updated_at"]);artifact.deleted_at=timezone.now();artifact.save(update_fields=["deleted_at","updated_at"])
    upload.status="cancelled";upload.save(update_fields=["status","updated_at"]);deployment.observed_state="stopped";deployment.save(update_fields=["observed_state","updated_at"])
    job.state="succeeded";job.save(update_fields=["state","updated_at"])
    preview=client.get(f"/api/v1/projects/{project.id}/retirement-preview/");assert preview.data["can_retire"]
    key="retire-project-once";headers={"HTTP_IDEMPOTENCY_KEY":key,"HTTP_X_EXPECTED_UPDATED_AT":preview.data["updated_at"]}
    retired=client.post(f"/api/v1/projects/{project.id}/retire/",{},format="json",**headers)
    replay=client.post(f"/api/v1/projects/{project.id}/retire/",{},format="json",**headers)
    assert retired.status_code==replay.status_code==200 and retired.data==replay.data
    project.refresh_from_db();assert project.deleted_at and ProjectMembership.objects.filter(project=project).count()==2
    assert LabRevision.objects.filter(id=revision.id).exists() and LabDeployment.objects.filter(id=deployment.id).exists()
    assert client.get(f"/api/v1/projects/{project.id}/").status_code==404
    assert client.post("/api/v1/labs/",{"project":str(project.id),"name":"blocked","tags":[]},format="json").status_code==400
    client.force_authenticate(owner)
    replacement=client.post("/api/v1/projects/",{"name":"Reusable project","description":"replacement","tags":[]},format="json")
    assert replacement.status_code==201 and AuditEvent.objects.filter(action="project.retired",target_id=project.id).count()==1

@pytest.mark.django_db
def test_project_retirement_rejects_stale_preview_and_normal_destroy():
    owner=User.objects.create_user("project-retire-stale",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Stale project");client=APIClient();client.force_authenticate(owner)
    endpoint=f"/api/v1/projects/{project.id}/";assert client.delete(endpoint).status_code==405
    preview=client.get(endpoint+"retirement-preview/");project.description="changed";project.save(update_fields=["description","updated_at"])
    response=client.post(endpoint+"retire/",{},format="json",HTTP_IDEMPOTENCY_KEY="stale-project-retire",
        HTTP_X_EXPECTED_UPDATED_AT=preview.data["updated_at"])
    assert response.status_code==409 and response.data["error"]["code"]=="project_changed"

@pytest.mark.django_db
def test_project_quotas_are_admin_managed_reported_and_enforced_for_labs_members_and_uploads():
    owner=User.objects.create_user("quota-owner",password="long-enough-password")
    editor=User.objects.create_user("quota-editor",password="long-enough-password")
    candidate=User.objects.create_user("quota-candidate",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="quota-project")
    ProjectMembership.objects.create(project=project,user=editor,role="editor")
    endpoint=f"/api/v1/projects/{project.id}/quotas/";client=APIClient();client.force_authenticate(editor)
    assert client.patch(endpoint,{"max_labs":1},format="json").status_code==403
    client.force_authenticate(owner)
    response=client.patch(endpoint,{"max_labs":1,"max_nodes_per_lab":1,"max_running_deployments":1,"max_members":2,"max_image_bytes":1024**3},format="json")
    assert response.status_code==200 and response.data["limits"]["max_labs"]==1
    assert response.data["usage"]["members"]==2
    first=client.post("/api/v1/labs/",{"project":str(project.id),"name":"allowed","tags":[]},format="json")
    second=client.post("/api/v1/labs/",{"project":str(project.id),"name":"blocked","tags":[]},format="json")
    assert first.status_code==201 and second.status_code==409 and second.data["error"]["code"]=="project_quota_exceeded"
    member=client.post(f"/api/v1/projects/{project.id}/members/",{"username":candidate.username,"role":"viewer"},format="json")
    assert member.status_code==409 and member.data["error"]["code"]=="project_quota_exceeded"
    upload=client.post("/api/v1/uploads/",{"project":str(project.id),"original_filename":"too-large.tar","expected_size":2*1024**3},format="json")
    assert upload.status_code==409 and upload.data["error"]["code"]=="project_quota_exceeded"
    project.refresh_from_db();assert project.quotas["max_members"]==2
    assert AuditEvent.objects.filter(project=project,action="project.quotas_changed").count()==1

@pytest.mark.django_db
def test_quota_cannot_be_lowered_below_current_usage():
    owner=User.objects.create_user("quota-floor",password="long-enough-password");project=Project.objects.create(owner=owner,name="floor")
    Lab.objects.create(project=project,name="one");Lab.objects.create(project=project,name="two")
    client=APIClient();client.force_authenticate(owner)
    response=client.patch(f"/api/v1/projects/{project.id}/quotas/",{"max_labs":1},format="json")
    assert response.status_code==409 and response.data["error"]["code"]=="quota_below_current_usage"

@pytest.mark.django_db
def test_lab_metadata_update_is_scoped_audited_and_cannot_move_projects():
    owner=User.objects.create_user("lab-edit-owner",password="long-enough-password")
    viewer=User.objects.create_user("lab-edit-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="edit-project");other=Project.objects.create(owner=owner,name="other-project")
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="before",description="old",tags=["old"])
    client=APIClient();client.force_authenticate(viewer)
    assert client.patch(f"/api/v1/labs/{lab.id}/",{"name":"forbidden"},format="json").status_code==403
    client.force_authenticate(owner)
    moved=client.patch(f"/api/v1/labs/{lab.id}/",{"project":str(other.id)},format="json")
    assert moved.status_code==400
    changed=client.patch(f"/api/v1/labs/{lab.id}/",{"name":"after","description":"new","tags":["bgp"]},format="json")
    assert changed.status_code==200
    lab.refresh_from_db();assert lab.project==project and (lab.name,lab.description,lab.tags)==("after","new",["bgp"])
    event=AuditEvent.objects.get(action="lab.metadata_updated",target_id=lab.id)
    assert set(event.metadata["changed_fields"])=={"name","description","tags"} and "new" not in str(event.metadata)

@pytest.mark.django_db
def test_guarded_lab_deletion_blocks_runtime_then_preserves_history_releases_quota_and_is_idempotent():
    owner=User.objects.create_user("lab-delete-owner",password="long-enough-password")
    viewer=User.objects.create_user("lab-delete-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="delete-project",quotas={"max_labs":1})
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="Reusable lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-delete-test",runtime_version="0.8.0",observed_state="running")
    client=APIClient();client.force_authenticate(viewer)
    assert client.get(f"/api/v1/labs/{lab.id}/delete-preview/").status_code==403
    client.force_authenticate(owner)
    blocked=client.get(f"/api/v1/labs/{lab.id}/delete-preview/")
    assert blocked.status_code==200 and not blocked.data["can_delete"] and blocked.data["references"]["active_deployments"]==1
    rejected=client.post(f"/api/v1/labs/{lab.id}/delete/",{},format="json",HTTP_IDEMPOTENCY_KEY="blocked-delete",
        HTTP_X_EXPECTED_UPDATED_AT=blocked.data["updated_at"])
    assert rejected.status_code==409 and rejected.data["error"]["code"]=="lab_delete_blocked"
    deployment.observed_state="stopped";deployment.save(update_fields=["observed_state","updated_at"])
    preview=client.get(f"/api/v1/labs/{lab.id}/delete-preview/");key="safe-delete"
    deleted=client.post(f"/api/v1/labs/{lab.id}/delete/",{},format="json",HTTP_IDEMPOTENCY_KEY=key,
        HTTP_X_EXPECTED_UPDATED_AT=preview.data["updated_at"])
    replay=client.post(f"/api/v1/labs/{lab.id}/delete/",{},format="json",HTTP_IDEMPOTENCY_KEY=key,
        HTTP_X_EXPECTED_UPDATED_AT=preview.data["updated_at"])
    assert deleted.status_code==replay.status_code==200 and deleted.data==replay.data
    lab.refresh_from_db();assert lab.deleted_at and project_usage(project)["labs"]==0
    assert client.get(f"/api/v1/labs/{lab.id}/").status_code==404
    assert LabRevision.objects.filter(id=revision.id).exists() and LabDeployment.objects.filter(id=deployment.id).exists()
    replacement=client.post("/api/v1/labs/",{"project":str(project.id),"name":"Reusable lab","tags":[]},format="json")
    assert replacement.status_code==201
    assert AuditEvent.objects.filter(action="lab.deleted",target_id=lab.id).count()==1

@pytest.mark.django_db
def test_lab_deletion_rejects_stale_preview_and_normal_destroy():
    owner=User.objects.create_user("lab-delete-stale",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="stale-project");lab=Lab.objects.create(project=project,name="stale-lab")
    client=APIClient();client.force_authenticate(owner);endpoint=f"/api/v1/labs/{lab.id}/"
    assert client.delete(endpoint).status_code==405
    preview=client.get(endpoint+"delete-preview/");lab.description="changed";lab.save(update_fields=["description","updated_at"])
    response=client.post(endpoint+"delete/",{},format="json",HTTP_IDEMPOTENCY_KEY="stale-delete",HTTP_X_EXPECTED_UPDATED_AT=preview.data["updated_at"])
    assert response.status_code==409 and response.data["error"]["code"]=="lab_changed"

@pytest.mark.django_db
def test_lab_clone_is_deep_project_scoped_audited_and_quota_enforced():
    owner=User.objects.create_user("clone-owner",password="long-enough-password")
    viewer=User.objects.create_user("clone-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="clone-project",quotas={"max_labs":3})
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="source",description="reference",tags=["bgp"])
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="c"*64,annotations=[{"type":"text","text":"edge"}])
    lab.current_draft=revision;lab.save(update_fields=["current_draft"])
    template=DeviceTemplateVersion.objects.get(template__name="Linux Host")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="alpine",detected_format="oci-registry",
        byte_size=0,checksum="d"*64,architecture="amd64",storage_reference="docker.io/alpine",validation_status="validated")
    image=PublishedImage.objects.create(artifact=artifact,registry_digest="docker.io/alpine@sha256:"+"d"*64,repository="docker.io/alpine",architecture="amd64")
    configuration=ConfigurationVersion.objects.create(project=project,name="source/client/startup",version=1,
        encrypted_content=encrypt_configuration("hostname client\n"),checksum="e"*64,created_by=owner)
    nodes=[]
    for index,name in enumerate(("client","server")):
        node=LabNode.objects.create(revision=revision,name=name,template_version=template,published_image=image,position={"x":index*200,"y":80},startup_configuration=configuration if index==0 else None)
        interfaces=[LabInterface.objects.create(node=node,name=f"eth{number}") for number in range(1,5)];nodes.append((node,interfaces))
    LabLink.objects.create(revision=revision,endpoint_a=nodes[0][1][0],endpoint_b=nodes[1][1][0],label="data")
    client=APIClient();client.force_authenticate(viewer)
    assert client.post(f"/api/v1/labs/{lab.id}/clone/",{"name":"viewer-copy"},format="json").status_code==403
    client.force_authenticate(owner);response=client.post(f"/api/v1/labs/{lab.id}/clone/",{"name":"source copy"},format="json")
    assert response.status_code==201 and response.data["node_count"]==2 and response.data["link_count"]==1
    assert response.data["current_draft"] and response.data["workspace_url"]==f"/labs/{response.data['id']}/topology/"
    clone=Lab.objects.get(pk=response.data["id"]);cloned=clone.current_draft
    assert clone.description=="reference" and clone.tags==["bgp"] and cloned.id!=revision.id
    assert revision.annotations==[{"type":"text","text":"edge"}]
    assert cloned.annotations[0]["type"]=="note" and cloned.annotations[0]["text"]=="edge" and uuid.UUID(cloned.annotations[0]["id"])
    assert set(cloned.nodes.values_list("name",flat=True))=={"client","server"} and cloned.links.get().label=="data"
    cloned_config=cloned.nodes.get(name="client").startup_configuration
    assert cloned_config.id!=configuration.id and decrypt_configuration(cloned_config.encrypted_content)=="hostname client\n"
    assert AuditEvent.objects.filter(project=project,action="lab.cloned",target_id=clone.id,metadata__source_lab=str(lab.id)).exists()
    conflict=client.post(f"/api/v1/labs/{lab.id}/clone/",{"name":"source copy"},format="json")
    assert conflict.status_code==409 and conflict.data["error"]["code"]=="lab_name_conflict"
    Lab.objects.create(project=project,name="quota filler")
    quota=client.post(f"/api/v1/labs/{lab.id}/clone/",{"name":"over quota"},format="json")
    assert quota.status_code==409 and quota.data["error"]["code"]=="project_quota_exceeded"

@pytest.mark.django_db
def test_revision_history_restore_creates_new_draft_is_concurrency_safe_and_idempotent():
    owner=User.objects.create_user("history-owner",password="long-enough-password");viewer=User.objects.create_user("history-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="history-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="history-lab");template=DeviceTemplateVersion.objects.get(template__name="Linux Host")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="alpine",detected_format="oci-registry",
        byte_size=0,checksum="1"*64,architecture="amd64",storage_reference="docker.io/alpine",validation_status="validated")
    image=PublishedImage.objects.create(artifact=artifact,registry_digest="docker.io/alpine@sha256:"+"1"*64,repository="docker.io/alpine",architecture="amd64")
    config=ConfigurationVersion.objects.create(project=project,name="history/r1/startup",version=1,encrypted_content=encrypt_configuration("hostname restored\n"),checksum="2"*64,created_by=owner)
    published=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="3"*64,immutable=True,annotations=[{"text":"known-good"}])
    source_node=LabNode.objects.create(revision=published,name="r1",template_version=template,published_image=image,startup_configuration=config,position={"x":10,"y":20})
    for number in range(1,5): LabInterface.objects.create(node=source_node,name=f"eth{number}")
    LabDeployment.objects.create(revision=published,cluster_identity="test",namespace="clab-history-test",runtime_version="0.8.0",observed_state="stopped")
    draft=LabRevision.objects.create(lab=lab,revision_number=2,topology_checksum="4"*64)
    draft_node=LabNode.objects.create(revision=draft,name="temporary",template_version=template,published_image=image)
    for number in range(1,5): LabInterface.objects.create(node=draft_node,name=f"eth{number}")
    lab.current_draft=draft;lab.save(update_fields=["current_draft"])
    client=APIClient();client.force_authenticate(viewer)
    history=client.get(f"/api/v1/labs/{lab.id}/revisions/")
    assert history.status_code==200 and history.data["current_draft"]==str(draft.id) and len(history.data["revisions"])==2
    assert history.data["revisions"][1]["deployment_count"]==1
    comparison=client.get(f"/api/v1/labs/{lab.id}/revisions/compare/?left={published.id}&right={draft.id}")
    assert comparison.status_code==200 and comparison.data["nodes"]["added"]==["temporary"] and comparison.data["nodes"]["removed"]==["r1"]
    assert comparison.data["summary"]["nodes_added"]==comparison.data["summary"]["nodes_removed"]==1
    assert "hostname restored" not in str(comparison.data) and comparison["Cache-Control"]=="no-store"
    assert AuditEvent.objects.filter(project=project,action="lab.revisions_compared",metadata__left_revision=1,metadata__right_revision=2).exists()
    duplicate=client.get(f"/api/v1/labs/{lab.id}/revisions/compare/?left={published.id}&right={published.id}")
    assert duplicate.status_code==422 and duplicate.data["error"]["code"]=="duplicate_revision_selection"
    endpoint=f"/api/v1/labs/{lab.id}/revisions/{published.id}/restore/"
    assert client.post(endpoint,{"expected_current_draft":str(draft.id)},format="json",HTTP_IDEMPOTENCY_KEY="viewer-restore").status_code==403
    client.force_authenticate(owner)
    stale=client.post(endpoint,{"expected_current_draft":None},format="json",HTTP_IDEMPOTENCY_KEY="stale-restore")
    assert stale.status_code==409 and stale.data["error"]["code"]=="draft_changed"
    restored=client.post(endpoint,{"expected_current_draft":str(draft.id)},format="json",HTTP_IDEMPOTENCY_KEY="restore-v1")
    assert restored.status_code==201 and restored.data["revision_number"]==3 and restored.data["node_count"]==1
    replay=client.post(endpoint,{"expected_current_draft":str(draft.id)},format="json",HTTP_IDEMPOTENCY_KEY="restore-v1")
    assert replay.status_code==200 and replay.data["revision_id"]==restored.data["revision_id"]
    lab.refresh_from_db();new_draft=lab.current_draft
    assert new_draft.id==uuid.UUID(restored.data["revision_id"]) and not LabRevision.objects.filter(id=draft.id).exists()
    assert LabRevision.objects.filter(id=published.id,immutable=True,annotations=[{"text":"known-good"}]).exists()
    assert new_draft.annotations[0]["type"]=="note" and new_draft.annotations[0]["text"]=="known-good" and uuid.UUID(new_draft.annotations[0]["id"])
    cloned_node=new_draft.nodes.get();assert cloned_node.name=="r1" and cloned_node.id!=source_node.id
    assert decrypt_configuration(cloned_node.startup_configuration.encrypted_content)=="hostname restored\n"
    assert OperationJob.objects.filter(owner=owner,operation_type="restore_revision",idempotency_key="restore-v1",state="succeeded").count()==1
    assert AuditEvent.objects.filter(project=project,action="lab.revision_restored",target_id=new_draft.id,metadata__source_revision=str(published.id)).exists()

@pytest.mark.django_db
def test_upload_creation_is_project_scoped_and_checksum_optional():
    owner=User.objects.create_user("upload-owner",password="long-enough-password");viewer=User.objects.create_user("upload-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="uploads");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    payload={"project":str(project.id),"original_filename":"router.tar","expected_size":1024,"expected_checksum":""}
    client=APIClient();client.force_authenticate(viewer)
    assert client.post("/api/v1/uploads/",payload,format="json").status_code==403
    client.force_authenticate(owner);response=client.post("/api/v1/uploads/",payload,format="json")
    assert response.status_code==201 and response.data["expected_checksum"]=="" and "artifact_destination" not in response.data
    session=UploadSession.objects.get(id=response.data["id"])
    assert session.owner==owner and AuditEvent.objects.filter(action="image.upload_created",target_id=session.id).exists()

@pytest.mark.django_db
def test_octet_stream_chunk_endpoint_advances_exact_offset(settings,tmp_path):
    settings.MEDIA_ROOT=tmp_path
    owner=User.objects.create_user("chunk-owner",password="long-enough-password");project=Project.objects.create(owner=owner,name="chunks")
    client=APIClient();client.force_authenticate(owner)
    created=client.post("/api/v1/uploads/",{"project":str(project.id),"original_filename":"image.tar","expected_size":6,"expected_checksum":""},format="json")
    response=client.put(f"/api/v1/uploads/{created.data['id']}/chunks/",b"abc",content_type="application/octet-stream",HTTP_UPLOAD_OFFSET="0")
    assert response.status_code==204 and response["Upload-Offset"]=="3"
    session=UploadSession.objects.get(id=created.data["id"])
    assert session.received_bytes==3 and Path(session.artifact_destination).read_bytes()==b"abc"

@pytest.mark.django_db
def test_local_publication_is_licensed_project_scoped_and_idempotent(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("publisher",password="long-enough-password")
    viewer=User.objects.create_user("publish-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="publish")
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,original_filename="alpine.tar",detected_format="docker-archive",
        byte_size=10,checksum="a"*64,architecture="amd64",storage_reference="/artifacts/quarantine/image",validation_status="validated",
        inspection_result={"deployable":True,"import_source":"sha256:"+"b"*64})
    client=APIClient();client.force_authenticate(viewer)
    assert client.post(f"/api/v1/images/{artifact.id}/publish/",{},format="json",HTTP_IDEMPOTENCY_KEY="viewer-publish").status_code==403
    client.force_authenticate(owner)
    assert client.post(f"/api/v1/images/{artifact.id}/publish/",{},format="json",HTTP_IDEMPOTENCY_KEY="unlicensed").status_code==422
    artifact.license_acknowledged=True;artifact.save(update_fields=["license_acknowledged"])
    first=client.post(f"/api/v1/images/{artifact.id}/publish/",{},format="json",HTTP_IDEMPOTENCY_KEY="publish-once")
    second=client.post(f"/api/v1/images/{artifact.id}/publish/",{},format="json",HTTP_IDEMPOTENCY_KEY="publish-once")
    assert first.status_code==second.status_code==202 and first.data["id"]==second.data["id"]
    assert OperationJob.objects.filter(operation_type="publish_image",target_id=artifact.id).count()==1
    assert AuditEvent.objects.filter(action="image.publication_scheduled",target_id=artifact.id).count()==1

@pytest.mark.django_db
def test_owner_can_force_republish_ready_node_local_image(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("repair-publisher",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="repair")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="upload",original_filename="router.tar",detected_format="docker-archive",
        byte_size=10,checksum="c"*64,architecture="amd64",storage_reference="/artifacts/router.tar",validation_status="validated",license_acknowledged=True,
        inspection_result={"deployable":True,"import_source":"sha256:"+"d"*64})
    published=PublishedImage.objects.create(artifact=artifact,registry_digest="containerlab.local/router:checksum",repository="containerlab.local/router",architecture="amd64",lifecycle_status="ready")
    client=APIClient();client.force_authenticate(owner)
    assert client.post(f"/api/v1/images/{artifact.id}/publish/",{},format="json",HTTP_IDEMPOTENCY_KEY="return-ready").status_code==200
    response=client.post(f"/api/v1/images/{artifact.id}/publish/",{"force":True},format="json",HTTP_IDEMPOTENCY_KEY="repair-node-copy")
    assert response.status_code==202 and response.data["request_payload"]["force"] is True
    published.refresh_from_db();assert published.lifecycle_status=="reconciling"
    assert AuditEvent.objects.filter(action="image.republication_scheduled",target_id=artifact.id).exists()

@pytest.mark.django_db
def test_successful_image_publication_retains_bounded_build_output(monkeypatch):
    owner=User.objects.create_user("build-log-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="build-log-project")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="upload",original_filename="frr.tar",
        detected_format="docker-archive",byte_size=10,checksum="f"*64,architecture="amd64",storage_reference="/artifacts/frr.tar",
        validation_status="validated",license_acknowledged=True,inspection_result={"deployable":True})
    build=ImageBuild.objects.create(artifact=artifact,recipe_version="node-containerd-v1",job_identity="retained-build")
    job=OperationJob.objects.create(owner=owner,operation_type="publish_image",target_id=artifact.id,idempotency_key="retain-build-log",
        request_payload={"build_id":str(build.id)})
    class Adapter:
        def publish_local_image(self,received_artifact,received_build):
            assert (received_artifact.id,received_build.id)==(artifact.id,build.id)
            return {"reference":"containerlab.local/frr:retained","repository":"containerlab.local/frr",
                "publication_mode":"node-containerd","logs":"discarded-prefix\n"+("x"*12020)+"\nverified"}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter);execute_operation.run(str(job.id));build.refresh_from_db();job.refresh_from_db()
    assert job.state=="succeeded" and "logs" not in job.result_payload
    assert len(build.log_excerpt)==12000 and build.log_excerpt.endswith("verified") and "discarded-prefix" not in build.log_excerpt
    assert PublishedImage.objects.get(artifact=artifact).compatibility_result["publication_mode"]=="node-containerd"

@pytest.mark.django_db
def test_image_evidence_is_project_scoped_no_store_and_excludes_storage_path():
    owner=User.objects.create_user("image-evidence-owner",password="long-enough-password")
    viewer=User.objects.create_user("image-evidence-viewer",password="long-enough-password")
    stranger=User.objects.create_user("image-evidence-stranger",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="image-evidence-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="upload",original_filename="frr.tar",vendor="FRRouting",
        category="router",version="10.4.1",detected_format="docker-archive",byte_size=4096,checksum="e"*64,architecture="amd64",
        storage_reference="/private/quarantine/frr.tar",validation_status="validated",license_acknowledged=True,
        inspection_result={"deployable":True,"manifest_count":1})
    build=ImageBuild.objects.create(artifact=artifact,recipe_version="node-containerd-v1",job_identity="evidence-build",status="succeeded",
        started_at=timezone.now()-timezone.timedelta(seconds=3),finished_at=timezone.now(),log_reference="kubernetes-job/evidence-build",
        log_excerpt="imported frr.tar\nverified node containerd image")
    PublishedImage.objects.create(artifact=artifact,build=build,registry_digest="containerlab.local/frr:sha256-"+artifact.checksum,
        repository="containerlab.local/frr",architecture="amd64",compatibility_result={"publication_mode":"node-containerd"},lifecycle_status="ready")
    client=APIClient();client.force_authenticate(viewer)
    response=client.get(f"/api/v1/images/{artifact.id}/evidence/")
    assert response.status_code==200 and response["Cache-Control"]=="no-store" and response["X-Content-Type-Options"]=="nosniff"
    assert response.data["vendor"]=="FRRouting" and response.data["builds"][0]["status"]=="succeeded"
    assert response.data["builds"][0]["log_excerpt"].endswith("verified node containerd image")
    assert response.data["publications"][0]["compatibility"]["publication_mode"]=="node-containerd"
    assert "storage_reference" not in response.data and "/private/quarantine" not in str(response.data)
    client.force_authenticate(stranger)
    assert client.get(f"/api/v1/images/{artifact.id}/evidence/").status_code==404

@pytest.mark.django_db
def test_image_metadata_is_operator_only_validated_optimistic_and_audited():
    owner=User.objects.create_user("image-metadata-owner",password="long-enough-password")
    editor=User.objects.create_user("image-metadata-editor",password="long-enough-password")
    viewer=User.objects.create_user("image-metadata-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="image-metadata-project")
    ProjectMembership.objects.create(project=project,user=editor,role="editor");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="upload",original_filename="frr.tar",detected_format="docker-archive",
        byte_size=4096,checksum="9"*64,architecture="amd64",storage_reference="/private/frr.tar",validation_status="validated",license_acknowledged=True)
    endpoint=f"/api/v1/images/{artifact.id}/metadata/";payload={"vendor":"FRRouting","category":"router","version":"10.4.1"}
    client=APIClient();client.force_authenticate(viewer)
    assert client.put(endpoint,payload,format="json",HTTP_X_EXPECTED_UPDATED_AT=artifact.updated_at.isoformat()).status_code==403
    client.force_authenticate(editor)
    assert client.put(endpoint,payload,format="json").status_code==400
    invalid=client.put(endpoint,{**payload,"vendor":"FRR\nInjected"},format="json",HTTP_X_EXPECTED_UPDATED_AT=artifact.updated_at.isoformat())
    assert invalid.status_code==400
    expected=artifact.updated_at.isoformat();response=client.put(endpoint,payload,format="json",HTTP_X_EXPECTED_UPDATED_AT=expected)
    assert response.status_code==200 and (response.data["vendor"],response.data["category"],response.data["version"])==("FRRouting","router","10.4.1")
    artifact.refresh_from_db();assert artifact.checksum=="9"*64 and artifact.original_filename=="frr.tar"
    listing=client.get("/api/v1/images/");retrieved=client.get(f"/api/v1/images/{artifact.id}/")
    assert "storage_reference" not in listing.data["results"][0] and "storage_reference" not in retrieved.data
    assert "/private/frr.tar" not in str(listing.data) and "/private/frr.tar" not in str(retrieved.data)
    event=AuditEvent.objects.get(action="image.metadata_updated",target_id=artifact.id)
    assert event.actor==editor and event.metadata["changed"]["vendor"]=={"from":"","to":"FRRouting"}
    stale=client.put(endpoint,{**payload,"version":"10.5"},format="json",HTTP_X_EXPECTED_UPDATED_AT=expected)
    assert stale.status_code==409 and stale.data["error"]["code"]=="image_changed"
    replay=client.put(endpoint,payload,format="json",HTTP_X_EXPECTED_UPDATED_AT=artifact.updated_at.isoformat())
    assert replay.status_code==200 and AuditEvent.objects.filter(action="image.metadata_updated",target_id=artifact.id).count()==1

@pytest.mark.django_db
def test_registry_credentials_are_project_scoped_encrypted_redacted_rotatable_and_deactivated():
    owner=User.objects.create_user("credential-owner",password="long-enough-password")
    editor=User.objects.create_user("credential-editor",password="long-enough-password")
    viewer=User.objects.create_user("credential-viewer",password="long-enough-password")
    stranger=User.objects.create_user("credential-stranger",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="credential-project")
    ProjectMembership.objects.create(project=project,user=editor,role="editor");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    endpoint="/api/v1/image-credentials/";payload={"project":str(project.id),"name":"Private registry","registry_host":"registry.example:5000",
        "credential_type":"basic","username":"studio-pull","secret":"Top-Secret-Registry-Token"}
    client=APIClient();client.force_authenticate(viewer)
    assert client.post(endpoint,payload,format="json").status_code==403
    client.force_authenticate(editor);created=client.post(endpoint,payload,format="json")
    assert created.status_code==201 and created.data["credential_present"] is True and created.data["secret_fingerprint"]
    serialized=str(created.data);assert "Top-Secret" not in serialized and "encrypted_secret" not in serialized and "secret_name" not in serialized
    credential=ImageCredentialReference.objects.get(id=created.data["id"])
    assert decrypt_secret(credential.encrypted_secret)=="Top-Secret-Registry-Token" and b"Top-Secret" not in bytes(credential.encrypted_secret)
    client.force_authenticate(viewer);listing=client.get(endpoint)
    assert listing.status_code==200 and "Top-Secret" not in str(listing.data) and listing.data["results"][0]["credential_present"] is True
    client.force_authenticate(stranger)
    assert client.get(f"{endpoint}{credential.id}/").status_code==404
    client.force_authenticate(editor)
    artifact=ImageArtifact.objects.create(project=project,owner=owner,credential_reference=credential,source_type="registry",registry_reference="registry.example:5000/frr@sha256:"+"a"*64,
        original_filename="frr",detected_format="oci-registry",byte_size=0,checksum="a"*64,architecture="amd64",storage_reference="registry.example:5000/frr",
        validation_status="validated",license_acknowledged=True)
    rejected=client.patch(f"{endpoint}{credential.id}/",{"registry_host":"other.example"},format="json")
    assert rejected.status_code==400
    rotated=client.patch(f"{endpoint}{credential.id}/",{"secret":"Rotated-Registry-Token"},format="json")
    credential.refresh_from_db();assert rotated.status_code==200 and decrypt_secret(credential.encrypted_secret)=="Rotated-Registry-Token"
    assert client.delete(f"{endpoint}{credential.id}/").status_code==204
    credential.refresh_from_db();assert credential.is_active is False and artifact.credential_reference_id==credential.id
    audit_text=str(list(AuditEvent.objects.filter(target_id=credential.id).values_list("metadata",flat=True)))
    assert "Registry-Token" not in audit_text and AuditEvent.objects.filter(action="image_credential.deactivated",target_id=credential.id).exists()

@pytest.mark.django_db
def test_image_deletion_is_previewed_guarded_audited_idempotent_and_releases_storage(settings,tmp_path):
    settings.MEDIA_ROOT=tmp_path
    owner=User.objects.create_user("image-delete-owner",password="long-enough-password")
    viewer=User.objects.create_user("image-delete-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="image-delete-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    stored=tmp_path/"quarantine"/"unsupported.bin";stored.parent.mkdir();stored.write_bytes(b"not an image")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="upload",original_filename="unsupported.bin",
        detected_format="unknown",byte_size=12,checksum="a"*64,architecture="",storage_reference=str(stored),validation_status="unsupported")
    client=APIClient();client.force_authenticate(viewer)
    assert client.get(f"/api/v1/images/{artifact.id}/delete-preview/").status_code==403
    client.force_authenticate(owner);preview=client.get(f"/api/v1/images/{artifact.id}/delete-preview/")
    assert preview.status_code==200 and preview.data["can_delete"] is True and preview.data["references"]=={
        "publications":0,"builds":0,"lab_revisions":0,"active_operations":0}
    endpoint=f"/api/v1/images/{artifact.id}/delete/"
    stale=client.post(endpoint,{},format="json",HTTP_IDEMPOTENCY_KEY="delete-stale",HTTP_X_EXPECTED_CHECKSUM="b"*64)
    assert stale.status_code==409 and stored.exists()
    deleted=client.post(endpoint,{},format="json",HTTP_IDEMPOTENCY_KEY="delete-once",HTTP_X_EXPECTED_CHECKSUM=artifact.checksum)
    replay=client.post(endpoint,{},format="json",HTTP_IDEMPOTENCY_KEY="delete-once",HTTP_X_EXPECTED_CHECKSUM=artifact.checksum)
    assert deleted.status_code==replay.status_code==200 and deleted.data==replay.data and deleted.data["storage_removed"] is True
    artifact.refresh_from_db();assert artifact.deleted_at and artifact.storage_reference=="" and not stored.exists()
    assert client.get("/api/v1/images/").data["count"]==0 and project_usage(project)["image_bytes"]==0
    assert OperationJob.objects.filter(operation_type="delete_image",target_id=artifact.id,state="succeeded").count()==1
    assert AuditEvent.objects.filter(action="image.deleted",target_id=artifact.id,metadata__storage_removed=True).exists()
    replacement=ImageArtifact.objects.create(project=project,owner=owner,source_type="upload",original_filename="replacement.bin",
        detected_format="unknown",byte_size=1,checksum=artifact.checksum,storage_reference="",validation_status="unsupported")
    assert replacement.id!=artifact.id

@pytest.mark.django_db
def test_image_deletion_refuses_published_or_built_artifacts():
    owner=User.objects.create_user("image-protected-owner",password="long-enough-password");project=Project.objects.create(owner=owner,name="protected-image-project")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="router",
        detected_format="oci-registry",byte_size=0,checksum="c"*64,architecture="amd64",storage_reference="registry/router",validation_status="validated")
    PublishedImage.objects.create(artifact=artifact,registry_digest="registry/router@sha256:"+"c"*64,repository="registry/router",architecture="amd64")
    client=APIClient();client.force_authenticate(owner);preview=client.get(f"/api/v1/images/{artifact.id}/delete-preview/")
    assert preview.status_code==200 and preview.data["can_delete"] is False and preview.data["references"]["publications"]==1
    blocked=client.post(f"/api/v1/images/{artifact.id}/delete/",{},format="json",HTTP_IDEMPOTENCY_KEY="blocked-delete",HTTP_X_EXPECTED_CHECKSUM=artifact.checksum)
    assert blocked.status_code==409 and blocked.data["error"]["code"]=="image_in_use"
    artifact.refresh_from_db();assert artifact.deleted_at is None

@pytest.mark.django_db
def test_owner_can_publish_and_schedule_deployable_lab(django_capture_on_commit_callbacks):
    owner=User.objects.create_user("deployer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="runtime")
    lab=Lab.objects.create(project=project,name="connectivity")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64)
    lab.current_draft=revision; lab.save()
    template=DeviceTemplateVersion.objects.get(template__name="Linux Host")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="alpine",
        detected_format="oci-registry",byte_size=0,checksum="b"*64,architecture="amd64",storage_reference="docker.io/alpine",validation_status="validated")
    image=PublishedImage.objects.create(artifact=artifact,registry_digest="docker.io/alpine@sha256:"+"b"*64,repository="docker.io/alpine",architecture="amd64")
    LabNode.objects.create(revision=revision,name="client",template_version=template,published_image=image)
    c=APIClient(); c.force_authenticate(owner)
    preview=c.get(f"/api/v1/labs/{lab.id}/deploy-preview/")
    assert preview.status_code==200 and preview.data["can_deploy"] is True
    assert preview.data["draft"]["id"]==str(revision.id) and preview.data["capacity"]["after"]==1
    with django_capture_on_commit_callbacks(execute=False):
        response=c.post(f"/api/v1/labs/{lab.id}/deploy/",{"expected_draft":str(revision.id),"strategy":"new_runtime",
            "acknowledge_existing_runtimes":False},format="json",HTTP_IDEMPOTENCY_KEY="test-deploy")
    assert response.status_code==202, response.data
    lab.refresh_from_db(); assert lab.current_draft is None
    assert response.data["deployment"]["namespace"].startswith("clab-")

@pytest.mark.django_db
def test_topology_validation_report_explains_deployment_readiness_without_configuration_content():
    owner=User.objects.create_user("preflight-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="preflight-project")
    lab=Lab.objects.create(project=project,name="preflight-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64,edit_version=3)
    lab.current_draft=revision;lab.save(update_fields=["current_draft"])
    template=DeviceTemplateVersion.objects.get(template__name="Linux Host")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="alpine:3.22",
        detected_format="oci-registry",byte_size=0,checksum="b"*64,architecture="amd64",storage_reference="docker.io/alpine",
        validation_status="validated")
    image=PublishedImage.objects.create(artifact=artifact,registry_digest="docker.io/alpine@sha256:"+"b"*64,
        repository="docker.io/alpine",architecture="amd64",lifecycle_status="ready")
    node=LabNode.objects.create(revision=revision,name="client",template_version=template,published_image=image)
    LabInterface.objects.create(node=node,name="eth1")
    client=APIClient();client.force_authenticate(owner)
    response=client.get(f"/api/v1/labs/{lab.id}/validation-report/")
    assert response.status_code==200 and response.data["ready"] is True
    assert response.data["revision"]=={"id":str(revision.id),"number":1,"edit_version":3,"checksum":"a"*64,"immutable":False}
    assert response.data["summary"]["passed_checks"]==6 and response.data["adapter"]["clabernetes"]=="0.8.0"
    device=response.data["devices"][0]
    assert device["name"]=="client" and device["image"]["status"]=="compatible"
    assert device["interfaces"]=={"total":1,"linked":0,"free":1,"required":0}
    serialized=repr(response.data).lower()
    assert "encrypted_content" not in serialized and "storage_reference" not in serialized and "configuration_content" not in serialized

@pytest.mark.django_db
def test_topology_validation_report_returns_actionable_device_failures_and_empty_draft_state():
    owner=User.objects.create_user("preflight-failure-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="preflight-failure-project")
    empty=Lab.objects.create(project=project,name="empty-preflight")
    client=APIClient();client.force_authenticate(owner)
    no_draft=client.get(f"/api/v1/labs/{empty.id}/validation-report/")
    assert no_draft.status_code==200 and no_draft.data["ready"] is False and no_draft.data["revision"] is None
    lab=Lab.objects.create(project=project,name="broken-preflight")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="c"*64)
    lab.current_draft=revision;lab.save(update_fields=["current_draft"])
    template=DeviceTemplateVersion.objects.get(template__name="Linux Firewall")
    node=LabNode.objects.create(revision=revision,name="fw1",template_version=template)
    LabInterface.objects.create(node=node,name="eth1")
    report=client.get(f"/api/v1/labs/{lab.id}/validation-report/")
    assert report.status_code==200 and report.data["ready"] is False
    assert report.data["devices"][0]["status"]=="failed" and report.data["devices"][0]["image"] is None
    assert report.data["devices"][0]["configuration"]["state"]=="required"
    assert any("no immutable published image" in item for item in report.data["errors"])
    assert any("startup configuration is required" in item for item in report.data["errors"])
    assert {check["key"]:check["status"] for check in report.data["checks"]}["adapter"]=="failed"

@pytest.mark.django_db
def test_active_deployment_quota_blocks_new_runtime_without_consuming_draft():
    owner=User.objects.create_user("deployment-quota",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="bounded-runtime",quotas={"max_running_deployments":1})
    existing_lab=Lab.objects.create(project=project,name="already-running")
    existing_revision=LabRevision.objects.create(lab=existing_lab,revision_number=1,topology_checksum="7"*64,immutable=True)
    LabDeployment.objects.create(revision=existing_revision,cluster_identity="test",namespace="quota-existing-runtime",runtime_version="0.8.0",observed_state="running")
    lab=Lab.objects.create(project=project,name="new-runtime")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="8"*64)
    lab.current_draft=revision;lab.save(update_fields=["current_draft"])
    catalog=DeviceTemplate.objects.create(name="Quota Linux")
    template=DeviceTemplateVersion.objects.create(template=catalog,version=1,containerlab_kind="linux",interface_rules={"prefix":"eth","start":1,"count":2})
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="alpine",detected_format="oci-registry",
        byte_size=0,checksum="9"*64,architecture="amd64",storage_reference="docker.io/alpine",validation_status="validated")
    image=PublishedImage.objects.create(artifact=artifact,registry_digest="docker.io/alpine@sha256:"+"9"*64,repository="docker.io/alpine",architecture="amd64")
    LabNode.objects.create(revision=revision,name="client",template_version=template,published_image=image)
    client=APIClient();client.force_authenticate(owner)
    preview=client.get(f"/api/v1/labs/{lab.id}/deploy-preview/")
    assert preview.status_code==200 and preview.data["can_deploy"] is False
    assert preview.data["capacity"]=={"used":1,"limit":1,"after":2}
    response=client.post(f"/api/v1/labs/{lab.id}/deploy/",{"expected_draft":str(revision.id),"strategy":"new_runtime",
        "acknowledge_existing_runtimes":False},format="json",HTTP_IDEMPOTENCY_KEY="quota-blocked-deploy")
    assert response.status_code==409 and response.data["error"]["code"]=="project_quota_exceeded"
    lab.refresh_from_db();revision.refresh_from_db()
    assert lab.current_draft_id==revision.id and revision.immutable is False

@pytest.mark.django_db
def test_deployment_plan_protects_existing_runtime_and_rejects_stale_or_unacknowledged_drafts(django_capture_on_commit_callbacks):
    owner=User.objects.create_user("safe-deployer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="safe-runtime",quotas={"max_running_deployments":3})
    lab=Lab.objects.create(project=project,name="branch-office")
    published=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="1"*64,immutable=True)
    running=LabDeployment.objects.create(revision=published,cluster_identity="test",namespace="clab-pinned-runtime",
        runtime_version="0.8.0",observed_state="running")
    draft=LabRevision.objects.create(lab=lab,revision_number=2,topology_checksum="2"*64)
    lab.current_draft=draft;lab.save(update_fields=["current_draft"])
    template=DeviceTemplateVersion.objects.get(template__name="Linux Host")
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="alpine",detected_format="oci-registry",
        byte_size=0,checksum="3"*64,architecture="amd64",storage_reference="docker.io/alpine",validation_status="validated")
    image=PublishedImage.objects.create(artifact=artifact,registry_digest="docker.io/alpine@sha256:"+"3"*64,repository="docker.io/alpine",architecture="amd64")
    LabNode.objects.create(revision=draft,name="client",template_version=template,published_image=image)
    client=APIClient();client.force_authenticate(owner)
    preview=client.get(f"/api/v1/labs/{lab.id}/deploy-preview/")
    assert preview.status_code==200 and preview.data["requires_active_runtime_acknowledgement"] is True
    assert preview.data["active_runtimes"][0]["id"]==str(running.id)
    assert preview.data["capacity"]=={"used":1,"limit":3,"after":2}
    assert any("pinned revision unchanged" in item for item in preview.data["impact"])
    payload={"expected_draft":str(draft.id),"strategy":"new_runtime","acknowledge_existing_runtimes":False}
    blocked=client.post(f"/api/v1/labs/{lab.id}/deploy/",payload,format="json",HTTP_IDEMPOTENCY_KEY="ack-required")
    assert blocked.status_code==409 and blocked.data["error"]["code"]=="active_runtime_acknowledgement_required"
    stale=client.post(f"/api/v1/labs/{lab.id}/deploy/",{**payload,"expected_draft":str(uuid.uuid4()),"acknowledge_existing_runtimes":True},
        format="json",HTTP_IDEMPOTENCY_KEY="stale-preview")
    assert stale.status_code==409 and stale.data["error"]["code"]=="draft_changed"
    with django_capture_on_commit_callbacks(execute=False):
        accepted=client.post(f"/api/v1/labs/{lab.id}/deploy/",{**payload,"acknowledge_existing_runtimes":True},
            format="json",HTTP_IDEMPOTENCY_KEY="safe-new-runtime")
    assert accepted.status_code==202
    running.refresh_from_db();published.refresh_from_db()
    assert running.observed_state=="running" and running.revision_id==published.id
    event=AuditEvent.objects.get(action="lab.deployment_scheduled",target_id=accepted.data["deployment"]["id"])
    assert event.metadata["existing_active_runtimes"]==[str(running.id)]

@pytest.mark.django_db
def test_viewer_can_read_runtime_but_cannot_run_diagnostics():
    owner=User.objects.create_user("runtime-owner",password="long-enough-password")
    viewer=User.objects.create_user("runtime-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="shared-runtime")
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="shared-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="c"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-viewer-test",runtime_version="0.8.0")
    c=APIClient(); c.force_authenticate(viewer)
    assert c.get(f"/api/v1/deployments/{deployment.id}/runtime/").status_code==200
    response=c.post(f"/api/v1/deployments/{deployment.id}/diagnostics/",{"target":"10.0.0.1"},format="json",HTTP_IDEMPOTENCY_KEY="viewer-ping")
    assert response.status_code==403

@pytest.mark.django_db
def test_diagnostic_rejects_non_ip_targets_before_scheduling():
    owner=User.objects.create_user("diagnostic-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="diagnostics")
    lab=Lab.objects.create(project=project,name="lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="d"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-diagnostic-test",runtime_version="0.8.0")
    c=APIClient(); c.force_authenticate(owner)
    response=c.post(f"/api/v1/deployments/{deployment.id}/diagnostics/",{"target":"example.com"},format="json",HTTP_IDEMPOTENCY_KEY="invalid-ping")
    assert response.status_code==422
    assert response.data["error"]["code"]=="invalid_target"

@pytest.mark.django_db
def test_traceroute_diagnostic_is_bounded_idempotent_audited_and_executed(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("trace-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="trace-project");lab=Lab.objects.create(project=project,name="trace-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="e"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.get(template__name="FRR Router"))
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-trace",runtime_version="0.8.0",observed_state="running")
    DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-pod"})
    client=APIClient();client.force_authenticate(owner);endpoint=f"/api/v1/deployments/{deployment.id}/diagnostics/"
    payload={"operation":"traceroute","node_id":str(node.id),"target":"10.2.2.2","max_hops":20,"timeout":2,"probes":1}
    invalid=client.post(endpoint,{**payload,"max_hops":31},format="json",HTTP_IDEMPOTENCY_KEY="bad-trace")
    assert invalid.status_code==422 and invalid.data["error"]["code"]=="invalid_bounds"
    first=client.post(endpoint,payload,format="json",HTTP_IDEMPOTENCY_KEY="trace-once")
    second=client.post(endpoint,payload,format="json",HTTP_IDEMPOTENCY_KEY="trace-once")
    assert first.status_code==second.status_code==202 and first.data["id"]==second.data["id"]
    conflict=client.post(endpoint,{**payload,"target":"10.3.3.3"},format="json",HTTP_IDEMPOTENCY_KEY="trace-once")
    assert conflict.status_code==409 and conflict.data["error"]["code"]=="idempotency_conflict"
    assert AuditEvent.objects.filter(action="diagnostic.scheduled",target_id=deployment.id,metadata__operation="traceroute").count()==1
    class Adapter:
        def traceroute(self,received_deployment,received_node,target,max_hops,timeout,probes):
            assert (received_deployment.id,received_node.id,target,max_hops,timeout,probes)==(deployment.id,node.id,"10.2.2.2",20,2,1)
            return {"node":"r1","target":target,"command":"traceroute","max_hops":max_hops,"timeout":timeout,"probes":probes,
                "output":"1 10.0.0.2 0.4 ms\n2 10.2.2.2 0.6 ms\n"}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter);execute_operation.run(str(first.data["id"]))
    job=OperationJob.objects.get(id=first.data["id"]);deployment.refresh_from_db()
    assert job.state=="succeeded" and job.result_payload["command"]=="traceroute" and "10.2.2.2" in job.result_payload["output"]
    assert deployment.observed_state=="running"

@pytest.mark.django_db
def test_structured_device_inspection_is_authorized_idempotent_audited_and_executed(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("inspect-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="inspect-project");lab=Lab.objects.create(project=project,name="inspect-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="4"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.get(template__name="FRR Router"))
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-inspect",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-pod"})
    client=APIClient();client.force_authenticate(owner);endpoint=f"/api/v1/deployments/{deployment.id}/device-operations/"
    payload={"operation":"inspect_device","device_id":str(device.id)}
    first=client.post(endpoint,payload,format="json",HTTP_IDEMPOTENCY_KEY="inspect-once")
    second=client.post(endpoint,payload,format="json",HTTP_IDEMPOTENCY_KEY="inspect-once")
    assert first.status_code==second.status_code==202 and first.data["id"]==second.data["id"]
    assert AuditEvent.objects.filter(action="device.inspection_requested",target_id=device.id).count()==1
    class Adapter:
        def inspect_device(self,received_deployment,received_device):
            assert (received_deployment.id,received_device.id)==(deployment.id,device.id)
            return {"device":"r1","interfaces":[{"name":"eth1","state":"UP","addresses":[]}],"routes":[],"neighbors":[],"truncated":{}}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter);execute_operation.run(str(first.data["id"]))
    job=OperationJob.objects.get(id=first.data["id"])
    assert job.state=="succeeded" and job.result_payload["interfaces"][0]["name"]=="eth1"

@pytest.mark.django_db
def test_runtime_device_contract_exposes_logical_node_identity():
    owner=User.objects.create_user("device-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="devices")
    lab=Lab.objects.create(project=project,name="lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="f"*64,immutable=True)
    template=DeviceTemplateVersion.objects.first()
    node=LabNode.objects.create(revision=revision,name="r1",template_version=template,position={"x":240,"y":120})
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-device-test",runtime_version="0.8.0")
    DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready")
    c=APIClient(); c.force_authenticate(owner)
    device=c.get(f"/api/v1/deployments/{deployment.id}/runtime/").data["devices"][0]
    assert str(device["node_id"])==str(node.id) and device["position"]=={"x":240,"y":120}
    assert device["template_name"]==template.template.name

@pytest.mark.django_db
def test_redeploy_preview_and_operation_are_authorized_audited_and_idempotent(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("redeploy-owner",password="long-enough-password")
    viewer=User.objects.create_user("redeploy-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="redeploy-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="redeploy-lab");revision=LabRevision.objects.create(lab=lab,revision_number=3,topology_checksum="e"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-redeploy",runtime_version="0.8.0",observed_state="running")
    client=APIClient();client.force_authenticate(viewer)
    preview=client.get(f"/api/v1/deployments/{deployment.id}/redeploy-preview/")
    assert preview.status_code==200 and preview.data["revision"]==3 and preview.data["runtime_exists"] is True
    forbidden=client.post(f"/api/v1/deployments/{deployment.id}/operations/",{"operation":"redeploy_lab"},format="json",HTTP_IDEMPOTENCY_KEY="viewer-redeploy")
    assert forbidden.status_code==403
    client.force_authenticate(owner)
    first=client.post(f"/api/v1/deployments/{deployment.id}/operations/",{"operation":"redeploy_lab"},format="json",HTTP_IDEMPOTENCY_KEY="owner-redeploy")
    second=client.post(f"/api/v1/deployments/{deployment.id}/operations/",{"operation":"redeploy_lab"},format="json",HTTP_IDEMPOTENCY_KEY="owner-redeploy")
    assert first.status_code==second.status_code==202 and first.data["id"]==second.data["id"]
    assert AuditEvent.objects.filter(action="deployment.redeploy_scheduled",target_id=deployment.id).count()==1
    conflict=client.post(f"/api/v1/deployments/{deployment.id}/operations/",{"operation":"stop_lab"},format="json",HTTP_IDEMPOTENCY_KEY="owner-redeploy")
    assert conflict.status_code==409 and conflict.data["error"]["code"]=="idempotency_conflict"

@pytest.mark.django_db
def test_redeploy_worker_replaces_runtime_and_marks_deploying(monkeypatch):
    owner=User.objects.create_user("redeploy-worker",password="long-enough-password");project=Project.objects.create(owner=owner,name="redeploy-worker-project")
    lab=Lab.objects.create(project=project,name="lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-redeploy-worker",runtime_version="0.8.0",observed_state="running")
    job=OperationJob.objects.create(deployment=deployment,owner=owner,operation_type="redeploy_lab",target_id=deployment.id,idempotency_key="worker-redeploy",state="scheduled")
    calls=[]
    class Adapter:
        def redeploy_lab(self,received): calls.append(received.id);return {"created":True}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    execute_operation.run(str(job.id));job.refresh_from_db();deployment.refresh_from_db()
    assert calls==[deployment.id] and job.state=="succeeded" and deployment.observed_state=="deploying"
    assert deployment.resource_identities["topology"]["name"]=="topology" and deployment.resource_identities["last_redeploy_at"]

@pytest.mark.django_db
def test_guarded_runtime_removal_is_operator_only_capture_safe_concurrent_and_idempotent(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("remove-runtime-owner",password="long-enough-password")
    viewer=User.objects.create_user("remove-runtime-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="remove-runtime-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="remove-runtime-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="b"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.first());interface=LabInterface.objects.create(node=node,name="eth1")
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-remove-runtime",runtime_version="0.8.0",observed_state="running")
    DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-pod"})
    capture=CaptureSession.objects.create(deployment=deployment,interface=interface,owner=owner,status="capturing",expires_at=timezone.now()+timezone.timedelta(minutes=5))
    client=APIClient();client.force_authenticate(viewer)
    assert client.get(f"/api/v1/deployments/{deployment.id}/removal-preview/").status_code==403
    client.force_authenticate(owner);blocked=client.get(f"/api/v1/deployments/{deployment.id}/removal-preview/")
    assert blocked.status_code==200 and not blocked.data["can_remove"] and blocked.data["references"]["active_captures"]==1
    capture.status="complete";capture.save(update_fields=["status","updated_at"])
    stale=client.get(f"/api/v1/deployments/{deployment.id}/removal-preview/");deployment.error_details={"changed":True};deployment.save(update_fields=["error_details","updated_at"])
    rejected=client.post(f"/api/v1/deployments/{deployment.id}/remove/",{},format="json",HTTP_IDEMPOTENCY_KEY="stale-runtime-remove",
        HTTP_X_EXPECTED_UPDATED_AT=stale.data["updated_at"])
    assert rejected.status_code==409 and rejected.data["error"]["code"]=="deployment_changed"
    preview=client.get(f"/api/v1/deployments/{deployment.id}/removal-preview/");key="remove-runtime-once"
    first=client.post(f"/api/v1/deployments/{deployment.id}/remove/",{},format="json",HTTP_IDEMPOTENCY_KEY=key,
        HTTP_X_EXPECTED_UPDATED_AT=preview.data["updated_at"])
    replay=client.post(f"/api/v1/deployments/{deployment.id}/remove/",{},format="json",HTTP_IDEMPOTENCY_KEY=key,
        HTTP_X_EXPECTED_UPDATED_AT=preview.data["updated_at"])
    assert first.status_code==replay.status_code==202 and first.data["id"]==replay.data["id"]
    deployment.refresh_from_db();assert deployment.observed_state=="deleting" and deployment.requested_desired_state=="removed"
    generic=client.post(f"/api/v1/deployments/{deployment.id}/operations/",{"operation":"delete_runtime"},format="json",HTTP_IDEMPOTENCY_KEY="unsafe-remove")
    assert generic.status_code==422 and AuditEvent.objects.filter(action="deployment.removal_scheduled",target_id=deployment.id).count()==1

@pytest.mark.django_db
def test_runtime_removal_worker_preserves_history_revokes_consoles_and_records_cleanup(monkeypatch):
    owner=User.objects.create_user("remove-worker-owner",password="long-enough-password");project=Project.objects.create(owner=owner,name="remove-worker-project")
    lab=Lab.objects.create(project=project,name="remove-worker-lab");revision=LabRevision.objects.create(lab=lab,revision_number=2,topology_checksum="c"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.first())
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-remove-worker",runtime_version="0.8.0",observed_state="deleting")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",worker_placement="worker-1",runtime_resources={"pod":"r1-pod","pod_uid":"uid"})
    console=ConsoleSession.objects.create(device=device,user=owner,token_hash="a"*64,expires_at=timezone.now()+timezone.timedelta(minutes=10))
    job=OperationJob.objects.create(deployment=deployment,owner=owner,operation_type="delete_runtime",target_id=deployment.id,idempotency_key="worker-remove",state="scheduled")
    class Adapter:
        def delete_runtime(self,received):
            assert received.id==deployment.id
            return {"namespace":received.namespace,"namespaceDeleted":True,"configMapsDeleted":2}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter);execute_operation.run(str(job.id))
    deployment.refresh_from_db();device.refresh_from_db();console.refresh_from_db();job.refresh_from_db()
    assert deployment.observed_state=="removed" and deployment.removed_at and deployment.requested_desired_state=="removed"
    assert deployment.resource_identities["removal"]["namespaceDeleted"] is True and revision.deployments.filter(id=deployment.id).exists()
    assert device.observed_readiness=="removed" and device.runtime_resources["pod"] is None and not device.worker_placement
    assert console.revoked_at and job.state=="succeeded" and job.result_payload["namespaceDeleted"] is True
    assert AuditEvent.objects.filter(action="deployment.removed",target_id=deployment.id,metadata__namespace_deleted=True).count()==1
    client=APIClient();client.force_authenticate(owner)
    assert client.post(f"/api/v1/deployments/{deployment.id}/operations/",{"operation":"deploy_lab"},format="json",HTTP_IDEMPOTENCY_KEY="cannot-restart-removed").status_code==409
    assert client.post(f"/api/v1/deployments/{deployment.id}/refresh/",{},format="json").status_code==409
    assert reconcile_deployment.run(str(deployment.id))=="removed"

@pytest.mark.django_db
def test_device_logs_are_operator_only_bounded_async_no_store_and_audited(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("logs-owner",password="long-enough-password");viewer=User.objects.create_user("logs-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="logs-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="logs-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="b"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.get(template__name="FRR Router"))
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-logs",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-pod"})
    client=APIClient();client.force_authenticate(viewer)
    url=f"/api/v1/deployments/{deployment.id}/device-operations/";payload={"device_id":str(device.id),"operation":"get_device_logs","source":"launcher","tail":500}
    assert client.post(url,payload,format="json",HTTP_IDEMPOTENCY_KEY="viewer-logs").status_code==403
    client.force_authenticate(owner);response=client.post(url,payload,format="json",HTTP_IDEMPOTENCY_KEY="owner-logs")
    replay=client.post(url,payload,format="json",HTTP_IDEMPOTENCY_KEY="owner-logs")
    assert response.status_code==replay.status_code==202 and response.data["id"]==replay.data["id"]
    assert AuditEvent.objects.filter(action="device.logs_requested",target_id=device.id).count()==1
    invalid=client.post(url,{**payload,"tail":50000},format="json",HTTP_IDEMPOTENCY_KEY="invalid-logs")
    assert invalid.status_code==422 and invalid.data["error"]["code"]=="invalid_log_request"
    job=OperationJob.objects.get(id=response.data["id"])
    class Adapter:
        def get_device_logs(self,received_deployment,received_device,source,tail):
            assert received_deployment.id==deployment.id and received_device.id==device.id and source=="launcher" and tail==500
            return {"device_id":str(device.id),"device":"r1","source":source,"tail":tail,"output":"launcher ready\n","truncated":False}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter);execute_operation.run(str(job.id));job.refresh_from_db()
    assert job.state=="succeeded" and job.result_payload["output"]=="launcher ready\n"
    event_response=client.post(url,{**payload,"source":"events","tail":200},format="json",HTTP_IDEMPOTENCY_KEY="owner-events")
    assert event_response.status_code==202
    event_job=OperationJob.objects.get(id=event_response.data["id"])
    assert event_job.request_payload["source"]=="events"
    assert AuditEvent.objects.filter(action="device.logs_requested",target_id=device.id,metadata__source="events").count()==1
    runtime=client.get(f"/api/v1/deployments/{deployment.id}/runtime/")
    assert runtime["Cache-Control"]=="no-store" and runtime["X-Content-Type-Options"]=="nosniff"
    expected=node.template_version.resource_requirements
    assert runtime.data["devices"][0]["resource_profile"]=={"cpu":expected.get("cpu"),"memory":expected.get("memory"),
        "template_version":node.template_version.version}
    assert runtime.data["devices"][0]["startup_order"] is None

@pytest.mark.django_db
def test_console_authorization_is_session_bound_and_viewer_read_only():
    owner=User.objects.create_user("console-owner",password="long-enough-password")
    viewer=User.objects.create_user("console-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="console-project")
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="console-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="1"*64,immutable=True)
    template=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first()
    node=LabNode.objects.create(revision=revision,name="client",template_version=template)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-console-test",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"client-pod"})
    c=APIClient(); c.force_authenticate(viewer)
    response=c.post(f"/api/v1/deployments/{deployment.id}/consoles/",{"device_id":str(device.id)},format="json")
    assert response.status_code==201, response.data
    assert response.data["read_only"] is True
    assert "token" not in response.data and response.data["websocket"].endswith("/")
    session=ConsoleSession.objects.get(id=response.data["id"])
    assert session.token_hash and session.user==viewer
    assert AuditEvent.objects.filter(action="console.authorized",target_id=device.id).exists()

@pytest.mark.django_db
def test_device_lifecycle_is_idempotent_audited_and_operator_only(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_: None)
    owner=User.objects.create_user("lifecycle-owner",password="long-enough-password")
    viewer=User.objects.create_user("lifecycle-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="lifecycle-project")
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="lifecycle-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="2"*64,immutable=True)
    template=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first()
    node=LabNode.objects.create(revision=revision,name="client",template_version=template)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-lifecycle-test",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"client-pod"})
    payload={"device_id":str(device.id),"operation":"restart_device"}
    client=APIClient(); client.force_authenticate(viewer)
    assert client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="viewer-restart").status_code==403
    client.force_authenticate(owner)
    first=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="owner-restart")
    second=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="owner-restart")
    assert first.status_code==second.status_code==202
    assert first.data["id"]==second.data["id"]
    assert AuditEvent.objects.filter(action="device.restart",target_id=device.id).count()==1

@pytest.mark.django_db
def test_guarded_device_reset_preview_concurrency_capture_block_and_console_revocation(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    monkeypatch.setattr("studio.tasks.reconcile_deployment.apply_async",lambda *args,**kwargs:None)
    owner=User.objects.create_user("reset-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="reset-project");lab=Lab.objects.create(project=project,name="reset-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=3,topology_checksum="d"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first())
    interface=LabInterface.objects.create(node=node,name="eth1")
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-reset-test",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-old","pod_uid":"old-uid"})
    client=APIClient();client.force_authenticate(owner);preview_url=f"/api/v1/deployments/{deployment.id}/device-reset-preview/?device_id={device.id}"
    preview=client.get(preview_url)
    assert preview.status_code==200 and preview.data["can_reset"] is True and preview.data["revision"]==3
    payload={"device_id":str(device.id),"operation":"reset_device"};operation_url=f"/api/v1/deployments/{deployment.id}/device-operations/"
    assert client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="missing-reset-token").status_code==400
    stale=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="stale-reset",
        HTTP_X_EXPECTED_DEVICE_UPDATED_AT="2000-01-01T00:00:00+00:00")
    assert stale.status_code==409 and stale.data["error"]["code"]=="device_changed"
    capture=CaptureSession.objects.create(deployment=deployment,interface=interface,owner=owner,status="capturing",expires_at=timezone.now()+timezone.timedelta(minutes=5))
    blocked=client.get(preview_url)
    assert blocked.status_code==200 and blocked.data["can_reset"] is False and blocked.data["active_captures"]==1
    capture.status="complete";capture.save(update_fields=["status","updated_at"])
    preview=client.get(preview_url)
    ConsoleSession.objects.create(device=device,user=owner,token_hash="f"*64,expires_at=timezone.now()+timezone.timedelta(minutes=5))
    first=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="reset-r1",
        HTTP_X_EXPECTED_DEVICE_UPDATED_AT=preview.data["updated_at"])
    replay=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="reset-r1",
        HTTP_X_EXPECTED_DEVICE_UPDATED_AT=preview.data["updated_at"])
    assert first.status_code==replay.status_code==202 and first.data["id"]==replay.data["id"]
    assert AuditEvent.objects.filter(action="device.reset",target_id=device.id).count()==1
    class Adapter:
        def reset_device(self,*_): return {"device":"r1","operation":"reset","replaced_pod":"r1-old","readiness":"resetting",
            "baseline_revision":3,"saved_configuration_restored":False}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    execute_operation.run(str(first.data["id"]));device.refresh_from_db()
    assert device.observed_readiness=="resetting" and ConsoleSession.objects.filter(device=device,revoked_at__isnull=False).count()==1

@pytest.mark.django_db(transaction=True)
def test_bulk_device_lifecycle_is_authorized_preflighted_atomic_idempotent_and_audited(monkeypatch):
    scheduled=[];monkeypatch.setattr("studio.api.execute_operation.delay",lambda job_id:scheduled.append(job_id))
    owner=User.objects.create_user("bulk-owner",password="long-enough-password");viewer=User.objects.create_user("bulk-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="bulk-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="bulk-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="b"*64,immutable=True)
    template=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first()
    nodes=[LabNode.objects.create(revision=revision,name=f"r{index}",template_version=template) for index in (1,2)]
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-bulk-test",runtime_version="0.8.0",observed_state="running")
    devices=[DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":f"{node.name}-pod"}) for node in nodes]
    payload={"operation":"restart_device","device_ids":[str(device.id) for device in devices]}
    preview_url=f"/api/v1/deployments/{deployment.id}/device-bulk-preview/";operation_url=f"/api/v1/deployments/{deployment.id}/device-bulk-operations/"
    client=APIClient();client.force_authenticate(viewer);assert client.post(preview_url,payload,format="json").status_code==403
    client.force_authenticate(owner)
    preview=client.post(preview_url,payload,format="json")
    assert preview.status_code==200 and preview.data["can_schedule"] is True and len(preview.data["devices"])==2
    assert client.post(operation_url,payload,format="json").status_code==400
    first=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="restart-selected")
    replay=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="restart-selected")
    assert first.status_code==replay.status_code==202 and first.data["count"]==2
    assert {row["id"] for row in first.data["jobs"]}=={row["id"] for row in replay.data["jobs"]} and len(scheduled)==2
    assert AuditEvent.objects.filter(action="device.restart",metadata__bulk=True).count()==2
    assert AuditEvent.objects.filter(action="device.bulk_scheduled",target_id=deployment.id).count()==1
    OperationJob.objects.filter(id=first.data["jobs"][0]["id"]).update(state="succeeded")
    blocked=client.post(preview_url,payload,format="json")
    assert blocked.status_code==200 and blocked.data["can_schedule"] is False and sum(row["eligible"] for row in blocked.data["devices"])==1
    conflict=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="another-selected-operation")
    assert conflict.status_code==409 and conflict.data["error"]["code"]=="bulk_device_operation_blocked"
    duplicate={"operation":"restart_device","device_ids":[str(devices[0].id),str(devices[0].id)]}
    assert client.post(preview_url,duplicate,format="json").status_code==422

@pytest.mark.django_db(transaction=True)
def test_staged_device_start_is_ordered_bounded_concurrent_idempotent_and_audited(monkeypatch):
    queued=[];monkeypatch.setattr("studio.api.execute_staged_start.delay",lambda job_id:queued.append(job_id))
    owner=User.objects.create_user("staged-owner",password="long-enough-password");viewer=User.objects.create_user("staged-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="staged-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="staged-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="d"*64,immutable=True)
    catalog=DeviceTemplate.objects.create(name="Staged Linux")
    template=DeviceTemplateVersion.objects.create(template=catalog,version=1,containerlab_kind="linux",interface_rules={"prefix":"eth","start":1,"count":2})
    nodes=[LabNode.objects.create(revision=revision,name=name,template_version=template) for name in ("core","firewall","edge")]
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-staged-test",runtime_version="0.8.0",observed_state="running")
    devices=[DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="stopped",
        runtime_resources={"manual_desired_state":"stopped","pod":None}) for node in nodes]
    order=[devices[1],devices[0],devices[2]];base={"device_ids":[str(device.id) for device in order],"interval_seconds":4}
    preview_url=f"/api/v1/deployments/{deployment.id}/device-staged-start-preview/";operation_url=f"/api/v1/deployments/{deployment.id}/device-staged-start/"
    client=APIClient();client.force_authenticate(viewer);assert client.post(preview_url,base,format="json").status_code==403
    client.force_authenticate(owner)
    assert client.post(preview_url,{**base,"interval_seconds":61},format="json").status_code==422
    preview=client.post(preview_url,base,format="json")
    assert preview.status_code==200 and preview.data["can_schedule"] is True and preview.data["total_delay_seconds"]==8
    assert [row["name"] for row in preview.data["devices"]]==["firewall","core","edge"]
    expected={row["id"]:row["updated_at"] for row in preview.data["devices"]};payload={**base,"expected_devices":expected}
    assert client.post(operation_url,base,format="json",HTTP_IDEMPOTENCY_KEY="missing-preview").status_code==400
    first=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="ordered-start")
    replay=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="ordered-start")
    assert first.status_code==replay.status_code==202 and first.data["id"]==replay.data["id"] and queued==[first.data["id"]]
    assert AuditEvent.objects.filter(action="device.staged_start_scheduled",target_id=deployment.id).count()==1
    conflicting=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",
        {"device_id":str(order[0].id),"operation":"start_device"},format="json",HTTP_IDEMPOTENCY_KEY="conflicting-start")
    assert conflicting.status_code==409 and conflicting.data["error"]["code"]=="device_operation_in_progress"
    blocked_bulk=client.post(f"/api/v1/deployments/{deployment.id}/device-bulk-preview/",
        {"operation":"start_device","device_ids":[str(order[0].id),str(order[1].id)]},format="json")
    assert blocked_bulk.status_code==200 and blocked_bulk.data["can_schedule"] is False
    calls=[];next_steps=[]
    class Adapter:
        def start_device(self,_deployment,device):
            calls.append(device.lab_node.name);return {"device":device.lab_node.name,"operation":"start","desired_state":"running","readiness":"starting"}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    monkeypatch.setattr("studio.tasks.execute_staged_start.apply_async",lambda **kwargs:next_steps.append(kwargs["countdown"]))
    monkeypatch.setattr("studio.tasks.reconcile_deployment.apply_async",lambda **_:None)
    execute_staged_start.run(first.data["id"]);execute_staged_start.run(first.data["id"]);execute_staged_start.run(first.data["id"])
    job=OperationJob.objects.get(pk=first.data["id"])
    assert calls==["firewall","core","edge"] and next_steps==[4,4] and job.state=="succeeded" and job.progress==100
    assert [row["device"] for row in job.result_payload["devices"]]==calls
    assert set(DeviceInstance.objects.filter(id__in=[device.id for device in devices]).values_list("observed_readiness",flat=True))=={"starting"}
    assert not DeviceInstance.objects.filter(id__in=[device.id for device in devices],runtime_resources__has_key="manual_desired_state").exists()
    assert AuditEvent.objects.filter(action="device.staged_start_completed",target_id=deployment.id).count()==1

@pytest.mark.django_db(transaction=True)
def test_bulk_device_reset_is_guarded_concurrent_idempotent_and_revokes_consoles(monkeypatch):
    scheduled=[];monkeypatch.setattr("studio.api.execute_operation.delay",lambda job_id:scheduled.append(job_id))
    owner=User.objects.create_user("bulk-reset-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="bulk-reset-project");lab=Lab.objects.create(project=project,name="bulk-reset-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=7,topology_checksum="7"*64,immutable=True)
    catalog=DeviceTemplate.objects.create(name="Bulk reset Linux")
    template=DeviceTemplateVersion.objects.create(template=catalog,version=1,containerlab_kind="linux",interface_rules={"prefix":"eth","start":1,"count":2})
    nodes=[LabNode.objects.create(revision=revision,name=f"r{index}",template_version=template) for index in (1,2)]
    interfaces=[LabInterface.objects.create(node=node,name="eth1") for node in nodes]
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-bulk-reset",runtime_version="0.8.0",observed_state="running")
    devices=[DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":f"{node.name}-pod"}) for node in nodes]
    capture=CaptureSession.objects.create(deployment=deployment,interface=interfaces[0],owner=owner,status="capturing",expires_at=timezone.now()+timezone.timedelta(hours=1))
    ConsoleSession.objects.create(device=devices[1],user=owner,token_hash="9"*64,expires_at=timezone.now()+timezone.timedelta(minutes=5))
    preview_url=f"/api/v1/deployments/{deployment.id}/device-bulk-preview/";operation_url=f"/api/v1/deployments/{deployment.id}/device-bulk-operations/"
    base={"operation":"reset_device","device_ids":[str(device.id) for device in devices]};client=APIClient();client.force_authenticate(owner)
    blocked=client.post(preview_url,base,format="json")
    assert blocked.status_code==200 and blocked.data["can_schedule"] is False
    assert blocked.data["devices"][0]["active_captures"]==1 and blocked.data["devices"][1]["active_consoles"]==1
    capture.status="complete";capture.save(update_fields=["status","updated_at"])
    preview=client.post(preview_url,base,format="json");assert preview.data["can_schedule"] is True
    expected={row["id"]:row["updated_at"] for row in preview.data["devices"]}
    assert client.post(operation_url,base,format="json",HTTP_IDEMPOTENCY_KEY="missing-versions").status_code==400
    stale=client.post(operation_url,{**base,"expected_devices":{**expected,str(devices[0].id):"1970-01-01T00:00:00+00:00"}},format="json",HTTP_IDEMPOTENCY_KEY="stale-reset")
    assert stale.status_code==409 and stale.data["error"]["code"]=="device_changed" and OperationJob.objects.count()==0
    payload={**base,"expected_devices":expected}
    first=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="reset-selected")
    replay=client.post(operation_url,payload,format="json",HTTP_IDEMPOTENCY_KEY="reset-selected")
    assert first.status_code==replay.status_code==202 and first.data["count"]==2 and len(scheduled)==2
    assert AuditEvent.objects.filter(action="device.reset",metadata__bulk=True).count()==2
    class Adapter:
        def reset_device(self,received_deployment,device): return {"device":device.lab_node.name,"operation":"reset",
            "replaced_pod":device.runtime_resources["pod"],"readiness":"resetting","baseline_revision":7,"saved_configuration_restored":False}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter);monkeypatch.setattr("studio.tasks.reconcile_deployment.apply_async",lambda **_:None)
    for row in first.data["jobs"]:execute_operation.run(row["id"])
    assert ConsoleSession.objects.filter(device__in=devices,revoked_at__isnull=False).count()==1
    assert set(DeviceInstance.objects.filter(id__in=[device.id for device in devices]).values_list("observed_readiness",flat=True))=={"resetting"}

@pytest.mark.django_db
def test_device_worker_updates_node_without_failing_running_lab(monkeypatch):
    owner=User.objects.create_user("device-worker",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="device-worker-project")
    lab=Lab.objects.create(project=project,name="device-worker-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="3"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="node-a",template_version=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first())
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-worker-test",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"node-a-pod"})
    job=OperationJob.objects.create(deployment=deployment,owner=owner,operation_type="restart_device",target_id=device.id,idempotency_key="worker-restart",state="scheduled")
    class Adapter:
        def restart_device(self,received_deployment,received_device):
            assert received_deployment.id==deployment.id and received_device.id==device.id
            return {"device":"node-a","operation":"restart","replaced_pod":"node-a-pod","readiness":"restarting"}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    scheduled=[]
    monkeypatch.setattr("studio.tasks.reconcile_deployment.apply_async",lambda **kwargs: scheduled.append(kwargs["countdown"]))
    execute_operation.run(str(job.id))
    job.refresh_from_db(); device.refresh_from_db(); deployment.refresh_from_db()
    assert job.state=="succeeded" and job.result_payload["readiness"]=="restarting"
    assert device.observed_readiness=="restarting"
    assert deployment.observed_state=="running"
    assert scheduled==[3,10,30]

@pytest.mark.django_db
def test_device_suspend_resume_is_authorized_audited_and_persists_desired_state(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("suspend-owner",password="long-enough-password");viewer=User.objects.create_user("suspend-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="suspend-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="suspend-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="7"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.get(template__name="FRR Router"))
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-suspend",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-pod"})
    client=APIClient();payload={"device_id":str(device.id),"operation":"suspend_device"};client.force_authenticate(viewer)
    assert client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="viewer-suspend").status_code==403
    client.force_authenticate(owner);response=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="owner-suspend")
    assert response.status_code==202 and AuditEvent.objects.filter(action="device.suspend",target_id=device.id).exists()
    suspend_job=OperationJob.objects.get(id=response.data["id"])
    class Adapter:
        def suspend_device(self,*_): return {"device":"r1","operation":"suspend","desired_state":"suspended","readiness":"suspended","output":"r1"}
        def resume_device(self,*_): return {"device":"r1","operation":"resume","desired_state":"running","readiness":"ready","output":"r1"}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter);execute_operation.run(str(suspend_job.id));device.refresh_from_db()
    assert device.observed_readiness=="suspended" and device.runtime_resources["manual_desired_state"]=="suspended"
    resume=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",{"device_id":str(device.id),"operation":"resume_device"},format="json",HTTP_IDEMPOTENCY_KEY="owner-resume")
    assert resume.status_code==202 and AuditEvent.objects.filter(action="device.resume",target_id=device.id).exists()
    execute_operation.run(str(resume.data["id"]));device.refresh_from_db()
    assert device.observed_readiness=="ready" and "manual_desired_state" not in device.runtime_resources

@pytest.mark.django_db
def test_device_stop_start_is_authorized_audited_idempotent_and_persists_desired_state(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    monkeypatch.setattr("studio.tasks.reconcile_deployment.apply_async",lambda *args,**kwargs:None)
    owner=User.objects.create_user("stop-owner",password="long-enough-password");viewer=User.objects.create_user("stop-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="stop-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="stop-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="8"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r2",template_version=DeviceTemplateVersion.objects.get(template__name="FRR Router"))
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-stop",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r2-pod"})
    client=APIClient();payload={"device_id":str(device.id),"operation":"stop_device"};client.force_authenticate(viewer)
    assert client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="viewer-stop").status_code==403
    client.force_authenticate(owner);stop=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="owner-stop")
    replay=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="owner-stop")
    assert stop.status_code==replay.status_code==202 and stop.data["id"]==replay.data["id"]
    class Adapter:
        def stop_device(self,*_): return {"device":"r2","operation":"stop","desired_state":"stopped","readiness":"stopped","launcher_deleted":True}
        def start_device(self,*_): return {"device":"r2","operation":"start","desired_state":"running","readiness":"starting"}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter);execute_operation.run(str(stop.data["id"]));device.refresh_from_db()
    assert device.observed_readiness=="stopped" and device.runtime_resources["manual_desired_state"]=="stopped"
    assert AuditEvent.objects.filter(action="device.stop",target_id=device.id).count()==1
    blocked=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",{"device_id":str(device.id),"operation":"restart_device"},format="json",HTTP_IDEMPOTENCY_KEY="restart-stopped")
    assert blocked.status_code==409 and blocked.data["error"]["code"]=="device_stopped"
    start=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",{"device_id":str(device.id),"operation":"start_device"},format="json",HTTP_IDEMPOTENCY_KEY="owner-start")
    assert start.status_code==202 and AuditEvent.objects.filter(action="device.start",target_id=device.id).exists()
    execute_operation.run(str(start.data["id"]));device.refresh_from_db()
    assert device.observed_readiness=="starting" and "manual_desired_state" not in device.runtime_resources

@pytest.mark.django_db
def test_live_configuration_collection_is_operator_only_versioned_and_encrypted(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("collector-owner",password="long-enough-password")
    viewer=User.objects.create_user("collector-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="collector-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="collector-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="6"*64,immutable=True)
    template=DeviceTemplateVersion.objects.get(template__name="FRR Router")
    node=LabNode.objects.create(revision=revision,name="r1",template_version=template)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-collector",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-pod"})
    payload={"device_id":str(device.id),"operation":"collect_configuration"};client=APIClient();client.force_authenticate(viewer)
    assert client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="viewer-collect").status_code==403
    client.force_authenticate(owner)
    response=client.post(f"/api/v1/deployments/{deployment.id}/device-operations/",payload,format="json",HTTP_IDEMPOTENCY_KEY="owner-collect")
    assert response.status_code==202
    job=OperationJob.objects.get(id=response.data["id"])
    class Adapter:
        def collect_configuration(self,received_deployment,received_device):
            assert received_deployment.id==deployment.id and received_device.id==device.id
            content="hostname r1\nrouter bgp 65001\n"
            return {"device":"r1","content":content,"byte_size":len(content.encode())}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    execute_operation.run(str(job.id));job.refresh_from_db()
    collected=ConfigurationVersion.objects.get(id=job.result_payload["configuration_version_id"])
    assert decrypt_configuration(collected.encrypted_content)=="hostname r1\nrouter bgp 65001\n"
    assert b"router bgp" not in bytes(collected.encrypted_content) and "content" not in job.result_payload
    audit=AuditEvent.objects.get(action="configuration.collected",target_id=collected.id)
    assert audit.metadata["checksum"]==collected.checksum and "content" not in audit.metadata
    client.force_authenticate(viewer)
    assert client.get(job.result_payload["download"]).status_code==403
    client.force_authenticate(owner);download=client.get(job.result_payload["download"])
    assert download.status_code==200 and download.content==b"hostname r1\nrouter bgp 65001\n"
    assert download["Cache-Control"]=="no-store" and download["X-Content-Type-Options"]=="nosniff"
    assert AuditEvent.objects.filter(action="configuration.downloaded",target_id=collected.id).exists()
    history=client.get(f"/api/v1/deployments/{deployment.id}/configurations/")
    assert history.status_code==200 and history.data==[{"id":str(collected.id),"name":collected.name,"version":1,
        "checksum":collected.checksum,"byte_size":29,"created_at":collected.created_at,"device":"r1","restorable":True,
        "download":job.result_payload["download"]}]

@pytest.mark.django_db
def test_configuration_archive_exports_latest_per_device_with_manifest_and_redacted_audit():
    owner=User.objects.create_user("archive-owner",password="long-enough-password")
    viewer=User.objects.create_user("archive-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="archive-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="Archive Lab");revision=LabRevision.objects.create(lab=lab,revision_number=7,topology_checksum="7"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-archive",runtime_version="0.8.0",observed_state="running")
    contents={"r1-old":"hostname r1-old\n","r1":"hostname r1\nrouter bgp 65001\n","r2":"hostname r2\nrouter bgp 65002\n"}
    configurations={}
    for index,(key,content) in enumerate(contents.items(),1):
        device="r1" if key.startswith("r1") else "r2"
        configuration=ConfigurationVersion.objects.create(project=project,name=f"archive/{key}",version=index,
            encrypted_content=encrypt_configuration(content),checksum=hashlib.sha256(content.encode()).hexdigest(),created_by=owner)
        configurations[key]=configuration
        AuditEvent.objects.create(actor=owner,project=project,action="configuration.collected",target_type="ConfigurationVersion",
            target_id=configuration.id,correlation_id="archive-test",occurred_at=timezone.now()+timezone.timedelta(seconds=index),
            metadata={"deployment":str(deployment.id),"device":device,"version":index,"checksum":configuration.checksum,"byte_size":len(content.encode())})
    endpoint=f"/api/v1/deployments/{deployment.id}/configurations/export/";client=APIClient();client.force_authenticate(viewer)
    assert client.get(endpoint).status_code==403
    client.force_authenticate(owner);response=client.get(endpoint)
    assert response.status_code==200 and response["Content-Type"]=="application/zip"
    assert response["Content-Disposition"]=='attachment; filename="archive-lab-revision-7-configurations.zip"'
    assert response["Cache-Control"]=="no-store" and response["Pragma"]=="no-cache" and response["X-Content-Type-Options"]=="nosniff"
    assert int(response["Content-Length"])==len(response.content)
    assert response["X-Archive-SHA256"]==hashlib.sha256(response.content).hexdigest()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names=archive.namelist();manifest=json.loads(archive.read("manifest.json"))
        assert len(names)==3 and names[-1]=="manifest.json"
        assert [row["device"] for row in manifest["configurations"]]==["r1","r2"]
        assert manifest["deployment_id"]==str(deployment.id) and manifest["revision"]==7 and manifest["lab"]=="Archive Lab"
        exported={row["device"]:archive.read(row["filename"]).decode() for row in manifest["configurations"]}
        assert exported=={"r1":contents["r1"],"r2":contents["r2"]} and contents["r1-old"] not in exported.values()
        assert {row["checksum"] for row in manifest["configurations"]}=={configurations["r1"].checksum,configurations["r2"].checksum}
    audit=AuditEvent.objects.get(action="configuration.archive_exported",target_id=deployment.id)
    assert audit.metadata["device_count"]==2 and audit.metadata["archive_checksum"]==response["X-Archive-SHA256"]
    assert "hostname" not in json.dumps(audit.metadata) and "content" not in audit.metadata

@pytest.mark.django_db
def test_configuration_archive_requires_collected_configuration():
    owner=User.objects.create_user("empty-archive-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="empty-archive-project");lab=Lab.objects.create(project=project,name="empty-archive-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="8"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-empty-archive",runtime_version="0.8.0")
    client=APIClient();client.force_authenticate(owner);response=client.get(f"/api/v1/deployments/{deployment.id}/configurations/export/")
    assert response.status_code==409 and response.data["error"]["code"]=="configuration_export_empty"

@pytest.mark.django_db
def test_configuration_compare_and_restore_creates_concurrency_safe_draft_without_touching_runtime():
    owner=User.objects.create_user("restore-config-owner",password="long-enough-password")
    viewer=User.objects.create_user("restore-config-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="restore-config-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="restore-config-lab")
    template=DeviceTemplateVersion.objects.get(template__name="FRR Router")
    startup=ConfigurationVersion.objects.create(project=project,name="r1/startup",version=1,
        encrypted_content=encrypt_configuration("hostname r1\nrouter bgp 65000\n"),checksum="1"*64,created_by=owner)
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="2"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=template,startup_configuration=startup,position={"x":25,"y":50})
    for number in range(1,9): LabInterface.objects.create(node=node,name=f"eth{number}")
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-restore-config",runtime_version="0.8.0",observed_state="running")
    DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-pod"})
    contents=("hostname r1\nrouter bgp 65001\n","hostname r1\nrouter bgp 65100\n network 10.1.1.1/32\n")
    collected=[]
    for version,content in enumerate(contents,1):
        config=ConfigurationVersion.objects.create(project=project,name=f"{lab.name}/r1/collected",version=version,
            encrypted_content=encrypt_configuration(content),checksum=hashlib.sha256(content.encode()).hexdigest(),created_by=owner);collected.append(config)
        AuditEvent.objects.create(actor=owner,project=project,action="configuration.collected",target_type="ConfigurationVersion",target_id=config.id,
            correlation_id="test",metadata={"deployment":str(deployment.id),"device":"r1","version":version,"checksum":config.checksum,"byte_size":len(content)})
    endpoint=f"/api/v1/deployments/{deployment.id}"
    client=APIClient();client.force_authenticate(viewer)
    assert client.post(f"{endpoint}/configuration-compare/",{"left_id":str(collected[0].id),"right_id":str(collected[1].id)},format="json").status_code==403
    client.force_authenticate(owner)
    compared=client.post(f"{endpoint}/configuration-compare/",{"left_id":str(collected[0].id),"right_id":str(collected[1].id)},format="json")
    assert compared.status_code==200 and compared.data["changed"] is True and "65001" in compared.data["diff"] and "65100" in compared.data["diff"]
    assert compared["Cache-Control"]=="no-store" and AuditEvent.objects.filter(action="configuration.compared",target_id=deployment.id).exists()
    preview=client.get(f"{endpoint}/configurations/{collected[1].id}/restore-preview/")
    assert preview.status_code==200 and preview.data["requires_deploy"] is True and preview.data["expected_current_draft"] is None
    missing_key=client.post(f"{endpoint}/configurations/{collected[1].id}/restore/",{},format="json",HTTP_X_EXPECTED_DRAFT="none")
    assert missing_key.status_code==400
    restored=client.post(f"{endpoint}/configurations/{collected[1].id}/restore/",{},format="json",
        HTTP_X_EXPECTED_DRAFT="none",HTTP_IDEMPOTENCY_KEY="restore-collected-v2")
    assert restored.status_code==201 and restored.data["device"]=="r1"
    replay=client.post(f"{endpoint}/configurations/{collected[1].id}/restore/",{},format="json",
        HTTP_X_EXPECTED_DRAFT="none",HTTP_IDEMPOTENCY_KEY="restore-collected-v2")
    assert replay.status_code==200 and replay.data==restored.data
    lab.refresh_from_db();deployment.refresh_from_db();draft=lab.current_draft
    assert draft and not draft.immutable and draft.id!=revision.id
    restored_configuration=draft.nodes.get(name="r1").startup_configuration
    assert restored_configuration.id!=collected[1].id and restored_configuration.checksum==collected[1].checksum
    assert decrypt_configuration(restored_configuration.encrypted_content)==contents[1]
    assert deployment.revision_id==revision.id and deployment.observed_state=="running" and revision.immutable
    assert OperationJob.objects.filter(operation_type="restore_configuration",state="succeeded").count()==1
    assert AuditEvent.objects.filter(action="configuration.restore_draft_created",target_id=draft.id).exists()
    stale=client.post(f"{endpoint}/configurations/{collected[0].id}/restore/",{},format="json",
        HTTP_X_EXPECTED_DRAFT="none",HTTP_IDEMPOTENCY_KEY="stale-config-restore")
    assert stale.status_code==409 and stale.data["error"]["code"]=="draft_changed"

@pytest.mark.django_db
def test_reconciliation_drops_manual_lifecycle_after_launcher_replacement(monkeypatch):
    owner=User.objects.create_user("reconcile-stop",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="reconcile-stop-project")
    lab=Lab.objects.create(project=project,name="reconcile-stop-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="4"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="node-a",template_version=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first())
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-reconcile-stop",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="restarting",
        runtime_resources={"pod":"node-a-pod","pod_uid":"old-pod","manual_lifecycle":"restart_device"})
    class Adapter:
        def get_observed_state(self,_): return {"topologyReady":True}
        def observe_devices(self,_): return [{"name":"node-a","node_uid":"node-uid","readiness":"ready","pod":"node-a-pod",
            "pod_uid":"new-pod","worker":"worker","pod_phase":"Running","appliance_running":True,"appliance_paused":False}]
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    reconcile_deployment.run(str(deployment.id))
    device.refresh_from_db()
    assert device.observed_readiness=="ready" and "manual_lifecycle" not in device.runtime_resources

@pytest.mark.django_db
def test_reconciliation_keeps_topology_deploying_until_appliance_container_runs(monkeypatch):
    owner=User.objects.create_user("reconcile-appliance",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="appliance-project");lab=Lab.objects.create(project=project,name="appliance-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="5"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="node-a",template_version=DeviceTemplateVersion.objects.get(template__name="Linux Host"))
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-appliance",runtime_version="0.8.0",observed_state="deploying")
    class Adapter:
        def get_observed_state(self,_): return {"topologyReady":True}
        def observe_devices(self,_): return [{"name":"node-a","node_uid":"node-uid","readiness":"starting","pod":"node-a-pod",
            "pod_uid":"pod-uid","worker":"worker","pod_phase":"Running","appliance_running":False,"appliance_paused":False}]
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    assert reconcile_deployment.run(str(deployment.id))==LabDeployment.State.DEPLOYING
    deployment.refresh_from_db();device=DeviceInstance.objects.get(deployment=deployment,lab_node=node)
    assert deployment.observed_state=="deploying" and deployment.error_details=={"waiting_for_devices":["node-a"]}
    assert device.observed_readiness=="starting" and device.runtime_resources["appliance_running"] is False

@pytest.mark.django_db
def test_reconciliation_reapplies_intentional_suspension_after_launcher_replacement(monkeypatch):
    owner=User.objects.create_user("durable-suspend",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="durable-project");lab=Lab.objects.create(project=project,name="durable-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="8"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.get(template__name="FRR Router"))
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-durable",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="suspended",
        runtime_resources={"pod":"old-pod","pod_uid":"old-uid","manual_desired_state":"suspended"})
    pauses=[]
    class Adapter:
        def get_observed_state(self,_): return {"topologyReady":True}
        def observe_devices(self,_): return [{"name":"r1","node_uid":"node-uid","readiness":"ready","pod":"new-pod","pod_uid":"new-uid",
            "worker":"worker","pod_phase":"Running","appliance_running":True,"appliance_paused":False}]
        def linked_data_interfaces(self,node): return ["eth1"]
        def set_device_pause(self,deployment,node_name,pod,paused,interfaces): pauses.append((node_name,pod,paused,interfaces))
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    assert reconcile_deployment.run(str(deployment.id))==LabDeployment.State.RUNNING
    device.refresh_from_db();deployment.refresh_from_db()
    assert pauses==[("r1","new-pod",True,["eth1"])] and device.observed_readiness=="suspended"
    assert device.runtime_resources["appliance_paused"] is True and device.runtime_resources["manual_desired_state"]=="suspended"
    assert deployment.error_details=={}

@pytest.mark.django_db
def test_reconciliation_reapplies_durable_stop_if_launcher_reappears(monkeypatch):
    owner=User.objects.create_user("durable-stop",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="durable-stop-project");lab=Lab.objects.create(project=project,name="durable-stop-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r2",template_version=DeviceTemplateVersion.objects.get(template__name="FRR Router"))
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-durable-stop",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="stopped",
        runtime_resources={"pod":None,"pod_uid":None,"manual_desired_state":"stopped"})
    stopped=[]
    class Adapter:
        def get_observed_state(self,_): return {"topologyReady":True}
        def observe_devices(self,_): return [{"name":"r2","node_uid":"node-uid","readiness":"ready","pod":"unexpected-pod","pod_uid":"unexpected-uid",
            "worker":"worker","pod_phase":"Running","appliance_running":True,"appliance_paused":False,"deployment_disabled":False}]
        def ensure_device_stopped(self,deployment,current): stopped.append((deployment.id,current.id))
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    assert reconcile_deployment.run(str(deployment.id))==LabDeployment.State.RUNNING
    device.refresh_from_db();deployment.refresh_from_db()
    assert stopped==[(deployment.id,device.id)] and device.observed_readiness=="stopped"
    assert device.runtime_resources["manual_desired_state"]=="stopped" and device.runtime_resources["pod"] is None
    assert deployment.error_details=={}

@pytest.mark.django_db
def test_reconciliation_preserves_suspension_while_replacement_appliance_starts(monkeypatch):
    owner=User.objects.create_user("starting-suspend",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="starting-project");lab=Lab.objects.create(project=project,name="starting-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="9"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.get(template__name="FRR Router"))
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-starting",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="suspended",
        runtime_resources={"pod":"old-pod","pod_uid":"old-uid","manual_desired_state":"suspended"})
    class Adapter:
        def get_observed_state(self,_): return {"topologyReady":True}
        def observe_devices(self,_): return [{"name":"r1","node_uid":"node-uid","readiness":"starting","pod":"new-pod","pod_uid":"new-uid",
            "worker":"worker","pod_phase":"Running","appliance_running":False,"appliance_paused":False}]
        def linked_data_interfaces(self,node): return ["eth1"]
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    assert reconcile_deployment.run(str(deployment.id))==LabDeployment.State.DEPLOYING
    device.refresh_from_db()
    assert device.runtime_resources["manual_desired_state"]=="suspended" and device.runtime_resources["pod_uid"]=="new-uid"

@pytest.mark.django_db
def test_capture_api_is_bounded_idempotent_and_operator_only(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("capture-owner",password="long-enough-password")
    viewer=User.objects.create_user("capture-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="capture-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="capture-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="5"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first())
    interface=LabInterface.objects.create(node=node,name="eth1")
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-capture-test",runtime_version="0.8.0",observed_state="running")
    device=DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-pod"})
    payload={"device_id":str(device.id),"interface_id":str(interface.id),"duration":10,"packet_limit":500}
    client=APIClient();client.force_authenticate(viewer)
    assert client.post(f"/api/v1/deployments/{deployment.id}/captures/",payload,format="json",HTTP_IDEMPOTENCY_KEY="viewer-capture").status_code==403
    client.force_authenticate(owner)
    invalid={**payload,"duration":31}
    assert client.post(f"/api/v1/deployments/{deployment.id}/captures/",invalid,format="json",HTTP_IDEMPOTENCY_KEY="bad-capture").status_code==422
    first=client.post(f"/api/v1/deployments/{deployment.id}/captures/",payload,format="json",HTTP_IDEMPOTENCY_KEY="capture-one")
    second=client.post(f"/api/v1/deployments/{deployment.id}/captures/",payload,format="json",HTTP_IDEMPOTENCY_KEY="capture-one")
    assert first.status_code==second.status_code==202 and first.data["id"]==second.data["id"]
    assert CaptureSession.objects.filter(deployment=deployment,status="scheduled").count()==1
    assert AuditEvent.objects.filter(action="capture.started",project=project).count()==1

@pytest.mark.django_db
def test_capture_download_is_scoped_and_streams_pcap(settings,tmp_path):
    settings.MEDIA_ROOT=tmp_path
    owner=User.objects.create_user("capture-download",password="long-enough-password")
    stranger=User.objects.create_user("capture-stranger",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="capture-download-project");lab=Lab.objects.create(project=project,name="capture-download-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="6"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first())
    interface=LabInterface.objects.create(node=node,name="eth1");deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-download-test",runtime_version="0.8.0")
    path=tmp_path/"captures"/str(deployment.id)/"sample.pcap";path.parent.mkdir(parents=True);pcap=b"\xd4\xc3\xb2\xa1"+b"\x00"*20;path.write_bytes(pcap)
    capture=CaptureSession.objects.create(deployment=deployment,interface=interface,owner=owner,status="complete",expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_reference=str(path))
    client=APIClient();client.force_authenticate(owner)
    response=client.get(f"/api/v1/deployments/{deployment.id}/captures/{capture.id}/download/")
    assert response.status_code==200 and b"".join(response.streaming_content)==pcap
    client.force_authenticate(stranger)
    assert client.get(f"/api/v1/deployments/{deployment.id}/captures/{capture.id}/download/").status_code==404

@pytest.mark.django_db
def test_capture_analysis_is_bounded_scoped_and_no_store(settings,tmp_path):
    import struct
    settings.MEDIA_ROOT=tmp_path
    owner=User.objects.create_user("capture-analysis",password="long-enough-password")
    stranger=User.objects.create_user("capture-analysis-stranger",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="capture-analysis-project");lab=Lab.objects.create(project=project,name="capture-analysis-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first())
    interface=LabInterface.objects.create(node=node,name="eth1");deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-analysis-test",runtime_version="0.8.0")
    frame=bytes.fromhex("00112233445566778899aabb08004500001c00000000400100000a0000010a0000020800000000000000")
    pcap=b"\xd4\xc3\xb2\xa1"+struct.pack("<HHiiii",2,4,0,0,65535,1)+struct.pack("<IIII",1,0,len(frame),len(frame))+frame
    path=tmp_path/"captures"/str(deployment.id)/"analysis.pcap";path.parent.mkdir(parents=True);path.write_bytes(pcap)
    capture=CaptureSession.objects.create(deployment=deployment,interface=interface,owner=owner,status="complete",expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_reference=str(path))
    client=APIClient();client.force_authenticate(owner)
    response=client.get(f"/api/v1/deployments/{deployment.id}/captures/{capture.id}/analysis/")
    assert response.status_code==200 and response.data["protocols"][0]["protocol"]=="ICMP"
    assert response["Cache-Control"]=="no-store" and response["X-Content-Type-Options"]=="nosniff"
    capture.status="capturing";capture.save(update_fields=["status"])
    assert client.get(f"/api/v1/deployments/{deployment.id}/captures/{capture.id}/analysis/").status_code==409
    client.force_authenticate(stranger)
    assert client.get(f"/api/v1/deployments/{deployment.id}/captures/{capture.id}/analysis/").status_code==404

@pytest.mark.django_db
def test_capture_worker_persists_auditable_artifact_without_changing_lab_state(monkeypatch,settings,tmp_path):
    settings.MEDIA_ROOT=tmp_path
    owner=User.objects.create_user("capture-worker",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="capture-worker-project");lab=Lab.objects.create(project=project,name="capture-worker-lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="7"*64,immutable=True)
    node=LabNode.objects.create(revision=revision,name="r1",template_version=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first())
    interface=LabInterface.objects.create(node=node,name="eth1");deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-capture-worker",runtime_version="0.8.0",observed_state="running")
    DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready",runtime_resources={"pod":"r1-pod"})
    capture=CaptureSession.objects.create(deployment=deployment,interface=interface,owner=owner,status="scheduled",expires_at=timezone.now()+timezone.timedelta(hours=1))
    job=OperationJob.objects.create(deployment=deployment,owner=owner,operation_type="capture_packets",target_id=capture.id,idempotency_key="worker-capture",state="scheduled",request_payload={"duration":5,"packet_limit":50})
    pcap=b"\xd4\xc3\xb2\xa1"+b"\x00"*20
    class Adapter:
        def capture_packets(self,*args): return pcap
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    execute_operation.run(str(job.id))
    job.refresh_from_db();capture.refresh_from_db();deployment.refresh_from_db()
    artifact=LabArtifact.objects.get(deployment=deployment,artifact_type="packet_capture")
    assert job.state=="succeeded" and job.result_payload["byte_size"]==len(pcap)
    assert capture.status=="complete" and Path(capture.artifact_reference).read_bytes()==pcap
    assert artifact.checksum==job.result_payload["checksum"] and deployment.observed_state=="running"

@pytest.mark.django_db
def test_link_condition_api_is_bounded_idempotent_audited_and_operator_only(monkeypatch):
    monkeypatch.setattr("studio.api.execute_operation.delay",lambda *_:None)
    owner=User.objects.create_user("link-owner",password="long-enough-password");viewer=User.objects.create_user("link-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="link-project");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    lab=Lab.objects.create(project=project,name="link-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="8"*64,immutable=True)
    template=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first()
    a=LabNode.objects.create(revision=revision,name="a",template_version=template);b=LabNode.objects.create(revision=revision,name="b",template_version=template)
    ia=LabInterface.objects.create(node=a,name="eth1");ib=LabInterface.objects.create(node=b,name="eth1");link=LabLink.objects.create(revision=revision,endpoint_a=ia,endpoint_b=ib)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-link-test",runtime_version="0.8.0",observed_state="running")
    DeviceInstance.objects.create(deployment=deployment,lab_node=a,observed_readiness="ready",runtime_resources={"pod":"a-pod"})
    DeviceInstance.objects.create(deployment=deployment,lab_node=b,observed_readiness="ready",runtime_resources={"pod":"b-pod"})
    payload={"link_id":str(link.id),"latency_ms":100,"jitter_ms":10,"loss_percent":2.5,"corruption_percent":0.5,"rate_kbps":1000,"disabled":False}
    client=APIClient();client.force_authenticate(viewer)
    assert client.post(f"/api/v1/deployments/{deployment.id}/link-conditions/",payload,format="json",HTTP_IDEMPOTENCY_KEY="viewer-link").status_code==403
    client.force_authenticate(owner)
    assert client.post(f"/api/v1/deployments/{deployment.id}/link-conditions/",{**payload,"latency_ms":2001},format="json",HTTP_IDEMPOTENCY_KEY="bad-link").status_code==422
    assert client.post(f"/api/v1/deployments/{deployment.id}/link-conditions/",{**payload,"corruption_percent":100.1},format="json",HTTP_IDEMPOTENCY_KEY="bad-corruption").status_code==422
    first=client.post(f"/api/v1/deployments/{deployment.id}/link-conditions/",payload,format="json",HTTP_IDEMPOTENCY_KEY="link-one")
    second=client.post(f"/api/v1/deployments/{deployment.id}/link-conditions/",payload,format="json",HTTP_IDEMPOTENCY_KEY="link-one")
    assert first.status_code==second.status_code==202 and first.data["id"]==second.data["id"]
    assert AuditEvent.objects.filter(action="link.condition_changed",target_id=link.id).count()==1

@pytest.mark.django_db
def test_link_condition_worker_persists_runtime_state_without_failing_lab(monkeypatch):
    owner=User.objects.create_user("link-worker",password="long-enough-password");project=Project.objects.create(owner=owner,name="link-worker-project")
    lab=Lab.objects.create(project=project,name="link-worker-lab");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="9"*64,immutable=True)
    template=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first();a=LabNode.objects.create(revision=revision,name="a",template_version=template);b=LabNode.objects.create(revision=revision,name="b",template_version=template)
    ia=LabInterface.objects.create(node=a,name="eth1");ib=LabInterface.objects.create(node=b,name="eth1");link=LabLink.objects.create(revision=revision,endpoint_a=ia,endpoint_b=ib)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-link-worker",runtime_version="0.8.0",observed_state="running")
    condition={"active":True,"disabled":False,"latency_ms":50,"jitter_ms":0,"loss_percent":0.0,"rate_kbps":0}
    job=OperationJob.objects.create(deployment=deployment,owner=owner,operation_type="set_link_condition",target_id=link.id,idempotency_key="worker-link",state="scheduled",request_payload={"condition":condition})
    class Adapter:
        def set_link_condition(self,received_deployment,received_link,received_condition):
            assert received_deployment==deployment and received_link==link and received_condition==condition
            return {"link_id":str(link.id),"condition":condition,"endpoints":[]}
    monkeypatch.setattr("studio.tasks.ClabernetesAdapter",Adapter)
    execute_operation.run(str(job.id));job.refresh_from_db();deployment.refresh_from_db()
    assert job.state=="succeeded" and job.result_payload["condition"]==condition
    assert deployment.resource_identities["link_conditions"][str(link.id)]==condition and deployment.observed_state=="running"
