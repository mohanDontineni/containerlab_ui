import json
import uuid
from datetime import timedelta
import pytest
from django.db.models import F
from django.test import Client
from django.utils import timezone
from studio.configurations import decrypt_secret
from studio.models import AuditEvent, DeviceTemplate, DeviceTemplateVersion, ImageArtifact, ImageCredentialReference, Lab, LabDeployment, LabFolder, LabLink, LabNode, LabRevision, OperationJob, Project, ProjectMembership, PublishedImage, User

@pytest.mark.django_db
def test_dashboard_reports_expiring_worker_verified_platform_capabilities(client,monkeypatch):
    user=User.objects.create_user("health-viewer",password="long-enough-password");client.force_login(user)
    values={"studio:platform:metrics":{"available":True,"checked_at":"2026-09-01T10:00:00Z"},
        "studio:platform:runtime":{"available":True,"version":"0.8.0","checked_at":"2026-09-01T10:00:00Z"},
        "studio:platform:registry":{"available":True,"version":"3.1.1","mode":"Persistent filesystem · internal ClusterIP","checked_at":"2026-09-01T10:00:00Z"},
        "studio:platform:network_isolation":{"available":True,"verified":5,"expected":5,"checked_at":"2026-09-01T10:00:00Z"}}
    monkeypatch.setattr("studio.views.cache.get",lambda key:values.get(key))
    response=client.get("/");html=response.content.decode()
    assert response.status_code==200 and "Metrics API · worker verified" in html and "Runtime v0.8.0 · reconciled" in html
    assert "OCI registry" in html and "Persistent filesystem · internal ClusterIP" in html
    assert "Network isolation" in html and "5/5 ingress policies verified" in html
    assert html.count('class="healthy">Ready</b>')==6

@pytest.mark.django_db
def test_dashboard_exposes_complete_state_quota_and_actionable_failure_evidence(client,monkeypatch):
    user=User.objects.create_user("dashboard-operator",password="long-enough-password")
    project=Project.objects.create(owner=user,name="Capacity lab",quotas={"max_labs":7,"max_running_deployments":3,
        "max_members":9,"max_image_bytes":2*1024**3,"max_nodes_per_lab":12})
    lab=Lab.objects.create(project=project,name="Failure topology")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64,immutable=True)
    running=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-dashboard-running",
        runtime_version="0.8.0",observed_state="running")
    LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-dashboard-stopped",
        runtime_version="0.8.0",observed_state="stopped")
    LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-dashboard-degraded",
        runtime_version="0.8.0",observed_state="degraded")
    job=OperationJob.objects.create(owner=user,deployment=running,operation_type="ping",target_id=uuid.uuid4(),
        idempotency_key="dashboard-failure",state="failed",progress=100,
        error_details={"type":"CapabilityError","message":"The device launcher pod is not ready"})
    monkeypatch.setattr("studio.views.cache.get",lambda key:{"available":False,"checked_at":"2026-09-01T10:00:00Z"})
    client.force_login(user);response=client.get("/");html=response.content.decode()
    assert response.status_code==200
    assert "1 running" in html and "1 stopped or removed" in html and "1 degraded" in html
    assert "Labs <b>1/7</b>" in html and "Active runtimes <b>2/3</b>" in html and "Largest draft <b>0/12 nodes</b>" in html
    assert "CapabilityError" in html and "The device launcher pod is not ready" in html
    assert "Ping" in html and "Capturepackets" not in html
    assert f'href="/deployments/{running.id}/"' in html and "Live CPU and memory usage: <b>Unavailable</b>" in html
    assert str(job.id) not in html

@pytest.mark.django_db
def test_operations_center_filters_owner_jobs_and_redacts_failure_evidence(client):
    owner=User.objects.create_user("operations-owner",password="long-enough-password")
    outsider=User.objects.create_user("operations-outsider",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Operations evidence")
    lab=Lab.objects.create(project=project,name="Recovery lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="b"*64,immutable=True)
    deployment=LabDeployment.objects.create(revision=revision,cluster_identity="test",namespace="clab-operation-center",
        runtime_version="0.8.0",observed_state="running")
    failed=OperationJob.objects.create(owner=owner,deployment=deployment,operation_type="capture_packets",target_id=uuid.uuid4(),
        idempotency_key="acceptance-correlation",state="failed",attempts=1,progress=100,
        request_payload={"authorization":"must-never-render"},
        error_details={"type":"CapabilityError","message":"capture failed token=private-value password:also-private"})
    OperationJob.objects.create(owner=outsider,operation_type="publish_image",target_id=uuid.uuid4(),idempotency_key="outsider-job",
        state="failed",error_details={"message":"outsider-only-evidence"})
    client.force_login(owner)
    response=client.get("/operations/",{"state":"failed","type":"capture_packets","q":"Recovery"});html=response.content.decode()
    assert response.status_code==200 and "Capture Packets" in html and "Recovery lab" in html
    assert "capture failed token=[redacted] password:[redacted]" in html
    assert "private-value" not in html and "also-private" not in html and "must-never-render" not in html and "outsider-only-evidence" not in html
    assert f'href="/deployments/{deployment.id}/"' in html and str(failed.id) in html
    assert "Why there is no generic cancel or retry" in html and "1–1 of 1" not in html

@pytest.mark.django_db
def test_topology_edit_lease_blocks_second_editor_and_expires(client):
    owner=User.objects.create_user("lease-owner",password="long-enough-password")
    editor=User.objects.create_user("lease-editor",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Lease project")
    ProjectMembership.objects.create(project=project,user=editor,role=ProjectMembership.Role.EDITOR)
    lab=Lab.objects.create(project=project,name="Protected topology")
    client.force_login(owner)
    acquired=client.post(f"/api/v1/labs/{lab.id}/topology/edit-lease/")
    token=acquired.json()["token"]
    rejected=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":0,"nodes":[],"links":[]}),content_type="application/json")
    assert acquired.status_code==200 and rejected.status_code==409
    saved=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":0,"nodes":[],"links":[]}),content_type="application/json",HTTP_X_EDIT_LEASE=token)
    assert saved.status_code==200
    client.force_login(editor)
    conflict=client.post(f"/api/v1/labs/{lab.id}/topology/edit-lease/")
    assert conflict.status_code==409 and conflict.json()["error"]["owner"]=="lease-owner"
    lab.refresh_from_db();lab.edit_lock_expires_at=timezone.now()-timedelta(seconds=1);lab.save(update_fields=["edit_lock_expires_at"])
    takeover=client.post(f"/api/v1/labs/{lab.id}/topology/edit-lease/")
    assert takeover.status_code==200 and takeover.json()["token"]!=token

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
    assert 'href="/admin/"' not in client.get("/settings/").content.decode()

@pytest.mark.django_db
def test_registry_credential_gui_never_renders_secret_and_registry_registration_pins_reference(client):
    owner=User.objects.create_user("registry-gui-owner",password="long-enough-password");project=Project.objects.create(owner=owner,name="Registry GUI")
    client.force_login(owner);secret="GUI-Registry-Secret-2026"
    created=client.post("/images/credentials/new/",{"project":str(project.id),"name":"Private lab registry","registry_host":"registry.example:5000",
        "credential_type":"token","username":"","secret":secret,"is_active":"on"})
    assert created.status_code==302 and created.url=="/images/credentials/",created.context["form"].errors
    credential=ImageCredentialReference.objects.get(project=project);assert decrypt_secret(credential.encrypted_secret)==secret
    page=client.get("/images/credentials/");html=page.content.decode()
    assert page.status_code==200 and "Private lab registry" in html and credential.secret_fingerprint in html and secret not in html
    digest="registry.example:5000/frr@sha256:"+"b"*64
    registered=client.post("/images/register/",{"project":str(project.id),"name":"Private FRR","registry_digest":digest,"architecture":"amd64",
        "vendor":"FRRouting","version":"10.4.1","credential_reference":str(credential.id)})
    assert registered.status_code==302 and registered.url=="/images/"
    artifact=ImageArtifact.objects.get(project=project);assert artifact.credential_reference==credential and artifact.registry_reference==digest
    publication=PublishedImage.objects.get(artifact=artifact)
    assert publication.compatibility_result=={"digest_pinned":True,"runtime_pull":"not_yet_verified","credential_configured":True}
    assert AuditEvent.objects.filter(action="image.registry_registered",target_id=artifact.id,metadata__credential_reference=str(credential.id)).exists()
    mismatch=client.post("/images/register/",{"project":str(project.id),"name":"Wrong host","registry_digest":"other.example/frr@sha256:"+"c"*64,
        "architecture":"amd64","vendor":"","version":"","credential_reference":str(credential.id)})
    assert mismatch.status_code==200 and "This reference resolves to other.example" in mismatch.content.decode()

@pytest.mark.django_db
def test_native_platform_user_administration_is_staff_only_and_audited(client):
    operator=User.objects.create_user("ordinary-operator",password="Original-Password-2026!")
    client.force_login(operator)
    assert client.get("/users/").status_code==403
    admin=User.objects.create_user("platform-admin",password="Original-Password-2026!",is_staff=True)
    client.force_login(admin)
    response=client.post("/users/",{"username":"new-operator","first_name":"Network","last_name":"Engineer",
        "email":"ENGINEER@EXAMPLE.TEST","timezone":"America/Chicago","password1":"Secure-Onboarding-2026!","password2":"Secure-Onboarding-2026!"},follow=True)
    assert response.status_code==200 and b"created and ready for project access" in response.content
    created=User.objects.get(username="new-operator")
    assert created.is_active and created.must_change_password and created.check_password("Secure-Onboarding-2026!") and created.email=="engineer@example.test"
    event=AuditEvent.objects.get(action="account.created",target_id=created.id)
    assert event.actor==admin and event.metadata=={"username":"new-operator"}

@pytest.mark.django_db
def test_guarded_account_status_preserves_roles_revokes_consoles_and_blocks_owners(client):
    admin=User.objects.create_user("status-admin",password="Original-Password-2026!",is_staff=True)
    target=User.objects.create_user("status-target",password="Original-Password-2026!")
    owner=User.objects.create_user("active-owner",password="Original-Password-2026!")
    project=Project.objects.create(owner=owner,name="Owned project")
    ProjectMembership.objects.create(project=project,user=target,role=ProjectMembership.Role.EDITOR)
    client.force_login(admin)
    own_preview=client.get(f"/users/{owner.id}/status/").json()
    assert own_preview["can_change"] is False and "Transfer or retire" in own_preview["blockers"][0]
    assert client.post(f"/users/{owner.id}/status/",{"expected_action":"deactivate"}).status_code==409
    preview=client.get(f"/users/{target.id}/status/")
    assert preview.status_code==200 and preview.json()["can_change"] is True and preview.json()["references"]["memberships"]==1
    changed=client.post(f"/users/{target.id}/status/",{"expected_action":"deactivate"})
    assert changed.status_code==200 and changed.json()["is_active"] is False
    target.refresh_from_db();assert not target.is_active and ProjectMembership.objects.filter(project=project,user=target).exists()
    assert AuditEvent.objects.get(action="account.deactivated",target_id=target.id).metadata["revoked_consoles"]==0
    activated=client.post(f"/users/{target.id}/status/",{"expected_action":"activate"})
    assert activated.status_code==200 and activated.json()["is_active"] is True
    assert AuditEvent.objects.filter(action="account.activated",target_id=target.id).exists()

@pytest.mark.django_db
def test_platform_admin_cannot_deactivate_self(client):
    admin=User.objects.create_user("self-admin",password="Original-Password-2026!",is_staff=True)
    client.force_login(admin);preview=client.get(f"/users/{admin.id}/status/").json()
    assert preview["can_change"] is False and "current account" in preview["blockers"][0]

@pytest.mark.django_db
def test_native_password_reset_revokes_sessions_and_forces_personal_rotation(client):
    admin=User.objects.create_user("reset-admin",password="Original-Password-2026!",is_staff=True)
    target=User.objects.create_user("reset-target",password="Original-Password-2026!")
    operator=Client();operator.force_login(target);assert operator.get("/").status_code==200
    client.force_login(admin);preview=client.get(f"/users/{target.id}/password-reset/")
    assert preview.status_code==200 and preview.json()["can_reset"] is True
    weak=client.post(f"/users/{target.id}/password-reset/",{"password1":"short","password2":"short"})
    assert weak.status_code==422
    reset=client.post(f"/users/{target.id}/password-reset/",{"password1":"Temporary-Zebra-2026!","password2":"Temporary-Zebra-2026!"})
    assert reset.status_code==200 and reset.json()["revoked_sessions"]==1
    target.refresh_from_db();assert target.must_change_password and target.check_password("Temporary-Zebra-2026!")
    assert operator.get("/").status_code==302 and "/accounts/login/" in operator.get("/").url
    assert operator.login(username=target.username,password="Temporary-Zebra-2026!")
    forced=operator.get("/");assert forced.status_code==302 and forced.url=="/settings/"
    rotated=operator.post("/settings/",{"action":"password","password-old_password":"Temporary-Zebra-2026!",
        "password-new_password1":"Personal-Router-2026!","password-new_password2":"Personal-Router-2026!"},follow=True)
    target.refresh_from_db();assert rotated.status_code==200 and not target.must_change_password and target.check_password("Personal-Router-2026!")
    assert operator.get("/").status_code==200
    reset_event=AuditEvent.objects.get(action="account.password_reset",target_id=target.id)
    changed_event=AuditEvent.objects.get(action="account.password_changed",target_id=target.id)
    assert reset_event.metadata["revoked_sessions"]==1 and changed_event.metadata["forced_rotation"] is True

@pytest.mark.django_db
def test_project_create_is_native_and_scoped(client):
    user = User.objects.create_user("project-owner", password="long-enough-password")
    client.force_login(user)
    response = client.post("/projects/new/", {"name": "Network Engineering", "description": "Labs", "tags": "[]"})
    assert response.status_code == 302
    assert Project.objects.filter(owner=user, name="Network Engineering").exists()

@pytest.mark.django_db
def test_lab_folders_are_hierarchical_project_scoped_and_guarded(client):
    owner=User.objects.create_user("folder-owner",password="long-enough-password")
    outsider=User.objects.create_user("folder-outsider",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Training")
    other=Project.objects.create(owner=outsider,name="Private")
    client.force_login(owner)
    root_response=client.post("/labs/folders/new/",{"project":project.id,"name":"Routing","parent":""})
    root=LabFolder.objects.get(project=project,name="Routing")
    nested_response=client.post("/labs/folders/new/",{"project":project.id,"name":"BGP","parent":root.id})
    nested=LabFolder.objects.get(project=project,name="BGP")
    assert root_response.status_code==302 and root_response.url==f"/labs/?folder={root.id}"
    assert nested_response.status_code==302 and nested_response.url==f"/labs/?folder={nested.id}" and nested.path=="Routing / BGP"
    assert str(project)=="Training" and str(nested)=="Training · Routing / BGP"
    assert not client.post("/labs/folders/new/",{"project":other.id,"name":"Leaked","parent":""}).wsgi_request.user.is_anonymous
    assert not LabFolder.objects.filter(project=other,name="Leaked").exists()
    lab=Lab.objects.create(project=project,folder=nested,name="Peering")
    root_page=client.get("/labs/").content.decode()
    assert "Routing" in root_page and "BGP" not in root_page and "Peering" not in root_page
    assert f'/labs/?folder={root.id}' in root_page
    routing_page=client.get(f"/labs/?folder={root.id}").content.decode()
    assert "All projects" in routing_page and "Routing" in routing_page and "BGP" in routing_page and "Peering" not in routing_page
    bgp_page=client.get(f"/labs/?folder={nested.id}").content.decode()
    assert "Routing" in bgp_page and "BGP" in bgp_page and "Peering" in bgp_page
    assert f'/labs/new/?project={project.id}&amp;folder={nested.id}' in bgp_page
    assert f'/labs/folders/new/?parent={nested.id}' in bgp_page
    folder_form=client.get(f"/labs/folders/new/?parent={nested.id}").context["form"]
    lab_form=client.get(f"/labs/new/?project={project.id}&folder={nested.id}").context["form"]
    assert folder_form.initial["project"]==project.id and folder_form.initial["parent"]==nested.id
    assert lab_form.initial["project"]==project.id and lab_form.initial["folder"]==nested.id
    assert client.get("/labs/?folder=not-a-uuid").status_code==404
    assert client.get(f"/labs/?folder={nested.id}").status_code==200
    blocked=client.post(f"/labs/folders/{nested.id}/delete/",follow=True)
    nested.refresh_from_db();assert blocked.status_code==200 and nested.deleted_at is None and b"cannot be deleted" in blocked.content
    lab.folder=None;lab.save(update_fields=["folder"])
    removed=client.post(f"/labs/folders/{nested.id}/delete/",follow=True)
    nested.refresh_from_db();assert removed.status_code==200 and nested.deleted_at is not None
    assert AuditEvent.objects.filter(action="lab_folder.created",target_id=root.id).exists()
    assert AuditEvent.objects.filter(action="lab_folder.deleted",target_id=nested.id).exists()

@pytest.mark.django_db
def test_lab_folder_rejects_cross_project_parent_and_descendant_cycle(client):
    owner=User.objects.create_user("folder-rules",password="long-enough-password")
    first=Project.objects.create(owner=owner,name="First");second=Project.objects.create(owner=owner,name="Second")
    root=LabFolder.objects.create(project=first,name="Root");child=LabFolder.objects.create(project=first,parent=root,name="Child")
    foreign=LabFolder.objects.create(project=second,name="Foreign")
    client.force_login(owner)
    cross=client.post(f"/labs/folders/{child.id}/edit/",{"project":first.id,"name":"Child","parent":foreign.id})
    cycle=client.post(f"/labs/folders/{root.id}/edit/",{"project":first.id,"name":"Root","parent":child.id})
    project_move=client.post(f"/labs/folders/{root.id}/edit/",{"project":second.id,"name":"Root","parent":""})
    root.refresh_from_db();child.refresh_from_db()
    assert cross.status_code==200 and b"selected project" in cross.content and child.parent_id==root.id
    assert cycle.status_code==200 and b"descendants" in cycle.content and root.parent_id is None
    assert project_move.status_code==302 and project_move.url==f"/labs/?folder={root.id}" and root.project_id==first.id

@pytest.mark.django_db
def test_lab_folder_navigation_is_read_only_for_viewer_and_cross_project_safe(client):
    owner=User.objects.create_user("folder-nav-owner",password="long-enough-password")
    viewer=User.objects.create_user("folder-nav-viewer",password="long-enough-password")
    outsider=User.objects.create_user("folder-nav-outsider",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Shared");private=Project.objects.create(owner=outsider,name="Private")
    ProjectMembership.objects.create(project=project,user=viewer,role=ProjectMembership.Role.VIEWER)
    folder=LabFolder.objects.create(project=project,name="Visible");hidden=LabFolder.objects.create(project=private,name="Hidden")
    Lab.objects.create(project=project,folder=folder,name="Readable")
    client.force_login(viewer)
    page=client.get(f"/labs/?folder={folder.id}")
    html=page.content.decode();heading=html.split('<section class="page-heading">',1)[1].split("</section>",1)[0]
    assert page.status_code==200 and "Readable" in html and "New subfolder" not in heading and f"folder={folder.id}" not in heading
    assert client.get(f"/labs/?folder={hidden.id}").status_code==404
    assert client.get(f"/labs/folders/new/?parent={hidden.id}").status_code==404
    assert client.get(f"/labs/folders/new/?parent={folder.id}").status_code==403
    assert client.get(f"/labs/new/?project={project.id}&folder={folder.id}").status_code==403

@pytest.mark.django_db
def test_lab_create_and_edit_assign_active_folder_only(client):
    owner=User.objects.create_user("folder-labs",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Organized")
    folder=LabFolder.objects.create(project=project,name="Security")
    client.force_login(owner)
    created=client.post("/labs/new/",{"project":project.id,"folder":folder.id,"name":"Firewall","description":"Policy","tags":"[]"})
    lab=Lab.objects.get(name="Firewall");assert created.status_code==302 and created.url==f"/labs/?folder={folder.id}" and lab.folder==folder
    edited=client.post(f"/labs/{lab.id}/edit/",{"folder":"","name":"Firewall","description":"Policy","tags":"[]"})
    lab.refresh_from_db();assert edited.status_code==302 and edited.url=="/labs/" and lab.folder_id is None

@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/projects/", "/labs/", "/deployments/", "/images/", "/images/upload/", "/device-templates/", "/operations/", "/audit/", "/settings/"])
def test_native_portal_pages_render(client, path):
    user = User.objects.create_user(f"user-{path.strip('/').replace('/', '-') or 'home'}", password="long-enough-password")
    client.force_login(user)
    assert client.get(path).status_code == 200

@pytest.mark.django_db
def test_native_audit_trail_is_project_scoped_filterable_and_content_safe(client):
    owner=User.objects.create_user("audit-owner",password="long-enough-password")
    viewer=User.objects.create_user("audit-viewer",password="long-enough-password")
    outsider=User.objects.create_user("audit-outsider",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Shared audit")
    private=Project.objects.create(owner=outsider,name="Private audit")
    ProjectMembership.objects.create(project=project,user=viewer,role=ProjectMembership.Role.VIEWER)
    visible=AuditEvent.objects.create(actor=owner,project=project,action="deployment.started",target_type="LabDeployment",target_id=project.id,
        correlation_id="trace-visible",metadata={"safe":"<script>alert(1)</script>"})
    AuditEvent.objects.create(actor=outsider,project=private,action="deployment.secret",target_type="LabDeployment",target_id=private.id,
        correlation_id="trace-private",metadata={"secret":"hidden"})
    AuditEvent.objects.create(actor=viewer,action="account.profile_updated",target_type="User",target_id=viewer.id,correlation_id="trace-own",metadata={})
    AuditEvent.objects.create(actor=outsider,action="account.profile_updated",target_type="User",target_id=outsider.id,correlation_id="trace-other",metadata={})
    client.force_login(viewer)
    page=client.get("/audit/?days=all")
    html=page.content.decode()
    assert page.status_code==200 and str(visible.id) not in html and "deployment.started" in html and "trace-own" in html
    assert "deployment.secret" not in html and "trace-other" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    filtered=client.get(f"/audit/?days=all&project={project.id}&action=started&actor=audit-owner&target_type=LabDeployment&correlation=visible")
    assert filtered.status_code==200 and b"1 matching event" in filtered.content and b"deployment.started" in filtered.content
    assert client.get(f"/audit/?days=all&project={private.id}").status_code==404
    assert client.get(f"/audit/?days=all&project={project.id}&format=csv").status_code==403

@pytest.mark.django_db
def test_project_admin_audit_export_is_bounded_safe_audited_and_no_store(client):
    owner=User.objects.create_user("audit-exporter",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="=SUM(1,1)")
    AuditEvent.objects.create(actor=owner,project=project,action="lab.created",target_type="Lab",target_id=project.id,
        correlation_id="export-trace",metadata={"count":1})
    client.force_login(owner)
    response=client.get(f"/audit/?days=all&project={project.id}&format=csv")
    body=response.content.decode()
    assert response.status_code==200 and response["Cache-Control"]=="no-store" and response["X-Content-Type-Options"]=="nosniff"
    assert response["X-Export-Truncated"]=="false" and response["Content-Disposition"].endswith('.csv"')
    assert "lab.created" in body and "'=SUM(1,1)" in body and "export-trace" in body
    event=AuditEvent.objects.get(action="audit.exported",project=project)
    assert event.actor==owner and event.metadata=={"rows":1,"truncated":False,"days":"all","filtered":True}

@pytest.mark.django_db
def test_platform_admin_can_page_and_export_global_audit_events(client):
    admin=User.objects.create_user("audit-platform",password="long-enough-password",is_staff=True)
    for index in range(51):
        AuditEvent.objects.create(actor=admin,action=f"platform.test_{index:02d}",target_type="Platform",target_id=admin.id,
            correlation_id=f"page-{index}",metadata={"index":index})
    client.force_login(admin)
    first=client.get("/audit/?days=all");second=client.get("/audit/?days=all&page=2")
    assert first.status_code==200 and b"51 matching events" in first.content and b"Older" in first.content
    assert second.status_code==200 and b"51 matching events" in second.content and b"Newer" in second.content
    exported=client.get("/audit/?days=all&format=csv")
    assert exported.status_code==200 and exported.content.count(b"\n")>=52

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
    assert event.metadata=={"forced_rotation":False} and event.project_id is None

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
        {"id": a, "name": "router-1", "templateVersionId": str(template.id), "position": {"x": 10, "y": 20},"properties":{"startupOrder":20}},
        {"id": b, "name": "router-2", "templateVersionId": str(template.id), "position": {"x": 210, "y": 20},"properties":{"startupOrder":10}},
    ], "links": [{"id": link, "sourceNode": a, "sourceInterface": "eth1", "targetNode": b, "targetInterface": "eth1"}]}
    response = client.put(f"/api/v1/labs/{lab.id}/topology/", json.dumps(payload), content_type="application/json")
    assert response.status_code == 200
    lab.refresh_from_db()
    assert LabNode.objects.filter(revision=lab.current_draft).count() == 2
    assert LabLink.objects.filter(revision=lab.current_draft).count() == 1
    document = client.get(f"/api/v1/labs/{lab.id}/topology/").json()
    assert document["links"][0]["sourceInterface"] == "eth1"
    assert {node["name"]:node["properties"]["startupOrder"] for node in document["nodes"]}=={"router-1":20,"router-2":10}
    audit=AuditEvent.objects.get(action="lab.topology_saved",target_id=lab.current_draft_id)
    assert audit.metadata["node_count"]==2 and audit.metadata["link_count"]==1 and audit.metadata["startup_plan_nodes"]==2 and "startupConfiguration" not in audit.metadata
    payload["editVersion"]=response.json()["editVersion"];payload["nodes"][0]["properties"]["startupOrder"]=251
    rejected=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps(payload),content_type="application/json")
    assert rejected.status_code==422 and "startup order" in rejected.json()["error"]

@pytest.mark.django_db
def test_template_image_compatibility_is_visible_and_enforced_in_topology_save(client):
    owner=User.objects.create_user("compatibility-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Compatibility project");lab=Lab.objects.create(project=project,name="Compatibility lab")
    template=DeviceTemplate.objects.create(name="Compatibility router")
    version=DeviceTemplateVersion.objects.create(template=template,version=1,containerlab_kind="linux",
        interface_rules={"prefix":"eth","start":1,"count":2,"management":"eth0"},
        image_requirements={"digest_required_for_deploy":True,"architectures":["amd64"],"categories":["router"]})
    template.active_version=version;template.save(update_fields=["active_version"])
    def publication(name,architecture,category,checksum):
        artifact=ImageArtifact.objects.create(project=project,owner=owner,source_type="registry",original_filename=name,
            detected_format="oci-registry",byte_size=0,checksum=checksum*64,category=category,architecture=architecture,
            storage_reference=f"registry/{name}",validation_status="validated")
        return PublishedImage.objects.create(artifact=artifact,registry_digest=f"registry/{name}@sha256:{checksum*64}",
            repository=f"registry/{name}",architecture=architecture,lifecycle_status="ready")
    compatible=publication("router-amd64","amd64","router","a");incompatible=publication("host-arm64","arm64","host","b")
    client.force_login(owner)
    image_data=client.get(f"/api/v1/labs/{lab.id}/topology/images/").json()["images"]
    decisions={row["id"]:row["templateCompatibility"][str(version.id)] for row in image_data}
    assert decisions[str(compatible.id)]["status"]=="compatible" and decisions[str(compatible.id)]["selectable"] is True
    assert decisions[str(incompatible.id)]["status"]=="incompatible" and "Architecture arm64" in decisions[str(incompatible.id)]["reasons"][0]
    page=client.get(f"/device-templates/{template.id}/");html=page.content.decode()
    assert page.status_code==200 and "Accessible image compatibility" in html and "router-amd64" in html and "host-arm64" in html
    node={"id":str(uuid.uuid4()),"name":"r1","templateVersionId":str(version.id),"publishedImageId":str(incompatible.id),"position":{"x":10,"y":20}}
    rejected=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":0,"nodes":[node],"links":[]}),content_type="application/json")
    assert rejected.status_code==422 and "selected image is incompatible" in rejected.json()["error"] and lab.current_draft_id is None
    node["publishedImageId"]=str(compatible.id)
    saved=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":0,"nodes":[node],"links":[]}),content_type="application/json")
    assert saved.status_code==200 and LabNode.objects.get(revision=Lab.objects.get(pk=lab.id).current_draft).published_image==compatible

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
def test_legacy_text_annotations_upgrade_deterministically_and_remain_editable(client):
    owner=User.objects.create_user("legacy-annotation-owner",password="long-enough-password")
    project=Project.objects.create(owner=owner,name="Legacy annotation project");lab=Lab.objects.create(project=project,name="Legacy lab")
    revision=LabRevision.objects.create(lab=lab,revision_number=1,topology_checksum="a"*64,
        annotations=[{"type":"text","x":125,"y":80,"text":"Pre-canvas BGP note"}])
    lab.current_draft=revision;lab.save(update_fields=["current_draft"]);client.force_login(owner)
    first=client.get(f"/api/v1/labs/{lab.id}/topology/").json();second=client.get(f"/api/v1/labs/{lab.id}/topology/").json()
    annotation=first["annotations"][0]
    assert annotation==second["annotations"][0]
    assert annotation["type"]=="note" and annotation["text"]=="Pre-canvas BGP note"
    assert uuid.UUID(annotation["id"])
    saved=client.put(f"/api/v1/labs/{lab.id}/topology/",json.dumps({"editVersion":first["editVersion"],"nodes":[],"links":[],"annotations":first["annotations"]}),content_type="application/json")
    assert saved.status_code==200
    revision.refresh_from_db();assert revision.annotations[0]==annotation and revision.edit_version==2

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
    assert "GUARDED DEVICE RESET" in response.content.decode() and "device-reset-preview" in response.content.decode()
    assert "SELECTED DEVICE OPERATION" in response.content.decode() and "device-bulk-preview" in response.content.decode()
    assert "⌫ Reset" in response.content.decode() and "expected_devices" in response.content.decode()
    assert "LIVE NETWORK STATE" in response.content.decode() and "inspect_device" in response.content.decode()
    assert "Traffic and rate" in response.content.decode() and "interfaceCounterSamples" in response.content.decode()
    assert "OPERATIONAL TOPOLOGY" in response.content.decode() and "runtime-map-viewport" in response.content.decode()
    assert "open-device-console" in response.content.decode() and "data-runtime-map-action" in response.content.decode()
    client.force_login(stranger)
    assert client.get(f"/deployments/{deployment.id}/").status_code == 404
