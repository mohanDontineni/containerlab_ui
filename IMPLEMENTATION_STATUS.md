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
- Live link latency, jitter, loss, corruption, rate, disable, and restore controls, with bounded validation, bidirectional application, persisted conditions, idempotent operations, and audit events.
- Resumable 4 MiB image archive onboarding with pause/resume/cancel UX, optional expected checksum, server-side SHA-256, quarantine inspection, per-project deduplication, and audit events.
- Licensed Docker/OCI archive publication into single-node containerd with a checksum-derived immutable tag, isolated Kubernetes Job, project-scoped idempotent API, audit trail, build status, and image-library action.
- In-product project collaboration management with administrator/editor/viewer roles, delegated administrator controls, owner protection, cross-project isolation, exact-user lookup, and audited add/change/remove lifecycle.
- Enforced project quotas for labs, nodes per topology, active deployments, image storage/reservations, and members, with row-locked accounting, usage reporting, administrator controls, audit events, and a consistent conflict contract.
- Encrypted, versioned startup configuration delivery for supported appliance templates using deployment-scoped ConfigMaps and Clabernetes launcher mounts, plus content-free topology/configuration audit events.
- Production BGP reference lab using a resumably uploaded and locally published FRR 10.4.1 image, two configured routers, explicit eth1 link endpoints, established eBGP, learned loopback routes, and bidirectional routed reachability.
- Production nftables firewall reference lab using a dedicated checksum-published appliance, encrypted policy delivery, default-deny forwarding, permitted ICMP, denied TCP/8080, and named policy counters.
- Audited node-local image repair workflow with explicit operator intent, reconciling/failed states, and appliance-container readiness probes that prevent false-green deployments.
- Verified live configuration collection for FRR and nftables appliances with template-scoped commands, immutable encrypted versions, persistent deployment history, content-free audit records, and operator-only no-store downloads.
- Durable per-device Suspend and Resume controls that pause the nested appliance and isolate every linked data interface, with audited asynchronous operations and automatic reconciliation after launcher replacement.
- A hardened Celery Beat scheduler that reconciles active deployments every 30 seconds without a Kubernetes service-account token, writable root filesystem, or Linux capabilities.
- EVE-style Save As workflow in the topology workspace with an accessible modal, project authorization, quota/name-conflict handling, deep topology identity remapping, encrypted configuration re-versioning, and audit history.
- Topology revision-history workspace with immutable/deployed/draft status, counts and checksums, optimistic draft-conflict protection, idempotent restore into a new editable revision, encrypted configuration re-versioning, and audited provenance.
- Native self-service account and security page with profile/timezone management, normalized email, verified current-password changes, strengthened password validation, CSRF protection, session continuity, legacy-route safety, and content-free audit events.
- Explicit GUI-only operator contract: topology Backup/Restore is labeled as a product-native bundle workflow, while Containerlab/Kubernetes YAML remains internal to the validated runtime adapter.
- Bounded per-device appliance and Clabernetes launcher log inspection through audited worker jobs, with operator authorization, selectable 20-1000 line limits, 100 KB output caps, no-store polling, refresh, and copy controls.
- Server-validated lab backup restore preview with bundle checksum, topology/configuration/template/image inventory, deployability issues, explicit impact confirmation, stale-draft protection, idempotent replay, immutable/running revision preservation, and audit events.
- Audited configuration-version comparison and safe restore with same-device validation, bounded no-store unified diffs, explicit impact preview, stale-draft protection, idempotent replay, newly encrypted draft configuration versions, and immutable/running revision preservation.
- GUI-native appliance traceroute with ready-device authorization, literal IP validation, bounded hop/probe/timeout controls, idempotent audited worker execution, and capped output from the selected nested appliance.
- Guarded image artifact deletion with server-counted publication/build/revision/job references, explicit impact preview, optimistic checksum confirmation, idempotent audited soft deletion, bounded owned-file removal, quota release, hidden deleted records, and checksum reuse.
- A 31-screen, read-only Firefox training capture catalog covering projects, labs, visual topology design, verified backup/restore, image onboarding/deletion, deployment/device lifecycle, safe redeploy preview, runtime logs, link controls, ping/traceroute diagnostics, packet capture, console, configuration history/compare/restore, templates, jobs, security, and API discovery.

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
| 20 | Backup/restore | PASS | A 1,888-byte GUI-native backup was server-previewed and restored into an independent lab with two devices, one link, one configuration, and unchanged source state; a deployable BGP backup then replaced that draft under concurrency protection. |
| 21 | Project collaboration lifecycle | PASS | Live owner assigned admin/editor/viewer roles; editor mutation returned 403; delegated admin add/change/remove returned 201/200/204; owner and viewer pages rendered the correct controls. |
| 22 | Project resource governance | PASS | Live one-unit limits allowed the first lab and rejected excess labs, members, image reservations, and topology nodes with typed 409 conflicts; usage/UI/audit checks passed. |
| 23 | Versioned startup configuration | PASS | FRR configs remained encrypted in PostgreSQL, materialized into deployment-scoped ConfigMaps, mounted into launchers, and applied to both ready devices. |
| 24 | BGP reference lab | PASS | FRR neighbor reached Established with one received prefix; 10.2.2.2/32 installed via BGP/eth1; both sourced loopback pings passed 3/3 with 0% loss. |
| 25 | Firewall reference lab | PASS | Routed ICMP passed 3/3 with 0% loss; a locally verified TCP/8080 listener was unreachable through the firewall; nftables recorded 1 allowed ICMP flow and 3 denied TCP SYNs. |
| 26 | Node-local image repair | PASS | A missing FRR node image was republished through the authenticated audited product operation, restored to containerd, and enabled waiting launchers without manual runtime import. |
| 27 | Appliance readiness | PASS | Reconciliation probes the nested appliance container and keeps the deployment in `deploying` when Clabernetes reports a ready Node without a running device. |
| 28 | Live configuration collection | PASS | FRR and firewall collection operations succeeded; repeated firewall collection created v2; three versions remained encrypted at rest; downloaded payload checksums matched and responses used `no-store`/`nosniff`. |
| 29 | Per-device suspend/resume | PASS | Suspending the live server paused its appliance, set `server-eth1` down, and produced 100% packet loss; Resume unpaused it, restored the link, and recovered 3/3 packets with 0% loss. |
| 30 | Convergent device lifecycle | PASS | After the suspended server launcher was deleted, Kubernetes assigned a new pod and UID; periodic reconciliation preserved suspend intent, re-paused the replacement, kept its data interface down, and maintained 100% packet loss until Resume. |
| 31 | Lab Save As / deep clone | PASS | A deployed immutable two-router BGP revision was copied through the production API with new revision/node/configuration identities, preserved two nodes/one link/two encrypted FRR configs, a non-null editable draft, and an audit event; the cloned lab deployed, established eBGP, and passed 3/3 sourced loopback pings in both directions. |
| 32 | Revision history and restore | PASS | The deployed BGP revision remained immutable and its original runtime stayed healthy while restore created revision 2 as a new editable draft with two encrypted FRR configs; replay returned the same result, a stale draft token returned typed 409, and deploying the restored revision established eBGP and passed 3/3 routed pings both ways. |
| 33 | Self-service account security | PASS | A temporary production account rendered the native page, rejected an incorrect current password, normalized and saved profile/timezone data, changed to a policy-compliant password, authenticated with the replacement credential, retained its active session, and emitted content-free profile/password audit events; the temporary user was then removed. |
| 34 | Packet-corruption impairment | PASS | The authenticated product API applied 0.5% corruption to a live two-router link, `tc netem` reported `corrupt 0.5%` on both launcher endpoints, and a second idempotent operation restored the persisted/runtime condition to 0%. |
| 35 | Safe whole-lab redeploy | PASS | Firefox verified the read-only impact preview; an authenticated audited redeploy recreated a stopped two-device/one-link Alpine runtime from pinned revision 1, reached 2/2 ready and `running`, and a final stop restored its original stopped state. |
| 36 | Device and launcher logs | PASS | Authenticated worker jobs collected 2,682 bytes of live FRR appliance startup output and 11,799 bytes of Clabernetes launcher output for the same router; both completed successfully through bounded, audited, no-store GUI contracts. |
| 37 | Restored topology runtime | PASS | The independently restored two-router BGP backup reached 2/2 ready after audited node-local image repair; product diagnostics passed 3/3 with 0% loss in both directions, the test runtime was stopped, and the displaced capacity runtime was returned to running. |
| 38 | Configuration compare and safe restore | PASS | Firewall collected versions 1 and 2 were compared through the authenticated no-store API; version 2 then produced editable revision 2 under idempotency and draft-concurrency protection while the original immutable deployment remained running with 3/3 devices ready and an unchanged revision. |
| 39 | Appliance traceroute | PASS | The authenticated product operation executed bounded traceroute inside live FRR router r1 toward 10.2.2.2 and returned the real one-hop path (`10.2.2.2`, 0.018 ms); the same completed workflow was captured in Firefox with explicit probe, timeout, and hop bounds. |
| 40 | Guarded image deletion | PASS | A published firewall artifact was correctly protected by 1 publication, 1 build, and 2 lab revisions; a disposable 66-byte malformed upload showed zero references, was previewed in Firefox, removed its owned PVC file, released exactly 66 quota bytes, disappeared from the API/UI, returned the same idempotent replay result, and retained audit provenance. |

Automated tests: **105 passed**. Django checks and migration drift checks: **pass**. React TypeScript/Vite production build: **pass**. Firefox training capture: **31 live screens passed**. Helm lint/render: **pass**. Native runtime ping: **3 transmitted, 3 received, 0% loss, 0.445 ms average RTT**. Live appliance traceroute: **1 verified hop to 10.2.2.2**. Bidirectional 120 ms link condition: **240.563 ms average RTT**. Disabled link: **100% loss**. Restored qdiscs: **native `noqueue` on both endpoints**.

## Known limitations

- No `/dev/kvm`; all VM-backed vendor templates are unsupported on the current worker.
- No dynamic StorageClass; this installation uses three application-owned static hostPath PVs on the single node and is not highly available.
- Node-local publication is intentionally a single-node mode. Multi-node and highly available installations still require a trusted OCI registry so every worker can resolve the same immutable image.
- npm reported transitive frontend audit findings during the clean container build; these require dependency analysis before production use.
- Clabernetes 0.8.0 emitted an auxiliary Alpine puller warning (`exit` executable missing), although launcher pulls, topology readiness, device creation, and traffic all succeeded.
