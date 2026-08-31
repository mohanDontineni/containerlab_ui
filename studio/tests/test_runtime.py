import yaml
import base64
from types import SimpleNamespace
from studio.runtime import ClabernetesAdapter,API_GROUP,API_VERSION,RUNTIME_VERSION,CapabilityError
def test_adapter_is_pinned(): assert (API_GROUP,API_VERSION,RUNTIME_VERSION)==("c9s.run","v1alpha1","0.8.0")
def test_unsupported_capability_is_explicit():
    adapter=object.__new__(ClabernetesAdapter)
    try: adapter.set_link_condition(None)
    except CapabilityError as e: assert "does not expose" in str(e)
    else: raise AssertionError("must fail explicitly")

def test_plan_uses_clabernetes_080_string_definition():
    node=SimpleNamespace(name="r1",template_version=SimpleNamespace(containerlab_kind="linux"),published_image=SimpleNamespace(registry_digest="registry/alpine@sha256:abc"))
    nodes=SimpleNamespace(select_related=lambda *_:[node])
    links=SimpleNamespace(select_related=lambda *_:[])
    revision=SimpleNamespace(nodes=nodes,links=links)
    deployment=SimpleNamespace(id="12345678-0000-0000-0000-000000000000",namespace="lab-12345678",revision=revision)
    plan=object.__new__(ClabernetesAdapter).plan_deployment(deployment)
    definition=plan.manifest["spec"]["definition"]["containerlab"]
    assert isinstance(definition,str)
    assert yaml.safe_load(definition)["topology"]["nodes"]["r1"]["image"].endswith("@sha256:abc")
    assert plan.manifest["spec"]["expose"]["disableExpose"] is True

def test_observe_devices_resolves_only_topology_owned_pods():
    node={"metadata":{"name":"r1","uid":"node-uid","labels":{"c9s.run/topologyNode":"r1"}},"status":{"readiness":"ready"}}
    custom=SimpleNamespace(list_namespaced_custom_object=lambda *_args,**_kwargs:{"items":[node]})
    metadata=SimpleNamespace(name="r1-pod",uid="pod-uid",labels={"c9s.run/topologyNode":"r1"})
    pod=SimpleNamespace(metadata=metadata,spec=SimpleNamespace(node_name="worker-1"),status=SimpleNamespace(phase="Running"))
    core=SimpleNamespace(list_namespaced_pod=lambda *_args,**_kwargs:SimpleNamespace(items=[pod]))
    adapter=ClabernetesAdapter(custom_api=custom,core_api=core)
    observed=adapter.observe_devices(SimpleNamespace(namespace="lab-one"))
    assert observed==[{"name":"r1","node_uid":"node-uid","readiness":"ready","pod":"r1-pod","pod_uid":"pod-uid","worker":"worker-1","pod_phase":"Running"}]

def test_device_restart_replaces_only_selected_clabernetes_pod():
    calls=[]
    core=SimpleNamespace(delete_namespaced_pod=lambda pod,namespace,body: calls.append((pod,namespace,body)))
    adapter=ClabernetesAdapter(custom_api=SimpleNamespace(),core_api=core)
    deployment=SimpleNamespace(id="deployment-id",namespace="lab-one")
    device=SimpleNamespace(deployment_id="deployment-id",runtime_resources={"pod":"r1-launcher"},lab_node=SimpleNamespace(name="r1"))
    result=adapter.restart_device(deployment,device)
    assert result=={"device":"r1","operation":"restart","replaced_pod":"r1-launcher","readiness":"restarting"}
    assert calls[0][0:2]==("r1-launcher","lab-one")
    assert calls[0][2].grace_period_seconds==0

def test_bounded_capture_uses_verified_host_interface_and_returns_pcap(monkeypatch):
    pcap=b"\xd4\xc3\xb2\xa1"+b"\x00"*20
    calls=[]
    monkeypatch.setattr("studio.runtime.stream",lambda method,pod,namespace,**kwargs: calls.append((pod,namespace,kwargs)) or base64.b64encode(pcap).decode())
    core=SimpleNamespace(connect_get_namespaced_pod_exec=object())
    adapter=ClabernetesAdapter(custom_api=SimpleNamespace(),core_api=core)
    node=SimpleNamespace(name="r1"); interface=SimpleNamespace(name="eth2")
    device=SimpleNamespace(runtime_resources={"pod":"r1-launcher"})
    deployment=SimpleNamespace(namespace="lab-one",devices=SimpleNamespace(get=lambda **_:device))
    assert adapter.capture_packets(deployment,node,interface,7,250)==pcap
    command=calls[0][2]["command"]
    assert command[:2]==["sh","-c"] and "r1-eth2" in command[2] and "-s 256" in command[2] and "-c 250" in command[2]
    assert calls[0][2]["_request_timeout"]==22
