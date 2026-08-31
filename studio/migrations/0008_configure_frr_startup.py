from django.db import migrations

FRR_DAEMONS="""zebra=yes
bgpd=yes
staticd=yes
vtysh_enable=yes
MAX_FDS=1024
"""

def configure_frr(apps,schema_editor):
    version=apps.get_model("studio","DeviceTemplateVersion").objects.filter(template__name="FRR Router",version=1).first()
    if not version: return
    profile=dict(version.launch_profile)
    profile.update({"startup_config_target":"/etc/frr/frr.conf","auxiliary_config_files":[{
        "key":"daemons","launcher_path":"/clabernetes/studio/daemons","target":"/etc/frr/daemons","content":FRR_DAEMONS}]})
    version.launch_profile=profile;version.save(update_fields=["launch_profile"])

def unconfigure_frr(apps,schema_editor):
    version=apps.get_model("studio","DeviceTemplateVersion").objects.filter(template__name="FRR Router",version=1).first()
    if not version: return
    profile=dict(version.launch_profile);profile.pop("startup_config_target",None);profile.pop("auxiliary_config_files",None)
    version.launch_profile=profile;version.save(update_fields=["launch_profile"])

class Migration(migrations.Migration):
    dependencies=[("studio","0007_uploadsession_license_acknowledged")]
    operations=[migrations.RunPython(configure_frr,unconfigure_frr)]
