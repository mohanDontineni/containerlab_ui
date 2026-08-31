from rest_framework import serializers
from . import models

class ProjectSerializer(serializers.ModelSerializer):
    class Meta: model=models.Project; fields="__all__"; read_only_fields=("owner",)
class MembershipSerializer(serializers.ModelSerializer):
    class Meta: model=models.ProjectMembership; fields="__all__"
class LabSerializer(serializers.ModelSerializer):
    class Meta: model=models.Lab; fields="__all__"
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
    class Meta: model=models.UploadSession; fields="__all__"; read_only_fields=("owner","received_bytes","received_parts","status","computed_checksum","artifact_destination")
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
    def get_interfaces(self,obj):
        return [{"id":str(interface.id),"name":interface.name} for interface in obj.lab_node.interfaces.all() if not interface.reserved_management]
    class Meta: model=models.DeviceInstance; fields=("id","node_id","name","kind","interfaces","observed_readiness","worker_placement","runtime_resources","console_endpoints")
