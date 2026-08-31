import hashlib
import io
import json
import re
import yaml

MAX_YAML_BYTES=2*1024*1024
NAME=re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
ALLOWED_NODE_FIELDS={"kind","image","startup-config","type","env","ports","labels"}

class TopologyError(ValueError): pass

def safe_load_topology(data: bytes):
    if len(data)>MAX_YAML_BYTES: raise TopologyError("Topology exceeds 2 MiB limit")
    try: doc=yaml.safe_load(io.BytesIO(data))
    except yaml.YAMLError as e: raise TopologyError(f"Invalid YAML: {e}") from e
    if not isinstance(doc,dict) or not isinstance(doc.get("topology"),dict): raise TopologyError("Missing topology mapping")
    nodes=doc["topology"].get("nodes",{}); links=doc["topology"].get("links",[])
    if not isinstance(nodes,dict) or len(nodes)>500: raise TopologyError("nodes must be a mapping with at most 500 entries")
    for name,spec in nodes.items():
        if not isinstance(name,str) or not NAME.fullmatch(name) or len(name)>63: raise TopologyError(f"Invalid node name: {name}")
        unknown=set(spec or {})-ALLOWED_NODE_FIELDS
        if unknown: raise TopologyError(f"Unsupported fields for {name}: {sorted(unknown)}")
        for forbidden in ("binds","exec","cmd","user","network-mode","cap-add"):
            if forbidden in (spec or {}): raise TopologyError(f"Unsafe field {forbidden} is not accepted")
    used=set()
    for link in links:
        endpoints=link.get("endpoints") if isinstance(link,dict) else None
        if not isinstance(endpoints,list) or len(endpoints)!=2: raise TopologyError("Each link needs exactly two endpoints")
        for endpoint in endpoints:
            if not isinstance(endpoint,str) or ":" not in endpoint: raise TopologyError(f"Invalid endpoint {endpoint}")
            node,iface=endpoint.split(":",1)
            if node not in nodes: raise TopologyError(f"Unknown node {node}")
            if endpoint in used: raise TopologyError(f"Point-to-point interface reused: {endpoint}")
            used.add(endpoint)
    return doc

def canonical_checksum(doc):
    return hashlib.sha256(json.dumps(doc,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def export_containerlab(revision):
    nodes={n.name:{"kind":n.template_version.containerlab_kind,"image":n.published_image.registry_digest if n.published_image else n.properties.get("image","")} for n in revision.nodes.all()}
    links=[{"endpoints":[f"{x.endpoint_a.node.name}:{x.endpoint_a.name}",f"{x.endpoint_b.node.name}:{x.endpoint_b.name}"]} for x in revision.links.select_related("endpoint_a__node","endpoint_b__node")]
    return yaml.safe_dump({"name":revision.lab.name,"topology":{"nodes":nodes,"links":links}},sort_keys=False)

