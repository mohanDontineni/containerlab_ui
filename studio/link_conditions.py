DISABLED_CONDITION = {
    "active": True,
    "disabled": True,
    "latency_ms": 0,
    "jitter_ms": 0,
    "loss_percent": 0.0,
    "corruption_percent": 0.0,
    "rate_kbps": 0,
}


def normalize_link_properties(value):
    if not isinstance(value, dict):
        raise ValueError("Link properties must be an object")
    state = value.get("adminState", "enabled")
    if state not in ("enabled", "disabled"):
        raise ValueError("Link state must be enabled or disabled")
    return {"adminState": "disabled"} if state == "disabled" else {}


def initial_link_conditions(revision):
    return {
        str(link.id): dict(DISABLED_CONDITION)
        for link in revision.links.all()
        if link.properties.get("adminState") == "disabled"
    }


def runtime_endpoint_signature(deployment, link):
    devices = {
        device.lab_node_id: device
        for device in deployment.devices.filter(lab_node_id__in=(link.endpoint_a.node_id, link.endpoint_b.node_id))
    }
    signature = []
    for node_id in (link.endpoint_a.node_id, link.endpoint_b.node_id):
        device = devices.get(node_id)
        if not device or device.observed_readiness != "ready":
            return None
        identity = device.runtime_resources.get("pod_uid") or device.runtime_resources.get("pod")
        if not identity:
            return None
        signature.append(str(identity))
    return signature
