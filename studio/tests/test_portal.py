import json
import uuid
import pytest
from django.db.models import F
from django.urls import reverse
from studio.models import DeviceTemplateVersion, ImageArtifact, Lab, LabLink, LabNode, Project, ProjectMembership, PublishedImage, User

@pytest.mark.django_db
def test_product_navigation_never_links_to_model_admin(client):
    user = User.objects.create_user("portal-user", password="long-enough-password")
    client.force_login(user)
    response = client.get("/")
    assert response.status_code == 200
    html = response.content.decode()
    assert 'href="/projects/"' in html
    assert 'href="/labs/"' in html
    assert '/admin/studio/' not in html

@pytest.mark.django_db
def test_project_create_is_native_and_scoped(client):
    user = User.objects.create_user("project-owner", password="long-enough-password")
    client.force_login(user)
    response = client.post("/projects/new/", {"name": "Network Engineering", "description": "Labs", "tags": "[]"})
    assert response.status_code == 302
    assert Project.objects.filter(owner=user, name="Network Engineering").exists()

@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/projects/", "/labs/", "/deployments/", "/images/", "/device-templates/", "/operations/", "/settings/"])
def test_native_portal_pages_render(client, path):
    user = User.objects.create_user(f"user-{path.strip('/').replace('/', '-') or 'home'}", password="long-enough-password")
    client.force_login(user)
    assert client.get(path).status_code == 200

@pytest.mark.django_db
def test_topology_workspace_persists_device_interfaces_and_links(client):
    owner = User.objects.create_user("topology-owner", password="long-enough-password")
    project = Project.objects.create(owner=owner, name="Topology")
    lab = Lab.objects.create(project=project, name="Core")
    template = DeviceTemplateVersion.objects.filter(template__active_version_id=F("id")).first()
    client.force_login(owner)
    assert client.get("/api/v1/topology/templates/").json()["templates"]
    a, b, link = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    payload = {"editVersion": 0, "nodes": [
        {"id": a, "name": "router-1", "templateVersionId": str(template.id), "position": {"x": 10, "y": 20}},
        {"id": b, "name": "router-2", "templateVersionId": str(template.id), "position": {"x": 210, "y": 20}},
    ], "links": [{"id": link, "sourceNode": a, "sourceInterface": "eth1", "targetNode": b, "targetInterface": "eth1"}]}
    response = client.put(f"/api/v1/labs/{lab.id}/topology/", json.dumps(payload), content_type="application/json")
    assert response.status_code == 200
    lab.refresh_from_db()
    assert LabNode.objects.filter(revision=lab.current_draft).count() == 2
    assert LabLink.objects.filter(revision=lab.current_draft).count() == 1
    document = client.get(f"/api/v1/labs/{lab.id}/topology/").json()
    assert document["links"][0]["sourceInterface"] == "eth1"

@pytest.mark.django_db
def test_viewer_cannot_change_topology(client):
    owner = User.objects.create_user("owner", password="long-enough-password")
    viewer = User.objects.create_user("viewer", password="long-enough-password")
    project = Project.objects.create(owner=owner, name="Read only")
    lab = Lab.objects.create(project=project, name="Shared")
    ProjectMembership.objects.create(project=project, user=viewer, role=ProjectMembership.Role.VIEWER)
    client.force_login(viewer)
    response = client.put(f"/api/v1/labs/{lab.id}/topology/", json.dumps({"nodes": [], "links": []}), content_type="application/json")
    assert response.status_code == 403

@pytest.mark.django_db
def test_register_digest_pinned_registry_image(client):
    owner = User.objects.create_user("image-owner", password="long-enough-password")
    project = Project.objects.create(owner=owner, name="Images")
    client.force_login(owner)
    digest = "registry.example/library/alpine@sha256:" + "a" * 64
    response = client.post("/images/register/", {"project": project.id, "name": "Alpine", "registry_digest": digest,
        "architecture": "amd64", "vendor": "Alpine", "version": "3.22"})
    assert response.status_code == 302
    artifact = ImageArtifact.objects.get(project=project)
    assert artifact.source_type == ImageArtifact.Source.REGISTRY
    assert artifact.upload_session is None
    assert PublishedImage.objects.get(artifact=artifact).registry_digest == digest
