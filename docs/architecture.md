# Architecture

```mermaid
flowchart LR
  Browser -->|HTTPS/WSS :30444| Gateway
  Gateway --> Web[Django ASGI + Channels]
  Web --> Postgres
  Web --> Redis
  Worker[Celery worker/reconciler] --> Redis
  Worker --> Postgres
  Worker -->|c9s.run API| K8s[Kubernetes API]
  K8s --> C9s[Clabernetes manager]
  C9s --> Launchers[Launcher pods + device containers]
  Web --> Artifacts[Persistent quarantine/artifacts]
```

Saved `LabRevision` state is immutable and separate from `LabDeployment` desired/observed state. Uploads become quarantined `ImageArtifact` records; only a successful approved build and immutable digest creates a `PublishedImage`.

Example 1: a duplicate deploy click reuses its idempotency record and the database prevents another active operation. Example 2: pod Running does not set device readiness; reconciliation consumes Clabernetes Node/Topology readiness separately.

