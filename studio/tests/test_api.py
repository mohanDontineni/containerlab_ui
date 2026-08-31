import uuid
import pytest
from rest_framework.test import APIClient
from studio.models import (DeviceTemplateVersion, ImageArtifact, Lab, LabInterface, LabNode, LabRevision,
    Project, ProjectMembership, PublishedImage, User)

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
