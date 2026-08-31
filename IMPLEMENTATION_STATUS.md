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
- Resumable 4 MiB image archive onboarding with pause/resume/cancel UX, optional expected checksum, server-side SHA-256, quarantine inspection, per-project deduplication, and audit events.
- Licensed Docker/OCI archive publication into single-node containerd with a checksum-derived immutable tag, isolated Kubernetes Job, project-scoped idempotent API, audit trail, build status, and image-library action.
- In-product project collaboration management with administrator/editor/viewer roles, delegated administrator controls, owner protection, cross-project isolation, exact-user lookup, and audited add/change/remove lifecycle.
- Enforced project quotas for labs, nodes per topology, active deployments, image storage/reservations, and members, with row-locked accounting, usage reporting, administrator controls, audit events, and a consistent conflict contract.
- Encrypted, versioned startup configuration delivery for supported appliance templates using deployment-scoped ConfigMaps and Clabernetes launcher mounts, plus content-free topology/configuration audit events.
- Production BGP reference lab using a resumably uploaded and locally published FRR 10.4.1 image, two configured routers, explicit eth1 link endpoints, established eBGP, learned loopback routes, and bidirectional routed reachability.
- Production nftables firewall reference lab using a dedicated checksum-published appliance, encrypted policy delivery, default-deny forwarding, permitted ICMP, denied TCP/8080, and named policy counters.
- Audited node-local image repair workflow with explicit operator intent, reconciling/failed states, and appliance-container readiness probes that prevent false-green deployments.
- Verified live configuration collection for FRR and nftables appliances with template-scoped commands, immutable encrypted versions, persistent deployment history, content-free audit records, and operator-only no-store downloads.

## Acceptance results

| # | Check | Result | Evidence / exact blocker |
|---|---|---|---|
| 1 | GUI through 30444 | PASS | TLS admin login returned HTTP 200 at `192.168.1.148:30444`. |
| 2 | Authentication/project authorization | PASS | Django admin login deployed; API guessed-UUID and viewer-write tests pass. |
| 3 | Supported upload/preparation | PASS | A real 8,585,216-byte Alpine Docker archive was resumed across three chunks, checksum-matched, safely identified, validated, audited, and deduplicated; malformed input remained unsupported. |
| 4 | Invalid formats stay undeployable | PASS | Unit tests verify malformed data and traversal archives are unsupported. |
| 5 | Published images pull through runtime layers | PASS | Uploaded Alpine archive published through the API; Clabernetes copied its immutable node-local tag into the launcher Docker daemon. |
| 6 | Layout persists | PASS | Editor draft save/load persists node positions, annotations, interfaces, links, images, and encrypted startup configurations. |
| 7 | Interface links validated | PASS | Parser and serializer reject duplicate point-to-point interfaces. |
| 8 | Real Kubernetes lab workload | PASS | `studio-smoke` TopologyReady with two launcher/device containers and VXLAN link. |
| 9 | Duplicate deploy idempotency | PASS | DB constraints and unit test cover idempotency; cluster replay test not run. |
| 10 | Device vs pod readiness | PASS | Separate observed readiness fields; Clabernetes node/topology readiness inspected. |
| 11 | Browser console reaches device | PASS | Session-bound, expiring, project-scoped WebSocket console was exercised against live Alpine devices; viewers are read-only. |
| 12 | Reference-lab traffic | PASS | Two-AS BGP and default-deny nftables firewall reference acceptance both pass with positive and negative traffic evidence. |
| 13 | PCAP download | PASS | Live eth1 capture downloaded through the authenticated API; checksum matched and tcpdump decoded 14 genuine ICMP/ARP packets with internal stop frames removed. |
| 14 | Stop preserves saved lab/config | PASS | Runtime stop/redeploy preserves immutable revision data, saved topology, and encrypted startup configuration records. |
| 15 | Redeploy pinned revisions/images | PASS | Adapter accepts registry digests or checksum-derived node-local publications; a fresh saved lab deployed the published Alpine 3.22.5 image and reached ready. |
| 16 | Restart avoids duplicate labs | PASS | Live per-device restart replaced only the selected launcher pod; multi-stage reconciliation restored readiness without creating another topology. |
| 17 | Cross-project isolation | PASS | Guessed UUID API test returns 404. |
| 18 | Cleanup preserves unrelated resources | PASS | Only `containerlab` and owned PVs touched; `trading` namespace unchanged. |
| 19 | Existing workloads unaffected | PASS | Trading workloads remained running during inspection/deployment. |
| 20 | Backup/restore | NOT RUN | Commands supplied; destructive restore exercise not performed on live instance. |
| 21 | Project collaboration lifecycle | PASS | Live owner assigned admin/editor/viewer roles; editor mutation returned 403; delegated admin add/change/remove returned 201/200/204; owner and viewer pages rendered the correct controls. |
| 22 | Project resource governance | PASS | Live one-unit limits allowed the first lab and rejected excess labs, members, image reservations, and topology nodes with typed 409 conflicts; usage/UI/audit checks passed. |
| 23 | Versioned startup configuration | PASS | FRR configs remained encrypted in PostgreSQL, materialized into deployment-scoped ConfigMaps, mounted into launchers, and applied to both ready devices. |
| 24 | BGP reference lab | PASS | FRR neighbor reached Established with one received prefix; 10.2.2.2/32 installed via BGP/eth1; both sourced loopback pings passed 3/3 with 0% loss. |
| 25 | Firewall reference lab | PASS | Routed ICMP passed 3/3 with 0% loss; a locally verified TCP/8080 listener was unreachable through the firewall; nftables recorded 1 allowed ICMP flow and 3 denied TCP SYNs. |
| 26 | Node-local image repair | PASS | A missing FRR node image was republished through the authenticated audited product operation, restored to containerd, and enabled waiting launchers without manual runtime import. |
| 27 | Appliance readiness | PASS | Reconciliation probes the nested appliance container and keeps the deployment in `deploying` when Clabernetes reports a ready Node without a running device. |
| 28 | Live configuration collection | PASS | FRR and firewall collection operations succeeded; repeated firewall collection created v2; three versions remained encrypted at rest; downloaded payload checksums matched and responses used `no-store`/`nosniff`. |

Automated tests: **81 passed**. Django checks and migration drift checks: **pass**. React TypeScript/Vite production build: **pass in the clean multi-stage image build**. Helm lint/render: **pass**. Native runtime ping: **3 transmitted, 3 received, 0% loss, 0.445 ms average RTT**. Bidirectional 120 ms link condition: **240.563 ms average RTT**. Disabled link: **100% loss**. Restored qdiscs: **native `noqueue` on both endpoints**.

## Known limitations

- No `/dev/kvm`; all VM-backed vendor templates are unsupported on the current worker.
- No dynamic StorageClass; this installation uses three application-owned static hostPath PVs on the single node and is not highly available.
- Node-local publication is intentionally a single-node mode. Multi-node and highly available installations still require a trusted OCI registry so every worker can resolve the same immutable image.
- npm reported transitive frontend audit findings during the clean container build; these require dependency analysis before production use.
- Clabernetes 0.8.0 emitted an auxiliary Alpine puller warning (`exit` executable missing), although launcher pulls, topology readiness, device creation, and traffic all succeeded.
