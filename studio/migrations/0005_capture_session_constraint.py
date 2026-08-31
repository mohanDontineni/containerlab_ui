from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("studio", "0004_operation_payloads")]
    operations = [
        migrations.AddConstraint(
            model_name="capturesession",
            constraint=models.UniqueConstraint(
                fields=("deployment", "interface"),
                condition=models.Q(status__in=("scheduled", "capturing")),
                name="one_active_interface_capture",
            ),
        ),
    ]
