import re
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
    def validate_folder(self,value):
        if value and value.deleted_at: raise serializers.ValidationError("Choose an active lab folder.")
        return value
    def validate(self,attrs):
        project=attrs.get("project",self.instance.project if self.instance else None);name=attrs.get("name",self.instance.name if self.instance else None)
        folder=attrs.get("folder",self.instance.folder if self.instance else None)
        if folder and project and folder.project_id!=project.id: raise serializers.ValidationError({"folder":"Choose a folder in the lab project."})
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
    active_version_number=serializers.IntegerField(source="active_version.version",read_only=True)
    active_profile=serializers.SerializerMethodField()
    version_count=serializers.IntegerField(read_only=True)
    def get_active_profile(self,obj) -> dict | None:
        version=obj.active_version
        if not version: return None
        return {"id":str(version.id),"version":version.version,"containerlab_kind":version.containerlab_kind,
            "launch_profile":version.launch_profile,"interface_rules":version.interface_rules,
            "image_requirements":version.image_requirements,"resource_requirements":version.resource_requirements,
            "console_method":version.console_method,"readiness_checks":version.readiness_checks,
            "configuration_operations":version.configuration_operations,"capabilities":version.capabilities}
    class Meta:
        model=models.DeviceTemplate
        fields=("id","name","description","privileged","active_version","active_version_number","active_profile","version_count","created_at","updated_at")
        read_only_fields=fields

class ManagedTemplateSerializer(serializers.Serializer):
    name=serializers.CharField(max_length=120)
    description=serializers.CharField(required=False,allow_blank=True,max_length=2000)
    privileged=serializers.BooleanField(default=False)
    containerlab_kind=serializers.RegexField(r"^[a-z][a-z0-9_-]{0,79}$")
    category=serializers.ChoiceField(choices=("Routing","Switching","Security","Endpoints","Other"),default="Other")
    icon=serializers.ChoiceField(choices=("router","switch","firewall","host"),default="host")
    interface_prefix=serializers.RegexField(r"^[A-Za-z][A-Za-z0-9_.-]{0,15}$",default="eth")
    interface_start=serializers.IntegerField(min_value=0,max_value=63,default=1)
    interface_count=serializers.IntegerField(min_value=1,max_value=64,default=4)
    management_interface=serializers.RegexField(r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$",default="eth0")
    cpu=serializers.RegexField(r"^[1-9][0-9]{1,4}m$",default="500m")
    memory=serializers.RegexField(r"^[1-9][0-9]{1,4}(Mi|Gi)$",default="512Mi")
    console_method=serializers.ChoiceField(choices=("shell","ssh","telnet"),default="shell")
    configuration_profile=serializers.ChoiceField(choices=("none","frr","nftables"),default="none")
    verified=serializers.BooleanField(default=False)
    def validate(self,attrs):
        start=attrs["interface_start"];count=attrs["interface_count"]
        if start+count>64: raise serializers.ValidationError({"interface_count":"The last generated interface index cannot exceed 63."})
        generated={f'{attrs["interface_prefix"]}{index}' for index in range(start,start+count)}
        if attrs["management_interface"] in generated:
            raise serializers.ValidationError({"management_interface":"The management interface must not overlap a generated data interface."})
        memory=int(re.match(r"^[0-9]+",attrs["memory"]).group())*(1024 if attrs["memory"].endswith("Gi") else 1)
        if not 64<=memory<=32768: raise serializers.ValidationError({"memory":"Memory must be between 64Mi and 32Gi."})
        cpu=int(attrs["cpu"][:-1])
        if not 50<=cpu<=16000: raise serializers.ValidationError({"cpu":"CPU must be between 50m and 16000m."})
        if attrs["configuration_profile"] in ("frr","nftables") and attrs["containerlab_kind"]!="linux":
            raise serializers.ValidationError({"configuration_profile":"Verified configuration presets require the linux kind."})
        return attrs
class DeploymentSerializer(serializers.ModelSerializer):
    class Meta: model=models.LabDeployment; fields="__all__"; read_only_fields=("observed_state","resource_identities","error_details")
class OperationSerializer(serializers.ModelSerializer):
    class Meta: model=models.OperationJob; fields="__all__"
class DeploymentScheduleSerializer(serializers.ModelSerializer):
    created_by_username=serializers.CharField(source="created_by.username",read_only=True)
    class Meta: model=models.DeploymentSchedule; fields=("id","deployment","created_by_username","action","execute_at","status","operation","cancelled_at","created_at","updated_at");read_only_fields=fields

class DeviceInstanceSerializer(serializers.ModelSerializer):
    name=serializers.CharField(source="lab_node.name",read_only=True)
    kind=serializers.CharField(source="lab_node.template_version.containerlab_kind",read_only=True)
    template_name=serializers.CharField(source="lab_node.template_version.template.name",read_only=True)
    position=serializers.JSONField(source="lab_node.position",read_only=True)
    node_id=serializers.UUIDField(source="lab_node_id",read_only=True)
    interfaces=serializers.SerializerMethodField()
    configuration_collection_supported=serializers.SerializerMethodField()
    resource_profile=serializers.SerializerMethodField()
    startup_order=serializers.SerializerMethodField()
    def get_interfaces(self,obj):
        return [{"id":str(interface.id),"name":interface.name} for interface in obj.lab_node.interfaces.all() if not interface.reserved_management]
    def get_configuration_collection_supported(self,obj):
        return bool(obj.lab_node.template_version.launch_profile.get("configuration_collect_command"))
    def get_resource_profile(self,obj):
        requirements=obj.lab_node.template_version.resource_requirements or {}
        return {"cpu":requirements.get("cpu"),"memory":requirements.get("memory"),
            "template_version":obj.lab_node.template_version.version}
    def get_startup_order(self,obj): return obj.lab_node.properties.get("startupOrder")
    class Meta: model=models.DeviceInstance; fields=("id","node_id","name","kind","template_name","position","interfaces","configuration_collection_supported","resource_profile","startup_order","observed_readiness","worker_placement","runtime_resources","console_endpoints")
