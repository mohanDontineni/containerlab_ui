import json
import uuid
import pytest
from django.db.models import F
from studio.models import DeviceTemplateVersion, ImageArtifact, Lab, LabDeployment, LabLink, LabNode, LabRevision, Project, ProjectMembership, PublishedImage, User

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
@pytest.mark.parametrize("path", ["/projects/", "/labs/", "/deployments/", "/images/", "/images/upload/", "/device-templates/", "/operations/", "/settings/"])
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
def test_project_access_ui_is_available_only_to_administrators(client):
    owner=User.objects.create_user("ui-owner",password="long-enough-password")
    viewer=User.objects.create_user("ui-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Shared UI")
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    client.force_login(owner); owner_page=client.get(f"/projects/{project.id}/")
    assert owner_page.status_code==200 and b"Add member" in owner_page.content and b"member-role" in owner_page.content
    client.force_login(viewer); viewer_page=client.get(f"/projects/{project.id}/")
    assert viewer_page.status_code==200 and b"Add member" not in viewer_page.content and b"member-role" not in viewer_page.content

@pytest.mark.django_db
def test_project_node_quota_is_visible_and_enforced_in_topology_workspace(client):
    owner=User.objects.create_user("node-quota-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Node quota",quotas={"max_nodes_per_lab":1})
    lab=Lab.objects.create(project=project,name="Bounded lab")
    template=DeviceTemplateVersion.objects.filter(template__active_version_id=F("id")).first()
    client.force_login(owner)
    page=client.get(f"/projects/{project.id}/").content
    assert b"Project quotas" in page and b"Nodes / lab" in page
    nodes=[{"id":str(uuid.uuid4()),"name":f"node-{index}","templateVersionId":str(template.id),"position":{"x":index*100,"y":0}} for index in range(2)]
    response=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":0,"nodes":nodes,"links":[]}),content_type="application/json")
    assert response.status_code==409 and response.json()["error"]["code"]=="project_quota_exceeded"

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

@pytest.mark.django_db
def test_image_library_exposes_professional_resumable_upload(client):
    owner=User.objects.create_user("upload-portal",password="long-enough-password");Project.objects.create(owner=owner,name="Images")
    client.force_login(owner)
    assert "Upload archive" in client.get("/images/").content.decode()
    html=client.get("/images/upload/").content.decode()
    assert "RESUMABLE UPLOAD" in html and "4 MiB chunks" in html and "/api/v1/uploads/" in html

@pytest.mark.django_db
def test_deployment_detail_is_native_and_scoped(client):
    owner = User.objects.create_user("deployment-owner", password="long-enough-password")
    stranger = User.objects.create_user("deployment-stranger", password="long-enough-password")
    project = Project.objects.create(owner=owner, name="Runtime project")
    lab = Lab.objects.create(project=project, name="Runtime lab")
    revision = LabRevision.objects.create(lab=lab, revision_number=1, topology_checksum="e" * 64, immutable=True)
    deployment = LabDeployment.objects.create(revision=revision, cluster_identity="test", namespace="clab-detail-test", runtime_version="0.8.0")
    client.force_login(owner)
    response = client.get(f"/deployments/{deployment.id}/")
    assert response.status_code == 200
    assert "Bounded ping" in response.content.decode()
    client.force_login(stranger)
    assert client.get(f"/deployments/{deployment.id}/").status_code == 404
