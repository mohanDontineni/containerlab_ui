from rest_framework import serializers
from . import models

class ProjectSerializer(serializers.ModelSerializer):
    class Meta: model=models.Project; fields="__all__"; read_only_fields=("owner","deleted_at"); validators=[]
    def validate(self,attrs):
        owner=self.instance.owner if self.instance else self.context["request"].user
        name=attrs.get("name",self.instance.name if self.instance else None)
        if name:
            matches=models.Project.objects.filter(owner=owner,name=name,deleted_at__isnull=True)
            if self.instance: matches=matches.exclude(pk=self.instance.pk)
            if matches.exists(): raise serializers.ValidationError({"name":"You already have an active project with this name."})
        return attrs
class MembershipSerializer(serializers.ModelSerializer):
    username=serializers.CharField(source="user.username",read_only=True)
    display_name=serializers.SerializerMethodField()
    def get_display_name(self,obj): return obj.user.get_full_name() or obj.user.username
    class Meta: model=models.ProjectMembership; fields=("id","project","user","username","display_name","role","created_at","updated_at"); read_only_fields=("project","user")
class LabSerializer(serializers.ModelSerializer):
    class Meta: model=models.Lab; fields="__all__"; read_only_fields=("current_draft","deleted_at"); validators=[]
    def validate_project(self,value):
        if value.deleted_at: raise serializers.ValidationError("This project has been retired.")
        if self.instance and value.id!=self.instance.project_id:
            raise serializers.ValidationError("A lab cannot be moved between projects.")
        return value
    def validate(self,attrs):
        project=attrs.get("project",self.instance.project if self.instance else None);name=attrs.get("name",self.instance.name if self.instance else None)
        if project and name:
            matches=models.Lab.objects.filter(project=project,name=name,deleted_at__isnull=True)
            if self.instance: matches=matches.exclude(pk=self.instance.pk)
            if matches.exists(): raise serializers.ValidationError({"name":"An active lab with this name already exists in the project."})
        return attrs
class LabRevisionSerializer(serializers.ModelSerializer):
    class Meta: model=models.LabRevision; fields="__all__"; read_only_fields=("topology_checksum",)
class LabNodeSerializer(serializers.ModelSerializer):
    class Meta: model=models.LabNode; fields="__all__"
class LabInterfaceSerializer(serializers.ModelSerializer):
    class Meta: model=models.LabInterface; fields="__all__"
class LabLinkSerializer(serializers.ModelSerializer):
    class Meta: model=models.LabLink; fields="__all__"
    def validate(self, attrs):
        a,b=attrs["endpoint_a"],attrs["endpoint_b"]
        if a.node.revision_id != attrs["revision"].id or b.node.revision_id != attrs["revision"].id: raise serializers.ValidationError("Both endpoints must belong to the revision.")
        if a.reserved_management or b.reserved_management: raise serializers.ValidationError("Reserved management interfaces cannot be linked.")
        for endpoint in (a,b):
            if not endpoint.shared_medium and (endpoint.links_as_a.exists() or endpoint.links_as_b.exists()): raise serializers.ValidationError({"interface": f"{endpoint.name} already has a peer"})
        return attrs
class UploadSessionSerializer(serializers.ModelSerializer):
    expected_checksum=serializers.CharField(required=False,allow_blank=True,max_length=64)
    def validate_expected_checksum(self,value):
        if value and (len(value)!=64 or any(character not in "0123456789abcdefABCDEF" for character in value)):
            raise serializers.ValidationError("Expected checksum must be a 64-character SHA-256 hex digest.")
        return value.lower()
    def validate_project(self,value):
        if value.deleted_at: raise serializers.ValidationError("This project has been retired.")
        return value
    class Meta: model=models.UploadSession; fields="__all__"; read_only_fields=("owner","received_bytes","received_parts","expires_at","status","computed_checksum","artifact_destination")
class ImageArtifactSerializer(serializers.ModelSerializer):
    class Meta: model=models.ImageArtifact; fields="__all__"
class PublishedImageSerializer(serializers.ModelSerializer):
    class Meta: model=models.PublishedImage; fields="__all__"
class DeviceTemplateSerializer(serializers.ModelSerializer):
    class Meta: model=models.DeviceTemplate; fields="__all__"
class DeploymentSerializer(serializers.ModelSerializer):
    class Meta: model=models.LabDeployment; fields="__all__"; read_only_fields=("observed_state","resource_identities","error_details")
class OperationSerializer(serializers.ModelSerializer):
    class Meta: model=models.OperationJob; fields="__all__"

class DeviceInstanceSerializer(serializers.ModelSerializer):
    name=serializers.CharField(source="lab_node.name",read_only=True)
    kind=serializers.CharField(source="lab_node.template_version.containerlab_kind",read_only=True)
    node_id=serializers.UUIDField(source="lab_node_id",read_only=True)
    interfaces=serializers.SerializerMethodField()
    configuration_collection_supported=serializers.SerializerMethodField()
    def get_interfaces(self,obj):
        return [{"id":str(interface.id),"name":interface.name} for interface in obj.lab_node.interfaces.all() if not interface.reserved_management]
    def get_configuration_collection_supported(self,obj):
        return bool(obj.lab_node.template_version.launch_profile.get("configuration_collect_command"))
    class Meta: model=models.DeviceInstance; fields=("id","node_id","name","kind","interfaces","configuration_collection_supported","observed_readiness","worker_placement","runtime_resources","console_endpoints")
