# ContainerLab Studio GUI training catalog

This catalog documents the supported operator workflows in ContainerLab Studio. Operators do not write Containerlab YAML, Kubernetes manifests, or shell scripts. The topology editor and guided forms collect intent, validate it, and generate the required runtime resources internally.

Open [GALLERY.md](GALLERY.md) for the complete rendered screenshot walkthrough.

Screenshots are generated from the deployed product with `scripts/capture-training.mjs`. The capture runner is non-destructive: it opens pages and safe dialogs, refreshes observed runtime status, and creates one expiring console session for visual evidence. It never saves, deploys, stops, restarts, publishes, uploads, changes membership, changes a password, applies link conditions, starts captures, or runs diagnostics.

## Screenshot index

| Screenshot | Operation shown | What the operator is doing |
|---|---|---|
| `01-overview.png` | Platform overview | Reviews project, lab, deployment, device, image, and recent-operation health from one dashboard. |
| `02-projects.png` | Project catalog | Searches available projects and opens an authorized workspace. |
| `03-create-project.png` | Create project | Defines a project name, description, and tags using a guided form. |
| `04-project-access.png` | Membership and quotas | Reviews role assignments, resource usage, and enforced project limits. |
| `05-lab-library.png` | Lab catalog | Searches labs, sees draft topology counts, and opens the visual workspace. |
| `06-create-lab.png` | Create lab | Creates an empty lab in a selected project without authoring YAML. |
| `07-topology-workspace.png` | Visual topology design | Drags devices onto the canvas, selects images, edits startup configuration, and connects explicit free interfaces. |
| `08-save-as-dialog.png` | Save topology as | Creates an independent lab copy with pinned images, links, layout, and encrypted configuration. |
| `09-revision-history.png` | Revision history | Reviews immutable published revisions and chooses a source for a new editable draft. |
| `10-image-library.png` | Image catalog | Reviews uploaded or registry-backed images, validation, architecture, publication, and repair state. |
| `11-upload-image.png` | Resumable image upload | Selects a licensed Docker/OCI archive, project, checksum, and acknowledgement before resumable upload. |
| `12-register-image.png` | Register OCI image | Adds an existing registry reference through a form instead of editing topology YAML. |
| `13-deployments.png` | Deployment catalog | Reviews desired and observed runtime state and opens a live deployment. |
| `14-runtime-overview.png` | Whole-lab lifecycle | Reviews device/link readiness and the controls for deploy, refresh, and stop. |
| `15-device-lifecycle.png` | Per-device lifecycle | Uses collect, suspend, resume, stop, start, and restart controls for individual devices. |
| `16-live-link-controls.png` | Link impairment | Applies bounded latency, jitter, loss, corruption, rate, disable, or restore actions bidirectionally. |
| `17-ping-diagnostic.png` | Ping diagnostic | Selects a source node and target address for a bounded reachability test. |
| `18-packet-capture.png` | Packet capture | Selects a device/interface plus bounded duration and packet count, then downloads PCAP evidence. |
| `19-device-console.png` | Browser console | Opens an authenticated, expiring, project-scoped device console. |
| `20-configuration-history.png` | Configuration collection | Reviews encrypted configuration versions and downloads authorized no-store copies. |
| `21-device-templates.png` | Device templates | Reviews kinds, interfaces, privilege requirements, console methods, and runtime capabilities. |
| `22-jobs-events.png` | Jobs and audit events | Tracks asynchronous operation state, progress, failures, and audit history. |
| `23-account-security.png` | Account and security | Updates profile/timezone or changes a password through the native product page. |
| `24-api-explorer.png` | API explorer | Reviews the authenticated automation API that backs the same authorization and validation rules. |
| `25-redeploy-preview.png` | Safe redeploy preview | Reviews compute replacement impact before explicitly recreating a stopped runtime. |
| `26-device-runtime-logs.png` | Runtime logs | Reads bounded appliance or launcher logs without shell access. |
| `27-backup-restore-preview.png` | Backup restore preview | Validates a native backup and reviews restore impact before confirmation. |
| `28-configuration-compare.png` | Configuration comparison | Compares two encrypted immutable configuration versions. |
| `29-configuration-restore-preview.png` | Configuration restore | Reviews the selected version before creating a new editable draft. |
| `30-traceroute-diagnostic.png` | Traceroute diagnostic | Runs a bounded appliance traceroute from a selected device. |
| `31-guarded-image-deletion.png` | Guarded image deletion | Reviews references and permits deletion only for an unused artifact. |
| `32-guarded-lab-deletion.png` | Guarded lab deletion | Reviews runtime/history blockers before removing a lab-library entry. |
| `33-guarded-project-retirement.png` | Guarded project retirement | Reviews workspace dependencies before retiring a project. |
| `34-guarded-runtime-removal.png` | Guarded runtime removal | Reviews compute and session impact before deleting the owned runtime namespace. |
| `35-versioned-device-template.png` | Versioned device templates | Creates or activates a validated launch-profile version while preserving lab pins and history. |
| `36-topology-canvas-objects.png` | Canvas notes and regions | Documents intent and groups devices with editable, styled objects that persist with the topology. |
| `37-enforced-device-resources.png` | Enforced device resources | Verifies the pinned CPU and memory profile on a real Clabernetes-backed device. |
| `38-subgraph-duplication.png` | Multi-device subgraph duplication | Copies selected devices and their internal links while preserving runtime-ready configuration and pins. |
| `39-topology-arrangement.png` | Topology arrangement and discovery | Automatically lays out linked groups clear of canvas objects, aligns a selection, and filters devices by name, kind, or category. |
| `40-guarded-device-reset.png` | Reset device to saved revision | Reviews the single-device replacement impact, saved baseline, sessions, and capture blockers before discarding ephemeral state. |
| `41-selected-device-lifecycle.png` | Selected-device lifecycle | Preflights and schedules start, stop, restart, suspend, or resume across several selected devices with independent audited jobs. |
| `42-topology-edit-lease.png` | Safe concurrent topology editing | Shows the second editor's named, expiring read-only session while another operator owns the topology draft. |
| `43-native-user-administration.png` | Native user administration | Creates local operators and previews guarded sign-in deactivation while preserving project roles and history. |
| `44-guarded-password-recovery.png` | Guarded password recovery | Issues a policy-compliant temporary password, previews active browser and console revocation, signs the operator out, and requires personal password rotation at next login. |
| `45-live-device-network-state.png` | Live device network state | Collects bounded interface, address, route, and neighbor data from a ready appliance and presents it as structured GUI tables. |
| `46-guarded-selected-device-reset.png` | Guarded selected-device reset | Preflights several devices together, reports saved baselines and session impact, then resets every eligible selection through independently tracked jobs. |
| `47-topology-revision-comparison.png` | Topology revision comparison | Selects two saved revisions and reviews structured device, link, canvas, object, image, template, and configuration-checksum changes before restore or deployment. |
| `48-hierarchical-lab-folders.png` | Hierarchical lab folders | Organizes a real lab into project-scoped nested folders and demonstrates guarded deletion while the folder still contains a lab. |
| `49-navigable-lab-folder-browser.png` | Navigable lab folder browser | Opens nested folder levels with breadcrumbs, contextual creation actions, project-aware preselection, and a catalog scoped to the current folder. |
| `50-native-security-audit-trail.png` | Native security audit trail | Filters real project activity by action, actor, target, correlation ID, and retention window, expands bounded metadata, and exports an authorized CSV. |
| `51-scheduled-lab-lifecycle.png` | Scheduled lab lifecycle | Creates, cancels, and tracks one-time start or stop actions with linked asynchronous jobs and eligibility rechecks. |
| `52-staged-device-start.png` | Dependency-aware staged start | Reorders stopped devices and applies a bounded interval while one durable job tracks the complete startup sequence. |
| `53-live-operational-topology.png` | Live operational topology | Renders saved node placement, link health, selected-device runtime facts, authorized actions, and direct console launch. |
| `54-live-device-resource-telemetry.png` | Live device resource telemetry | Compares current launcher CPU and memory use with each device's enforced limits, worker placement, sample window, and freshness. |
| `55-verified-platform-capabilities.png` | Verified platform capabilities | Shows database, worker/cache, Clabernetes reconciliation, and resource metrics as expiring evidence-backed health states. |
| `56-saved-topology-startup-plan.png` | Saved topology startup plan | Loads device priorities saved in the visual topology into the guarded staged-start workflow without selecting or reordering devices again. |
| `57-native-packet-analysis.png` | Native packet analysis | Decodes a bounded completed PCAP into protocol totals, conversations, and packet rows without external tools. |
| `58-kubernetes-device-events.png` | Kubernetes device events | Shows launcher scheduling, image, creation, start, warning, and retry evidence without cluster access. |
| `59-image-supply-chain-evidence.png` | Image supply-chain evidence | Filters validated device software and shows inspection, immutable publication compatibility, build history, and retained bounded build output. |
| `60-image-metadata-management.png` | Image catalog metadata | Assigns searchable vendor, category, and version details without changing immutable archive or publication identity. |
| `61-protected-registry-credentials.png` | Protected registry credentials | Creates and rotates an encrypted project credential while displaying only its fingerprint, references, and lifecycle state. |
| `62-stale-upload-cleanup.png` | Automatic stale-upload cleanup | Shows a partial upload after its 24-hour resume window expired and its quarantined storage was safely released. |
| `63-dashboard-failure-capacity.png` | Failure triage and project capacity | Summarizes every runtime state, real project quota consumption, telemetry availability, and failed jobs with resource-specific recovery actions. |
| `64-operations-job-center.png` | Searchable operations job center | Filters durable jobs by state, type, and lab, expands sanitized failure evidence, and navigates to the affected resource. |
| `65-template-image-compatibility.png` | Template-to-image compatibility | Evaluates accessible immutable publications against template architecture, category, verification, format, and lifecycle requirements. |
| `66-explicit-deployment-plan.png` | Explicit deployment plan | Reviews immutable publication, new-namespace creation, quota impact, active pinned runtimes, and required operator acknowledgement before scheduling. |
| `67-server-topology-preflight.png` | Server topology preflight | Runs pinned adapter validation and presents platform checks plus per-device image, interface, configuration, and resource evidence. |
| `68-containerlab-interoperability.png` | Containerlab interoperability | Exports a saved visual graph and previews a guarded re-import with explicit active-template and immutable-image mapping. |
| `69-kubernetes-oci-registry.png` | Persistent Kubernetes OCI registry | Shows the worker-verified internal Distribution registry, retained filesystem storage mode, and Ready state on the platform dashboard. |
| `70-verified-image-registry-mirror.png` | Verified image registry mirror | Repairs a validated upload through the GUI and shows its successful v2 build, internal registry reference, independently addressable manifest digest, and verified state. |
| `71-platform-network-isolation.png` | Verified platform network isolation | Shows five workload-scoped ingress policies verified by the worker after authorized service traffic succeeds and cross-namespace database, cache, and registry probes are denied. |
| `72-whole-lab-configuration-export.png` | Whole-lab configuration archive | Collects live configurations from both routers and exports the latest version per device as one audited ZIP with a checksum inventory. |
| `73-whole-lab-configuration-collection.png` | Whole-lab configuration collection | Runs one GUI action that atomically preflights every supported device, schedules independently tracked collection jobs, and advances both encrypted router histories. |
| `74-whole-lab-running-configuration-checkpoint.png` | Running configuration checkpoint | Reviews a checksum-bound preview and saves every latest collected device configuration into a new editable draft without changing the running revision or launcher identities. |
| `75-whole-lab-configuration-drift.png` | Whole-lab configuration drift | Compares each latest running configuration with the immutable deployed startup state using bounded per-device diffs and checksum evidence. |
| `76-live-interface-traffic-counters.png` | Live interface traffic counters | Shows bounded appliance RX/TX packets and bytes plus error/drop counters alongside interface addresses, routes, and neighbors. |
| `77-live-interface-traffic-rates.png` | Live interface traffic rates | Uses two bounded GUI samples around a five-packet diagnostic to show per-second RX/TX packet and byte rates alongside cumulative counters, with no YAML or shell access. |
| `78-live-device-resource-trends.png` | Live device resource trends | Shows bounded browser-local CPU and memory history for both running routers, current immutable limits, worker placement, and pressure classification without deploying a monitoring agent. |
| `79-topology-wide-link-traffic.png` | Topology-wide link traffic | Runs one bounded GUI inspection across every point-to-point link and shows both endpoints' live RX/TX packets, bytes, state, errors, and drops beneath the operational map. |
| `80-topology-link-traffic-rates.png` | Topology link traffic rates | Compares two explicitly requested topology snapshots and shows safe per-second RX/TX packet and byte rates for both endpoints, with counter-reset protection and no background polling. |
| `81-data-plane-reachability-matrix.png` | Data-plane reachability matrix | Automatically discovers usable addresses on linked interfaces and verifies every ordered device pair with bounded one-packet probes, normalized loss and latency, and no address entry or CLI access. |
| `82-durable-runtime-observation-recovery.png` | Durable observation recovery | Reloads the runtime page and restores the latest successful reachability and topology-traffic evidence with original completion times while requiring a fresh traffic-rate baseline. |
| `83-network-health-evidence-export.png` | Network-health evidence export | Downloads the latest normalized traffic and reachability observations as CSV files in one ZIP with a machine-readable SHA-256 integrity manifest, without YAML or CLI access. |

## No-YAML operating model

1. Create a project and assign roles and quotas in the GUI.
2. Create nested project folders in the lab library and assign or move labs with the native forms; populated folders are protected from deletion.
3. Add images through resumable upload or OCI registry registration.
3. Create a lab and add nodes from verified device templates.
4. Select a published image and enter optional or required startup configuration in the node inspector.
5. Connect devices by dragging between named interface handles. Used point-to-point interfaces cannot be selected twice.
6. Resolve the validation panel, save the draft, and deploy from the workspace.
7. Operate the whole topology or individual devices from the runtime page.
8. Use browser console, link conditions, bounded diagnostics, packet capture, and configuration collection from the same page.
9. Use Save As and revision restore for change workflows. Backup/Restore downloads a product-native JSON bundle; YAML is never required.
10. Add notes and colored regions directly on the canvas to document intent and visually organize larger topologies.
11. Multi-select devices to move, duplicate, or remove an internal subgraph as one safe, undoable canvas operation.
12. Arrange linked groups automatically, align selected devices into rows or columns, and filter the device palette by name, kind, or category.
13. Reset one ready device to its immutable deployed revision through a guarded preview; Studio preserves peers and topology while restoring the pinned startup configuration.
14. Select several runtime devices and preview a lifecycle action before Studio schedules independently tracked start, stop, restart, suspend, or resume jobs.
15. Open a topology with one active editor; additional editors receive a clearly labeled read-only view until the renewable five-minute editing session is released or expires.
16. Create operator accounts, search the directory, and safely activate or deactivate sign-in from the native staff-only user administration page.
17. Reset an operator credential through the guarded preview; Studio revokes active sessions and requires the temporary password to be replaced before any other operation.
18. Inspect a ready device's live interfaces, addresses, forwarding routes, and neighbor cache through structured tables without opening a shell.
19. Select two or more ready devices and review the reset-to-saved-revision impact before Studio discards ephemeral state, revokes consoles, and replaces each launcher.
20. Select any two saved topology revisions and compare their structural changes before choosing whether to restore or deploy; configuration content remains encrypted.
21. Schedule a one-time whole-lab start or stop from the runtime page, cancel it while pending, and follow the linked operation after dispatch.
22. Select stopped devices, arrange their dependency order, choose a bounded interval, and follow one resumable staged-start job until every launcher is ready.
23. Operate the running lab from its visual map: select devices, inspect live state, follow links to impairment controls, and open an authenticated console in context.
24. Monitor each running device's live CPU and memory use against its enforced template limits without Kubernetes access or CLI commands.
25. Confirm the shared platform services are healthy from the dashboard; worker and metrics status expires automatically if verification stops.
26. Assign bounded startup priorities in each device's topology properties, then load and run that saved dependency order from the runtime page.
27. Open a completed interface capture in Studio to inspect protocol mix, top conversations, and decoded packet metadata; download the raw PCAP only when deeper offline analysis is needed.
28. Select Kubernetes events in a device's runtime-evidence dialog to diagnose launcher scheduling, image pulls, container starts, probes, and controller warnings without `kubectl`.
29. Filter the image library by validation, architecture, and size; open supply-chain evidence to review checksums, safe inspection, immutable node publication, compatibility, and retained bounded build output.
30. Edit a device image's vendor, category, and version in the catalog; Studio protects against stale saves, audits the change, and keeps checksums and publication digests immutable.
31. Add, rotate, and deactivate project-scoped private-registry access; verify the browser and API expose only a one-way fingerprint and never return the password or token.
32. Review upload-session history to distinguish resumable, completed, failed, cancelled, and expired sessions; stale quarantine storage is released automatically and audited.
33. Triage failed background work from the overview, follow its bounded error evidence to the affected resource, and compare project usage with enforced quota allocations; live CPU and memory are shown only when measured telemetry is available.
34. Search and filter durable operations by lifecycle state, operation type, lab name, or correlation key; expand sanitized failures and follow the resource-aware recovery action instead of issuing an unsafe generic retry.
35. Review each template version's image policy and accessible publication matrix; the topology editor disables incompatible choices, explains warnings, and the server revalidates compatibility during draft save and runtime preflight.
36. Review the server-generated deployment plan before publishing a draft; Studio creates a separate runtime, leaves active pinned revisions unchanged, requires explicit acknowledgement, and keeps quota-blocked plans non-actionable.
37. Run Validate on a saved draft to inspect a server-authoritative readiness report; resolve blocking device, image, configuration, interface, or adapter findings before opening the deployment plan.
38. Use the optional Interop workspace to export a saved visual graph or import an existing supported `.clab.yml`; Studio rejects unsafe fields, requires explicit mappings, omits untrusted external configuration paths, and leaves normal design and operation GUI-only.
39. Confirm the namespace-local OCI registry is reachable and its persistent storage mode is healthy from the platform dashboard; this health evidence expires when worker probes stop.
40. Publish or repair a validated uploaded image from the catalog; Studio retains the node-local runtime tag, mirrors the manifest and layers into the internal registry, verifies the returned digest, and shows the proof without requiring registry CLI access.
41. Confirm the dashboard reports all platform ingress policies verified; web/console traffic is limited to the gateway, data services to named application workloads, and registry publication to the worker job boundary.
42. Export the latest collected configuration for every device as one integrity-described ZIP from the runtime page; no YAML, shell, or per-device download sequence is required.
43. Collect current configurations across every supported ready device with one runtime-page action; Studio refuses partial collection when any supported device is unavailable and shows each resulting version in encrypted history.
44. Save the latest whole-lab running state into a guarded editable draft; Studio revalidates both configuration history and the current draft while the deployed immutable revision remains untouched.
45. Review running-versus-deployed startup drift across the lab, switch between devices, and inspect bounded diffs without opening a shell or exposing configuration text in audit records.
46. Inspect per-interface RX/TX volume and packet, error, and drop counters from the native network-state dialog without using appliance or Kubernetes CLI access.
47. Refresh the native network-state dialog to calculate live per-interface RX/TX packet and byte rates from bounded counter samples; no polling agent, YAML, or shell workflow is required.
48. Review each running device's live CPU and memory trend against its immutable limit; Studio retains at most 30 distinct samples in the active browser tab and labels current normal, elevated, or critical pressure without persisting monitoring data.
49. Inspect every point-to-point link from the operational topology with one action; Studio atomically requires all linked devices to be ready and renders both endpoints' live RX/TX counters and faults without appliance or Kubernetes CLI access.
50. Refresh the topology traffic snapshot to calculate per-endpoint RX/TX packet and byte rates; Studio keeps only the prior browser-local sample, rejects negative counter deltas after resets, and performs no background server polling.
51. Run the whole-topology reachability matrix to discover linked-interface addresses and test every ordered device pair; Studio preflights all devices, caps the matrix at 10 devices and 90 probes, and renders normalized reachability, loss, and latency without raw command output.
52. Reload or revisit a runtime to recover its latest successful topology traffic and reachability evidence from durable jobs; Studio labels recovered timestamps and refuses to calculate rates from stale restored counters until a fresh baseline is collected.
53. Export the latest durable topology traffic and reachability observations as one network-health ZIP; Studio includes normalized CSV files, member byte sizes and SHA-256 checksums, observation job identities and completion times, and no raw command output or YAML.

Containerlab/Kubernetes YAML is an internal adapter concern. It may be inspected by platform administrators for troubleshooting, but it is not part of the normal user workflow.

## Regenerating screenshots

Install Playwright in an isolated tooling directory or make it available through `NODE_PATH`, then run:

```bash
TRAINING_BASE_URL=https://192.168.1.148:30444 \
TRAINING_USERNAME=admin \
TRAINING_PASSWORD='set-in-environment' \
node scripts/capture-training.mjs
```

The deployment must contain at least one project, lab with nodes/links, published image, and deployment so all detail screens are available. Self-signed home-lab TLS is supported by the runner. Do not commit credentials.
