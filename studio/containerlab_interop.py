import json
import math
import uuid

from django.db.models import F

from .bundles import BUNDLE_FORMAT, BUNDLE_VERSION, import_lab_bundle
from .image_compatibility import evaluate as evaluate_image_compatibility
from .models import DeviceTemplateVersion, PublishedImage
from .topology import MAX_YAML_BYTES, TopologyError, canonical_checksum, safe_load_topology


class ContainerlabInteropError(ValueError):
    pass


def read_containerlab_upload(upload):
    if not upload:
        raise ContainerlabInteropError("Choose a Containerlab topology file.")
    raw=upload.read(MAX_YAML_BYTES+1)
    if len(raw)>MAX_YAML_BYTES:
        raise ContainerlabInteropError("Topology exceeds the 2 MiB limit.")
    try:
        return raw,safe_load_topology(raw)
    except TopologyError as exc:
        raise ContainerlabInteropError(str(exc)) from exc


def _interfaces(template):
    rules=template.interface_rules if isinstance(template.interface_rules,dict) else {}
    start=int(rules.get("start",1));count=min(int(rules.get("count",4)),64);prefix=str(rules.get("prefix","eth"))
    return [f"{prefix}{number}" for number in range(start,start+count)]


def inspect_containerlab_topology(lab,document):
    topology=document["topology"];source_nodes=topology.get("nodes",{});links=topology.get("links",[])
    if len(source_nodes)>250: raise ContainerlabInteropError("GUI import supports at most 250 devices.")
    endpoints={name:set() for name in source_nodes};issues=[]
    for link in links:
        for endpoint in link["endpoints"]:
            node,interface=endpoint.split(":",1)
            if not interface or len(interface)>64 or any(character.isspace() for character in interface):
                raise ContainerlabInteropError(f"Invalid interface name: {interface or 'empty'}")
            endpoints[node].add(interface)
    template_rows=list(DeviceTemplateVersion.objects.filter(id=F("template__active_version_id")).select_related("template").order_by("template__name"))
    images=list(PublishedImage.objects.filter(artifact__project=lab.project,artifact__deleted_at__isnull=True,
        lifecycle_status__in=("ready","verified","unverified")).select_related("artifact").order_by("artifact__vendor","artifact__original_filename"))
    nodes=[];external_configurations=0
    for name,source in source_nodes.items():
        if not isinstance(source,dict): raise ContainerlabInteropError(f"Node {name} must be a mapping.")
        unsupported=sorted(set(source)&{"env","ports","labels","type"})
        if unsupported:issues.append(f"{name}: runtime fields are not supported by this importer: {', '.join(unsupported)}")
        source_kind=str(source.get("kind") or "").strip()
        if not source_kind:issues.append(f"{name}: a Containerlab kind is required")
        source_image=str(source.get("image") or "").strip();external=bool(str(source.get("startup-config") or "").strip())
        external_configurations+=int(external);choices=[]
        for template in template_rows:
            inventory=_interfaces(template)
            if template.containerlab_kind!=source_kind or not endpoints[name].issubset(set(inventory)):continue
            compatible=[]
            for image in images:
                decision=evaluate_image_compatibility(template,image)
                compatible.append({"id":str(image.id),"name":f"{image.artifact.vendor or image.artifact.original_filename} {image.artifact.version}".strip(),
                    "digest":image.registry_digest,"architecture":image.architecture,"status":decision["status"],"selectable":decision["selectable"],
                    "reasons":decision["reasons"],"warnings":decision["warnings"],"source_match":image.registry_digest==source_image})
            choices.append({"id":str(template.id),"name":template.template.name,"version":template.version,"kind":template.containerlab_kind,
                "verified":bool((template.capabilities or {}).get("verified",(template.launch_profile or {}).get("verified",False))),
                "interfaces":inventory,"images":compatible})
        if not choices:issues.append(f"{name}: no active {source_kind or 'unspecified'} template exposes interfaces {', '.join(sorted(endpoints[name])) or 'none'}")
        recommended_template=None;recommended_image=None
        exact=[(template,image) for template in choices for image in template["images"] if image["source_match"] and image["selectable"]]
        if len({template["id"] for template,_ in exact})==1:
            recommended_template=exact[0][0]["id"];recommended_image=exact[0][1]["id"]
        elif len(choices)==1:
            recommended_template=choices[0]["id"];selectable=[image for image in choices[0]["images"] if image["selectable"]]
            if len(selectable)==1:recommended_image=selectable[0]["id"]
        nodes.append({"name":name,"kind":source_kind,"source_image":source_image,"interfaces":sorted(endpoints[name]),
            "external_startup_configuration":external,"template_choices":choices,
            "recommended_template":recommended_template,"recommended_image":recommended_image})
    active_states=("pending","deploying","running","degraded")
    return {"format":"containerlab","source_name":str(document.get("name") or "Imported topology")[:120],
        "checksum":canonical_checksum(document),"node_count":len(nodes),"link_count":len(links),"nodes":nodes,
        "issues":issues,"structurally_importable":not issues,"external_configuration_count":external_configurations,
        "expected_current_draft":str(lab.current_draft_id) if lab.current_draft_id else None,
        "will_replace_draft":bool(lab.current_draft_id),"preserved_published_revisions":lab.revisions.filter(immutable=True).count(),
        "running_deployments_unchanged":lab.revisions.filter(deployments__observed_state__in=active_states).distinct().count(),
        "impact":["Create a new editable Studio revision from the imported nodes and point-to-point links.",
            "Require an active Studio template and compatible immutable project image for every device.",
            "Leave published revisions and active runtime namespaces unchanged.",
            "Omit external startup-file references; add or paste configuration later in the visual inspector."]}


def import_containerlab_topology(lab,user,document,mappings,acknowledge_external_configurations=False):
    preview=inspect_containerlab_topology(lab,document)
    if not preview["structurally_importable"]:raise ContainerlabInteropError("Resolve the topology compatibility issues before importing.")
    if preview["external_configuration_count"] and not acknowledge_external_configurations:
        raise ContainerlabInteropError("Acknowledge that external startup-file references will be omitted.")
    if not isinstance(mappings,dict) or set(mappings)!=set(document["topology"]["nodes"]):
        raise ContainerlabInteropError("Map every imported device to one template and one immutable image.")
    node_documents=[];source_ids={name:str(uuid.uuid5(uuid.NAMESPACE_URL,f"containerlab:{preview['checksum']}:{name}")) for name in mappings}
    columns=max(1,math.ceil(math.sqrt(len(mappings))))
    for index,node_preview in enumerate(preview["nodes"]):
        name=node_preview["name"];mapping=mappings.get(name)
        if not isinstance(mapping,dict):raise ContainerlabInteropError(f"Mapping is missing for {name}.")
        template=next((choice for choice in node_preview["template_choices"] if choice["id"]==str(mapping.get("template_id"))),None)
        if not template:raise ContainerlabInteropError(f"Selected template is unavailable for {name}.")
        image=next((choice for choice in template["images"] if choice["id"]==str(mapping.get("image_id")) and choice["selectable"]),None)
        if not image:raise ContainerlabInteropError(f"Select a compatible immutable image for {name}.")
        node_documents.append({"id":source_ids[name],"name":name,"template":{"name":template["name"],"version":template["version"],"kind":template["kind"]},
            "imageDigest":image["digest"],"position":{"x":100+(index%columns)*230,"y":100+(index//columns)*170},
            "properties":{"containerlabImport":{"sourceKind":node_preview["kind"],"sourceImage":node_preview["source_image"]}},
            "interfaces":template["interfaces"],"startupConfiguration":""})
    link_documents=[]
    for index,link in enumerate(document["topology"].get("links",[])):
        left,right=link["endpoints"];left_node,left_interface=left.split(":",1);right_node,right_interface=right.split(":",1)
        link_documents.append({"id":str(uuid.uuid5(uuid.NAMESPACE_URL,f"containerlab:{preview['checksum']}:link:{index}:{left}:{right}")),
            "sourceNode":source_ids[left_node],"sourceInterface":left_interface,"targetNode":source_ids[right_node],
            "targetInterface":right_interface,"label":"","properties":{"containerlabImport":True}})
    bundle={"format":BUNDLE_FORMAT,"version":BUNDLE_VERSION,"lab":{"name":preview["source_name"],"description":"","tags":["containerlab-import"]},
        "topology":{"nodes":node_documents,"links":link_documents,"annotations":[]}}
    return import_lab_bundle(lab,user,bundle),preview
