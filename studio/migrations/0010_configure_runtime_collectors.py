from django.db import migrations


COLLECTORS = {
    "FRR Router": ["vtysh", "-c", "show running-config"],
    "Linux Firewall": ["sh", "-c", "cat /etc/studio/firewall.sh; printf '\n# --- live nftables ruleset ---\n'; nft list ruleset"],
}


def configure_collectors(apps, schema_editor):
    DeviceTemplateVersion = apps.get_model("studio", "DeviceTemplateVersion")
    for name, command in COLLECTORS.items():
        for version in DeviceTemplateVersion.objects.filter(template__name=name):
            profile = dict(version.launch_profile or {})
            profile["configuration_collect_command"] = command
            version.launch_profile = profile
            operations = list(version.configuration_operations or [])
            if "collect" not in operations:
                operations.append("collect")
            version.configuration_operations = operations
            version.save(update_fields=["launch_profile", "configuration_operations"])


def unconfigure_collectors(apps, schema_editor):
    DeviceTemplateVersion = apps.get_model("studio", "DeviceTemplateVersion")
    for name in COLLECTORS:
        for version in DeviceTemplateVersion.objects.filter(template__name=name):
            profile = dict(version.launch_profile or {})
            profile.pop("configuration_collect_command", None)
            version.launch_profile = profile
            version.save(update_fields=["launch_profile"])


class Migration(migrations.Migration):
    dependencies = [("studio", "0009_configure_linux_firewall")]
    operations = [migrations.RunPython(configure_collectors, unconfigure_collectors)]
