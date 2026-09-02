# Installation lifecycle

These commands are for platform administrators. Normal project, image, topology, runtime, console, configuration, and diagnostic workflows remain GUI-only.

## Quick start

With `kubectl` pointed at the intended cluster and Helm 3 plus OpenSSL installed:

```bash
./studioctl install
./studioctl credentials
```

Quick start creates the namespace, installs the pinned Clabernetes chart when absent, generates strong database/application/bootstrap secrets, creates a self-signed certificate for the discovered node IP, waits for Helm readiness, and runs the smoke test. Existing Secrets are never overwritten. The generated administrator must rotate the password on first sign-in.

Defaults may be changed without editing manifests: `KUBE_CONTEXT`, `NAMESPACE`, `STUDIO_HOST`, `IMAGE_REPOSITORY`, `IMAGE_TAG`, `STORAGE_CLASS`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD_FILE`. The default registry PVC uses the cluster's dynamic StorageClass. Optional node-local registry storage requires an explicit values file, node hostname, and prepared host path.

Both lifecycle scripts fail closed when the active Kubernetes context differs from `KUBE_CONTEXT` (default: the currently selected context). They accept `NAMESPACE`, `RELEASE_NAME`, `VALUES_FILE`, `IMAGE_REPOSITORY`, `IMAGE_TAG`, and `STORAGE_CLASS` instead of requiring manifest edits. Plan is always the default and makes no cluster changes.

## Install or upgrade

Example 1 — render and inspect the exact intended release without mutation:

```bash
KUBE_CONTEXT=kubernetes-admin@kubernetes \
NAMESPACE=containerlab \
IMAGE_REPOSITORY=docker.io/library/containerlab-studio \
IMAGE_TAG=d82ee2d \
STORAGE_CLASS=studio-local \
PLAN_OUTPUT=/tmp/containerlab-studio-plan.yaml \
scripts/install.sh plan
```

The plan runs the read-only cluster preflight, lints the chart, renders the complete manifest, and identifies context, namespace, release, image, and output path. Review the output and provision `containerlab-studio-tls`, `containerlab-studio-secrets`, and `containerlab-studio-initial-admin` before applying. `./studioctl install` does this automatically for a new installation.

Apply uses the same variables and refuses `IMAGE_TAG=latest`:

```bash
KUBE_CONTEXT=kubernetes-admin@kubernetes \
NAMESPACE=containerlab \
IMAGE_REPOSITORY=docker.io/library/containerlab-studio \
IMAGE_TAG=d82ee2d \
STORAGE_CLASS=studio-local \
scripts/install.sh apply
```

For an existing installation previously maintained with rendered `kubectl apply`
manifests, inspect the plan first and set `ADOPT_EXISTING_RESOURCES=true` on the
first apply. This explicitly enables Helm ownership adoption; it is off by
default and must not be used to take over resources belonging to another
application.

After Helm readiness completes, the script records a `containerlab-studio-installation` ConfigMap containing the release, selected context, immutable image, and version-lock checksum, then verifies the HTTPS endpoint on NodePort 30444.

## Remove or purge

Example 2 — preview exactly what removal owns and what data it retains:

```bash
KUBE_CONTEXT=kubernetes-admin@kubernetes NAMESPACE=containerlab scripts/uninstall.sh plan
```

`remove` uninstalls only the named Helm release and namespaces labeled `app.kubernetes.io/managed-by=containerlab-studio`. Persistent PostgreSQL, Redis, artifacts, and registry claims plus the registry PV carry `helm.sh/resource-policy: keep` and remain. Externally supplied Secrets and the application namespace also remain.

```bash
KUBE_CONTEXT=kubernetes-admin@kubernetes NAMESPACE=containerlab scripts/uninstall.sh remove
```

`purge` performs the same workload removal and then deletes only the four exact PVCs and owned registry PV. It refuses to perform any mutation unless the explicit namespace-bound confirmation is present and a backup has been independently verified:

```bash
PURGE_CONFIRM=purge:containerlab \
KUBE_CONTEXT=kubernetes-admin@kubernetes \
NAMESPACE=containerlab \
scripts/uninstall.sh purge
```

Purge deliberately does not delete Secrets, the namespace, or node-local registry files. Those may be externally managed or require host-specific retention review; the script prints this residual scope instead of using recursive host deletion.
