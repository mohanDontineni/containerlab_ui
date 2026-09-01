from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("studio", "0014_runtime_removal_state")]
    operations = [
        migrations.AddField(model_name="lab", name="edit_lock_expires_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="lab", name="edit_lock_token_hash", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="lab", name="edit_lock_owner", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="topology_edit_locks", to=settings.AUTH_USER_MODEL)),
    ]
