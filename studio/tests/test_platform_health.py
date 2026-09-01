from email.message import Message
from types import SimpleNamespace
import pytest

from studio.tasks import probe_network_isolation, probe_registry_health


class RegistryResponse:
    status=200
    headers=Message()

    def __enter__(self):
        self.headers["Docker-Distribution-Api-Version"]="registry/2.0"
        return self

    def __exit__(self,*_): return False
    def read(self,_): return b"{}"


def test_registry_probe_records_verified_distribution_api(settings,monkeypatch):
    settings.REGISTRY_INTERNAL_URL="http://registry:5000"
    recorded={}
    monkeypatch.setattr("studio.tasks.urlopen",lambda request,timeout:RegistryResponse())
    monkeypatch.setattr("studio.tasks.publish_platform_health",lambda key,payload:recorded.update(key=key,payload=payload))
    result=probe_registry_health.run()
    assert result["available"] is True and result["api_version"]=="registry/2.0"
    assert recorded["key"]=="studio:platform:registry"


def test_registry_probe_reports_failure_without_raising(settings,monkeypatch):
    settings.REGISTRY_INTERNAL_URL="http://registry:5000"
    monkeypatch.setattr("studio.tasks.urlopen",lambda *_args,**_kwargs:(_ for _ in ()).throw(OSError("connection refused")))
    result=probe_registry_health.run()
    assert result["available"] is False and "connection refused" in result["reason"]


def network_policy(name,target,port,sources):
    peers=[]
    for source in sources:
        if source=="@image-build": selector=SimpleNamespace(match_labels={},match_expressions=[SimpleNamespace(
            key="studio.containerlab.io/image-build",operator="Exists")])
        else: selector=SimpleNamespace(match_labels={"app":source},match_expressions=[])
        peers.append(SimpleNamespace(pod_selector=selector))
    return SimpleNamespace(metadata=SimpleNamespace(name=name),spec=SimpleNamespace(policy_types=["Ingress"],
        pod_selector=SimpleNamespace(match_labels={"app":target}),ingress=[SimpleNamespace(
            ports=[SimpleNamespace(port=port)],_from=peers)]))


def test_network_isolation_probe_validates_targets_ports_and_allowed_workloads(settings,monkeypatch):
    policies=[
        network_policy("containerlab-studio-web","containerlab-studio-web",8000,{"containerlab-studio-gateway"}),
        network_policy("containerlab-studio-console","containerlab-studio-console",8000,{"containerlab-studio-gateway"}),
        network_policy("containerlab-studio-postgres","containerlab-studio-postgres",5432,{"containerlab-studio-web",
            "containerlab-studio-worker","containerlab-studio-scheduler","containerlab-studio-console","containerlab-studio-migrate"}),
        network_policy("containerlab-studio-redis","containerlab-studio-redis",6379,{"containerlab-studio-web",
            "containerlab-studio-worker","containerlab-studio-scheduler","containerlab-studio-console"}),
        network_policy("containerlab-studio-registry","containerlab-studio-registry",5000,{"containerlab-studio-worker","@image-build"}),
    ]
    settings.STUDIO_NAMESPACE="containerlab";recorded={}
    monkeypatch.setattr("studio.tasks.kubernetes_config.load_incluster_config",lambda:None)
    monkeypatch.setattr("studio.tasks.kubernetes_client.NetworkingV1Api",lambda:SimpleNamespace(
        list_namespaced_network_policy=lambda namespace:SimpleNamespace(items=policies)))
    monkeypatch.setattr("studio.tasks.publish_platform_health",lambda key,payload:recorded.update(key=key,payload=payload))
    result=probe_network_isolation.run()
    assert result["available"] is True and result["verified"]==result["expected"]==5
    assert recorded["key"]=="studio:platform:network_isolation"
    policies[2].spec.ingress[0].ports[0].port=15432
    result=probe_network_isolation.run()
    assert result["available"] is False and result["missing"]==["containerlab-studio-postgres"]
