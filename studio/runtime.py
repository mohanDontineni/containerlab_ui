from dataclasses import dataclass
import base64
import binascii
import shlex
import struct
import time
from pathlib import Path
from django.conf import settings
from django.db.models import Q
import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.stream import stream
from .configurations import decrypt_configuration

API_GROUP="c9s.run"; API_VERSION="v1alpha1"; RUNTIME_VERSION="0.8.0"
DISABLE_DEPLOYMENTS_LABEL="c9s.run/disableDeployments"

class CapabilityError(RuntimeError): pass
PCAP_MAGICS=(b"\xd4\xc3\xb2\xa1",b"\xa1\xb2\xc3\xd4",b"\x4d\x3c\xb2\xa1",b"\xa1\xb2\x3c\x4d")
CAPTURE_STOP_MARKER=b"CLABSTUDIOPCAPSTOP"
CAPTURE_STOP_DESTINATION=bytes.fromhex("ff020000000000000000000000000114")

def strip_capture_stop_packets(payload):
    """Remove locally generated stop frames from a classic PCAP stream."""
    if len(payload)<24 or payload[:4] not in PCAP_MAGICS: raise CapabilityError("Launcher did not return a valid PCAP file")
    endian="<" if payload[:4] in (b"\xd4\xc3\xb2\xa1",b"\x4d\x3c\xb2\xa1") else ">"
    cleaned=bytearray(payload[:24]); offset=24
    while offset<len(payload):
        if offset+16>len(payload): raise CapabilityError("Launcher returned a truncated PCAP record")
        captured_length=struct.unpack_from(f"{endian}I",payload,offset+8)[0]
        record_end=offset+16+captured_length
        if record_end>len(payload): raise CapabilityError("Launcher returned a truncated PCAP record")
        record=payload[offset:record_end]
        frame=record[16:]
        if CAPTURE_STOP_MARKER not in frame and CAPTURE_STOP_DESTINATION not in frame: cleaned.extend(record)
        offset=record_end
    return bytes(cleaned)
@dataclass(frozen=True)
class Plan: namespace:str; topology_name:str; manifest:dict; config_maps:tuple=()

class ClabernetesAdapter:
    capabilities={"deploy_lab":"supported","get_observed_state":"supported","delete_runtime":"supported","restart_device":"supported",
        "stop_device":"supported","start_device":"supported","resolve_console_target":"supported","start_capture":"experimental",
        "stop_lab":"delete_and_redeploy","set_link_condition":"supported","collect_configuration":"template_dependent"}
    def __init__(self, custom_api=None, core_api=None, batch_api=None, apps_api=None):
        if custom_api is None:
            try: config.load_incluster_config()
            except config.ConfigException: config.load_kube_config()
        self.custom=custom_api or client.CustomObjectsApi(); self.core=core_api or client.CoreV1Api(); self.batch=batch_api or client.BatchV1Api();self.apps=apps_api or client.AppsV1Api()
    @staticmethod
    def validate_topology(revision):
        errors=[]
        for node in revision.nodes.select_related("template_version","published_image").prefetch_related("interfaces"):
            profile=node.template_version.launch_profile or {}
            if not node.published_image: errors.append(f"{node.name}: no immutable published image")
            elif "@sha256:" not in node.published_image.registry_digest:
                publication=node.published_image.compatibility_result
                expected=f":sha256-{node.published_image.artifact.checksum}"
                if publication.get("publication_mode")!="node-containerd" or not node.published_image.registry_digest.endswith(expected):
                    errors.append(f"{node.name}: image is not content-addressed")
            if getattr(node,"startup_configuration_id",None) and not profile.get("startup_config_target"):
                errors.append(f"{node.name}: template does not support startup configuration")
            if profile.get("startup_config_required") and not getattr(node,"startup_configuration_id",None):
                errors.append(f"{node.name}: startup configuration is required")
            required_interfaces=int(profile.get("required_interfaces",0))
            if required_interfaces and node.interfaces.filter(reserved_management=False).count()<required_interfaces:
                errors.append(f"{node.name}: requires at least {required_interfaces} data-plane interfaces")
        return errors
    def publish_local_image(self,artifact,build):
        path=Path(artifact.storage_reference)
        if not path.is_file(): raise CapabilityError("The validated archive is no longer available")
        hasher=__import__("hashlib").sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda:source.read(4*1024*1024),b""): hasher.update(chunk)
        digest=hasher.hexdigest()
        if digest!=artifact.checksum: raise CapabilityError("Archive checksum changed after inspection")
        repository=f"containerlab.local/studio/{artifact.project_id.hex}/{artifact.id.hex}"
        reference=f"{repository}:sha256-{artifact.checksum}"
        source=artifact.inspection_result.get("import_source")
        if not source: raise CapabilityError("Archive inspection did not identify an import source")
        archive=f"/artifacts/{path.relative_to(settings.MEDIA_ROOT)}"
        staged_archive="/work/image-archive.tar"
        command=f"set -eu; ctr -a /run/containerd/containerd.sock -n k8s.io images import --digests '{staged_archive}'; ctr -a /run/containerd/containerd.sock -n k8s.io images tag --force '{source}' '{reference}'; ctr -a /run/containerd/containerd.sock -n k8s.io images label '{reference}' io.cri-containerd.image=managed"
        pod=client.V1PodTemplateSpec(metadata=client.V1ObjectMeta(labels={"studio.containerlab.io/image-build":str(build.id)}),spec=client.V1PodSpec(
            restart_policy="Never",service_account_name="containerlab-studio-reconciler",node_selector=settings.PUBLISHER_NODE_SELECTOR,
            security_context=client.V1PodSecurityContext(run_as_user=0,run_as_non_root=False,fs_group=10001,seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault")),
            init_containers=[client.V1Container(name="stage-archive",image=settings.PUBLISHER_IMAGE,command=["sh","-c",f"cp '{archive}' '{staged_archive}'"],security_context=client.V1SecurityContext(run_as_user=10001,run_as_non_root=True,allow_privilege_escalation=False,capabilities=client.V1Capabilities(drop=["ALL"])),volume_mounts=[client.V1VolumeMount(name="artifacts",mount_path="/artifacts",read_only=True),client.V1VolumeMount(name="work",mount_path="/work")])],
            containers=[client.V1Container(name="publisher",image=settings.PUBLISHER_IMAGE,command=["sh","-c",command],security_context=client.V1SecurityContext(allow_privilege_escalation=False,capabilities=client.V1Capabilities(drop=["ALL"])),volume_mounts=[client.V1VolumeMount(name="work",mount_path="/work",read_only=True),client.V1VolumeMount(name="containerd",mount_path="/run/containerd/containerd.sock")])],
            volumes=[client.V1Volume(name="artifacts",persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name="containerlab-studio-artifacts",read_only=True)),client.V1Volume(name="work",empty_dir=client.V1EmptyDirVolumeSource()),client.V1Volume(name="containerd",host_path=client.V1HostPathVolumeSource(path="/run/containerd/containerd.sock",type="Socket"))]))
        body=client.V1Job(metadata=client.V1ObjectMeta(name=build.job_identity,namespace=settings.STUDIO_NAMESPACE),spec=client.V1JobSpec(backoff_limit=0,ttl_seconds_after_finished=600,template=pod))
        self.batch.create_namespaced_job(settings.STUDIO_NAMESPACE,body)
        deadline=time.monotonic()+settings.PUBLISHER_TIMEOUT_SECONDS
        while time.monotonic()<deadline:
            status=self.batch.read_namespaced_job_status(build.job_identity,settings.STUDIO_NAMESPACE).status
            if status.succeeded: break
            if status.failed: raise CapabilityError("Node image publication job failed")
            time.sleep(2)
        else: raise CapabilityError("Node image publication timed out")
        pods=self.core.list_namespaced_pod(settings.STUDIO_NAMESPACE,label_selector=f"job-name={build.job_identity}").items
        logs=self.core.read_namespaced_pod_log(pods[0].metadata.name,settings.STUDIO_NAMESPACE,tail_lines=200) if pods else ""
        return {"reference":reference,"repository":repository,"archive_checksum":digest,"logs":logs[-12000:],"publication_mode":"node-containerd"}
    def plan_deployment(self,deployment):
        revision=deployment.revision
        nodes={};config_maps=[];files_from_config_map={}
        for node in revision.nodes.select_related("template_version","published_image","startup_configuration"):
            definition={"kind":node.template_version.containerlab_kind,"image":node.published_image.registry_digest}
            profile=getattr(node.template_version,"launch_profile",{})
            if getattr(node,"startup_configuration_id",None):
                config_name=f"studio-startup-{node.id.hex[:20]}";launcher_path="/clabernetes/studio/startup.cfg"
                data={"startup.cfg":decrypt_configuration(node.startup_configuration.encrypted_content)}
                mounts=[{"configMapName":config_name,"configMapPath":"startup.cfg","filePath":launcher_path,"mode":"read"}]
                binds=[f'{launcher_path}:{profile["startup_config_target"]}']
                for auxiliary in profile.get("auxiliary_config_files",[]):
                    key=auxiliary["key"];data[key]=auxiliary["content"];mounts.append({"configMapName":config_name,"configMapPath":key,"filePath":auxiliary["launcher_path"],"mode":"read"});binds.append(f'{auxiliary["launcher_path"]}:{auxiliary["target"]}')
                definition["binds"]=binds;files_from_config_map[node.name]=mounts
                config_maps.append(client.V1ConfigMap(metadata=client.V1ObjectMeta(name=config_name,namespace=deployment.namespace,
                    labels={"app.kubernetes.io/managed-by":"containerlab-studio","studio.containerlab.io/deployment":str(deployment.id),"studio.containerlab.io/node":str(node.id)}),data=data))
            nodes[node.name]=definition
        links=[{"endpoints":[f"{l.endpoint_a.node.name}:{l.endpoint_a.name}",f"{l.endpoint_b.node.name}:{l.endpoint_b.name}"]} for l in revision.links.select_related("endpoint_a__node","endpoint_b__node")]
        definition=yaml.safe_dump({"name":f"lab-{str(deployment.id)[:8]}","topology":{"nodes":nodes,"links":links}},sort_keys=False)
        body={"apiVersion":f"{API_GROUP}/{API_VERSION}","kind":"Topology","metadata":{"name":"topology","namespace":deployment.namespace,
            "labels":{"app.kubernetes.io/managed-by":"containerlab-studio","studio.containerlab.io/deployment":str(deployment.id)}},
            "spec":{"definition":{"containerlab":definition},"naming":"prefixed","expose":{"disableExpose":True,"exposeType":"LoadBalancer"}}}
        if files_from_config_map: body["spec"]["deployment"]={"filesFromConfigMap":files_from_config_map}
        return Plan(deployment.namespace,"topology",body,tuple(config_maps))
    def deploy_lab(self,deployment):
        errors=self.validate_topology(deployment.revision)
        if errors: raise ValueError(errors)
        plan=self.plan_deployment(deployment)
        try: self.core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=plan.namespace,labels={"app.kubernetes.io/managed-by":"containerlab-studio"})))
        except ApiException as e:
            if e.status!=409: raise
        for config_map in plan.config_maps:
            try: self.core.create_namespaced_config_map(plan.namespace,config_map)
            except ApiException as exc:
                if exc.status!=409: raise
                self.core.patch_namespaced_config_map(config_map.metadata.name,plan.namespace,config_map)
        try: return self.custom.create_namespaced_custom_object(API_GROUP,API_VERSION,plan.namespace,"topologies",plan.manifest)
        except ApiException as exc:
            if exc.status==409: return self.custom.get_namespaced_custom_object(API_GROUP,API_VERSION,plan.namespace,"topologies",plan.topology_name)
            raise
    def get_observed_state(self,deployment):
        try: obj=self.custom.get_namespaced_custom_object(API_GROUP,API_VERSION,deployment.namespace,"topologies","topology")
        except ApiException as exc:
            if exc.status==404: return {"topologyReady":False,"topologyState":"stopped","readyNodeCount":0,"nodeCount":deployment.revision.nodes.count(),"linkCount":deployment.revision.links.count()}
            raise
        return obj.get("status",{})
    def observe_devices(self,deployment):
        node_items=self.custom.list_namespaced_custom_object(API_GROUP,API_VERSION,deployment.namespace,"nodes").get("items",[])
        pods=self.core.list_namespaced_pod(deployment.namespace,label_selector="c9s.run/topologyOwner=topology").items
        pod_by_node={pod.metadata.labels.get("c9s.run/topologyNode"):pod for pod in pods if pod.metadata.labels}
        observed=[]
        for item in node_items:
            name=item.get("metadata",{}).get("labels",{}).get("c9s.run/topologyNode") or item.get("metadata",{}).get("name")
            pod=pod_by_node.get(name)
            appliance_running=False;appliance_paused=False
            if pod and pod.status.phase=="Running":
                try:
                    appliance_state=stream(self.core.connect_get_namespaced_pod_exec,pod.metadata.name,deployment.namespace,
                        command=["docker","inspect","-f","{{.State.Running}} {{.State.Paused}}",name],stderr=True,stdin=False,stdout=True,tty=False,
                        _request_timeout=5).strip().lower().split()
                    appliance_paused=len(appliance_state)==2 and appliance_state[1]=="true"
                    appliance_running=len(appliance_state)==2 and appliance_state[0]=="true" and not appliance_paused
                except Exception:
                    appliance_running=False;appliance_paused=False
            controller_readiness=item.get("status",{}).get("readiness","unknown")
            readiness="ready" if controller_readiness=="ready" and appliance_running else "starting"
            observed.append({"name":name,"node_uid":item.get("metadata",{}).get("uid"),"readiness":readiness,
                "pod":pod.metadata.name if pod else None,"pod_uid":str(pod.metadata.uid) if pod else None,"worker":pod.spec.node_name if pod else None,
                "pod_phase":pod.status.phase if pod else "Pending","appliance_running":appliance_running,"appliance_paused":appliance_paused,
                "deployment_disabled":DISABLE_DEPLOYMENTS_LABEL in item.get("metadata",{}).get("labels",{})})
        return observed
    def ping(self,deployment,node,target,count=3,timeout=2):
        device=deployment.devices.select_related("lab_node").get(lab_node=node)
        pod=device.runtime_resources.get("pod")
        if not pod: raise CapabilityError("The device launcher pod is not ready")
        command=["docker","exec",node.name,"ping","-c",str(count),"-W",str(timeout),target]
        output=stream(self.core.connect_get_namespaced_pod_exec,pod,deployment.namespace,command=command,stderr=True,stdin=False,stdout=True,tty=False)
        return {"node":node.name,"target":target,"command":"ping","output":output[-12000:]}
    def capture_packets(self,deployment,node,interface,duration=10,packet_limit=500):
        if not 1<=duration<=30 or not 1<=packet_limit<=5000: raise CapabilityError("Capture bounds are invalid")
        device=deployment.devices.get(lab_node=node)
        pod=device.runtime_resources.get("pod")
        if not pod: raise CapabilityError("The device launcher pod is not ready")
        host_interface=f"{node.name}-{interface.name}"
        stop_pattern=CAPTURE_STOP_MARKER.hex()
        scoped_stop_target=f"ff02::114%{host_interface}"
        # The hardened launcher cannot signal tcpdump after it drops privileges.
        # Marked local frames make tcpdump satisfy its own packet bound; those
        # frames are removed from the PCAP before it leaves this adapter.
        command=(
            "capture_file=$(mktemp /tmp/studio-capture.XXXXXX.pcap); "
            "trap 'rm -f \"$capture_file\"' EXIT; "
            f"tcpdump -n -s 256 -i {shlex.quote(host_interface)} -c {packet_limit} -U -w \"$capture_file\" 2>/dev/null & "
            "capture_pid=$!; "
            f"sleep {duration}; "
            f"ping6 -f -c {packet_limit} -w 3 -p {stop_pattern} {shlex.quote(scoped_stop_target)} >/dev/null 2>&1 & "
            "filler_pid=$!; wait \"$capture_pid\"; wait \"$filler_pid\" 2>/dev/null || true; "
            "printf '__STUDIO_PCAP_BEGIN__'; base64 -w 0 \"$capture_file\"; printf '__STUDIO_PCAP_END__'"
        )
        encoded=stream(self.core.connect_get_namespaced_pod_exec,pod,deployment.namespace,command=["sh","-c",command],
            stderr=True,stdin=False,stdout=True,tty=False,_request_timeout=duration+15)
        begin="__STUDIO_PCAP_BEGIN__"; end="__STUDIO_PCAP_END__"
        if begin not in encoded or end not in encoded: raise CapabilityError("Launcher did not complete the capture stream")
        encoded=encoded.split(begin,1)[1].split(end,1)[0]
        if len(encoded)>4*1024*1024: raise CapabilityError("Capture exceeded the encoded transfer limit")
        try: payload=base64.b64decode(encoded,validate=True)
        except (binascii.Error,ValueError) as exc: raise CapabilityError("Launcher returned an invalid capture stream") from exc
        return strip_capture_stop_packets(payload)
    def delete_runtime(self,deployment):
        try: result=self.custom.delete_namespaced_custom_object(API_GROUP,API_VERSION,deployment.namespace,"topologies","topology",
            body=client.V1DeleteOptions(propagation_policy="Foreground"))
        except ApiException as exc:
            if exc.status!=404: raise
            result={"status":"already_stopped"}
        selector=f"studio.containerlab.io/deployment={deployment.id}"
        deleted=0
        for config_map in self.core.list_namespaced_config_map(deployment.namespace,label_selector=selector).items:
            try:
                self.core.delete_namespaced_config_map(config_map.metadata.name,deployment.namespace,
                    body=client.V1DeleteOptions(propagation_policy="Background"))
                deleted+=1
            except ApiException as exc:
                if exc.status!=404: raise
        if isinstance(result,dict): result["configMapsDeleted"]=deleted
        return result
    def stop_lab(self,deployment): return self.delete_runtime(deployment)
    def resolve_console_target(self,device):
        if not device.runtime_resources.get("pod"): raise CapabilityError("Device pod is not ready")
        return {"namespace":device.deployment.namespace,"pod":device.runtime_resources["pod"],"method":device.lab_node.template_version.console_method}
    def restart_device(self,deployment,device):
        if device.deployment_id != deployment.id: raise CapabilityError("Device does not belong to this deployment")
        pod=device.runtime_resources.get("pod")
        if not pod: raise CapabilityError("The device launcher pod is not ready")
        name=device.lab_node.name
        self.core.delete_namespaced_pod(pod,deployment.namespace,body=client.V1DeleteOptions(grace_period_seconds=0,propagation_policy="Background"))
        return {"device":name,"operation":"restart","replaced_pod":pod,"readiness":"restarting"}
    def ensure_device_stopped(self,deployment,device):
        if device.deployment_id!=deployment.id: raise CapabilityError("Device does not belong to this deployment")
        name=device.lab_node.name
        self.custom.patch_namespaced_custom_object(API_GROUP,API_VERSION,deployment.namespace,"nodes",name,
            {"metadata":{"labels":{DISABLE_DEPLOYMENTS_LABEL:"true"}}})
        try:
            self.apps.delete_namespaced_deployment(name,deployment.namespace,
                body=client.V1DeleteOptions(propagation_policy="Background"))
            deleted=True
        except ApiException as exc:
            if exc.status!=404:
                self.custom.patch_namespaced_custom_object(API_GROUP,API_VERSION,deployment.namespace,"nodes",name,
                    {"metadata":{"labels":{DISABLE_DEPLOYMENTS_LABEL:None}}})
                raise
            deleted=False
        return {"device":name,"operation":"stop","desired_state":"stopped","readiness":"stopped","launcher_deleted":deleted}
    def stop_device(self,deployment,device): return self.ensure_device_stopped(deployment,device)
    def start_device(self,deployment,device):
        if device.deployment_id!=deployment.id: raise CapabilityError("Device does not belong to this deployment")
        name=device.lab_node.name
        self.custom.patch_namespaced_custom_object(API_GROUP,API_VERSION,deployment.namespace,"nodes",name,
            {"metadata":{"labels":{DISABLE_DEPLOYMENTS_LABEL:None}}})
        return {"device":name,"operation":"start","desired_state":"running","readiness":"starting"}
    @staticmethod
    def linked_data_interfaces(node):
        return list(node.interfaces.filter(Q(links_as_a__isnull=False)|Q(links_as_b__isnull=False),reserved_management=False).distinct().values_list("name",flat=True))
    def set_device_links(self,deployment,node_name,pod,interfaces,enabled):
        state="up" if enabled else "down";applied=[]
        for interface in interfaces:
            launcher_interface=f"{node_name}-{interface}"
            stream(self.core.connect_get_namespaced_pod_exec,pod,deployment.namespace,
                command=["ip","link","set",launcher_interface,state],stderr=True,stdin=False,stdout=True,tty=False,_request_timeout=10)
            applied.append(interface)
        return applied
    def set_device_pause(self,deployment,node_name,pod,paused,interfaces):
        if not pod: raise CapabilityError("Device launcher is not ready")
        if paused:
            applied=self.set_device_links(deployment,node_name,pod,interfaces,False)
            try:
                output=stream(self.core.connect_get_namespaced_pod_exec,pod,deployment.namespace,
                    command=["docker","pause",node_name],stderr=True,stdin=False,stdout=True,tty=False,_request_timeout=15)
            except Exception:
                self.set_device_links(deployment,node_name,pod,applied,True)
                raise
        else:
            output=stream(self.core.connect_get_namespaced_pod_exec,pod,deployment.namespace,
                command=["docker","unpause",node_name],stderr=True,stdin=False,stdout=True,tty=False,_request_timeout=15)
            applied=self.set_device_links(deployment,node_name,pod,interfaces,True)
        return {"device":node_name,"operation":"suspend" if paused else "resume","desired_state":"suspended" if paused else "running",
            "readiness":"suspended" if paused else "ready","interfaces":applied,"output":output[-2000:]}
    def suspend_device(self,deployment,device):
        if device.deployment_id!=deployment.id: raise CapabilityError("Device does not belong to this deployment")
        return self.set_device_pause(deployment,device.lab_node.name,device.runtime_resources.get("pod"),True,self.linked_data_interfaces(device.lab_node))
    def resume_device(self,deployment,device):
        if device.deployment_id!=deployment.id: raise CapabilityError("Device does not belong to this deployment")
        return self.set_device_pause(deployment,device.lab_node.name,device.runtime_resources.get("pod"),False,self.linked_data_interfaces(device.lab_node))
    def set_link_condition(self,deployment,link,condition):
        if link.revision_id!=deployment.revision_id: raise CapabilityError("Link does not belong to this deployment")
        endpoints=(link.endpoint_a,link.endpoint_b)
        applied=[]
        for interface in endpoints:
            node=interface.node
            device=deployment.devices.get(lab_node=node)
            pod=device.runtime_resources.get("pod")
            if device.observed_readiness!="ready" or not pod: raise CapabilityError(f"{node.name} launcher is not ready")
            host_interface=f"{node.name}-{interface.name}"
            if condition.get("active"):
                command=["tc","qdisc","replace","dev",host_interface,"root","netem"]
                latency=condition.get("latency_ms",0); jitter=condition.get("jitter_ms",0)
                if latency: command.extend(["delay",f"{latency}ms",f"{jitter}ms"] if jitter else ["delay",f"{latency}ms"])
                loss=100 if condition.get("disabled") else condition.get("loss_percent",0)
                if loss: command.extend(["loss",f"{loss:g}%"])
                corruption=condition.get("corruption_percent",0)
                if corruption and not condition.get("disabled"): command.extend(["corrupt",f"{corruption:g}%"])
                rate=condition.get("rate_kbps",0)
                if rate: command.extend(["rate",f"{rate}kbit"])
            else:
                command=["sh","-c",f"tc qdisc del dev {shlex.quote(host_interface)} root 2>/dev/null || true"]
            output=stream(self.core.connect_get_namespaced_pod_exec,pod,deployment.namespace,command=command,
                stderr=True,stdin=False,stdout=True,tty=False,_request_timeout=10)
            applied.append({"node":node.name,"interface":interface.name,"launcher_interface":host_interface,"output":output[-2000:]})
        return {"link_id":str(link.id),"condition":condition,"endpoints":applied}
    def collect_configuration(self,deployment,device):
        if device.deployment_id!=deployment.id: raise CapabilityError("Device does not belong to this deployment")
        pod=device.runtime_resources.get("pod")
        if device.observed_readiness!="ready" or not pod: raise CapabilityError("Device is not ready for configuration collection")
        command=device.lab_node.template_version.launch_profile.get("configuration_collect_command")
        if not isinstance(command,list) or not command or len(command)>16 or any(not isinstance(part,str) or not part or len(part)>4096 for part in command):
            raise CapabilityError("Template does not provide a verified collector")
        output=stream(self.core.connect_get_namespaced_pod_exec,pod,deployment.namespace,
            command=["docker","exec",device.lab_node.name,*command],stderr=False,stdin=False,stdout=True,tty=False,_request_timeout=15)
        if not output.strip(): raise CapabilityError("Device returned an empty configuration")
        encoded=output.encode("utf-8")
        if len(encoded)>1024*1024: raise CapabilityError("Collected configuration exceeds the 1 MiB limit")
        return {"device":device.lab_node.name,"content":output,"byte_size":len(encoded)}
    def start_capture(self,*_): raise CapabilityError("Use the bounded capture_packets operation")
