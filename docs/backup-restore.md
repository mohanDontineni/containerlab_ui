# Backup and restore

Back up PostgreSQL with `scripts/backup.sh /safe/output/directory`. Artifact and registry backups must be performed alongside the database so digest references remain resolvable. Restore only into a stopped application after verifying the target database and artifact generation; `scripts/restore.sh` requires an explicit dump path.

