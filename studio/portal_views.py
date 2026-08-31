from django.contrib import messages
from django.contrib.auth.decorators import login_required
import hashlib
import json
import uuid
from django.db import transaction
from django.db.models import Count, F, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from .forms import LabForm, ProjectForm, RegistryImageForm
from .models import (AuditEvent, ConfigurationVersion, DeviceTemplate, DeviceTemplateVersion, ImageArtifact, Lab, LabDeployment,
                     LabInterface, LabLink, LabNode, LabRevision, OperationJob, Project,
                     ProjectMembership, PublishedImage)
from .permissions import project_role
from .configurations import decrypt_configuration, encrypt_configuration
from .quotas import normalized_quotas,project_usage,quota_exceeded

def visible_projects(user):
    return Project.objects.filter(Q(owner=user) | Q(memberships__user=user)).distinct()

@login_required
def projects(request):
    queryset = visible_projects(request.user).annotate(lab_count=Count("labs", distinct=True), member_count=Count("memberships", distinct=True)).order_by("name")
    return render(request, "studio/catalog.html", {"section": "projects", "title": "Projects", "eyebrow": "WORKSPACES", "items": queryset,
        "description": "Organize labs, images, access, and quotas around engineering teams.", "create_url": "/projects/new/", "create_label": "New project"})

@login_required
def project_create(request):
    form = ProjectForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        project = form.save(commit=False); project.owner = request.user; project.save()
        messages.success(request, f'Project “{project.name}” created.')
        return redirect("portal-projects")
    return render(request, "studio/form.html", {"form": form, "title": "Create project", "eyebrow": "NEW WORKSPACE", "cancel_url": "/projects/", "submit_label": "Create project"})

@login_required
def project_detail(request, project_id):
    project = get_object_or_404(visible_projects(request.user), id=project_id)
    return render(request, "studio/project_detail.html", {"project": project, "labs": project.labs.order_by("name"),
        "members": project.memberships.select_related("user").order_by("user__username"),"can_manage_access":project_role(request.user,project)==ProjectMembership.Role.ADMIN,
        "quota_limits":normalized_quotas(project),"quota_usage":project_usage(project)})

@login_required
def labs(request):
    queryset = Lab.objects.filter(project__in=visible_projects(request.user)).select_related("project").annotate(revision_count=Count("revisions")).order_by("name")
    return render(request, "studio/catalog.html", {"section": "labs", "title": "Lab library", "eyebrow": "TOPOLOGY DESIGNS", "items": queryset,
        "description": "Create, organize, and publish reusable network topology designs.", "create_url": "/labs/new/", "create_label": "New lab"})

@login_required
def lab_create(request):
    form = LabForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        project=form.cleaned_data["project"]
        with transaction.atomic():
            project=Project.objects.select_for_update().get(pk=project.pk);used=project.labs.count();limit=normalized_quotas(project)["max_labs"]
            if used>=limit: form.add_error("project",f"This project has reached its {limit}-lab quota.")
            else:
                lab=form.save(commit=False);lab.project=project;lab.save();messages.success(request,f'Lab “{lab.name}” created.')
                return redirect("portal-labs")
    return render(request, "studio/form.html", {"form": form, "title": "Create lab", "eyebrow": "NEW TOPOLOGY", "cancel_url": "/labs/", "submit_label": "Create lab"})

@login_required
@ensure_csrf_cookie
def topology_workspace(request, lab_id):
    lab = get_object_or_404(Lab.objects.filter(project__in=visible_projects(request.user)), id=lab_id)
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
    lab=get_object_or_404(Lab.objects.filter(project__in=visible_projects(request.user)),id=lab_id)
    rows=PublishedImage.objects.filter(artifact__project=lab.project,lifecycle_status__in=("ready","verified","unverified")).select_related("artifact").order_by("artifact__vendor","artifact__version")
    return JsonResponse({"images":[{"id":str(row.id),"name":f"{row.artifact.vendor or row.artifact.category or 'Image'} {row.artifact.version}".strip(),
        "digest":row.registry_digest,"architecture":row.architecture,"status":row.lifecycle_status,"compatibility":row.compatibility_result} for row in rows]})

@login_required
@require_http_methods(["GET", "PUT"])
def topology_document(request, lab_id):
    lab = get_object_or_404(Lab.objects.filter(project__in=visible_projects(request.user)), id=lab_id)
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
            "nodes": nodes, "links": links, "annotations": revision.annotations})
    if project_role(request.user, lab.project) not in (ProjectMembership.Role.ADMIN, ProjectMembership.Role.EDITOR):
        return JsonResponse({"error": "Editor access is required to change this topology"}, status=403)
    try: payload = json.loads(request.body)
    except (ValueError, TypeError): return JsonResponse({"error": "Invalid JSON document"}, status=400)
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes", []), list) or not isinstance(payload.get("links", []), list):
        return JsonResponse({"error": "Topology nodes and links must be lists"}, status=400)
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
        revision = lab.current_draft
        if revision and revision.immutable: return JsonResponse({"error": "Published revisions cannot be edited"}, status=409)
        if revision and int(payload.get("editVersion", -1)) != revision.edit_version: return JsonResponse({"error": "This draft changed in another session", "editVersion": revision.edit_version}, status=409)
        canonical = json.dumps({"nodes": payload.get("nodes", []), "links": payload.get("links", [])}, sort_keys=True, separators=(",", ":"))
        if not revision:
            number = (lab.revisions.aggregate(n=Max("revision_number"))["n"] or 0) + 1
            revision = LabRevision.objects.create(lab=lab, revision_number=number, topology_checksum=hashlib.sha256(canonical.encode()).hexdigest(), canvas_layout={}, annotations=payload.get("annotations", []))
            lab.current_draft = revision; lab.save(update_fields=["current_draft", "updated_at"])
        else:
            revision.links.all().delete(); revision.nodes.all().delete()
            revision.edit_version += 1; revision.topology_checksum = hashlib.sha256(canonical.encode()).hexdigest(); revision.annotations = payload.get("annotations", []); revision.save()
        node_map, interface_map = {}, {}
        templates = {str(t.id): t for t in DeviceTemplateVersion.objects.filter(id__in=[n.get("templateVersionId") for n in payload.get("nodes", [])])}
        image_ids=[n.get("publishedImageId") for n in payload.get("nodes", []) if n.get("publishedImageId")]
        images={str(image.id):image for image in PublishedImage.objects.filter(id__in=image_ids,artifact__project=lab.project)}
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
    artifacts = ImageArtifact.objects.filter(project__in=visible_projects(request.user)).select_related("project").prefetch_related("published_images").order_by("-created_at")
    for artifact in artifacts:
        artifact.ready_publication=next((image for image in artifact.published_images.all() if image.lifecycle_status=="ready"),None)
        can_operate=project_role(request.user,artifact.project) in (ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR)
        artifact.can_publish=can_operate and artifact.validation_status==ImageArtifact.Validation.VALIDATED and artifact.detected_format in ("docker-archive","oci-archive") and artifact.license_acknowledged and not artifact.ready_publication
        artifact.can_republish=can_operate and bool(artifact.ready_publication) and artifact.source_type==ImageArtifact.Source.UPLOAD
    published = PublishedImage.objects.filter(artifact__project__in=visible_projects(request.user)).count()
    return render(request, "studio/catalog.html", {"section": "images", "title": "Image library", "eyebrow": "DEVICE SOFTWARE", "items": artifacts,
        "description": "Track quarantined uploads, inspection results, builds, and immutable publications.", "secondary_stat": f"{published} published",
        "create_url": "/images/register/", "create_label": "Register image", "upload_url":"/images/upload/"})

@login_required
@ensure_csrf_cookie
def image_upload(request):
    editable=Project.objects.filter(Q(owner=request.user)|Q(memberships__user=request.user,memberships__role__in=(ProjectMembership.Role.ADMIN,ProjectMembership.Role.EDITOR))).distinct().order_by("name")
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
        "description": "Browse verified kinds, interface rules, resource requirements, and capabilities."})

@login_required
def operations(request):
    queryset = OperationJob.objects.filter(owner=request.user).select_related("deployment__revision__lab").order_by("-created_at")
    return render(request, "studio/catalog.html", {"section": "operations", "title": "Jobs & events", "eyebrow": "OPERATIONS", "items": queryset,
        "description": "Inspect accepted, scheduled, running, completed, and failed background work."})

@login_required
def settings_view(request):
    return render(request, "studio/settings.html")
