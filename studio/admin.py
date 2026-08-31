from django.contrib import admin
from . import models

for model in (models.Project, models.ProjectMembership, models.ImageArtifact, models.PublishedImage,
              models.DeviceTemplate, models.DeviceTemplateVersion, models.Lab, models.LabRevision,
              models.LabDeployment, models.OperationJob, models.AuditEvent):
    admin.site.register(model)

