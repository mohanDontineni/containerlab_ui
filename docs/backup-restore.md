# Platform backup and restore

Studio's per-lab JSON Backup/Restore remains the normal GUI portability workflow. These scripts are platform-administrator disaster-recovery tools for rebuilding the complete installation.

## Create a coordinated bundle

```bash
scripts/backup.sh /safe/off-cluster/location --quiesce
```

`--quiesce` is mandatory. The script records replica counts, scales the gateway, worker, scheduler, and console to zero, waits for their pods to terminate, snapshots every data domain, restores the original replica counts, and waits for every restored deployment to become Ready before reporting success. The web pod remains available only as an internal read-only archive helper after the gateway is stopped. A failure or interruption attempts to restore the recorded replicas and removes the incomplete local bundle.

Each timestamped, mode-0700 bundle contains exactly:

- `database.dump`: PostgreSQL custom-format logical backup;
- `artifacts.tar.gz`: uploaded image archives, captures, and retained artifacts;
- `registry.tar.gz`: internal OCI manifests, layers, and metadata;
- `redis.tar.gz`: a forced Redis save plus persisted queue/cache data;
- `platform-secret.json`: sanitized Kubernetes Secret containing the database and Django encryption keys;
- `gateway-tls-secret.json`: sanitized TLS Secret;
- `platform-deployments.json`: source images and deployment recovery metadata;
- `manifest.json` and `SHA256SUMS`: schema, namespace, consistency mode, byte counts, and SHA-256 evidence.

Secret exports retain base64-encoded secret material and must be encrypted at rest, stored off cluster, access-controlled, and tested under the organization's key-recovery policy. Losing the Django key makes encrypted configurations and registry credentials unreadable even when PostgreSQL is restored.

## Validate without mutation

```bash
scripts/restore.sh /safe/off-cluster/location/containerlab-studio-YYYYMMDDTHHMMSSZ --verify-only
```

Validation performs no Kubernetes mutation. It requires the exact payload inventory, matching target namespace, PostgreSQL custom-archive signature, safe secret projections, deployment image metadata, byte counts, SHA-256 matches, and traversal/link-free tar members. Missing, additional, tampered, cross-namespace, or unsupported bundles are rejected.

## Destructive restore

First stop all five application deployments—web, gateway, worker, scheduler, and console—and confirm the target PostgreSQL, Redis, registry, and artifact volumes are the intended installation. Then run:

```bash
scripts/restore.sh /safe/off-cluster/location/containerlab-studio-YYYYMMDDTHHMMSSZ --confirm-stopped
```

The stopped-deployment guard is checked after full bundle validation and before any mutation. Restore cleans and reloads PostgreSQL, stops Redis and the registry, uses a short-lived no-token storage pod to replace the three retained volume trees, reapplies the protected Secrets, and brings Redis and the registry back to Ready. Failure cleanup removes the helper and attempts to return both StatefulSets to one replica.

Application deployments intentionally remain stopped. Inspect database migrations, Secret identity, registry manifests, artifact checksums, and platform configuration; run the pinned migration job; then explicitly restore the desired application replicas. A restore rehearsal should use an isolated cluster and copies of all volumes. Never test `--confirm-stopped` against the only production copy.
