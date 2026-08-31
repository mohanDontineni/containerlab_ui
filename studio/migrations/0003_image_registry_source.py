from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("studio", "0002_seed_device_templates")]
    operations = [
        migrations.AlterField(model_name="imageartifact", name="upload_session", field=models.OneToOneField(blank=True, null=True, on_delete=models.PROTECT, related_name="artifact", to="studio.uploadsession")),
        migrations.AddField(model_name="imageartifact", name="source_type", field=models.CharField(choices=[("upload", "Upload"), ("registry", "Registry")], default="upload", max_length=16)),
        migrations.AddField(model_name="imageartifact", name="registry_reference", field=models.CharField(blank=True, max_length=512)),
    ]
