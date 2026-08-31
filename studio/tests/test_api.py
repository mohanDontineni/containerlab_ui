import pytest
from django.conf import settings
from rest_framework.test import APIClient
from studio.models import (AuditEvent, ConsoleSession, DeviceInstance, DeviceTemplateVersion, ImageArtifact, Lab, LabDeployment, LabNode, LabRevision,
    Project, ProjectMembership, PublishedImage, User)
from studio.tasks import execute_operation

def test_web_process_uses_configured_celery_broker():
    assert execute_operation.app.conf.broker_url == settings.CELERY_BROKER_URL

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
def test_owner_can_publish_and_schedule_deployable_lab(django_capture_on_commit_callbacks):
    owner=User.objects.create_user("deployer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="runtime")
    lab=Lab.objects.create(project=project,name="connectivity")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64)
    lab.current_draft=revision; lab.save()
    template=DeviceTemplateVersion.objects.first()
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
