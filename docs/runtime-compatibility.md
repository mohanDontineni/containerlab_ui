# Runtime compatibility

## Version decision

Clabernetes **0.8.0** is the latest stable GitHub release inspected on 2026-08-30. The matching OCI chart is `oci://ghcr.io/clabernetes/clabernetes/clabernetes:0.8.0`; the older `ghcr.io/srl-labs/...` path resolves only to chart 0.6.0 and was rejected. The selected release uses `c9s.run/v1alpha1` Topology compilation into Node, Link, LauncherProfile, Config, and ImageRequest resources and launcher pods. Its launcher contains Containerlab 0.78.0.

Primary sources: the [Clabernetes releases](https://github.com/clabernetes/clabernetes/releases), [tagged source](https://github.com/clabernetes/clabernetes/tree/v0.8.0), and [Containerlab Clabernetes guide](https://containerlab.dev/manual/clabernetes/).

Kubernetes 1.36.3/amd64 was verified. Native Linux containers work. VM-backed devices are blocked because the worker has no `/dev/kvm` and no CPU virtualization flag was observed.

## Capability matrix

| Capability | Level | Notes |
|---|---|---|
| Topology compile/deploy/delete/observe | Supported | Verified with real Node/Link resources. |
| Native Linux device and point-to-point link | Supported | Alpine smoke topology and ICMP verified. |
| Browser console | Supported | Session-bound WebSocket console was exercised against live nested appliances; viewer sessions are read-only. |
| Per-device restart and PCAP | Supported | Launcher replacement and authenticated packet capture/download were verified against live deployments. |
| Live link impairment | Supported | Bidirectional latency, loss/disable, and clean qdisc restore were verified through Studio operations. |
| Structured appliance network state | Supported | Fixed bounded interface, address, route, and neighbor queries were verified against live FRR without arbitrary shell input. |
| Shared Linux bridge network | Unsupported here | Containerlab requires a pre-existing host bridge; Clabernetes workloads are namespace-isolated and Studio does not mutate worker host networking. |
| VM-backed vendor devices | Unsupported here | KVM missing; licensed images not supplied. |
| Private registry credentials | Unverified | Launcher-internal pull trust must be configured independently of pod pull secrets. |

Examples: a digest-pinned Alpine Linux node is within the native supported subset. A Cisco IOS-XRv disk is not accepted merely because it is qcow2; it needs licensing, an approved vrnetlab recipe, KVM, and a verified template.

Studio therefore exposes validated point-to-point links and named device interfaces in the visual topology editor, but does not offer a misleading shared-bridge control on this single-node Kubernetes profile. A future bridge workflow requires a separately administered host-network resource and an explicit security model before it can safely become a GUI operation.
