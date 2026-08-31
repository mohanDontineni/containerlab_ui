import hashlib
import json
import uuid

from django.db import transaction
from django.db.models import Max

from .configurations import decrypt_configuration, encrypt_configuration
from .models import (ConfigurationVersion, DeviceTemplateVersion, LabInterface,
                     LabLink, LabNode, LabRevision, PublishedImage)

BUNDLE_FORMAT = "io.containerlab.studio.lab"
BUNDLE_VERSION = 1
MAX_BUNDLE_BYTES = 4 * 1024 * 1024


class BundleError(ValueError):
    pass


def export_lab_bundle(lab):
    revision = lab.current_draft or lab.revisions.order_by("-revision_number").first()
    nodes, links = [], []
    if revision:
        for node in revision.nodes.select_related(
            "template_version__template", "published_image", "startup_configuration"
        ).prefetch_related("interfaces"):
            template = node.template_version
            nodes.append({
                "id": str(node.id), "name": node.name,
                "template": {"name": template.template.name, "version": template.version,
                             "kind": template.containerlab_kind},
                "imageDigest": node.published_image.registry_digest if node.published_image else None,
                "position": node.position, "properties": node.properties,
                "interfaces": [i.name for i in node.interfaces.all()],
                "startupConfiguration": decrypt_configuration(node.startup_configuration.encrypted_content)
                    if node.startup_configuration else "",
            })
        for link in revision.links.select_related("endpoint_a__node", "endpoint_b__node"):
            links.append({
                "id": str(link.id), "sourceNode": str(link.endpoint_a.node_id),
                "sourceInterface": link.endpoint_a.name, "targetNode": str(link.endpoint_b.node_id),
                "targetInterface": link.endpoint_b.name, "label": link.label,
                "properties": link.properties,
            })
    return {
        "format": BUNDLE_FORMAT, "version": BUNDLE_VERSION,
        "lab": {"name": lab.name, "description": lab.description, "tags": lab.tags},
        "topology": {"nodes": nodes, "links": links, "annotations": revision.annotations if revision else []},
    }


def _uuid(value, label):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BundleError(f"Invalid {label} UUID") from exc


@transaction.atomic
def import_lab_bundle(lab, user, raw):
    if isinstance(raw, bytes):
        if len(raw) > MAX_BUNDLE_BYTES:
            raise BundleError("Bundle exceeds the 4 MiB limit")
        try:
            bundle = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BundleError("Bundle is not valid UTF-8 JSON") from exc
    else:
        bundle = raw
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT or bundle.get("version") != BUNDLE_VERSION:
        raise BundleError("Unsupported lab bundle format or version")
    topology = bundle.get("topology")
    if not isinstance(topology, dict) or not isinstance(topology.get("nodes"), list) or not isinstance(topology.get("links"), list):
        raise BundleError("Bundle topology must contain node and link lists")
    if len(topology["nodes"]) > 250 or len(topology["links"]) > 1000:
        raise BundleError("Bundle exceeds workspace topology limits")

    lab = type(lab).objects.select_for_update().get(pk=lab.pk)
    if lab.current_draft and lab.current_draft.immutable:
        raise BundleError("Published revisions cannot be replaced")
    number = (lab.revisions.aggregate(n=Max("revision_number"))["n"] or 0) + 1
    canonical = json.dumps(topology, sort_keys=True, separators=(",", ":"))
    revision = LabRevision.objects.create(
        lab=lab, revision_number=number, topology_checksum=hashlib.sha256(canonical.encode()).hexdigest(),
        annotations=topology.get("annotations", []),
    )
    node_map, interface_map, names, source_ids = {}, {}, set(), set()
    for item in topology["nodes"]:
        if not isinstance(item, dict): raise BundleError("Every node must be an object")
        source_id = str(_uuid(item.get("id"), "node"))
        if source_id in source_ids: raise BundleError("Node IDs must be unique")
        source_ids.add(source_id)
        node_id = uuid.uuid4()
        name = str(item.get("name", "")).strip()
        if not name or len(name) > 63 or name in names: raise BundleError("Node names must be unique and 1-63 characters")
        names.add(name)
        descriptor = item.get("template") or {}
        template = DeviceTemplateVersion.objects.filter(
            template__name=descriptor.get("name"), version=descriptor.get("version"),
            containerlab_kind=descriptor.get("kind"),
        ).first()
        if not template: raise BundleError(f"Template is unavailable for {name}")
        digest = item.get("imageDigest")
        image = PublishedImage.objects.filter(artifact__project=lab.project, registry_digest=digest).first() if digest else None
        if digest and not image: raise BundleError(f"Image {digest} is unavailable in the destination project")
        config = None
        content = item.get("startupConfiguration", "")
        if content:
            base = f"{lab.name}/{name}/startup"
            version = (ConfigurationVersion.objects.filter(project=lab.project, name=base).aggregate(n=Max("version"))["n"] or 0) + 1
            encoded = content.encode("utf-8")
            config = ConfigurationVersion.objects.create(
                project=lab.project, name=base, version=version,
                encrypted_content=encrypt_configuration(content), checksum=hashlib.sha256(encoded).hexdigest(), created_by=user,
            )
        node = LabNode.objects.create(
            id=node_id, revision=revision, name=name, template_version=template, published_image=image,
            position=item.get("position") if isinstance(item.get("position"), dict) else {},
            properties=item.get("properties") if isinstance(item.get("properties"), dict) else {},
            startup_configuration=config,
        )
        node_map[source_id] = node
        allowed = {str(x) for x in item.get("interfaces", [])}
        expected = {f"{template.interface_rules.get('prefix', 'eth')}{n}" for n in range(int(template.interface_rules.get('start', 1)), int(template.interface_rules.get('start', 1)) + min(int(template.interface_rules.get('count', 4)), 64))}
        if allowed != expected: raise BundleError(f"Interface inventory does not match the template for {name}")
        for iface_name in sorted(expected):
            iface = LabInterface.objects.create(node=node, name=iface_name)
            interface_map[(source_id, iface_name)] = iface
    used, link_ids = set(), set()
    for item in topology["links"]:
        source_link_id = _uuid(item.get("id"), "link")
        if source_link_id in link_ids: raise BundleError("Link IDs must be unique")
        link_ids.add(source_link_id)
        a = (str(_uuid(item.get("sourceNode"), "source node")), str(item.get("sourceInterface", "")))
        b = (str(_uuid(item.get("targetNode"), "target node")), str(item.get("targetInterface", "")))
        if a == b or a in used or b in used or a not in interface_map or b not in interface_map:
            raise BundleError("A link contains an invalid or reused interface")
        used.update((a, b))
        LabLink.objects.create(id=uuid.uuid4(), revision=revision, endpoint_a=interface_map[a], endpoint_b=interface_map[b],
                               label=str(item.get("label", ""))[:120], properties=item.get("properties") if isinstance(item.get("properties"), dict) else {})
    previous = lab.current_draft
    lab.current_draft = revision
    lab.save(update_fields=["current_draft", "updated_at"])
    if previous and not previous.immutable:
        previous.links.all().delete()
        previous.nodes.all().delete()
        previous.delete()
    return revision
