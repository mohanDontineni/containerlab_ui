import yaml
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
