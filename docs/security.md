# Security notes

The web ServiceAccount has token automount disabled. The worker/reconciler gets narrowly enumerated Clabernetes and observation permissions; Django is not cluster-admin. Browser HTTP uses same-origin sessions/CSRF, WebSockets use origin and per-session authorization, and project querysets prevent guessed UUID access.

The current home-lab certificate is self-signed. Private registry publication remains blocked until a worker- and launcher-trusted CA/auth design is installed. Kubernetes Secrets are not described as encrypted at rest; enable Kubernetes encryption-at-rest separately. Namespaces do not strongly isolate privileged network devices.

