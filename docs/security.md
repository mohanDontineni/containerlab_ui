# Security notes

The web ServiceAccount has token automount disabled. The worker/reconciler gets narrowly enumerated Clabernetes and observation permissions; Django is not cluster-admin. Browser HTTP uses same-origin sessions/CSRF, WebSockets use origin and per-session authorization, and project querysets prevent guessed UUID access.

The current home-lab certificate is self-signed. Kubernetes Secrets are not described as encrypted at rest; enable Kubernetes encryption-at-rest separately. Namespaces do not strongly isolate privileged network devices.

## Node-local image publisher

The single-node installation publishes validated Docker/OCI archives through a short-lived Kubernetes Job. A non-root init container reads the artifact PVC and stages only the selected archive into an `emptyDir`; the publisher then runs as UID 0 with all Linux capabilities dropped and can access the host containerd socket. The worker ServiceAccount can create and observe Jobs, while the web ServiceAccount has no Kubernetes token.

Writing to the containerd socket is equivalent to node-runtime administration. Publication remains limited to administrator/editor roles and retains checksum re-verification, license acknowledgement, idempotency, and audit events. This mode is not an image distribution mechanism for multi-node clusters; use a trusted registry there.
