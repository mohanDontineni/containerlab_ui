# Backup and restore

Back up PostgreSQL with `scripts/backup.sh /safe/output/directory`. Artifact storage and the registry filesystem at the configured `registry.localPersistence.path` (default `/var/lib/containerlab-studio/registry`) must be snapshotted alongside the database so archive checksums and recorded manifest digests remain resolvable. Restore only into a stopped application after verifying the target database, artifact generation, and registry snapshot; `scripts/restore.sh` requires an explicit dump path.
