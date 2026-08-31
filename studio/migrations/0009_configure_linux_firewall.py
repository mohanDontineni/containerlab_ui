from django.db import migrations


def configure_firewall(apps, schema_editor):
    DeviceTemplateVersion = apps.get_model("studio", "DeviceTemplateVersion")
    versions = DeviceTemplateVersion.objects.filter(template__name="Linux Firewall")
    for version in versions:
        profile = dict(version.launch_profile or {})
        profile.update({
            "icon": "firewall",
            "category": "Security",
            "verified": True,
            "configuration_language": "shell",
            "startup_config_target": "/etc/studio/firewall.sh",
            "startup_config_required": True,
            "required_interfaces": 2,
        })
        version.launch_profile = profile
        version.save(update_fields=["launch_profile"])


def unconfigure_firewall(apps, schema_editor):
    DeviceTemplateVersion = apps.get_model("studio", "DeviceTemplateVersion")
    versions = DeviceTemplateVersion.objects.filter(template__name="Linux Firewall")
    for version in versions:
        profile = dict(version.launch_profile or {})
        for key in ("configuration_language", "startup_config_target", "startup_config_required", "required_interfaces"):
            profile.pop(key, None)
        version.launch_profile = profile
        version.save(update_fields=["launch_profile"])


class Migration(migrations.Migration):
    dependencies = [("studio", "0008_configure_frr_startup")]
    operations = [migrations.RunPython(configure_firewall, unconfigure_firewall)]
