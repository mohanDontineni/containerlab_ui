# ContainerLab Studio

ContainerLab Studio is a self-hosted Django/React application for designing and operating Kubernetes-hosted network labs through Clabernetes. This repository is an implementation-in-progress: the deployed foundation and runtime proof work, while several advanced product requirements remain explicitly tracked in `IMPLEMENTATION_STATUS.md`.

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

## Important limitations

Advanced acceptance remains in progress. Multi-node image distribution, reference-lab BGP/firewall validation, self-service account administration, quota enforcement, and several advanced CRUD surfaces remain incomplete. VM-backed devices are blocked on this node because `/dev/kvm` is absent. Browser consoles, packet capture, node-local image publication, topology lifecycle, and project role management are implemented and live-tested on the single-node deployment.
