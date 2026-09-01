DISABLED_CONDITION = {
    "active": True,
    "disabled": True,
    "latency_ms": 0,
    "jitter_ms": 0,
    "loss_percent": 0.0,
    "corruption_percent": 0.0,
    "rate_kbps": 0,
}

INTEGER_FIELDS = {
    "latencyMs": ("latency_ms", 2000),
    "jitterMs": ("jitter_ms", 1000),
    "rateKbps": ("rate_kbps", 10_000_000),
}
PERCENT_FIELDS = {
    "lossPercent": "loss_percent",
    "corruptionPercent": "corruption_percent",
}


def normalize_link_properties(value):
    if not isinstance(value, dict):
        raise ValueError("Link properties must be an object")
    state = value.get("adminState", "enabled")
    if state not in ("enabled", "disabled"):
        raise ValueError("Link state must be enabled or disabled")
    result = {"adminState": "disabled"} if state == "disabled" else {}
    for source, (_, maximum) in INTEGER_FIELDS.items():
        number = value.get(source, 0)
        if isinstance(number, bool) or not isinstance(number, int) or number < 0 or number > maximum:
            raise ValueError(f"{source} is outside its supported range")
        if number:
            result[source] = number
    for source in PERCENT_FIELDS:
        number = value.get(source, 0)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or number < 0 or number > 100:
            raise ValueError(f"{source} must be between 0 and 100")
        if number:
            result[source] = float(number)
    if result.get("jitterMs") and not result.get("latencyMs"):
        raise ValueError("jitterMs requires non-zero latencyMs")
    if result.get("rateKbps", 0) and result["rateKbps"] < 64:
        raise ValueError("rateKbps must be zero or at least 64")
    return result


def runtime_condition(properties):
    properties = normalize_link_properties(properties)
    condition = dict(DISABLED_CONDITION)
    condition["disabled"] = properties.get("adminState") == "disabled"
    for source, (target, _) in INTEGER_FIELDS.items():
        condition[target] = properties.get(source, 0)
    for source, target in PERCENT_FIELDS.items():
        condition[target] = properties.get(source, 0.0)
    condition["active"] = condition["disabled"] or any(
        condition[field] for field in ("latency_ms", "jitter_ms", "loss_percent", "corruption_percent", "rate_kbps")
    )
    return condition


def initial_link_conditions(revision):
    conditions = {}
    for link in revision.links.all():
        condition = runtime_condition(link.properties)
        if condition["active"]:
            conditions[str(link.id)] = condition
    return conditions


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
