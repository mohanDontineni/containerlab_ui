from django.db import migrations

TEMPLATES = [
    ("Linux Host", "linux", 4, False, {"icon": "host", "category": "Endpoints", "verified": True}),
    ("FRR Router", "linux", 8, False, {"icon": "router", "category": "Routing", "verified": True, "image": "quay.io/frrouting/frr:10.4.1"}),
    ("Ethernet Switch", "bridge", 16, False, {"icon": "switch", "category": "Switching", "verified": True}),
    ("Linux Firewall", "linux", 8, False, {"icon": "firewall", "category": "Security", "verified": True}),
    ("Nokia SR Linux", "nokia_srlinux", 16, False, {"icon": "router", "category": "Routing", "verified": False}),
    ("Arista cEOS", "arista_ceos", 16, True, {"icon": "switch", "category": "Switching", "verified": False}),
]

def seed(apps, schema_editor):
    DeviceTemplate = apps.get_model("studio", "DeviceTemplate")
    DeviceTemplateVersion = apps.get_model("studio", "DeviceTemplateVersion")
    for name, kind, count, privileged, profile in TEMPLATES:
        template, _ = DeviceTemplate.objects.get_or_create(name=name, defaults={"description": f"{name} topology node", "privileged": privileged})
        version, _ = DeviceTemplateVersion.objects.get_or_create(template=template, version=1, defaults={
            "containerlab_kind": kind, "launch_profile": profile,
            "interface_rules": {"prefix": "eth", "start": 1, "count": count, "management": "eth0"},
            "image_requirements": {"digest_required_for_deploy": True},
            "resource_requirements": {"cpu": "500m", "memory": "512Mi"},
            "console_method": "ssh", "readiness_checks": ["container_running", "device_ready"],
            "configuration_operations": ["startup", "collect"],
            "capabilities": {"console": True, "capture": True, "link_impairment": False, "verified": profile["verified"]},
        })
        if template.active_version_id != version.id:
            template.active_version = version; template.save(update_fields=["active_version"])

class Migration(migrations.Migration):
    dependencies = [("studio", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

