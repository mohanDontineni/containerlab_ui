import pytest
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from rest_framework.test import APIClient
from studio.configurations import decrypt_configuration
from studio.models import (AuditEvent, CaptureSession, ConsoleSession, DeviceInstance, DeviceTemplateVersion, ImageArtifact, Lab, LabDeployment,
    ConfigurationVersion, LabArtifact, LabInterface, LabLink, LabNode, LabRevision, OperationJob, Project, ProjectMembership, PublishedImage, UploadSession, User)
from studio.tasks import execute_operation, reconcile_active_deployments, reconcile_deployment

def test_web_process_uses_configured_celery_broker():
    assert execute_operation.app.conf.broker_url == settings.CELERY_BROKER_URL
    assert settings.CELERY_BEAT_SCHEDULE["reconcile-active-deployments"]["schedule"]==30.0

@pytest.mark.django_db
def test_periodic_reconciler_queues_only_active_deployments(monkeypatch):
    owner=User.objects.create_user("scheduler-owner",password="long-enough-password");project=Project.objects.create(owner=owner,name="scheduler")
    queued=[];monkeypatch.setattr("studio.tasks.reconcile_deployment.delay",lambda deployment_id:queued.append(deployment_id))
    for index,state in enumerate(("pending","deploying","running","degraded","failed","stopped"),1):
        lab=Lab.objects.create(project=project,name=f"lab-{index}");revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum=str(index)*64,immutable=True)
        LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace=f"clab-scheduler-{index}",runtime_version="0.8.0",observed_state=state)
    assert reconcile_active_deployments.run()==4 and len(queued)==4

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
def test_upload_creation_is_project_scoped_and_checksum_optional():
    owner=User.objects.create_user("upload-owner",password="long-enough-password");viewer=User.objects.create_user("upload-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="uploads");ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    payload={"project":str(project.id),"original_filename":"router.tar","expected_size":1024,"expected_checksum":""}
    client=APIClient();client.force_authenticate(viewer)
    assert client.post("/api/v1/uploads/",payload,format="json").status_code==403
    client.force_authenticate(owner);response=client.post("/api/v1/uploads/",payload,format="json")
    assert response.status_code==201 and response.data["expected_checksum"]==""
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
    with django_capture_on_commit_callbacks(execute=False):
        response=c.post(f"/api/v1/labs/{lab.id}/deploy/",{},format="json",HTTP_IDEMPOTENCY_KEY="test-deploy")
    assert response.status_code==202, response.data
    lab.refresh_from_db(); assert lab.current_draft is None
    assert response.data["deployment"]["namespace"].startswith("clab-")

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
    template=DeviceTemplateVersion.objects.filter(containerlab_kind="linux").first()
    artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename="alpine",detected_format="oci-registry",
        byte_size=0,checksum="9"*64,architecture="amd64",storage_reference="docker.io/alpine",validation_status="validated")
    image=PublishedImage.objects.create(artifact=artifact,registry_digest="docker.io/alpine@sha256:"+"9"*64,repository="docker.io/alpine",architecture="amd64")
    LabNode.objects.create(revision=revision,name="client",template_version=template,published_image=image)
    client=APIClient();client.force_authenticate(owner)
    response=client.post(f"/api/v1/labs/{lab.id}/deploy/",{},format="json",HTTP_IDEMPOTENCY_KEY="quota-blocked-deploy")
    assert response.status_code==409 and response.data["error"]["code"]=="project_quota_exceeded"
    lab.refresh_from_db();revision.refresh_from_db()
    assert lab.current_draft_id==revision.id and revision.immutable is False

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
def test_runtime_device_contract_exposes_logical_node_identity():
    owner=User.objects.create_user("device-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="devices")
    lab=Lab.objects.create(project=project,name="lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="f"*64,immutable=True)
    template=DeviceTemplateVersion.objects.first()
    node=LabNode.objects.create(revision=revision,name="r1",template_version=template)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-device-test",runtime_version="0.8.0")
    DeviceInstance.objects.create(deployment=deployment,lab_node=node,observed_readiness="ready")
    c=APIClient(); c.force_authenticate(owner)
    device=c.get(f"/api/v1/deployments/{deployment.id}/runtime/").data["devices"][0]
    assert str(device["node_id"])==str(node.id)

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
        "checksum":collected.checksum,"byte_size":29,"created_at":collected.created_at,
        "download":job.result_payload["download"]}]

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
    path=tmp_path/"captures"/"sample.pcap";path.parent.mkdir();pcap=b"\xd4\xc3\xb2\xa1"+b"\x00"*20;path.write_bytes(pcap)
    capture=CaptureSession.objects.create(deployment=deployment,interface=interface,owner=owner,status="complete",expires_at=timezone.now()+timezone.timedelta(hours=1),artifact_reference=str(path))
    client=APIClient();client.force_authenticate(owner)
    response=client.get(f"/api/v1/deployments/{deployment.id}/captures/{capture.id}/download/")
    assert response.status_code==200 and b"".join(response.streaming_content)==pcap
    client.force_authenticate(stranger)
    assert client.get(f"/api/v1/deployments/{deployment.id}/captures/{capture.id}/download/").status_code==404

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
    payload={"link_id":str(link.id),"latency_ms":100,"jitter_ms":10,"loss_percent":2.5,"rate_kbps":1000,"disabled":False}
    client=APIClient();client.force_authenticate(viewer)
    assert client.post(f"/api/v1/deployments/{deployment.id}/link-conditions/",payload,format="json",HTTP_IDEMPOTENCY_KEY="viewer-link").status_code==403
    client.force_authenticate(owner)
    assert client.post(f"/api/v1/deployments/{deployment.id}/link-conditions/",{**payload,"latency_ms":2001},format="json",HTTP_IDEMPOTENCY_KEY="bad-link").status_code==422
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
