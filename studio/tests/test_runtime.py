from studio.runtime import ClabernetesAdapter,API_GROUP,API_VERSION,RUNTIME_VERSION,CapabilityError
def test_adapter_is_pinned(): assert (API_GROUP,API_VERSION,RUNTIME_VERSION)==("c9s.run","v1alpha1","0.8.0")
def test_unsupported_capability_is_explicit():
    adapter=object.__new__(ClabernetesAdapter)
    try: adapter.set_link_condition(None)
    except CapabilityError as e: assert "does not expose" in str(e)
    else: raise AssertionError("must fail explicitly")

