# Security notes

The web ServiceAccount has token automount disabled. The worker/reconciler gets narrowly enumerated Clabernetes and observation permissions; Django is not cluster-admin. Browser HTTP uses same-origin sessions/CSRF, WebSockets use origin and per-session authorization, and project querysets prevent guessed UUID access.

The current home-lab certificate is self-signed. Kubernetes Secrets are not described as encrypted at rest; enable Kubernetes encryption-at-rest separately. Namespaces do not strongly isolate privileged network devices.

## Registry credentials

Private-registry passwords and access tokens are project-scoped and encrypted with authenticated Fernet encryption before PostgreSQL persistence. Browser and API responses expose only credential type, optional username, registry host, active state, reference count, and the first 16 hexadecimal characters of a one-way SHA-256 fingerprint. The current secret, encrypted bytes, and optional legacy Kubernetes Secret name are never serialized, prefilled into edit forms, included in audit metadata, written into image records, or exported with topology bundles. Rotation replaces the encrypted value and fingerprint; deactivation preserves referenced-image provenance.

The encryption key is derived from the installation's Django `SECRET_KEY`, so database backup recovery requires preserving that application Secret. Losing or changing the key makes encrypted registry credentials and saved configurations unreadable. Kubernetes Secret references remain supported for externally managed installations, but Kubernetes encryption at rest is still a separate cluster responsibility. Credential storage does not by itself prove launcher-internal registry authentication: trust, reachability, and pull behavior must be verified for the pinned Clabernetes runtime before a private image is marked ready.

## Account security

The native account page requires an authenticated same-origin session and CSRF token. Profile changes accept only IANA time zones and record changed field names rather than profile values. Password replacement requires the current credential and applies similarity, 12-character minimum, common-password, and numeric-only validators. Django rotates the session authentication hash after a successful change so the verified current browser stays signed in; no password value or derivative is written to an operation result or audit event. Legacy Django password-change URLs redirect to the native workflow instead of exposing an unstyled secondary surface.

## Node-local image publisher

The single-node installation publishes validated Docker/OCI archives through a short-lived Kubernetes Job. A non-root init container reads the artifact PVC and stages only the selected archive into an `emptyDir`; the publisher then runs as UID 0 with all Linux capabilities dropped and can access the host containerd socket. The worker ServiceAccount can create and observe Jobs, while the web ServiceAccount has no Kubernetes token.

Writing to the containerd socket is equivalent to node-runtime administration. Publication remains limited to administrator/editor roles and retains checksum re-verification, license acknowledgement, idempotency, and audit events. The chart now deploys a namespace-local Distribution registry with a retained filesystem PV, internal ClusterIP, network-policy-restricted ingress, and worker health evidence. Uploaded archives are still published to the selected node's containerd store; mirroring them into the registry and configuring verified multi-node launcher pulls remain separate required work before claiming multi-node distribution.

## Upload quarantine lifecycle

Incomplete image uploads receive a fixed 24-hour resume deadline. Every 15 minutes, a bounded Celery task locks at most 200 expired active or failed sessions, deletes only regular files whose resolved path remains under Studio's configured quarantine root, records bytes and removal outcome, and changes active sessions to `expired`. Replays skip sessions with an existing cleanup result, so duplicate scheduler delivery is harmless. System audit events contain filename, sizes, status, and removal result but never the internal path; browser/API upload projections likewise exclude `artifact_destination`. Files outside the quarantine root are never removed even if a corrupted database row points to them.

## Runtime startup configurations

Startup configurations are encrypted and versioned in PostgreSQL. At deployment time the worker decrypts only the selected versions and writes deployment-labeled ConfigMaps in the isolated lab namespace; Clabernetes mounts those files into the appropriate launcher and Containerlab binds them into supported appliances. The Topology definition contains only mount paths, not configuration content. Topology audit events contain checksums and counts rather than plaintext.

Kubernetes ConfigMaps are not secret stores: runtime configuration is plaintext in the lab namespace while the deployment exists. Stopping a deployment removes its labeled runtime ConfigMaps while retaining the encrypted database versions. Limit Kubernetes API access to the reconciler role, enable Kubernetes encryption at rest, and use a dedicated secret-delivery integration before placing privileged credentials in startup configurations.

Runtime collection is enabled only by administrator-defined template commands. Collected plaintext exists transiently in the worker process, is bounded to 1 MiB, and is immediately stored as an encrypted immutable `ConfigurationVersion`. Operation results, history APIs, and audit events contain identifiers, byte counts, versions, and checksums rather than content. Configuration downloads require administrator/editor project access, are audited, and return `Cache-Control: no-store` with MIME-sniffing protection.

## Device lifecycle reconciliation

Suspend is a deliberate data-plane isolation operation: the worker first sets every linked launcher interface down and then pauses the nested appliance container. Resume reverses that order by unpausing the appliance before restoring its links. If pausing fails, already-isolated links are brought back up. The user's desired lifecycle state is durable, so the 30-second reconciliation loop re-applies suspension when Kubernetes replaces a launcher pod.

Celery Beat schedules reconciliation without Kubernetes API credentials. Its pod disables service-account token automount, drops all Linux capabilities, uses a read-only root filesystem, and stores only its disposable schedule database in a bounded `/tmp` volume. Cluster mutation remains confined to the worker ServiceAccount and all user-triggered lifecycle operations are project-authorized and audited.

## Revision restore

Published revisions are immutable and deployments remain pinned to the exact revision they started from. Restore never edits that source or an existing runtime: it creates a new editable revision with new database identities and newly encrypted configuration versions. The API requires administrator/editor access, an idempotency key, and the current draft identifier observed by the client; a concurrent draft change returns a typed conflict instead of silently discarding newer work. Audit metadata records source and restored revision identifiers, topology counts, and the operation identifier without configuration plaintext.
