import uuid
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: abstract = True

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timezone = models.CharField(max_length=64, default="UTC")
    must_change_password = models.BooleanField(default=False)

class Project(UUIDModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_projects")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True)
    quotas = models.JSONField(default=dict, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["owner", "name"], condition=Q(deleted_at__isnull=True), name="unique_active_project_name_per_owner")]
    def __str__(self): return self.name

class ProjectMembership(UUIDModel):
    class Role(models.TextChoices): ADMIN="administrator"; EDITOR="editor"; VIEWER="viewer"
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="project_memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "user"], name="unique_project_membership")]

class LabFolder(UUIDModel):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="lab_folders")
    parent = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="children")
    name = models.CharField(max_length=120)
    deleted_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], condition=Q(parent__isnull=True, deleted_at__isnull=True), name="unique_active_root_lab_folder"),
            models.UniqueConstraint(fields=["project", "parent", "name"], condition=Q(parent__isnull=False, deleted_at__isnull=True), name="unique_active_nested_lab_folder"),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.parent_id:
            if self.parent_id == self.id: raise ValidationError({"parent": "A folder cannot contain itself."})
            if self.parent.project_id != self.project_id or self.parent.deleted_at:
                raise ValidationError({"parent": "Choose an active folder in the same project."})
            ancestor=self.parent;depth=1
            while ancestor:
                if ancestor.id == self.id: raise ValidationError({"parent": "A folder cannot be moved inside one of its descendants."})
                depth += 1
                if depth > 8: raise ValidationError({"parent": "Lab folders support a maximum depth of 8 levels."})
                ancestor=ancestor.parent

    @property
    def path(self):
        parts=[self.name];ancestor=self.parent
        while ancestor: parts.append(ancestor.name);ancestor=ancestor.parent
        return " / ".join(reversed(parts))
    def __str__(self): return f"{self.project.name} · {self.path}"

class UploadSession(UUIDModel):
    class Status(models.TextChoices): ACTIVE="active"; COMPLETE="complete"; CANCELLED="cancelled"; EXPIRED="expired"; FAILED="failed"
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="upload_sessions")
    original_filename = models.CharField(max_length=255)
    expected_size = models.BigIntegerField(validators=[MinValueValidator(1)])
    received_bytes = models.BigIntegerField(default=0)
    received_parts = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    expected_checksum = models.CharField(max_length=64, blank=True)
    license_acknowledged = models.BooleanField(default=False)
    computed_checksum = models.CharField(max_length=64, blank=True)
    artifact_destination = models.CharField(max_length=512)

class ImageArtifact(UUIDModel):
    class Source(models.TextChoices): UPLOAD="upload"; REGISTRY="registry"
    class Validation(models.TextChoices): QUARANTINED="quarantined"; INSPECTING="inspecting"; VALIDATED="validated"; UNSUPPORTED="unsupported"; FAILED="failed"
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="image_artifacts")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    upload_session = models.OneToOneField(UploadSession, on_delete=models.PROTECT, related_name="artifact", null=True, blank=True)
    source_type = models.CharField(max_length=16, choices=Source.choices, default=Source.UPLOAD)
    registry_reference = models.CharField(max_length=512, blank=True)
    original_filename = models.CharField(max_length=255)
    detected_format = models.CharField(max_length=40)
    byte_size = models.BigIntegerField()
    checksum = models.CharField(max_length=64)
    vendor = models.CharField(max_length=80, blank=True)
    category = models.CharField(max_length=80, blank=True)
    version = models.CharField(max_length=80, blank=True)
    architecture = models.CharField(max_length=20, blank=True)
    storage_reference = models.CharField(max_length=512)
    license_acknowledged = models.BooleanField(default=False)
    inspection_result = models.JSONField(default=dict)
    validation_status = models.CharField(max_length=20, choices=Validation.choices, default=Validation.QUARANTINED)
    deleted_at = models.DateTimeField(null=True, blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["project","checksum"],condition=Q(deleted_at__isnull=True),name="unique_active_image_checksum_per_project")]

class ImageBuild(UUIDModel):
    artifact = models.ForeignKey(ImageArtifact, on_delete=models.PROTECT, related_name="builds")
    recipe_version = models.CharField(max_length=80)
    job_identity = models.CharField(max_length=253, unique=True)
    status = models.CharField(max_length=24, default="pending")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    log_reference = models.CharField(max_length=512, blank=True)
    failure_details = models.JSONField(default=dict)

class PublishedImage(UUIDModel):
    artifact = models.ForeignKey(ImageArtifact, on_delete=models.PROTECT, related_name="published_images")
    build = models.ForeignKey(ImageBuild, on_delete=models.PROTECT, null=True, blank=True)
    registry_digest = models.CharField(max_length=255, unique=True)
    repository = models.CharField(max_length=255)
    architecture = models.CharField(max_length=20)
    compatibility_result = models.JSONField(default=dict)
    lifecycle_status = models.CharField(max_length=24, default="unverified")

class ImageCredentialReference(UUIDModel):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    name = models.CharField(max_length=120)
    secret_name = models.CharField(max_length=253)
    registry_host = models.CharField(max_length=253)
    class Meta: constraints=[models.UniqueConstraint(fields=["project", "name"], name="unique_credential_ref")]

class DeviceTemplate(UUIDModel):
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    privileged = models.BooleanField(default=False)
    active_version = models.ForeignKey("DeviceTemplateVersion", on_delete=models.PROTECT, null=True, blank=True, related_name="+")

class DeviceTemplateVersion(UUIDModel):
    template = models.ForeignKey(DeviceTemplate, on_delete=models.PROTECT, related_name="versions")
    version = models.PositiveIntegerField()
    containerlab_kind = models.CharField(max_length=80)
    launch_profile = models.JSONField(default=dict)
    interface_rules = models.JSONField(default=dict)
    image_requirements = models.JSONField(default=dict)
    resource_requirements = models.JSONField(default=dict)
    console_method = models.CharField(max_length=20, default="ssh")
    readiness_checks = models.JSONField(default=list)
    configuration_operations = models.JSONField(default=list)
    capabilities = models.JSONField(default=dict)
    class Meta: constraints=[models.UniqueConstraint(fields=["template", "version"], name="unique_template_version")]

class Lab(UUIDModel):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="labs")
    folder = models.ForeignKey(LabFolder, on_delete=models.PROTECT, related_name="labs", null=True, blank=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True)
    current_draft = models.ForeignKey("LabRevision", on_delete=models.SET_NULL, null=True, blank=True, related_name="draft_for_labs")
    edit_lock_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="topology_edit_locks")
    edit_lock_token_hash = models.CharField(max_length=64, blank=True)
    edit_lock_expires_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["project", "name"], condition=Q(deleted_at__isnull=True), name="unique_active_lab_name_per_project")]
    def __str__(self): return self.name

class LabRevision(UUIDModel):
    lab = models.ForeignKey(Lab, on_delete=models.PROTECT, related_name="revisions")
    revision_number = models.PositiveIntegerField()
    topology_checksum = models.CharField(max_length=64)
    immutable = models.BooleanField(default=False)
    canvas_layout = models.JSONField(default=dict)
    annotations = models.JSONField(default=list)
    edit_version = models.PositiveIntegerField(default=1)
    class Meta: constraints=[models.UniqueConstraint(fields=["lab", "revision_number"], name="unique_lab_revision")]

class LabNode(UUIDModel):
    revision = models.ForeignKey(LabRevision, on_delete=models.CASCADE, related_name="nodes")
    name = models.CharField(max_length=63)
    template_version = models.ForeignKey(DeviceTemplateVersion, on_delete=models.PROTECT)
    published_image = models.ForeignKey(PublishedImage, on_delete=models.PROTECT, null=True, blank=True)
    position = models.JSONField(default=dict)
    properties = models.JSONField(default=dict)
    startup_configuration = models.ForeignKey("ConfigurationVersion", on_delete=models.PROTECT, null=True, blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["revision", "name"], name="unique_node_name_per_revision")]

class LabInterface(UUIDModel):
    node = models.ForeignKey(LabNode, on_delete=models.CASCADE, related_name="interfaces")
    name = models.CharField(max_length=64)
    shared_medium = models.BooleanField(default=False)
    reserved_management = models.BooleanField(default=False)
    class Meta: constraints=[models.UniqueConstraint(fields=["node", "name"], name="unique_interface_per_node")]

class LabLink(UUIDModel):
    revision = models.ForeignKey(LabRevision, on_delete=models.CASCADE, related_name="links")
    endpoint_a = models.ForeignKey(LabInterface, on_delete=models.PROTECT, related_name="links_as_a")
    endpoint_b = models.ForeignKey(LabInterface, on_delete=models.PROTECT, related_name="links_as_b")
    label = models.CharField(max_length=120, blank=True)
    properties = models.JSONField(default=dict)
    class Meta:
        constraints=[models.CheckConstraint(condition=~Q(endpoint_a=models.F("endpoint_b")), name="link_distinct_endpoints")]

class LabDeployment(UUIDModel):
    class State(models.TextChoices): PENDING="pending"; DEPLOYING="deploying"; RUNNING="running"; DEGRADED="degraded"; FAILED="failed"; STOPPED="stopped"; DELETING="deleting"; REMOVED="removed"
    revision = models.ForeignKey(LabRevision, on_delete=models.PROTECT, related_name="deployments")
    requested_desired_state = models.CharField(max_length=20, default="running")
    observed_state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING)
    cluster_identity = models.CharField(max_length=255)
    namespace = models.CharField(max_length=253, unique=True)
    resource_identities = models.JSONField(default=dict)
    runtime_version = models.CharField(max_length=40)
    last_reconciliation = models.DateTimeField(null=True, blank=True)
    error_details = models.JSONField(default=dict)
    removed_at = models.DateTimeField(null=True, blank=True)

class DeviceInstance(UUIDModel):
    deployment = models.ForeignKey(LabDeployment, on_delete=models.CASCADE, related_name="devices")
    lab_node = models.ForeignKey(LabNode, on_delete=models.PROTECT)
    runtime_resources = models.JSONField(default=dict)
    observed_readiness = models.CharField(max_length=24, default="unknown")
    worker_placement = models.CharField(max_length=253, blank=True)
    console_endpoints = models.JSONField(default=dict)
    class Meta: constraints=[models.UniqueConstraint(fields=["deployment", "lab_node"], name="unique_deployment_node")]

class OperationJob(UUIDModel):
    deployment = models.ForeignKey(LabDeployment, on_delete=models.PROTECT, related_name="operations", null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    operation_type = models.CharField(max_length=40)
    target_id = models.UUIDField()
    idempotency_key = models.CharField(max_length=128)
    state = models.CharField(max_length=24, default="accepted")
    attempts = models.PositiveIntegerField(default=0)
    progress = models.PositiveSmallIntegerField(default=0)
    heartbeat = models.DateTimeField(null=True, blank=True)
    error_details = models.JSONField(default=dict)
    request_payload = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["owner", "idempotency_key"], name="unique_owner_idempotency"),
            models.UniqueConstraint(fields=["target_id", "operation_type"], condition=Q(state__in=["accepted","scheduled","started"]), name="one_active_target_operation")]

class ConsoleSession(UUIDModel):
    device = models.ForeignKey(DeviceInstance, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    read_only = models.BooleanField(default=False)

class CaptureSession(UUIDModel):
    deployment = models.ForeignKey(LabDeployment, on_delete=models.CASCADE)
    interface = models.ForeignKey(LabInterface, on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField(max_length=24, default="pending")
    expires_at = models.DateTimeField()
    artifact_reference = models.CharField(max_length=512, blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["deployment","interface"],condition=Q(status__in=["scheduled","capturing"]),name="one_active_interface_capture")]

class ConfigurationVersion(UUIDModel):
    project = models.ForeignKey(Project, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    version = models.PositiveIntegerField()
    encrypted_content = models.BinaryField()
    checksum = models.CharField(max_length=64)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    class Meta: constraints=[models.UniqueConstraint(fields=["project", "name", "version"], name="unique_config_version")]

class LabArtifact(UUIDModel):
    deployment = models.ForeignKey(LabDeployment, on_delete=models.PROTECT, related_name="artifacts")
    artifact_type = models.CharField(max_length=32)
    storage_reference = models.CharField(max_length=512)
    checksum = models.CharField(max_length=64)
    retention_until = models.DateTimeField(null=True, blank=True)

class AuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    project = models.ForeignKey(Project, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=80, db_index=True)
    target_type = models.CharField(max_length=80)
    target_id = models.UUIDField(null=True)
    correlation_id = models.CharField(max_length=128)
    metadata = models.JSONField(default=dict)
