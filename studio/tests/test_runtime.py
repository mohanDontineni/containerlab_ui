import yaml
import base64
import struct
from types import SimpleNamespace
from studio.runtime import ClabernetesAdapter,API_GROUP,API_VERSION,RUNTIME_VERSION,CapabilityError,CAPTURE_STOP_MARKER,CAPTURE_STOP_DESTINATION,DISABLE_DEPLOYMENTS_LABEL,strip_capture_stop_packets
def test_adapter_is_pinned(): assert (API_GROUP,API_VERSION,RUNTIME_VERSION)==("c9s.run","v1alpha1","0.8.0")
def test_unsupported_capability_is_explicit():
    adapter=object.__new__(ClabernetesAdapter)
    deployment=SimpleNamespace(id="deployment")
    device=SimpleNamespace(deployment_id="deployment",observed_readiness="ready",runtime_resources={"pod":"pod"},
        lab_node=SimpleNamespace(template_version=SimpleNamespace(launch_profile={})))
    try: adapter.collect_configuration(deployment,device)
    except CapabilityError as e: assert "verified collector" in str(e)
    else: raise AssertionError("must fail explicitly")

def test_verified_collector_executes_inside_selected_appliance(monkeypatch):
    calls=[]
    monkeypatch.setattr("studio.runtime.stream",lambda method,pod,namespace,**kwargs:calls.append((pod,namespace,kwargs)) or "hostname r1\nrouter bgp 65001\n")
    core=SimpleNamespace(connect_get_namespaced_pod_exec=object())
    adapter=ClabernetesAdapter(custom_api=SimpleNamespace(),core_api=core)
    profile={"configuration_collect_command":["vtysh","-c","show running-config"]}
    node=SimpleNamespace(name="r1",template_version=SimpleNamespace(launch_profile=profile))
    device=SimpleNamespace(deployment_id="deployment",observed_readiness="ready",runtime_resources={"pod":"r1-pod"},lab_node=node)
    result=adapter.collect_configuration(SimpleNamespace(id="deployment",namespace="lab-one"),device)
    assert result=={"device":"r1","content":"hostname r1\nrouter bgp 65001\n","byte_size":29}
    assert calls[0][2]["command"]==["docker","exec","r1","vtysh","-c","show running-config"]
    assert calls[0][2]["_request_timeout"]==15

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

def test_plan_materializes_supported_startup_configuration_without_embedding_secret_in_topology(monkeypatch):
    monkeypatch.setattr("studio.runtime.decrypt_configuration",lambda _:"router bgp 65001\n network 10.1.0.0/24\n")
    profile={"startup_config_target":"/etc/frr/frr.conf","auxiliary_config_files":[{"key":"daemons","launcher_path":"/clabernetes/studio/daemons","target":"/etc/frr/daemons","content":"zebra=yes\nbgpd=yes\n"}]}
    node=SimpleNamespace(id=SimpleNamespace(hex="a"*32),name="r1",template_version=SimpleNamespace(containerlab_kind="linux",launch_profile=profile),
        published_image=SimpleNamespace(registry_digest="quay.io/frr@sha256:abc"),startup_configuration_id="config-id",startup_configuration=SimpleNamespace(encrypted_content=b"ciphertext"))
    revision=SimpleNamespace(nodes=SimpleNamespace(select_related=lambda *_:[node]),links=SimpleNamespace(select_related=lambda *_:[]))
    deployment=SimpleNamespace(id="12345678-0000-0000-0000-000000000000",namespace="lab-config",revision=revision)
    plan=object.__new__(ClabernetesAdapter).plan_deployment(deployment)
    definition=yaml.safe_load(plan.manifest["spec"]["definition"]["containerlab"])
    assert definition["topology"]["nodes"]["r1"]["binds"]==[
        "/clabernetes/studio/startup.cfg:/etc/frr/frr.conf","/clabernetes/studio/daemons:/etc/frr/daemons"]
    assert "router bgp" not in plan.manifest["spec"]["definition"]["containerlab"]
    mounts=plan.manifest["spec"]["deployment"]["filesFromConfigMap"]["r1"]
    assert [mount["configMapPath"] for mount in mounts]==["startup.cfg","daemons"]
    assert plan.config_maps[0].data["startup.cfg"].startswith("router bgp")

def test_startup_configuration_is_rejected_when_template_has_no_runtime_target():
    artifact=SimpleNamespace(checksum="a"*64)
    image=SimpleNamespace(registry_digest="registry/image@sha256:"+"a"*64,artifact=artifact,compatibility_result={})
    node=SimpleNamespace(name="host",published_image=image,startup_configuration_id="config-id",template_version=SimpleNamespace(launch_profile={}))
    node.interfaces=SimpleNamespace(filter=lambda **_:SimpleNamespace(count=lambda:0))
    revision=SimpleNamespace(nodes=SimpleNamespace(select_related=lambda *_:SimpleNamespace(prefetch_related=lambda *_:[node])))
    assert ClabernetesAdapter.validate_topology(revision)==["host: template does not support startup configuration"]

def test_firewall_requires_policy_and_two_data_plane_interfaces():
    artifact=SimpleNamespace(checksum="a"*64)
    image=SimpleNamespace(registry_digest="registry/firewall@sha256:"+"a"*64,artifact=artifact,compatibility_result={})
    interfaces=SimpleNamespace(filter=lambda **_:SimpleNamespace(count=lambda:1))
    profile={"startup_config_target":"/etc/studio/firewall.sh","startup_config_required":True,"required_interfaces":2}
    node=SimpleNamespace(name="fw1",published_image=image,startup_configuration_id=None,
        template_version=SimpleNamespace(launch_profile=profile),interfaces=interfaces)
    nodes=SimpleNamespace(select_related=lambda *_:SimpleNamespace(prefetch_related=lambda *_:[node]))
    revision=SimpleNamespace(nodes=nodes)
    assert ClabernetesAdapter.validate_topology(revision)==[
        "fw1: startup configuration is required","fw1: requires at least 2 data-plane interfaces"]

def test_node_local_checksum_reference_is_accepted_as_immutable():
    checksum="a"*64
    artifact=SimpleNamespace(checksum=checksum)
    image=SimpleNamespace(registry_digest=f"containerlab.local/studio/p/a:sha256-{checksum}",artifact=artifact,compatibility_result={"publication_mode":"node-containerd"})
    node=SimpleNamespace(name="r1",published_image=image,template_version=SimpleNamespace(launch_profile={}))
    node.interfaces=SimpleNamespace(filter=lambda **_:SimpleNamespace(count=lambda:0))
    revision=SimpleNamespace(nodes=SimpleNamespace(select_related=lambda *_:SimpleNamespace(prefetch_related=lambda *_:[node])))
    assert ClabernetesAdapter.validate_topology(revision)==[]
    image.registry_digest="containerlab.local/studio/p/a:latest"
    assert ClabernetesAdapter.validate_topology(revision)==["r1: image is not content-addressed"]

def test_observe_devices_resolves_only_topology_owned_pods(monkeypatch):
    monkeypatch.setattr("studio.runtime.stream",lambda *_args,**_kwargs:"true false\n")
    node={"metadata":{"name":"r1","uid":"node-uid","labels":{"c9s.run/topologyNode":"r1"}},"status":{"readiness":"ready"}}
    custom=SimpleNamespace(list_namespaced_custom_object=lambda *_args,**_kwargs:{"items":[node]})
    metadata=SimpleNamespace(name="r1-pod",uid="pod-uid",labels={"c9s.run/topologyNode":"r1"})
    pod=SimpleNamespace(metadata=metadata,spec=SimpleNamespace(node_name="worker-1"),status=SimpleNamespace(phase="Running"))
    core=SimpleNamespace(list_namespaced_pod=lambda *_args,**_kwargs:SimpleNamespace(items=[pod]),connect_get_namespaced_pod_exec=object())
    adapter=ClabernetesAdapter(custom_api=custom,core_api=core)
    observed=adapter.observe_devices(SimpleNamespace(namespace="lab-one"))
    assert observed==[{"name":"r1","node_uid":"node-uid","readiness":"ready","pod":"r1-pod","pod_uid":"pod-uid","worker":"worker-1","pod_phase":"Running","appliance_running":True,"appliance_paused":False,"deployment_disabled":False}]

def test_observe_devices_does_not_trust_controller_readiness_without_appliance(monkeypatch):
    monkeypatch.setattr("studio.runtime.stream",lambda *_args,**_kwargs:"")
    node={"metadata":{"name":"r1","uid":"node-uid","labels":{"c9s.run/topologyNode":"r1"}},"status":{"readiness":"ready"}}
    custom=SimpleNamespace(list_namespaced_custom_object=lambda *_args,**_kwargs:{"items":[node]})
    metadata=SimpleNamespace(name="r1-pod",uid="pod-uid",labels={"c9s.run/topologyNode":"r1"})
    pod=SimpleNamespace(metadata=metadata,spec=SimpleNamespace(node_name="worker-1"),status=SimpleNamespace(phase="Running"))
    core=SimpleNamespace(list_namespaced_pod=lambda *_args,**_kwargs:SimpleNamespace(items=[pod]),connect_get_namespaced_pod_exec=object())
    observed=ClabernetesAdapter(custom_api=custom,core_api=core).observe_devices(SimpleNamespace(namespace="lab-one"))
    assert observed[0]["readiness"]=="starting" and observed[0]["appliance_running"] is False

def test_suspend_and_resume_use_persistent_nested_container_pause(monkeypatch):
    calls=[]
    monkeypatch.setattr("studio.runtime.stream",lambda method,pod,namespace,**kwargs:calls.append(kwargs["command"]) or "r1\n")
    adapter=ClabernetesAdapter(custom_api=SimpleNamespace(),core_api=SimpleNamespace(connect_get_namespaced_pod_exec=object()))
    monkeypatch.setattr(adapter,"linked_data_interfaces",lambda _:["eth1"])
    deployment=SimpleNamespace(id="deployment",namespace="lab-one")
    device=SimpleNamespace(deployment_id="deployment",runtime_resources={"pod":"r1-pod"},lab_node=SimpleNamespace(name="r1"))
    suspended=adapter.suspend_device(deployment,device);resumed=adapter.resume_device(deployment,device)
    assert calls==[["ip","link","set","r1-eth1","down"],["docker","pause","r1"],
        ["docker","unpause","r1"],["ip","link","set","r1-eth1","up"]]
    assert suspended["readiness"]=="suspended" and suspended["desired_state"]=="suspended"
    assert resumed["readiness"]=="ready" and resumed["desired_state"]=="running"

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

def test_device_stop_disables_clabernetes_reconcile_deletes_only_launcher_and_start_reenables_it():
    patches=[];deletes=[]
    custom=SimpleNamespace(patch_namespaced_custom_object=lambda *args:patches.append(args) or {})
    apps=SimpleNamespace(delete_namespaced_deployment=lambda name,namespace,body:deletes.append((name,namespace,body.propagation_policy)))
    adapter=ClabernetesAdapter(custom_api=custom,core_api=SimpleNamespace(),apps_api=apps)
    deployment=SimpleNamespace(id="deployment-id",namespace="lab-one")
    device=SimpleNamespace(deployment_id="deployment-id",lab_node=SimpleNamespace(name="r2"))
    stopped=adapter.stop_device(deployment,device);started=adapter.start_device(deployment,device)
    assert stopped=={"device":"r2","operation":"stop","desired_state":"stopped","readiness":"stopped","launcher_deleted":True}
    assert started=={"device":"r2","operation":"start","desired_state":"running","readiness":"starting"}
    assert deletes==[("r2","lab-one","Background")]
    assert patches[0][-1]=={"metadata":{"labels":{DISABLE_DEPLOYMENTS_LABEL:"true"}}}
    assert patches[1][-1]=={"metadata":{"labels":{DISABLE_DEPLOYMENTS_LABEL:None}}}

def test_device_stop_rolls_back_disable_label_when_launcher_delete_fails():
    from kubernetes.client.exceptions import ApiException
    patches=[]
    custom=SimpleNamespace(patch_namespaced_custom_object=lambda *args:patches.append(args) or {})
    apps=SimpleNamespace(delete_namespaced_deployment=lambda *_args,**_kwargs:(_ for _ in ()).throw(ApiException(status=403)))
    adapter=ClabernetesAdapter(custom_api=custom,core_api=SimpleNamespace(),apps_api=apps)
    deployment=SimpleNamespace(id="deployment-id",namespace="lab-one");device=SimpleNamespace(deployment_id="deployment-id",lab_node=SimpleNamespace(name="r2"))
    try: adapter.stop_device(deployment,device)
    except ApiException as exc: assert exc.status==403
    else: raise AssertionError("delete failure must propagate")
    assert patches[-1][-1]=={"metadata":{"labels":{DISABLE_DEPLOYMENTS_LABEL:None}}}

def test_stop_removes_plaintext_runtime_configuration_maps():
    deleted=[]
    custom=SimpleNamespace(delete_namespaced_custom_object=lambda *_args,**_kwargs:{"status":"Success"})
    config_maps=SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name="studio-startup-r1")),SimpleNamespace(metadata=SimpleNamespace(name="studio-startup-r2"))])
    core=SimpleNamespace(
        list_namespaced_config_map=lambda namespace,label_selector: config_maps,
        delete_namespaced_config_map=lambda name,namespace,body: deleted.append((name,namespace,body.propagation_policy)))
    adapter=ClabernetesAdapter(custom_api=custom,core_api=core)
    result=adapter.stop_lab(SimpleNamespace(id="deployment-id",namespace="lab-one"))
    assert deleted==[("studio-startup-r1","lab-one","Background"),("studio-startup-r2","lab-one","Background")]
    assert result["configMapsDeleted"]==2

def test_bounded_capture_uses_verified_host_interface_and_returns_pcap(monkeypatch):
    pcap=b"\xd4\xc3\xb2\xa1"+b"\x00"*20
    calls=[]
    monkeypatch.setattr("studio.runtime.stream",lambda method,pod,namespace,**kwargs: calls.append((pod,namespace,kwargs)) or "Killed\n__STUDIO_PCAP_BEGIN__"+base64.b64encode(pcap).decode()+"__STUDIO_PCAP_END__")
    core=SimpleNamespace(connect_get_namespaced_pod_exec=object())
    adapter=ClabernetesAdapter(custom_api=SimpleNamespace(),core_api=core)
    node=SimpleNamespace(name="r1"); interface=SimpleNamespace(name="eth2")
    device=SimpleNamespace(runtime_resources={"pod":"r1-launcher"})
    deployment=SimpleNamespace(namespace="lab-one",devices=SimpleNamespace(get=lambda **_:device))
    assert adapter.capture_packets(deployment,node,interface,7,250)==pcap
    command=calls[0][2]["command"]
    assert command[:2]==["sh","-c"] and "r1-eth2" in command[2] and "-s 256" in command[2] and "-c 250" in command[2]
    assert "mktemp /tmp/studio-capture" in command[2] and "sleep 7" in command[2] and "ping6 -f -c 250" in command[2]
    assert calls[0][2]["stderr"] is True
    assert calls[0][2]["_request_timeout"]==22

def test_capture_stop_frames_are_removed_from_pcap():
    header=b"\xd4\xc3\xb2\xa1"+b"\x00"*20
    real=b"real-packet"; stop=b"prefix"+CAPTURE_STOP_MARKER+b"suffix"; destination_stop=b"ethernet-ipv6"+CAPTURE_STOP_DESTINATION+b"payload"
    record=lambda body: struct.pack("<IIII",1,2,len(body),len(body))+body
    cleaned=strip_capture_stop_packets(header+record(real)+record(stop)+record(destination_stop))
    assert cleaned==header+record(real)

def test_link_condition_applies_bounded_netem_to_both_endpoints(monkeypatch):
    calls=[]
    monkeypatch.setattr("studio.runtime.stream",lambda method,pod,namespace,**kwargs: calls.append((pod,namespace,kwargs)) or "")
    adapter=ClabernetesAdapter(custom_api=SimpleNamespace(),core_api=SimpleNamespace(connect_get_namespaced_pod_exec=object()))
    node_a=SimpleNamespace(name="r1");node_b=SimpleNamespace(name="r2")
    endpoint_a=SimpleNamespace(name="eth1",node=node_a);endpoint_b=SimpleNamespace(name="eth2",node=node_b)
    link=SimpleNamespace(id="link-one",revision_id="revision-one",endpoint_a=endpoint_a,endpoint_b=endpoint_b)
    devices={"r1":SimpleNamespace(observed_readiness="ready",runtime_resources={"pod":"r1-pod"}),
        "r2":SimpleNamespace(observed_readiness="ready",runtime_resources={"pod":"r2-pod"})}
    deployment=SimpleNamespace(revision_id="revision-one",namespace="lab-one",devices=SimpleNamespace(get=lambda lab_node:devices[lab_node.name]))
    condition={"active":True,"disabled":False,"latency_ms":120,"jitter_ms":10,"loss_percent":2.5,"rate_kbps":1000}
    result=adapter.set_link_condition(deployment,link,condition)
    assert [call[0] for call in calls]==["r1-pod","r2-pod"]
    assert calls[0][2]["command"]==["tc","qdisc","replace","dev","r1-eth1","root","netem","delay","120ms","10ms","loss","2.5%","rate","1000kbit"]
    assert result["condition"]==condition and len(result["endpoints"])==2
