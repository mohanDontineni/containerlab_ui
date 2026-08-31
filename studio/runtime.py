from dataclasses import dataclass
from django.conf import settings
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

API_GROUP="c9s.run"; API_VERSION="v1alpha1"; RUNTIME_VERSION="0.8.0"

class CapabilityError(RuntimeError): pass
@dataclass(frozen=True)
class Plan: namespace:str; topology_name:str; manifest:dict

class ClabernetesAdapter:
    capabilities={"deploy_lab":"supported","get_observed_state":"supported","delete_runtime":"supported","restart_device":"experimental",
        "resolve_console_target":"supported","start_capture":"experimental","stop_lab":"delete_and_redeploy","set_link_condition":"unsupported","collect_configuration":"template_dependent"}
    def __init__(self, custom_api=None, core_api=None):
        if custom_api is None:
            try: config.load_incluster_config()
            except config.ConfigException: config.load_kube_config()
        self.custom=custom_api or client.CustomObjectsApi(); self.core=core_api or client.CoreV1Api()
    def validate_topology(self, revision):
        errors=[]
        for node in revision.nodes.select_related("template_version","published_image"):
            if not node.published_image: errors.append(f"{node.name}: no immutable published image")
            elif "@sha256:" not in node.published_image.registry_digest: errors.append(f"{node.name}: image is not digest-pinned")
        return errors
    def plan_deployment(self,deployment):
        revision=deployment.revision
        nodes={n.name:{"kind":n.template_version.containerlab_kind,"image":n.published_image.registry_digest} for n in revision.nodes.select_related("template_version","published_image")}
        links=[{"endpoints":[f"{l.endpoint_a.node.name}:{l.endpoint_a.name}",f"{l.endpoint_b.node.name}:{l.endpoint_b.name}"]} for l in revision.links.select_related("endpoint_a__node","endpoint_b__node")]
        body={"apiVersion":f"{API_GROUP}/{API_VERSION}","kind":"Topology","metadata":{"name":"topology","namespace":deployment.namespace,
            "labels":{"app.kubernetes.io/managed-by":"containerlab-studio","studio.containerlab.io/deployment":str(deployment.id)}},
            "spec":{"definition":{"containerlab":{"topology":{"nodes":nodes,"links":links}}}}}
        return Plan(deployment.namespace,"topology",body)
    def deploy_lab(self,deployment):
        errors=self.validate_topology(deployment.revision)
        if errors: raise ValueError(errors)
        plan=self.plan_deployment(deployment)
        try: self.core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=plan.namespace,labels={"app.kubernetes.io/managed-by":"containerlab-studio"})))
        except ApiException as e:
            if e.status!=409: raise
        return self.custom.create_namespaced_custom_object(API_GROUP,API_VERSION,plan.namespace,"topologies",plan.manifest)
    def get_observed_state(self,deployment):
        obj=self.custom.get_namespaced_custom_object(API_GROUP,API_VERSION,deployment.namespace,"topologies","topology")
        return obj.get("status",{})
    def delete_runtime(self,deployment):
        return self.custom.delete_namespaced_custom_object(API_GROUP,API_VERSION,deployment.namespace,"topologies","topology",
            body=client.V1DeleteOptions(propagation_policy="Foreground"))
    def stop_lab(self,deployment): return self.delete_runtime(deployment)
    def resolve_console_target(self,device):
        if not device.runtime_resources.get("pod"): raise CapabilityError("Device pod is not ready")
        return {"namespace":device.deployment.namespace,"pod":device.runtime_resources["pod"],"method":device.lab_node.template_version.console_method}
    def restart_device(self,*_): raise CapabilityError("Per-device restart is experimental and disabled")
    def collect_configuration(self,*_): raise CapabilityError("Template does not provide a verified collector")
    def start_capture(self,*_): raise CapabilityError("Capture requires verified runtime interface mapping")
    def set_link_condition(self,*_): raise CapabilityError("Clabernetes v0.8.0 does not expose a supported live impairment API")

