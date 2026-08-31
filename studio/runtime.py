from dataclasses import dataclass
import yaml
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from kubernetes.stream import stream

API_GROUP="c9s.run"; API_VERSION="v1alpha1"; RUNTIME_VERSION="0.8.0"

class CapabilityError(RuntimeError): pass
@dataclass(frozen=True)
class Plan: namespace:str; topology_name:str; manifest:dict

class ClabernetesAdapter:
    capabilities={"deploy_lab":"supported","get_observed_state":"supported","delete_runtime":"supported","restart_device":"supported",
        "resolve_console_target":"supported","start_capture":"experimental","stop_lab":"delete_and_redeploy","set_link_condition":"unsupported","collect_configuration":"template_dependent"}
    def __init__(self, custom_api=None, core_api=None):
        if custom_api is None:
            try: config.load_incluster_config()
            except config.ConfigException: config.load_kube_config()
        self.custom=custom_api or client.CustomObjectsApi(); self.core=core_api or client.CoreV1Api()
    @staticmethod
    def validate_topology(revision):
        errors=[]
        for node in revision.nodes.select_related("template_version","published_image"):
            if not node.published_image: errors.append(f"{node.name}: no immutable published image")
            elif "@sha256:" not in node.published_image.registry_digest: errors.append(f"{node.name}: image is not digest-pinned")
        return errors
    def plan_deployment(self,deployment):
        revision=deployment.revision
        nodes={n.name:{"kind":n.template_version.containerlab_kind,"image":n.published_image.registry_digest} for n in revision.nodes.select_related("template_version","published_image")}
        links=[{"endpoints":[f"{l.endpoint_a.node.name}:{l.endpoint_a.name}",f"{l.endpoint_b.node.name}:{l.endpoint_b.name}"]} for l in revision.links.select_related("endpoint_a__node","endpoint_b__node")]
        definition=yaml.safe_dump({"name":f"lab-{str(deployment.id)[:8]}","topology":{"nodes":nodes,"links":links}},sort_keys=False)
        body={"apiVersion":f"{API_GROUP}/{API_VERSION}","kind":"Topology","metadata":{"name":"topology","namespace":deployment.namespace,
            "labels":{"app.kubernetes.io/managed-by":"containerlab-studio","studio.containerlab.io/deployment":str(deployment.id)}},
            "spec":{"definition":{"containerlab":definition},"naming":"prefixed","expose":{"disableExpose":True,"exposeType":"LoadBalancer"}}}
        return Plan(deployment.namespace,"topology",body)
    def deploy_lab(self,deployment):
        errors=self.validate_topology(deployment.revision)
        if errors: raise ValueError(errors)
        plan=self.plan_deployment(deployment)
        try: self.core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=plan.namespace,labels={"app.kubernetes.io/managed-by":"containerlab-studio"})))
        except ApiException as e:
            if e.status!=409: raise
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
            observed.append({"name":name,"node_uid":item.get("metadata",{}).get("uid"),"readiness":item.get("status",{}).get("readiness","unknown"),
                "pod":pod.metadata.name if pod else None,"pod_uid":str(pod.metadata.uid) if pod else None,"worker":pod.spec.node_name if pod else None,
                "pod_phase":pod.status.phase if pod else "Pending"})
        return observed
    def ping(self,deployment,node,target,count=3,timeout=2):
        device=deployment.devices.select_related("lab_node").get(lab_node=node)
        pod=device.runtime_resources.get("pod")
        if not pod: raise CapabilityError("The device launcher pod is not ready")
        command=["docker","exec",node.name,"ping","-c",str(count),"-W",str(timeout),target]
        output=stream(self.core.connect_get_namespaced_pod_exec,pod,deployment.namespace,command=command,stderr=True,stdin=False,stdout=True,tty=False)
        return {"node":node.name,"target":target,"command":"ping","output":output[-12000:]}
    def delete_runtime(self,deployment):
        try: return self.custom.delete_namespaced_custom_object(API_GROUP,API_VERSION,deployment.namespace,"topologies","topology",
            body=client.V1DeleteOptions(propagation_policy="Foreground"))
        except ApiException as exc:
            if exc.status==404: return {"status":"already_stopped"}
            raise
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
    def collect_configuration(self,*_): raise CapabilityError("Template does not provide a verified collector")
    def start_capture(self,*_): raise CapabilityError("Capture requires verified runtime interface mapping")
    def set_link_condition(self,*_): raise CapabilityError("Clabernetes v0.8.0 does not expose a supported live impairment API")
