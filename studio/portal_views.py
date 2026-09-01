from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.core.exceptions import PermissionDenied
import hashlib
import json
import uuid
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from .forms import LabEditForm, LabFolderForm, LabForm, PlatformPasswordResetForm, PlatformUserCreateForm, ProfileForm, ProjectForm, RegistryImageForm, StudioPasswordChangeForm
from .models import (AuditEvent, ConfigurationVersion, DeviceTemplate, DeviceTemplateVersion, ImageArtifact, Lab, LabDeployment, LabFolder,
                     LabInterface, LabLink, LabNode, LabRevision, OperationJob, Project,
                     ProjectMembership, PublishedImage, User)
from .permissions import project_role
from .configurations import decrypt_configuration, encrypt_configuration
from .quotas import normalized_quotas,project_usage,quota_exceeded
from .topology_annotations import normalize_legacy_topology_annotations,validate_topology_annotations
from .edit_leases import acquire as acquire_edit_lease, conflict_payload as edit_lease_conflict, is_active as edit_lease_active, release as release_edit_lease, status_payload as edit_lease_status, valid_token as valid_edit_lease

def visible_projects(user):
    return Project.objects.filter(Q(owner=user) | Q(memberships__user=user),deleted_at__isnull=True).distinct()

def requested_folder(request,key="folder"):
    raw=request.GET.get(key)
    if not raw: return None
    try: folder_id=uuid.UUID(raw)
    except (TypeError,ValueError): raise Http404("Lab folder not found")
    return get_object_or_404(LabFolder.objects.filter(project__in=visible_projects(request.user),deleted_at__isnull=True).select_related("project","parent"),id=folder_id)

def _require_platform_admin(request):
    if not request.user.is_staff: raise PermissionDenied

def _user_sessions(user,delete=False):
    from django.contrib.sessions.models import Session
    revoked=0
    for session in Session.objects.filter(expire_date__gt=timezone.now()):
        try: matches=str(session.get_decoded().get("_auth_user_id"))==str(user.pk)
        except Exception: matches=False
        if matches:
            if delete: session.delete()
            revoked+=1
    return revoked

def _revoke_user_sessions(user): return _user_sessions(user,delete=True)

@login_required
@require_http_methods(["GET","POST"])
def platform_users(request):
    _require_platform_admin(request);form=PlatformUserCreateForm(request.POST or None)
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            user=form.save(commit=False);user.is_active=True;user.must_change_password=True;user.set_password(form.cleaned_data["password1"]);user.save()
            AuditEvent.objects.create(actor=request.user,action="account.created",target_type="User",target_id=user.id,
                correlation_id=getattr(request,"correlation_id",""),metadata={"username":user.username})
        messages.success(request,f'Account “{user.username}” created and ready for project access.');return redirect("portal-users")
    users=User.objects.annotate(owned_project_count=Count("owned_projects",filter=Q(owned_projects__deleted_at__isnull=True),distinct=True),
        membership_count=Count("project_memberships",distinct=True)).order_by("username")
    return render(request,"studio/users.html",{"users":users,"form":form,"active_count":users.filter(is_active=True).count()})

@login_required
@require_http_methods(["GET","POST"])
def platform_user_status(request,user_id):
    _require_platform_admin(request);target=get_object_or_404(User,pk=user_id);action="deactivate" if target.is_active else "activate"
    blockers=[]
    if action=="deactivate":
        if target==request.user: blockers.append("You cannot deactivate your current account.")
        if target.is_superuser and not request.user.is_superuser: blockers.append("Only a superuser can deactivate another superuser.")
        owned=target.owned_projects.filter(deleted_at__isnull=True).count()
        if owned: blockers.append(f"Transfer or retire {owned} active owned project{'s' if owned!=1 else ''} first.")
    payload={"user_id":str(target.id),"username":target.username,"action":action,"is_active":target.is_active,"updated_at":target.date_joined.isoformat(),
        "references":{"owned_projects":target.owned_projects.filter(deleted_at__isnull=True).count(),"memberships":target.project_memberships.count(),
            "active_consoles":target.consolesession_set.filter(revoked_at__isnull=True,expires_at__gt=timezone.now()).count()},"can_change":not blockers,"blockers":blockers,
        "impact":["Revoke active browser console sessions immediately." if action=="deactivate" else "Allow this account to sign in again.",
            "Preserve project memberships, audit history, and authored records.","Do not change project roles or ownership."]}
    if request.method=="GET":
        response=JsonResponse(payload);response["Cache-Control"]="no-store";return response
    if request.POST.get("expected_action")!=action: return JsonResponse({"error":{"code":"account_status_changed","details":"The account status changed after preview."}},status=409)
    if blockers: return JsonResponse({"error":{"code":"account_status_blocked","details":blockers}},status=409)
    with transaction.atomic():
        target=User.objects.select_for_update().get(pk=target.pk);target.is_active=action=="activate";target.save(update_fields=["is_active"])
        revoked=0
        if action=="deactivate": revoked=target.consolesession_set.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        AuditEvent.objects.create(actor=request.user,action=f"account.{action}d",target_type="User",target_id=target.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"username":target.username,"revoked_consoles":revoked})
    return JsonResponse({"user_id":str(target.id),"username":target.username,"is_active":target.is_active,"action":action})

@login_required
@require_http_methods(["GET","POST"])
def platform_user_password_reset(request,user_id):
    _require_platform_admin(request);target=get_object_or_404(User,pk=user_id);blockers=[]
    if target==request.user: blockers.append("Use Account & security to change your own password.")
    if target.is_superuser and not request.user.is_superuser: blockers.append("Only a superuser can reset another superuser credential.")
    if request.method=="GET":
        response=JsonResponse({"user_id":str(target.id),"username":target.username,"can_reset":not blockers,"blockers":blockers,
            "active_sessions":_user_sessions(target),"active_consoles":target.consolesession_set.filter(revoked_at__isnull=True,expires_at__gt=timezone.now()).count(),
            "impact":["Replace the current credential with an administrator-supplied temporary password.","Sign out every existing browser session and revoke active device consoles.","Require a personal password change before any other Studio operation."]})
        response["Cache-Control"]="no-store";return response
    if blockers: return JsonResponse({"error":{"code":"password_reset_blocked","details":blockers}},status=409)
    form=PlatformPasswordResetForm(target,request.POST)
    if not form.is_valid(): return JsonResponse({"error":{"code":"invalid_temporary_password","details":form.errors.get_json_data()}},status=422)
    with transaction.atomic():
        target=User.objects.select_for_update().get(pk=target.pk);target.set_password(form.cleaned_data["password1"]);target.must_change_password=True;target.save(update_fields=["password","must_change_password"])
        consoles=target.consolesession_set.filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
        sessions=_revoke_user_sessions(target)
        AuditEvent.objects.create(actor=request.user,action="account.password_reset",target_type="User",target_id=target.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"username":target.username,"revoked_sessions":sessions,"revoked_consoles":consoles})
    return JsonResponse({"user_id":str(target.id),"username":target.username,"must_change_password":True,"revoked_sessions":sessions,"revoked_consoles":consoles})

@login_required
def projects(request):
    queryset = visible_projects(request.user).annotate(lab_count=Count("labs",filter=Q(labs__deleted_at__isnull=True),distinct=True), member_count=Count("memberships", distinct=True)).order_by("name")
    return render(request, "studio/catalog.html", {"section": "projects", "title": "Projects", "eyebrow": "WORKSPACES", "items": queryset,
        "description": "Organize labs, images, access, and quotas around engineering teams.", "create_url": "/projects/new/", "create_label": "New project"})

@login_required
def project_create(request):
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if Project.objects.filter(owner=request.user,name=form.cleaned_data["name"],deleted_at__isnull=True).exists():
            form.add_error("name","You already have an active project with this name.")
        else:
            project = form.save(commit=False); project.owner = request.user; project.save()
            messages.success(request, f'Project “{project.name}” created.')
            return redirect("portal-projects")
    return render(request, "studio/form.html", {"form": form, "title": "Create project", "eyebrow": "NEW WORKSPACE", "cancel_url": "/projects/", "submit_label": "Create project"})

@login_required
def project_detail(request, project_id):
    project = get_object_or_404(visible_projects(request.user), id=project_id)
    return render(request, "studio/project_detail.html", {"project": project, "labs": project.labs.filter(deleted_at__isnull=True).order_by("name"),
        "members": project.memberships.select_related("user").order_by("user__username"),"can_manage_access":project_role(request.user,project)==ProjectMembership.Role.ADMIN,
        "quota_limits":normalized_quotas(project),"quota_usage":project_usage(project)})

@login_required
def project_edit(request,project_id):
    project=get_object_or_404(visible_projects(request.user),id=project_id)
    if project_role(request.user,project)!=ProjectMembership.Role.ADMIN:
        return JsonResponse({"error":"Administrator access is required to edit this project"},status=403)
    form=ProjectForm(request.POST or None,instance=project)
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            locked=Project.objects.select_for_update().get(pk=project.pk);before={key:getattr(locked,key) for key in ("name","description","tags")}
            locked.name=form.cleaned_data["name"];locked.description=form.cleaned_data["description"];locked.tags=form.cleaned_data["tags"]
            locked.save(update_fields=["name","description","tags","updated_at"])
            changed=[key for key,value in before.items() if value!=getattr(locked,key)]
            AuditEvent.objects.create(actor=request.user,project=locked,action="project.metadata_updated",target_type="Project",target_id=locked.id,
                correlation_id=getattr(request,"correlation_id",""),metadata={"changed_fields":changed})
        messages.success(request,f'Project “{locked.name}” updated.');return redirect("portal-project-detail",project_id=locked.id)
    return render(request,"studio/form.html",{"form":form,"title":f"Edit {project.name}","eyebrow":"PROJECT SETTINGS",
        "cancel_url":f"/projects/{project.id}/","submit_label":"Save changes"})

@login_required
def labs(request):
    projects=visible_projects(request.user)
    selected_folder=requested_folder(request)
    queryset = Lab.objects.filter(project__in=projects,deleted_at__isnull=True,folder=selected_folder).select_related("project","folder","folder__parent").annotate(revision_count=Count("revisions")).order_by("project__name","name")
    for lab in queryset: lab.can_manage=project_role(request.user,lab.project) in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR)
    folders=LabFolder.objects.filter(project__in=projects,parent=selected_folder,deleted_at__isnull=True).select_related("project","parent").annotate(lab_count=Count("labs",filter=Q(labs__deleted_at__isnull=True)),child_count=Count("children",filter=Q(children__deleted_at__isnull=True))).order_by("project__name","name")
    for folder in folders: folder.can_manage=project_role(request.user,folder.project) in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR)
    breadcrumbs=[];ancestor=selected_folder
    while ancestor:
        breadcrumbs.append(ancestor);ancestor=ancestor.parent
    breadcrumbs.reverse()
    can_manage_folder=selected_folder and project_role(request.user,selected_folder.project) in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR)
    can_create_any=Project.objects.filter(Q(owner=request.user)|Q(memberships__user=request.user,memberships__role__in=(ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR)),deleted_at__isnull=True).exists()
    create_url=(f"/labs/new/?project={selected_folder.project_id}&folder={selected_folder.id}" if selected_folder else "/labs/new/") if (can_manage_folder or (not selected_folder and can_create_any)) else None
    return render(request, "studio/catalog.html", {"section": "labs", "title": "Lab library", "eyebrow": "TOPOLOGY DESIGNS", "items": queryset,
        "folders":folders,"selected_folder":selected_folder,"folder_breadcrumbs":breadcrumbs,"can_manage_folder":can_manage_folder,
        "description": "Create, organize, and publish reusable network topology designs.","create_url":create_url,"create_label": "New lab"})

@login_required
def lab_folder_create(request):
    parent=requested_folder(request,"parent")
    if parent and project_role(request.user,parent.project) not in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR):
        raise PermissionDenied("Editor access is required to create a subfolder.")
    if not parent and not Project.objects.filter(Q(owner=request.user)|Q(memberships__user=request.user,memberships__role__in=(ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR)),deleted_at__isnull=True).exists():
        raise PermissionDenied("Editor access is required to create a folder.")
    initial={"project":parent.project_id,"parent":parent.id} if parent else {}
    form=LabFolderForm(request.user,request.POST or None,initial=initial)
    if request.method=="POST" and form.is_valid():
        folder=form.save(commit=False);folder.full_clean();folder.save()
        AuditEvent.objects.create(actor=request.user,project=folder.project,action="lab_folder.created",target_type="LabFolder",target_id=folder.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"parent_id":str(folder.parent_id) if folder.parent_id else None,"depth":len(folder.path.split(" / "))})
        messages.success(request,f'Folder “{folder.path}” created.');return redirect(f"/labs/?folder={folder.id}")
    return render(request,"studio/form.html",{"form":form,"title":"Create lab folder","eyebrow":"ORGANIZE LABS","cancel_url":"/labs/","submit_label":"Create folder"})

@login_required
def lab_folder_edit(request,folder_id):
    folder=get_object_or_404(LabFolder.objects.filter(project__in=visible_projects(request.user),deleted_at__isnull=True),id=folder_id)
    if project_role(request.user,folder.project) not in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR): return JsonResponse({"error":"Editor access is required"},status=403)
    form=LabFolderForm(request.user,request.POST or None,instance=folder)
    if request.method=="POST" and form.is_valid():
        before={"name":folder.name,"parent_id":str(folder.parent_id) if folder.parent_id else None};updated=form.save(commit=False);updated.full_clean();updated.save()
        AuditEvent.objects.create(actor=request.user,project=updated.project,action="lab_folder.updated",target_type="LabFolder",target_id=updated.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"changed_fields":[key for key,value in before.items() if value!=(str(updated.parent_id) if key=="parent_id" and updated.parent_id else getattr(updated,key,None))]})
        messages.success(request,f'Folder “{updated.path}” updated.');return redirect(f"/labs/?folder={updated.id}")
    return render(request,"studio/form.html",{"form":form,"title":f"Edit {folder.name}","eyebrow":"LAB FOLDER","cancel_url":"/labs/","submit_label":"Save folder"})

@login_required
@require_http_methods(["POST"])
def lab_folder_delete(request,folder_id):
    folder=get_object_or_404(LabFolder.objects.filter(project__in=visible_projects(request.user),deleted_at__isnull=True),id=folder_id)
    if project_role(request.user,folder.project) not in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR): return JsonResponse({"error":"Editor access is required"},status=403)
    active_labs=folder.labs.filter(deleted_at__isnull=True).count();children=folder.children.filter(deleted_at__isnull=True).count()
    return_url=f"/labs/?folder={folder.parent_id}" if folder.parent_id else "/labs/"
    if active_labs or children:
        messages.error(request,f'Folder “{folder.path}” cannot be deleted while it contains {active_labs} lab(s) and {children} subfolder(s).')
    else:
        folder.deleted_at=timezone.now();folder.save(update_fields=["deleted_at","updated_at"])
        AuditEvent.objects.create(actor=request.user,project=folder.project,action="lab_folder.deleted",target_type="LabFolder",target_id=folder.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"path":folder.path})
        messages.success(request,f'Folder “{folder.path}” deleted.')
    return redirect(return_url)

@login_required
def lab_create(request):
    folder=requested_folder(request)
    if folder and project_role(request.user,folder.project) not in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR):
        raise PermissionDenied("Editor access is required to create a lab in this folder.")
    requested_project=folder.project_id if folder else request.GET.get("project")
    initial={"project":requested_project,"folder":folder.id if folder else None} if requested_project else {}
    form = LabForm(request.user, request.POST or None,initial=initial)
    if request.method == "POST" and form.is_valid():
        project=form.cleaned_data["project"]
        with transaction.atomic():
            project=Project.objects.select_for_update().get(pk=project.pk);used=project.labs.filter(deleted_at__isnull=True).count();limit=normalized_quotas(project)["max_labs"]
            if used>=limit: form.add_error("project",f"This project has reached its {limit}-lab quota.")
            else:
                lab=form.save(commit=False);lab.project=project;lab.save();messages.success(request,f'Lab “{lab.name}” created.')
                return redirect(f"/labs/?folder={lab.folder_id}" if lab.folder_id else "/labs/")
    return render(request, "studio/form.html", {"form": form, "title": "Create lab", "eyebrow": "NEW TOPOLOGY", "cancel_url": "/labs/", "submit_label": "Create lab"})

@login_required
def lab_edit(request,lab_id):
    lab=get_object_or_404(Lab.objects.filter(project__in=visible_projects(request.user),deleted_at__isnull=True),id=lab_id)
    if project_role(request.user,lab.project) not in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR):
        return JsonResponse({"error":"Editor access is required to edit this lab"},status=403)
    form=LabEditForm(request.POST or None,instance=lab)
    if request.method=="POST" and form.is_valid():
        with transaction.atomic():
            locked=Lab.objects.select_for_update().get(pk=lab.pk);before={key:getattr(locked,key) for key in ("folder_id","name","description","tags")}
            locked.folder=form.cleaned_data["folder"];locked.name=form.cleaned_data["name"];locked.description=form.cleaned_data["description"];locked.tags=form.cleaned_data["tags"]
            locked.save(update_fields=["folder","name","description","tags","updated_at"])
            changed=[key for key,value in before.items() if value!=getattr(locked,key)]
            AuditEvent.objects.create(actor=request.user,project=locked.project,action="lab.metadata_updated",target_type="Lab",target_id=locked.id,
                correlation_id=getattr(request,"correlation_id",""),metadata={"changed_fields":changed})
        messages.success(request,f'Lab “{locked.name}” updated.');return redirect(f"/labs/?folder={locked.folder_id}" if locked.folder_id else "/labs/")
    return render(request,"studio/form.html",{"form":form,"title":f"Edit {lab.name}","eyebrow":"LAB SETTINGS","cancel_url":"/labs/","submit_label":"Save changes"})

@login_required
@ensure_csrf_cookie
def topology_workspace(request, lab_id):
    lab = get_object_or_404(Lab.objects.filter(project__in=visible_projects(request.user),deleted_at__isnull=True), id=lab_id)
    return render(request, "studio/workspace.html", {"lab": lab})

def _interfaces(rules):
    prefix, start, count = rules.get("prefix", "eth"), int(rules.get("start", 1)), min(int(rules.get("count", 4)), 64)
    return [f"{prefix}{number}" for number in range(start, start + count)]

@login_required
@require_http_methods(["GET"])
def topology_catalog(request):
    rows = DeviceTemplateVersion.objects.filter(template__active_version_id=F("id")).select_related("template").order_by("template__name")
    return JsonResponse({"templates": [{"id": str(row.id), "name": row.template.name, "kind": row.containerlab_kind,
        "category": row.launch_profile.get("category", "Other"), "icon": row.launch_profile.get("icon", "device"),
        "verified": bool(row.capabilities.get("verified")), "privileged": row.template.privileged,
        "interfaces": _interfaces(row.interface_rules), "managementInterface": row.interface_rules.get("management", "eth0"),
        "configurationLanguage": row.launch_profile.get("configuration_language", "text"),
        "startupConfigSupported": bool(row.launch_profile.get("startup_config_target")),
        "startupConfigRequired": bool(row.launch_profile.get("startup_config_required")),
        "requiredInterfaces": int(row.launch_profile.get("required_interfaces", 0)),
        "resources": row.resource_requirements, "capabilities": row.capabilities} for row in rows]})

@login_required
@require_http_methods(["GET"])
def topology_images(request, lab_id):
    lab=get_object_or_404(Lab.objects.filter(project__in=visible_projects(request.user),deleted_at__isnull=True),id=lab_id)
    rows=PublishedImage.objects.filter(artifact__project=lab.project,artifact__deleted_at__isnull=True,lifecycle_status__in=("ready","verified","unverified")).select_related("artifact").order_by("artifact__vendor","artifact__version")
    return JsonResponse({"images":[{"id":str(row.id),"name":f"{row.artifact.vendor or row.artifact.category or 'Image'} {row.artifact.version}".strip(),
        "digest":row.registry_digest,"architecture":row.architecture,"status":row.lifecycle_status,"compatibility":row.compatibility_result} for row in rows]})

@login_required
@require_http_methods(["GET", "POST", "DELETE"])
def topology_edit_lease(request, lab_id):
    lab=get_object_or_404(Lab.objects.filter(project__in=visible_projects(request.user),deleted_at__isnull=True).select_related("edit_lock_owner"),id=lab_id)
    if project_role(request.user,lab.project) not in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR):
        return JsonResponse({"error":{"code":"editor_access_required","details":"Editor access is required to change this topology."}},status=403)
    supplied=request.headers.get("X-Edit-Lease")
    if request.method=="GET":
        return JsonResponse(edit_lease_status(lab,request.user,supplied))
    with transaction.atomic():
        lab=Lab.objects.select_for_update().get(pk=lab.pk)
        if request.method=="DELETE":
            if not valid_edit_lease(lab,request.user,supplied): return JsonResponse(edit_lease_conflict(lab),status=409)
            release_edit_lease(lab)
            AuditEvent.objects.create(actor=request.user,project=lab.project,action="lab.edit_lease_released",target_type="Lab",target_id=lab.id,
                correlation_id=getattr(request,"correlation_id",""),metadata={})
            return JsonResponse({"active":False,"can_edit":False})
        was_active=edit_lease_active(lab);previous_owner=lab.edit_lock_owner_id
        token,payload=acquire_edit_lease(lab,request.user,supplied)
        if not token: return JsonResponse({"error":{"code":"edit_lease_conflict","details":f"{payload['owner']} is currently editing this topology.",**payload}},status=409)
        if not was_active or previous_owner!=request.user.id or not supplied:
            AuditEvent.objects.create(actor=request.user,project=lab.project,action="lab.edit_lease_acquired",target_type="Lab",target_id=lab.id,
                correlation_id=getattr(request,"correlation_id",""),metadata={"expires_at":payload["expires_at"]})
        return JsonResponse(payload)

@login_required
@require_http_methods(["GET", "PUT"])
def topology_document(request, lab_id):
    lab = get_object_or_404(Lab.objects.filter(project__in=visible_projects(request.user),deleted_at__isnull=True), id=lab_id)
    if request.method == "GET":
        revision = lab.current_draft
        if not revision:
            return JsonResponse({"lab": {"id": str(lab.id), "name": lab.name}, "editVersion": 0, "nodes": [], "links": [], "annotations": []})
        nodes = [{"id": str(node.id), "name": node.name, "templateVersionId": str(node.template_version_id), "publishedImageId": str(node.published_image_id) if node.published_image_id else None,
            "position": node.position, "properties": node.properties,
            "startupConfiguration": decrypt_configuration(node.startup_configuration.encrypted_content) if node.startup_configuration else "",
            "interfaces": [{"id": str(iface.id), "name": iface.name, "sharedMedium": iface.shared_medium} for iface in node.interfaces.all()]} for node in revision.nodes.select_related("template_version").prefetch_related("interfaces")]
        links = [{"id": str(link.id), "sourceNode": str(link.endpoint_a.node_id), "sourceInterface": link.endpoint_a.name,
            "targetNode": str(link.endpoint_b.node_id), "targetInterface": link.endpoint_b.name, "label": link.label,
            "properties": link.properties} for link in revision.links.select_related("endpoint_a__node", "endpoint_b__node")]
        return JsonResponse({"lab": {"id": str(lab.id), "name": lab.name}, "revisionId": str(revision.id), "editVersion": revision.edit_version,
            "nodes": nodes, "links": links, "annotations": normalize_legacy_topology_annotations(revision.annotations,revision.id)})
    if project_role(request.user, lab.project) not in (ProjectMembership.Role.ADMIN, ProjectMembership.Role.EDITOR):
        return JsonResponse({"error": "Editor access is required to change this topology"}, status=403)
    try: payload = json.loads(request.body)
    except (ValueError, TypeError): return JsonResponse({"error": "Invalid JSON document"}, status=400)
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes", []), list) or not isinstance(payload.get("links", []), list):
        return JsonResponse({"error": "Topology nodes and links must be lists"}, status=400)
    try: annotations=validate_topology_annotations(payload.get("annotations",[]))
    except ValueError as exc: return JsonResponse({"error":str(exc)},status=422)
    node_limit=normalized_quotas(lab.project)["max_nodes_per_lab"]
    if len(payload.get("nodes", []))>node_limit: return JsonResponse(quota_exceeded("nodes_per_lab",node_limit,0,len(payload.get("nodes",[]))),status=409)
    if len(payload.get("links", [])) > 1000: return JsonResponse({"error": "Topology exceeds workspace limits"}, status=422)
    names = [str(node.get("name", "")).strip()[:63] for node in payload.get("nodes", []) if isinstance(node, dict)]
    if len(names) != len(payload.get("nodes", [])) or any(not name for name in names) or len(names) != len(set(names)):
        return JsonResponse({"error": "Every node needs a unique name"}, status=422)
    try:
        node_ids = [str(uuid.UUID(str(node["id"]))) for node in payload.get("nodes", [])]
        link_ids = [str(uuid.UUID(str(link["id"]))) for link in payload.get("links", [])]
    except (KeyError, TypeError, ValueError, AttributeError):
        return JsonResponse({"error": "Every node and link needs a valid UUID"}, status=422)
    if len(node_ids) != len(set(node_ids)) or len(link_ids) != len(set(link_ids)):
        return JsonResponse({"error": "Node and link IDs must be unique"}, status=422)
    with transaction.atomic():
        lab = Lab.objects.select_for_update().get(id=lab.id)
        if edit_lease_active(lab) and not valid_edit_lease(lab,request.user,request.headers.get("X-Edit-Lease")):
            return JsonResponse(edit_lease_conflict(lab),status=409)
        revision = lab.current_draft
        if revision and revision.immutable: return JsonResponse({"error": "Published revisions cannot be edited"}, status=409)
        if revision and int(payload.get("editVersion", -1)) != revision.edit_version: return JsonResponse({"error": "This draft changed in another session", "editVersion": revision.edit_version}, status=409)
        canonical = json.dumps({"nodes": payload.get("nodes", []), "links": payload.get("links", []),"annotations":annotations}, sort_keys=True, separators=(",", ":"))
        if not revision:
            number = (lab.revisions.aggregate(n=Max("revision_number"))["n"] or 0) + 1
            revision = LabRevision.objects.create(lab=lab, revision_number=number, topology_checksum=hashlib.sha256(canonical.encode()).hexdigest(), canvas_layout={}, annotations=annotations)
            lab.current_draft = revision; lab.save(update_fields=["current_draft", "updated_at"])
        else:
            revision.links.all().delete(); revision.nodes.all().delete()
            revision.edit_version += 1; revision.topology_checksum = hashlib.sha256(canonical.encode()).hexdigest(); revision.annotations = annotations; revision.save()
        node_map, interface_map = {}, {}
        templates = {str(t.id): t for t in DeviceTemplateVersion.objects.filter(id__in=[n.get("templateVersionId") for n in payload.get("nodes", [])])}
        image_ids=[n.get("publishedImageId") for n in payload.get("nodes", []) if n.get("publishedImageId")]
        images={str(image.id):image for image in PublishedImage.objects.filter(id__in=image_ids,artifact__project=lab.project,artifact__deleted_at__isnull=True)}
        if len(images)!=len(set(image_ids)): transaction.set_rollback(True); return JsonResponse({"error":"A selected image is unavailable to this project"},status=422)
        for data in payload.get("nodes", []):
            template = templates.get(str(data.get("templateVersionId")))
            if not template: transaction.set_rollback(True); return JsonResponse({"error": f"Unknown template for {data.get('name', 'node')}"}, status=422)
            startup=None; startup_content=data.get("startupConfiguration", "")
            if not isinstance(startup_content,str): transaction.set_rollback(True); return JsonResponse({"error":"Startup configuration must be text"},status=422)
            if startup_content:
                encoded=startup_content.encode("utf-8")
                if len(encoded)>1024*1024: transaction.set_rollback(True); return JsonResponse({"error":"Startup configuration exceeds 1 MiB"},status=422)
                config_name=f"{lab.name}/{data['name'][:63]}/startup"; checksum=hashlib.sha256(encoded).hexdigest()
                startup=ConfigurationVersion.objects.filter(project=lab.project,name=config_name,checksum=checksum).order_by("-version").first()
                if not startup:
                    version=(ConfigurationVersion.objects.filter(project=lab.project,name=config_name).aggregate(n=Max("version"))["n"] or 0)+1
                    startup=ConfigurationVersion.objects.create(project=lab.project,name=config_name,version=version,
                        encrypted_content=encrypt_configuration(startup_content),checksum=checksum,created_by=request.user)
            node = LabNode.objects.create(id=uuid.UUID(data["id"]), revision=revision, name=data["name"][:63], template_version=template,
                published_image=images.get(str(data.get("publishedImageId"))),
                position=data.get("position", {}), properties=data.get("properties", {}),startup_configuration=startup)
            node_map[data["id"]] = node
            for name in _interfaces(template.interface_rules):
                iface = LabInterface.objects.create(node=node, name=name); interface_map[(data["id"], name)] = iface
        used = set()
        for data in payload.get("links", []):
            a=(data["sourceNode"],data["sourceInterface"]); b=(data["targetNode"],data["targetInterface"])
            if a in used or b in used or a not in interface_map or b not in interface_map: transaction.set_rollback(True); return JsonResponse({"error": "A link contains an invalid or already-used interface"}, status=422)
            used.update((a,b)); LabLink.objects.create(id=uuid.UUID(data["id"]), revision=revision, endpoint_a=interface_map[a], endpoint_b=interface_map[b], label=data.get("label", "")[:120], properties=data.get("properties", {}))
        AuditEvent.objects.create(actor=request.user,project=lab.project,action="lab.topology_saved",target_type="LabRevision",target_id=revision.id,
            correlation_id=getattr(request,"correlation_id",""),metadata={"lab":str(lab.id),"revision_number":revision.revision_number,
                "edit_version":revision.edit_version,"topology_checksum":revision.topology_checksum,"node_count":len(node_map),
                "link_count":len(payload.get("links",[])),"configured_node_count":sum(1 for item in payload.get("nodes",[]) if item.get("startupConfiguration"))})
    return JsonResponse({"revisionId": str(revision.id), "editVersion": revision.edit_version, "checksum": revision.topology_checksum})

@login_required
def deployments(request):
    queryset = LabDeployment.objects.filter(revision__lab__project__in=visible_projects(request.user)).select_related("revision__lab", "revision__lab__project").order_by("-updated_at")
    return render(request, "studio/catalog.html", {"section": "deployments", "title": "Deployments", "eyebrow": "RUNTIME", "items": queryset,
        "description": "Follow desired state, runtime readiness, placement, and failures."})

@login_required
@ensure_csrf_cookie
def deployment_detail(request, deployment_id):
    deployment=get_object_or_404(LabDeployment.objects.filter(revision__lab__project__in=visible_projects(request.user)).select_related("revision__lab__project"),id=deployment_id)
    role=project_role(request.user,deployment.revision.lab.project)
    return render(request,"studio/deployment_detail.html",{"deployment":deployment,"can_operate":role in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR)})

@login_required
def images(request):
    artifacts = ImageArtifact.objects.filter(project__in=visible_projects(request.user),deleted_at__isnull=True).select_related("project").prefetch_related("published_images","builds").order_by("-created_at")
    for artifact in artifacts:
        artifact.ready_publication=next((image for image in artifact.published_images.all() if image.lifecycle_status=="ready"),None)
        can_operate=project_role(request.user,artifact.project) in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR)
        artifact.can_manage=can_operate
        artifact.can_publish=can_operate and artifact.validation_status==ImageArtifact.Validation.VALIDATED and artifact.detected_format in ("docker-archive","oci-archive") and artifact.license_acknowledged and not artifact.ready_publication
        artifact.can_republish=can_operate and bool(artifact.ready_publication) and artifact.source_type==ImageArtifact.Source.UPLOAD
        artifact.revision_reference_count=LabNode.objects.filter(published_image__artifact=artifact).values("revision_id").distinct().count()
        artifact.reference_count=len(artifact.published_images.all())+len(artifact.builds.all())+artifact.revision_reference_count
        artifact.can_delete=can_operate and artifact.reference_count==0
    published = PublishedImage.objects.filter(artifact__project__in=visible_projects(request.user),artifact__deleted_at__isnull=True).count()
    return render(request, "studio/catalog.html", {"section": "images", "title": "Image library", "eyebrow": "DEVICE SOFTWARE", "items": artifacts,
        "description": "Track quarantined uploads, inspection results, builds, and immutable publications.", "secondary_stat": f"{published} published",
        "create_url": "/images/register/", "create_label": "Register image", "upload_url":"/images/upload/"})

@login_required
@ensure_csrf_cookie
def image_upload(request):
    editable=Project.objects.filter(Q(owner=request.user)|Q(memberships__user=request.user,memberships__role__in=(ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR)),deleted_at__isnull=True).distinct().order_by("name")
    return render(request,"studio/image_upload.html",{"projects":editable})

@login_required
def image_register(request):
    form = RegistryImageForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        digest = form.cleaned_data["registry_digest"]
        with transaction.atomic():
            artifact = ImageArtifact.objects.create(project=form.cleaned_data["project"], owner=request.user,
                source_type=ImageArtifact.Source.REGISTRY, registry_reference=digest, original_filename=form.cleaned_data["name"],
                detected_format="oci-registry", byte_size=0, checksum=digest.rsplit(":", 1)[1], vendor=form.cleaned_data["vendor"],
                category=form.cleaned_data["name"], version=form.cleaned_data["version"], architecture=form.cleaned_data["architecture"],
                storage_reference=digest, license_acknowledged=True, inspection_result={"source": "registry", "digest_pinned": True},
                validation_status=ImageArtifact.Validation.VALIDATED)
            PublishedImage.objects.create(artifact=artifact, registry_digest=digest, repository=digest.split("@", 1)[0],
                architecture=form.cleaned_data["architecture"], compatibility_result={"digest_pinned": True, "runtime_pull": "not_yet_verified"},
                lifecycle_status="unverified")
        messages.success(request, "Digest-pinned image registered. Runtime pull verification is still required.")
        return redirect("portal-images")
    return render(request, "studio/form.html", {"form": form, "title": "Register OCI image", "eyebrow": "IMAGE LIBRARY",
        "cancel_url": "/images/", "submit_label": "Register image"})

@login_required
def templates(request):
    queryset = DeviceTemplate.objects.select_related("active_version").order_by("name")
    return render(request, "studio/catalog.html", {"section": "templates", "title": "Device templates", "eyebrow": "LAUNCH PROFILES", "items": queryset,
        "description": "Browse verified kinds, interface rules, resource requirements, and capabilities.",
        "create_url":"/device-templates/new/" if request.user.is_staff else None,"create_label":"Create template"})

@login_required
def template_manage(request,template_id=None):
    template=get_object_or_404(DeviceTemplate.objects.select_related("active_version"),pk=template_id) if template_id else None
    if not template and not request.user.is_staff: raise PermissionDenied
    active=template.active_version if template else None
    profile=active.launch_profile if active else {}
    rules=active.interface_rules if active else {}
    resources=active.resource_requirements if active else {}
    target=profile.get("startup_config_target","")
    configuration_profile="frr" if target=="/etc/frr/frr.conf" else "nftables" if target=="/etc/studio/firewall.sh" else "none"
    initial={"name":template.name if template else "","description":template.description if template else "",
        "privileged":template.privileged if template else False,"containerlab_kind":active.containerlab_kind if active else "linux",
        "category":profile.get("category","Other"),"icon":profile.get("icon","host"),"interface_prefix":rules.get("prefix","eth"),
        "interface_start":rules.get("start",1),"interface_count":rules.get("count",4),
        "management_interface":rules.get("management","eth0"),"cpu":resources.get("cpu","500m"),
        "memory":resources.get("memory","512Mi"),"console_method":active.console_method if active else "shell",
        "configuration_profile":configuration_profile,"verified":bool(profile.get("verified",False))}
    versions=list(template.versions.order_by("-version")) if template else []
    return render(request,"studio/template_manage.html",{"template_obj":template,"active":active,"initial":initial,
        "versions":versions,"can_manage":request.user.is_staff})

@login_required
def operations(request):
    queryset = OperationJob.objects.filter(owner=request.user).select_related("deployment__revision__lab").order_by("-created_at")
    return render(request, "studio/catalog.html", {"section": "operations", "title": "Jobs & events", "eyebrow": "OPERATIONS", "items": queryset,
        "description": "Inspect accepted, scheduled, running, completed, and failed background work."})

@login_required
@require_http_methods(["GET","POST"])
def settings_view(request):
    profile_form=ProfileForm(instance=request.user,prefix="profile")
    password_form=StudioPasswordChangeForm(request.user,prefix="password")
    if request.method=="POST":
        action=request.POST.get("action")
        if action=="profile":
            profile_form=ProfileForm(request.POST,instance=request.user,prefix="profile")
            if profile_form.is_valid():
                changed=list(profile_form.changed_data);profile_form.save()
                AuditEvent.objects.create(actor=request.user,action="account.profile_updated",target_type="User",target_id=request.user.id,
                    correlation_id=getattr(request,"correlation_id",""),metadata={"changed_fields":changed})
                messages.success(request,"Profile settings saved.");return redirect("portal-settings")
        elif action=="password":
            password_form=StudioPasswordChangeForm(request.user,request.POST,prefix="password")
            if password_form.is_valid():
                forced=request.user.must_change_password;user=password_form.save();user.must_change_password=False;user.save(update_fields=["must_change_password"]);update_session_auth_hash(request,user)
                AuditEvent.objects.create(actor=user,action="account.password_changed",target_type="User",target_id=user.id,
                    correlation_id=getattr(request,"correlation_id",""),metadata={"forced_rotation":forced})
                messages.success(request,"Password changed. Your current session remains active.");return redirect("portal-settings")
        else: messages.error(request,"Unknown settings action.")
    return render(request,"studio/settings.html",{"profile_form":profile_form,"password_form":password_form})
