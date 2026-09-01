from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0015_lab_edit_lease")]
    operations = [migrations.AddField(model_name="user", name="must_change_password", field=models.BooleanField(default=False))]
