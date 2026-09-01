import json
import uuid
import pytest
from django.db.models import F
from django.test import Client
from studio.models import AuditEvent, DeviceTemplateVersion, ImageArtifact, Lab, LabDeployment, LabLink, LabNode, LabRevision, Project, ProjectMembership, PublishedImage, User

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
def test_native_account_profile_update_validates_timezone_and_is_audited(client):
    user=User.objects.create_user("profile-user",password="Original-Password-2026!",email="old@example.test")
    client.force_login(user)
    invalid=client.post("/settings/",{"action":"profile","profile-first_name":"Mohan","profile-last_name":"D",
        "profile-email":"MOHAN@EXAMPLE.TEST","profile-timezone":"Invalid/Zone"})
    assert invalid.status_code==200 and b"Select a valid choice" in invalid.content
    user.refresh_from_db();assert user.timezone=="UTC" and user.email=="old@example.test"
    response=client.post("/settings/",{"action":"profile","profile-first_name":"Mohan","profile-last_name":"Dontineni",
        "profile-email":"MOHAN@EXAMPLE.TEST","profile-timezone":"America/Chicago"},follow=True)
    assert response.status_code==200 and b"Profile settings saved" in response.content
    user.refresh_from_db();assert (user.first_name,user.last_name,user.email,user.timezone)==("Mohan","Dontineni","mohan@example.test","America/Chicago")
    event=AuditEvent.objects.get(action="account.profile_updated",target_id=user.id)
    assert set(event.metadata["changed_fields"])=={"first_name","last_name","email","timezone"} and event.project_id is None

@pytest.mark.django_db
def test_native_password_change_requires_current_password_applies_policy_keeps_session_and_audits(client):
    old="Original-Password-2026!";new="Zebra-Routing-2026!"
    user=User.objects.create_user("security-user",password=old)
    client.force_login(user)
    wrong=client.post("/settings/",{"action":"password","password-old_password":"wrong-password",
        "password-new_password1":new,"password-new_password2":new})
    assert wrong.status_code==200 and b"old password was entered incorrectly" in wrong.content
    weak=client.post("/settings/",{"action":"password","password-old_password":old,
        "password-new_password1":"short","password-new_password2":"short"})
    assert weak.status_code==200 and b"at least 12 characters" in weak.content
    changed=client.post("/settings/",{"action":"password","password-old_password":old,
        "password-new_password1":new,"password-new_password2":new},follow=True)
    assert changed.status_code==200 and b"current session remains active" in changed.content and changed.wsgi_request.user.is_authenticated
    user.refresh_from_db();assert user.check_password(new) and not user.check_password(old)
    event=AuditEvent.objects.get(action="account.password_changed",target_id=user.id)
    assert event.metadata=={} and event.project_id is None

@pytest.mark.django_db
def test_account_mutations_require_csrf_and_legacy_password_page_redirects_to_native_settings():
    user=User.objects.create_user("csrf-account",password="Original-Password-2026!")
    protected=Client(enforce_csrf_checks=True);protected.force_login(user)
    assert protected.post("/settings/",{"action":"profile","profile-timezone":"UTC"}).status_code==403
    regular=Client();regular.force_login(user)
    response=regular.get("/accounts/password_change/")
    assert response.status_code==302 and response.url=="/settings/"

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
    audit=AuditEvent.objects.get(action="lab.topology_saved",target_id=lab.current_draft_id)
    assert audit.metadata["node_count"]==2 and audit.metadata["link_count"]==1 and "startupConfiguration" not in audit.metadata

@pytest.mark.django_db
def test_topology_annotations_are_bounded_persisted_and_checksum_protected(client):
    owner=User.objects.create_user("annotation-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Annotation project");lab=Lab.objects.create(project=project,name="Annotated lab")
    client.force_login(owner);note_id=str(uuid.uuid4());region_id=str(uuid.uuid4())
    annotations=[{"id":note_id,"type":"note","x":120,"y":85,"width":240,"height":90,"text":"Change window 22:00 UTC",
        "color":"amber","fontSize":14,"zIndex":10},{"id":region_id,"type":"region","x":50.1234,"y":40,"width":620,"height":310,
        "text":"Core routing","color":"blue","fontSize":16,"zIndex":-10}]
    first=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":0,"nodes":[],"links":[],"annotations":annotations}),content_type="application/json")
    assert first.status_code==200;first_checksum=first.json()["checksum"]
    document=client.get(f"/api/v1/labs/{lab.id}/topology/").json()
    assert document["annotations"][0]["text"]=="Change window 22:00 UTC" and document["annotations"][1]["x"]==50.12
    annotations[0]["text"]="Approved change window"
    second=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":1,"nodes":[],"links":[],"annotations":annotations}),content_type="application/json")
    assert second.status_code==200 and second.json()["checksum"]!=first_checksum
    invalid={**annotations[0],"id":region_id}
    rejected=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":2,"nodes":[],"links":[],"annotations":[annotations[1],invalid]}),content_type="application/json")
    assert rejected.status_code==422 and "unique" in rejected.json()["error"]
    oversized={**annotations[0],"id":str(uuid.uuid4()),"text":"x"*2001}
    rejected=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":2,"nodes":[],"links":[],"annotations":[oversized]}),content_type="application/json")
    assert rejected.status_code==422 and "2000" in rejected.json()["error"]

@pytest.mark.django_db
def test_firewall_catalog_exposes_policy_and_interface_requirements(client):
    user=User.objects.create_user("firewall-catalog",password="long-enough-password")
    client.force_login(user)
    templates=client.get("/api/v1/topology/templates/").json()["templates"]
    firewall=next(row for row in templates if row["name"]=="Linux Firewall")
    assert firewall["icon"]=="firewall"
    assert firewall["startupConfigSupported"] is True
    assert firewall["startupConfigRequired"] is True
    assert firewall["configurationLanguage"]=="shell"
    assert firewall["requiredInterfaces"]==2

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
def test_native_project_edit_and_retirement_controls_are_admin_only(client):
    owner=User.objects.create_user("project-ui-owner",password="long-enough-password")
    editor=User.objects.create_user("project-ui-editor",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Project before");ProjectMembership.objects.create(project=project,user=editor,role="editor")
    client.force_login(editor);page=client.get(f"/projects/{project.id}/")
    assert page.status_code==200 and b"Edit project" not in page.content and b"Retire" not in page.content
    assert client.post(f"/projects/{project.id}/edit/",{"name":"Denied","description":"","tags":"[]"}).status_code==403
    client.force_login(owner);page=client.get(f"/projects/{project.id}/")
    assert b"Edit project" in page.content and b"Retire" in page.content and b"project-retire-dialog" in page.content
    response=client.post(f"/projects/{project.id}/edit/",{"name":"Project after","description":"Purpose","tags":'["training"]'},follow=True)
    assert response.status_code==200 and b"updated" in response.content
    project.refresh_from_db();assert project.name=="Project after" and AuditEvent.objects.filter(action="project.metadata_updated",target_id=project.id).exists()

@pytest.mark.django_db
def test_native_lab_edit_is_operator_only_and_audited(client):
    owner=User.objects.create_user("portal-lab-owner",password="long-enough-password")
    viewer=User.objects.create_user("portal-lab-viewer",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Portal lab project");lab=Lab.objects.create(project=project,name="Before")
    ProjectMembership.objects.create(project=project,user=viewer,role="viewer")
    client.force_login(viewer);assert client.post(f"/labs/{lab.id}/edit/",{"name":"Denied","description":"","tags":"[]"}).status_code==403
    client.force_login(owner)
    page=client.get("/labs/").content.decode();assert f'/labs/{lab.id}/edit/' in page and f'data-delete-lab="{lab.id}"' in page
    response=client.post(f"/labs/{lab.id}/edit/",{"name":"After","description":"Purpose","tags":'["training"]'},follow=True)
    assert response.status_code==200 and b"updated" in response.content
    lab.refresh_from_db();assert lab.name=="After" and AuditEvent.objects.filter(action="lab.metadata_updated",target_id=lab.id).exists()

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
    assert "Remove…" in response.content.decode() and "runtime-removal-dialog" in response.content.decode()
    client.force_login(stranger)
    assert client.get(f"/deployments/{deployment.id}/").status_code == 404
