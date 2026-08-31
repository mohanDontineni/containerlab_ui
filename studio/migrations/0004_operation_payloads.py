from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("studio", "0003_image_registry_source")]
    operations = [
        migrations.AddField(model_name="operationjob", name="request_payload", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="operationjob", name="result_payload", field=models.JSONField(blank=True, default=dict)),
    ]
