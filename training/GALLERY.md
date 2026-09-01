# ContainerLab Studio operator screenshot gallery

These screenshots were captured from the live single-node Kubernetes deployment through Firefox. They show the administrator experience without requiring Containerlab YAML, Kubernetes manifests, or shell scripts for normal operations.

## 01 — Platform overview

![Platform overview](01-overview.png)

The operator reviews project, lab, deployment, image, device-readiness, and recent-operation summaries from the landing dashboard.

## 02 — Project catalog

![Project catalog](02-projects.png)

The operator searches projects they are authorized to access and opens the project workspace.

## 03 — Create a project

![Create project](03-create-project.png)

The operator creates an isolated project using guided fields for its name, description, and tags.

## 04 — Project access and quotas

![Project access and quotas](04-project-access.png)

The project administrator reviews members, role assignments, resource usage, enforced quotas, and the labs belonging to the project.

## 05 — Lab library

![Lab library](05-lab-library.png)

The operator searches saved labs, reviews project and revision information, and opens a visual topology workspace.

## 06 — Create a lab

![Create lab](06-create-lab.png)

The operator creates an empty topology in an authorized project through a form; no YAML file is needed.

## 07 — Visual topology workspace

![Visual topology workspace](07-topology-workspace.png)

The operator drags verified device templates onto the canvas, selects published images, edits encrypted startup configuration, and connects explicitly named free interfaces. The example shows a valid two-node topology and link.

## 08 — Save topology as a new lab

![Save As dialog](08-save-as-dialog.png)

The operator creates an independent lab copy while preserving devices, links, image pins, layout, annotations, and encrypted startup configuration.

## 09 — Revision history

![Revision history](09-revision-history.png)

The operator compares the current editable draft with immutable published revisions and may restore a published version into a new draft without changing a running deployment.

## 10 — Image library

![Image library](10-image-library.png)

The operator reviews uploaded and registry-backed artifacts, validation state, architecture, publication status, and repair actions.

## 11 — Resumable image upload

![Image upload](11-upload-image.png)

The operator selects a Docker/OCI archive, project, optional expected checksum, and license acknowledgement before starting the resumable upload workflow.

## 12 — Register an OCI image

![Register image](12-register-image.png)

The operator registers an existing OCI registry reference through guided fields instead of embedding an image reference in topology YAML.

## 13 — Deployment catalog

![Deployment catalog](13-deployments.png)

The operator reviews saved deployment records, desired and observed state, runtime namespace, revision, and reconciliation status.

## 14 — Live runtime overview

![Runtime overview](14-runtime-overview.png)

The operator sees whole-lab state, ready-device count, link count, reconciliation time, and the deploy, stop, and refresh controls.

## 15 — Per-device lifecycle

![Device lifecycle](15-device-lifecycle.png)

The operator sees each device’s readiness, placement, and controls for configuration collection, suspend/resume, stop/start, and restart.

## 16 — Live link conditions

![Live link controls](16-live-link-controls.png)

The operator selects bounded latency, jitter, packet loss, corruption, and rate values for a named interface pair, or disables/restores the bidirectional link.

## 17 — Bounded ping diagnostic

![Ping diagnostic](17-ping-diagnostic.png)

The operator selects a ready source device and IPv4/IPv6 target for a bounded, audited reachability test.

## 18 — Packet capture

![Packet capture](18-packet-capture.png)

The operator chooses a ready device, named interface, maximum duration, and packet limit before starting an authenticated PCAP capture.

## 19 — Browser device console

![Browser console](19-device-console.png)

The operator opens an expiring, authenticated console session to a ready device. The screenshot shows the live connected shell prompt and device tabs.

## 20 — Collected configuration history

![Configuration history](20-configuration-history.png)

The operator reviews encrypted immutable configuration versions and uses authorized, no-store downloads when configuration evidence is needed.

## 21 — Device template catalog

![Device templates](21-device-templates.png)

The operator reviews reusable device kinds, interface rules, privilege requirements, console methods, resource profiles, and supported capabilities.

## 22 — Jobs and events

![Jobs and events](22-jobs-events.png)

The operator tracks asynchronous actions, progress, success/failure state, correlation information, and audited operational history.

## 23 — Account and security

![Account security](23-account-security.png)

The user updates profile and timezone settings or changes their password using current-password verification and product password policy.

## 24 — Authenticated API explorer

![API explorer](24-api-explorer.png)

The operator or automation engineer reviews the authenticated API behind the GUI. The API enforces the same authorization, validation, quota, and idempotency rules as the visual workflows.

## 25 — Safe redeploy preview

![Redeploy preview](25-redeploy-preview.png)

Before replacing device compute, the operator reviews the pinned revision, device and link counts, current state, preserved records, session impact, and any conflicting active job. Redeploy is submitted only after explicit confirmation.

## 26 — Device runtime logs

![Device runtime logs](26-device-runtime-logs.png)

The operator opens the device inspector from a live device, switches between appliance and Clabernetes launcher sources, selects a bounded line count, refreshes, and copies the audited no-store result without using Kubernetes or Containerlab commands.

## 27 — Verified backup restore preview

![Backup restore preview](27-backup-restore-preview.png)

After selecting a product-native backup, the server validates its format, checksum, templates, images, interfaces, and deployability without changing the lab. The operator reviews device, link, configuration, image, draft, published-revision, and active-deployment impact before explicitly confirming restore.

## 28 — Configuration version comparison

![Configuration comparison](28-configuration-compare.png)

The operator selects two encrypted, immutable versions collected from the same device and opens an audited, no-store unified comparison. Identical versions are reported explicitly; changed versions show a bounded 256 KiB diff without placing configuration content in job or audit metadata.

## 29 — Safe configuration restore preview

![Configuration restore preview](29-configuration-restore-preview.png)

Before restoring a collected configuration, the operator reviews the device, version, checksum, source revision, and impact. Confirmation creates a new editable draft pinned to the selected content while the running deployment, immutable revision, and collected history remain unchanged until the operator explicitly deploys the draft.

## 30 — Bounded appliance traceroute

![Traceroute diagnostic](30-traceroute-diagnostic.png)

The operator selects a ready appliance, literal IPv4/IPv6 destination, probes per hop, timeout, and maximum hop count. The authenticated worker executes the bounded command inside the owned device and returns the real hop path without exposing a launcher shell or accepting arbitrary command text.

## 31 — Guarded image deletion

![Guarded image deletion](31-guarded-image-deletion.png)

Before deletion, the server counts publications, build records, lab revisions, and active jobs. Only an unreferenced artifact may be confirmed; Studio then removes its owned quarantine file, releases project quota, hides the artifact from image/topology libraries, and retains upload, operation, and audit provenance.

## 32 — Guarded lab deletion

![Guarded lab deletion](32-guarded-lab-deletion.png)

The operator reviews revision and deployment history plus active runtime and job blockers before confirming. A safe deletion removes only the lab-library entry, releases one project lab quota unit, preserves immutable operational history, and allows the lab name to be reused without exposing YAML or cluster commands.

## 33 — Guarded project retirement

![Guarded project retirement](33-guarded-project-retirement.png)

An administrator reviews active labs, images, uploads, runtimes, and jobs before retiring a workspace. Retirement is available only when every active dependency is resolved; Studio then hides the project and its collaboration access, preserves all historical records, and permits the owner to reuse the project name.

## 34 — Guarded runtime removal

![Guarded runtime removal](34-guarded-runtime-removal.png)

The operator reviews the runtime namespace, device, console, capture, artifact, and active-job impact before confirming permanent compute removal. Studio deletes only the deployment-owned Kubernetes namespace, revokes console access, clears live compute references, preserves the immutable lab revision and operation history, and prevents the removed runtime from being redeployed or refreshed.

## 35 — Versioned device template management

![Versioned device template management](35-versioned-device-template.png)

A platform administrator manages the catalog identity, Containerlab kind, generated data interfaces, reserved management port, bounded compute resources, console mode, reviewed configuration preset, privilege requirement, and verification state without Django admin or YAML. Saving creates and activates a new immutable version; the history panel shows earlier versions that remain pinned by existing lab revisions.

## 36 — Topology canvas notes and regions

![Topology canvas objects](36-topology-canvas-objects.png)

The designer adds, moves, resizes, colors, and layers notes and regions directly on the topology canvas. The inspector edits the selected object without YAML; the objects participate in undo/redo, autosave, checksums, native backup/restore, export, revision history, and deep lab cloning.

## 37 — Enforced device compute resources

![Enforced device resources](37-enforced-device-resources.png)

The operator sees the CPU and memory profile pinned to each live device beside its Clabernetes pod and worker placement. Values configured through versioned device-template forms become matching Kubernetes requests and limits, so scheduling and isolation reflect the reviewed GUI policy without YAML or cluster access.

## 38 — Multi-device subgraph duplication

![Subgraph duplication](38-subgraph-duplication.png)

The designer selects several devices and duplicates the complete internal subgraph in one action. Studio creates collision-free device and link identities, offsets the copy, preserves pinned templates, images, interface bindings, and encrypted startup configurations, excludes links leaving the selection, and supports undo/redo plus keyboard duplication and deletion.

## 39 — Topology arrangement and device discovery

![Topology arrangement](39-topology-arrangement.png)

The designer automatically arranges connected device groups into a deterministic layout that stays clear of notes and regions. Multi-selected devices can be aligned into a row or column, while the device palette filters immediately by template name, Containerlab kind, or category. Arrangement and alignment are undoable, saved with the draft, and restored on reload without YAML.

## 40 — Guarded device reset

![Guarded device reset](40-guarded-device-reset.png)

The operator previews a single-device reset before discarding ephemeral appliance changes. Studio reports the immutable revision, pinned configuration source, active consoles, capture blockers, and exact preservation boundary. Confirmation replaces only the selected launcher, revokes its console sessions, restores its saved baseline, preserves every peer and topology link, and reconciles the device back to ready without YAML or cluster access.

## 41 — Selected-device lifecycle

![Selected-device lifecycle](41-selected-device-lifecycle.png)

The operator selects multiple runtime devices and previews one coordinated start, stop, restart, suspend, or resume action. The server rechecks every device together and schedules nothing unless the complete selection is eligible. Confirmation creates an independent idempotent job and audit record per device plus an aggregate audit event, making partial progress visible without hiding failures or exposing cluster commands.

## 42 — Safe concurrent topology editing

![Topology edit lease](42-topology-edit-lease.png)

The first editor receives a renewable, token-bound five-minute editing session. A second project editor can still inspect the complete topology, validation state, backups, and history, but sees the active owner's name and expiry in a prominent read-only banner. Canvas mutations, restores, saves, revision replacement, configuration restore, and deployment publication are server-protected so another browser cannot silently overwrite the active draft. Sessions renew automatically, release on navigation, and can be safely acquired after expiry without YAML or coordination outside the GUI.

## 43 — Native user administration

![Native user administration](43-native-user-administration.png)

A platform administrator creates a local operator with policy-validated temporary credentials, normalized profile data, and an explicit time zone entirely inside Studio. The searchable directory shows owned projects, memberships, privilege level, and sign-in state. Before deactivation, the guarded dialog reports dependencies and impact; active project owners and the current administrator are protected, browser consoles are revoked, and memberships plus audit history remain intact. Firefox verified disabled sign-in rejection, reactivation, and restored sign-in without using Django administration.

## 44 — Guarded password recovery

![Guarded password recovery](44-guarded-password-recovery.png)

A platform administrator searches for an operator and opens the credential-recovery preview entirely inside Studio. The dialog reports active browser and console sessions before accepting a policy-compliant temporary password. Confirmation revokes those sessions, invalidates the previous credential, and blocks every non-security page until the operator signs in with the temporary password and replaces it with a personal one. Firefox verified revocation, forced rotation, final access, and content-free audit events without opening Django administration.

## 45 — Live device network state

![Live device network state](45-live-device-network-state.png)

The operator opens a ready FRR device's live network-state inspector directly from the runtime inventory. Studio executes only fixed, bounded `iproute2` queries inside the selected appliance and renders interface state, assigned IPv4/IPv6 addresses, forwarding routes, gateways, protocols, metrics, and neighbor reachability as searchable visual evidence. Firefox verified 3 real interfaces, 23 real routes—including the learned BGP route to `10.2.2.2`—and one data-plane neighbor with no console errors or failed application requests.

## 46 — Guarded selected-device reset

![Guarded selected-device reset](46-guarded-selected-device-reset.png)

The operator selects both live BGP routers and opens one guarded reset preview. Studio rechecks readiness, active captures, saved startup baselines, console impact, and an optimistic version for every selected device before scheduling anything. Confirmation creates an independent audited reset job per router, revokes its console sessions, replaces each launcher, and restores the immutable deployed configuration without changing topology wiring or history. Firefox verified both launcher UIDs changed, both routers returned ready, and routed reachability recovered to 3/3 packets with 0% loss.

## 47 — Topology revision comparison

![Topology revision comparison](47-topology-revision-comparison.png)

The designer selects two saved revisions inside the visual workspace and opens a read-only structural comparison. Studio matches devices by topology name and links by canonical node/interface endpoints, then reports added, removed, and modified devices and links plus canvas and annotation changes. Template versions, immutable image references, interface sets, and startup-configuration checksums participate without decrypting configuration content. Firefox compared Alpine Connectivity QA revisions 1 and 2, correctly isolated the `client-a` startup-configuration change, and left both revisions, the active draft, and every runtime unchanged.

## 48 — Hierarchical lab folders

![Hierarchical lab folders](48-hierarchical-lab-folders.png)

The operator creates a project-scoped parent folder and nested child folder entirely through Studio, then moves the live Alpine Connectivity QA design into that hierarchy with the native lab form. Folder cards expose their complete path, direct lab and child counts, and editor-only management controls; the lab catalog repeats the path beside each design for fast scanning. Firefox verified that deleting the populated child is blocked with an exact dependency count, then restored the lab to root and removed both empty test folders without YAML, cluster access, console errors, or failed application requests.

## 49 — Navigable lab folder browser

![Navigable lab folder browser](49-navigable-lab-folder-browser.png)

The operator drills from the project-wide lab root into `Navigable Evidence 49 / Routing Validation` using folder cards and linked breadcrumbs. Contextual actions create subfolders and labs with the project and parent already selected, while the catalog shows only the current folder's direct labs instead of repeating the entire library. Firefox moved the real Alpine Connectivity QA design into the nested folder, verified the exact one-item scope and folder-preserving redirect, captured this view without console or request failures, restored the lab to root, and deleted both disposable folders through the GUI.

## 50 — Native security audit trail

![Native security audit trail](50-native-security-audit-trail.png)

An authorized administrator filters immutable project activity by project, action fragment, actor, target type, correlation ID, and bounded time window without opening Django administration or querying PostgreSQL. Each row exposes its actor, project, exact target, request trace, and an escaped, size-bounded metadata record; viewers see only accessible-project events and their own platform events. Firefox rendered eight real lab-folder events, expanded the deletion evidence, and downloaded the same filtered set as a nine-line, 1,804-byte CSV. The export neutralizes spreadsheet formulas, uses no-store/nosniff headers, caps output at 5,000 records, and emits its own content-free `audit.exported` event.

## 51 — Scheduled lab lifecycle

![Scheduled lab lifecycle](51-scheduled-lab-lifecycle.png)

The operator schedules one-time whole-lab start or stop actions directly on the runtime page. Pending actions can be cancelled with optimistic concurrency protection; due actions are rechecked against current runtime eligibility, converted into standard idempotent operation jobs, and retained as dispatched, cancelled, or skipped history. Firefox created and cancelled a future stop, dispatched a scheduled start through Celery Beat, followed its linked job to success, and restored the acceptance lab to its original stopped state with no pending test actions.

## 52 — Dependency-aware staged device start

![Dependency-aware staged device start](52-staged-device-start.png)

The operator selects stopped devices, opens the guarded staged-start preview, arranges the exact dependency order with accessible controls, and chooses a bounded 0–60 second interval. Studio rechecks project role, current device state, active operations, order uniqueness, optimistic versions, the 20-device limit, and the five-minute total bound before creating one idempotent parent job. Each start is a separately persisted Celery step rather than a sleeping worker; progress, heartbeat, timestamps, failures, and completion remain durable. Firefox started production BGP routers in `r2 → r1` order 8.213 seconds apart, returned both replacement launchers to ready, and verified routed reachability with 3/3 packets and 0% loss.

## 53 — Live operational topology

![Live operational topology](53-live-operational-topology.png)

The runtime page now preserves the designer's saved node placement in a responsive EVE-style operational map. Device cards continuously reflect appliance readiness and compute presence; links distinguish healthy, impaired, disabled, and endpoint-down state. Selecting a node exposes its template, worker, launcher, interfaces, console, network-state, log, selection, and authorized lifecycle controls, while selecting a link navigates directly to its bounded impairment form. Firefox rendered the production revision-2 BGP topology at its saved positions, selected `r1`, followed the healthy `r1:eth1 ↔ r2:eth1` link, and opened an authenticated `r1` console to `connected` through a same-origin message contract without changing runtime or link state.

## 54 — Live device resource telemetry

![Live device resource telemetry](54-live-device-resource-telemetry.png)

The runtime page displays current CPU and memory use for every device launcher beside its enforced template limits, worker placement, metrics window, and sample freshness. Proportional bars make capacity pressure visible without Kubernetes access, CLI commands, or YAML. The reconciler alone receives narrowly scoped read permission for pod metrics and persists bounded snapshots for authorized viewers; released compute and unavailable metrics render explicit non-error states. Firefox verified real metrics for both production BGP routers against their 500m CPU and 512Mi memory limits.

## 55 — Verified platform capabilities

![Verified platform capabilities](55-verified-platform-capabilities.png)

The dashboard reports database connectivity, Redis-backed worker execution, Clabernetes reconciliation, and the Kubernetes Metrics API in the native product instead of relying on static optimistic labels. Worker and runtime evidence is published through the shared cache with a two-minute expiry, so stale control-plane observations return to Pending automatically. The production preflight requires a serving metrics API, accepts the release's own occupied NodePort during upgrades, and offers an explicit pinned metrics-server installer for new clusters. Firefox rendered all four services Ready after a real reconciliation, and the idempotent installer retained live BGP pod samples.

## 56 — Saved topology startup plan

![Saved topology startup plan](56-saved-topology-startup-plan.png)

The visual topology inspector lets operators assign each device a bounded startup priority from 1–250. Those priorities persist with drafts, immutable revisions, clones, backups, and runtime instances; equal priorities are resolved deterministically by device name. When all planned devices are stopped, the runtime's Saved plan action selects them automatically and opens the existing guarded staged-start preview, where the operator can still review the exact order and choose a bounded interval. Firefox restored the production BGP design as revision 3, saved `r2=10` and `r1=20`, deployed it, stopped both routers, loaded `r2 → r1` without manual selection, and ran the durable sequence 8.101 seconds apart. Both routers returned Ready and routed reachability passed 3/3 with 0% loss.

## 57 — Native packet analysis

![Native packet analysis](57-native-packet-analysis.png)

The operator opens a completed interface capture directly in the runtime GUI instead of leaving Studio for Wireshark or a CLI decoder. A bounded, read-only classic-PCAP parser reports packet and byte totals, protocol distribution, normalized bidirectional conversations, relative timestamps, endpoints, lengths, and safe protocol summaries; raw payload bytes are never rendered. Firefox captured `r1:eth1` while the GUI ran five live pings to `10.2.2.2`, then verified all 10 request/reply frames as one ICMP conversation. The same dialog retains an authorized raw-PCAP download for advanced offline analysis.

## 58 — Kubernetes device events

![Kubernetes device events](58-kubernetes-device-events.png)

The operator selects Kubernetes events from the same native device-evidence dialog used for appliance and launcher logs. Studio resolves the recorded launcher UID internally, reads only that pod's events through the reconciler identity, caps the request at 200 events, normalizes occurrence time, type, reason, count, component, and bounded message text, and renders warning-aware cards. Firefox restarted production BGP router `r1` through the GUI, observed its launcher UID change, then displayed the replacement pod's real Scheduled, Pulled, Created, and Started events. A post-restart product diagnostic passed 3/3 routed packets with 0% loss.

## 59 — Image supply-chain evidence

![Image supply-chain evidence](59-image-supply-chain-evidence.png)

The image library now combines free-text search with working validation, architecture, and size filters. Its project-authorized evidence dialog presents SHA-256 identity, safe archive inspection, license acknowledgement, immutable publication digest, node-containerd compatibility, build lineage, failure details, and the final 12,000 characters of retained build output without exposing private storage paths. Firefox filtered the validated amd64 FRR 10.4.1 archive, requested a real node-copy repair through the GUI, waited for the isolated publisher job, and displayed its 425-character retained output and ready immutable publication. Both production BGP routers remained ready after republication.

## 60 — Image catalog metadata

![Image catalog metadata](60-image-metadata-management.png)

Project administrators and editors can give uploaded device software a searchable vendor, category, and version without renaming or mutating its archive, checksum, inspection, or immutable publication. The dialog reads a fresh safe metadata projection before editing, rejects controls and overlong values, uses an exact optimistic timestamp to prevent stale overwrites, and records field-level before/after audit evidence. Firefox assigned `FRRouting / Router / 10.4.1` to the real production archive, found it through the combined catalog search, reopened the persisted values, and verified both image list and detail APIs omit internal storage paths. Production routers `r1` and `r2` remained ready.

## 61 — Protected registry credentials

![Protected registry credentials](61-protected-registry-credentials.png)

Project administrators and editors manage private OCI access in a dedicated no-YAML workspace. Studio encrypts passwords and tokens before persistence, displays only a short one-way fingerprint, never prepopulates the edit secret, prevents cross-project and registry-host mismatches, and retains image references when access is deactivated. Firefox created a disposable token reference for `registry.example.invalid`, confirmed neither the DOM nor redacted API contained either secret, rotated it to a different fingerprint, captured the active record, and deactivated it. The non-routable host deliberately validates credential lifecycle without claiming a private image pull; launcher authentication remains unverified until real registry reachability and CA trust are supplied. Both production BGP routers remained ready.

## 62 — Automatic stale-upload cleanup

![Automatic stale-upload cleanup](62-stale-upload-cleanup.png)

The image onboarding page retains the operator's 20 most recent upload sessions with exact byte progress, resume deadline, lifecycle state, and quarantine disposition. Celery Beat schedules a bounded cleanup every 15 minutes; the worker row-locks expired active or failed sessions, removes only files resolved beneath Studio's quarantine root, persists an idempotent cleanup result, releases reservation accounting, and emits system audit evidence. Firefox created a real 4 KiB upload session and transferred 1 KiB into quarantine. Acceptance moved only that test deadline into the past, dispatched the actual Celery task `669c775e…`, and verified `Expired / Released`, 1 KiB of retained progress, no internal path in the API, and a visible `image.upload_expired` audit event. Both production routers remained ready.
