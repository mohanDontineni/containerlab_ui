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
