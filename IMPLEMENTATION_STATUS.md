# Implementation status — 2026-08-31

## Implemented

- Django 5.2 data model and initial migration for users, projects/memberships, upload sessions, image artifacts/builds/publications, template versions, immutable lab revisions, nodes/interfaces/links, deployments/device instances, durable operations, console/capture sessions, configurations, artifacts, and audit events.
- Database constraints for membership uniqueness, revision numbering, node/interface uniqueness, link endpoint distinction, idempotency keys, and one active target operation.
- Session-authenticated `/api/v1/` foundation with project scoping, viewer write denial, pagination, correlation IDs, OpenAPI, uploads, labs, deployments, and async operation acceptance.
- Resumable streamed uploads with exact offsets, SHA-256 verification, cancellation, size limits, quarantine paths, OCI/Docker archive distinction, traversal rejection, and unsupported-format outcomes.
- Safe bounded Containerlab YAML parser and export helper; interface reuse and unsafe fields are rejected.
- Clabernetes `c9s.run/v1alpha1` adapter pinned to 0.8.0 with explicit unsupported capability errors.
- React/TypeScript/React Flow workspace build with device nodes, interface handles, invalid peer reuse prevention, undo, pan/zoom/fit, minimap, validation and job panels.
- Multi-stage container build, non-root runtime, TLS gateway, fixed NodePort 30444, PostgreSQL, Redis, retained PVCs, worker, migration job, ServiceAccounts/RBAC, probes, and resource bounds.
- Actual cluster deployment and real two-node Clabernetes link traffic proof.
- Professional project, lab, image, topology-editor, runtime, console, diagnostics, configuration, import/export, and settings workflows outside Django admin.
- Session-bound browser consoles, reliable per-device restart, bounded ping diagnostics, authenticated PCAP capture/download, and bidirectional live link controls.
- Live link latency, disable, and restore validation against the deployed two-node lab, with persisted conditions, idempotent operations, and audit events.

## Acceptance results

| # | Check | Result | Evidence / exact blocker |
|---|---|---|---|
| 1 | GUI through 30444 | PASS | TLS admin login returned HTTP 200 at `192.168.1.148:30444`. |
| 2 | Authentication/project authorization | PASS | Django admin login deployed; API guessed-UUID and viewer-write tests pass. |
| 3 | Supported upload/preparation | BLOCKED | Upload/inspection implemented and unit-tested; registry publication workflow is not integrated. |
| 4 | Invalid formats stay undeployable | PASS | Unit tests verify malformed data and traversal archives are unsupported. |
| 5 | Published images pull through runtime layers | BLOCKED | Clabernetes public pulls proved; private worker-trusted TLS registry prerequisite not completed. |
| 6 | Layout persists | PASS | Editor draft save/load persists node positions, annotations, interfaces, links, images, and encrypted startup configurations. |
| 7 | Interface links validated | PASS | Parser and serializer reject duplicate point-to-point interfaces. |
| 8 | Real Kubernetes lab workload | PASS | `studio-smoke` TopologyReady with two launcher/device containers and VXLAN link. |
| 9 | Duplicate deploy idempotency | PASS | DB constraints and unit test cover idempotency; cluster replay test not run. |
| 10 | Device vs pod readiness | PASS | Separate observed readiness fields; Clabernetes node/topology readiness inspected. |
| 11 | Browser console reaches device | PASS | Session-bound, expiring, project-scoped WebSocket console was exercised against live Alpine devices; viewers are read-only. |
| 12 | Reference-lab traffic | PARTIAL | Smoke traffic passed 3/3; BGP and firewall reference acceptance not run. |
| 13 | PCAP download | PASS | Live eth1 capture downloaded through the authenticated API; checksum matched and tcpdump decoded 14 genuine ICMP/ARP packets with internal stop frames removed. |
| 14 | Stop preserves saved lab/config | PASS | Runtime stop/redeploy preserves immutable revision data, saved topology, and encrypted startup configuration records. |
| 15 | Redeploy pinned revisions/images | BLOCKED | Model/adapter enforce digests; publication path incomplete. |
| 16 | Restart avoids duplicate labs | PASS | Live per-device restart replaced only the selected launcher pod; multi-stage reconciliation restored readiness without creating another topology. |
| 17 | Cross-project isolation | PASS | Guessed UUID API test returns 404. |
| 18 | Cleanup preserves unrelated resources | PASS | Only `containerlab` and owned PVs touched; `trading` namespace unchanged. |
| 19 | Existing workloads unaffected | PASS | Trading workloads remained running during inspection/deployment. |
| 20 | Backup/restore | NOT RUN | Commands supplied; destructive restore exercise not performed on live instance. |

Automated tests: **50 passed**. Django checks and migration drift checks: **pass**. React TypeScript/Vite production build: **pass in the clean multi-stage image build**. Helm lint/render: **pass**. Native runtime ping: **3 transmitted, 3 received, 0% loss, 0.445 ms average RTT**. Bidirectional 120 ms link condition: **240.563 ms average RTT**. Disabled link: **100% loss**. Restored qdiscs: **native `noqueue` on both endpoints**.

## Known limitations

- No `/dev/kvm`; all VM-backed vendor templates are unsupported on the current worker.
- No dynamic StorageClass; this installation uses three application-owned static hostPath PVs on the single node and is not highly available.
- The private OCI registry and object-storage service are not deployed. The application image was built by an in-cluster Kaniko job and imported into containerd without changing runtime configuration.
- npm reported transitive frontend audit findings during the clean container build; these require dependency analysis before production use.
- Clabernetes 0.8.0 emitted an auxiliary Alpine puller warning (`exit` executable missing), although launcher pulls, topology readiness, device creation, and traffic all succeeded.
