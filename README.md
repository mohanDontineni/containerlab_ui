# ContainerLab Studio

ContainerLab Studio is a self-hosted Django/React application for designing and operating Kubernetes-hosted network labs through Clabernetes. This repository is an implementation-in-progress: the deployed foundation and runtime proof work, while several advanced product requirements remain explicitly tracked in `IMPLEMENTATION_STATUS.md`.

The normal operator workflow is GUI-only. Users create projects and labs, add images, design interface-aware topologies, enter startup configuration, deploy, troubleshoot, and control devices without writing Containerlab YAML, Kubernetes manifests, or shell scripts. The application keeps generated runtime resources behind its validated adapter boundary. Product-native lab Backup/Restore uses a JSON bundle for portability and does not require YAML editing.

## Deployed home-lab instance

- Kubernetes context: `kubernetes-admin@kubernetes`
- API server: `https://192.168.1.148:6443`
- Namespace: `containerlab`
- GUI/admin endpoint: `https://192.168.1.148:30444/admin/login/`
- TLS: locally generated certificate with IP SAN `192.168.1.148`; import the certificate into the browser trust store or accept the home-lab warning.

Retrieve the one-time bootstrap login without printing it into shell history:

```bash
kubectl -n containerlab get secret containerlab-studio-initial-admin -o jsonpath='{.data.username}' | base64 -d; echo
kubectl -n containerlab get secret containerlab-studio-initial-admin -o jsonpath='{.data.password}' | base64 -d; echo
```

Sign in, change the password, then delete that bootstrap Secret.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python manage.py migrate
.venv/bin/pytest -q
pnpm --dir frontend install
pnpm --dir frontend build
```

The production image uses Python 3.13, Gunicorn with Uvicorn workers, a multi-stage React build, and a non-root UID. See `scripts/` for preflight, install, smoke test, backup, restore, and uninstall entry points.

Administrative installation is plan-first and context-bound. `scripts/install.sh` defaults to a non-mutating render; `apply` refuses the mutable `latest` tag, requires the TLS/application Secrets, records the selected context and exact image in-cluster, runs Helm with readiness waits, and performs the HTTPS smoke test. `scripts/uninstall.sh` also defaults to a non-mutating ownership/data preview. `remove` deletes the Helm workloads and labeled Studio runtime namespaces while Helm keep policies preserve all four PVCs and the registry PV; `purge` additionally deletes those exact Kubernetes storage objects only after `PURGE_CONFIRM=purge:<namespace>`. See [`docs/installation-lifecycle.md`](docs/installation-lifecycle.md).

Platform disaster recovery uses a mandatory-quiesce, integrity-manifested bundle of PostgreSQL, artifacts, registry, Redis, protected Secrets, and deployment metadata. Always validate with `scripts/restore.sh BUNDLE --verify-only` before an isolated restore rehearsal; see [`docs/backup-restore.md`](docs/backup-restore.md).

The labeled operator screenshot catalog and its read-only capture procedure are in [`training/README.md`](training/README.md).

## Important limitations

Advanced acceptance remains in progress. Uploaded images are mirrored into a persistent internal OCI registry, while multi-node launcher pulls remain unverified. VM-backed devices are blocked on this node because `/dev/kvm` is absent. Native self-service account security, browser consoles, packet capture, verified image publication/repair, workload-scoped platform ingress, guarded topology/runtime removal, Save As and revision restore, durable per-device suspend/resume, project role management, resource quotas, encrypted startup and collected configuration versioning, BGP routing, and default-deny nftables firewall policy are implemented and live-tested on the single-node deployment.

The frontend build is reproducible from its frozen pnpm lockfile and currently reports zero known dependency vulnerabilities. The deployed interface is self-contained and does not require third-party font or asset hosts at runtime.
