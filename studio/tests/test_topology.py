import pytest
from studio.topology import TopologyError,safe_load_topology

def test_accepts_typed_topology():
    doc=safe_load_topology(b"name: t\ntopology:\n  nodes:\n    a: {kind: linux, image: alpine}\n    b: {kind: linux, image: alpine}\n  links:\n    - endpoints: ['a:eth1', 'b:eth1']\n")
    assert set(doc["topology"]["nodes"])=={"a","b"}
@pytest.mark.parametrize("payload",[
    b"topology: {nodes: {A_BAD: {kind: linux}}, links: []}",
    b"topology: {nodes: {a: {kind: linux, binds: ['/:/host']}}, links: []}",
    b"topology: {nodes: {a: {kind: linux}}, links: [{endpoints: ['a:eth1','missing:eth1']}]}",
    b"topology: {nodes: {a: {kind: linux}, b: {kind: linux}, c: {kind: linux}}, links: [{endpoints: ['a:eth1','b:eth1']}, {endpoints: ['a:eth1','c:eth1']}]}",
])
def test_rejects_unsafe_or_invalid(payload):
    with pytest.raises(TopologyError): safe_load_topology(payload)

