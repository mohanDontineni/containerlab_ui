# Security notes

The web ServiceAccount has token automount disabled. The worker/reconciler gets narrowly enumerated Clabernetes and observation permissions; Django is not cluster-admin. Browser HTTP uses same-origin sessions/CSRF, WebSockets use origin and per-session authorization, and project querysets prevent guessed UUID access.

The current home-lab certificate is self-signed. Kubernetes Secrets are not described as encrypted at rest; enable Kubernetes encryption-at-rest separately. Namespaces do not strongly isolate privileged network devices.

## Node-local image publisher

The single-node installation publishes validated Docker/OCI archives through a short-lived Kubernetes Job. A non-root init container reads the artifact PVC and stages only the selected archive into an `emptyDir`; the publisher then runs as UID 0 with all Linux capabilities dropped and can access the host containerd socket. The worker ServiceAccount can create and observe Jobs, while the web ServiceAccount has no Kubernetes token.

Writing to the containerd socket is equivalent to node-runtime administration. Publication remains limited to administrator/editor roles and retains checksum re-verification, license acknowledgement, idempotency, and audit events. This mode is not an image distribution mechanism for multi-node clusters; use a trusted registry there.

## Runtime startup configurations

Startup configurations are encrypted and versioned in PostgreSQL. At deployment time the worker decrypts only the selected versions and writes deployment-labeled ConfigMaps in the isolated lab namespace; Clabernetes mounts those files into the appropriate launcher and Containerlab binds them into supported appliances. The Topology definition contains only mount paths, not configuration content. Topology audit events contain checksums and counts rather than plaintext.

Kubernetes ConfigMaps are not secret stores: runtime configuration is plaintext in the lab namespace while the deployment exists. Stopping a deployment removes its labeled runtime ConfigMaps while retaining the encrypted database versions. Limit Kubernetes API access to the reconciler role, enable Kubernetes encryption at rest, and use a dedicated secret-delivery integration before placing privileged credentials in startup configurations.

Runtime collection is enabled only by administrator-defined template commands. Collected plaintext exists transiently in the worker process, is bounded to 1 MiB, and is immediately stored as an encrypted immutable `ConfigurationVersion`. Operation results, history APIs, and audit events contain identifiers, byte counts, versions, and checksums rather than content. Configuration downloads require administrator/editor project access, are audited, and return `Cache-Control: no-store` with MIME-sniffing protection.

## Device lifecycle reconciliation

Suspend is a deliberate data-plane isolation operation: the worker first sets every linked launcher interface down and then pauses the nested appliance container. Resume reverses that order by unpausing the appliance before restoring its links. If pausing fails, already-isolated links are brought back up. The user's desired lifecycle state is durable, so the 30-second reconciliation loop re-applies suspension when Kubernetes replaces a launcher pod.

Celery Beat schedules reconciliation without Kubernetes API credentials. Its pod disables service-account token automount, drops all Linux capabilities, uses a read-only root filesystem, and stores only its disposable schedule database in a bounded `/tmp` volume. Cluster mutation remains confined to the worker ServiceAccount and all user-triggered lifecycle operations are project-authorized and audited.

## Revision restore

Published revisions are immutable and deployments remain pinned to the exact revision they started from. Restore never edits that source or an existing runtime: it creates a new editable revision with new database identities and newly encrypted configuration versions. The API requires administrator/editor access, an idempotency key, and the current draft identifier observed by the client; a concurrent draft change returns a typed conflict instead of silently discarding newer work. Audit metadata records source and restored revision identifiers, topology counts, and the operation identifier without configuration plaintext.
