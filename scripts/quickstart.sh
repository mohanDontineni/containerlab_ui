#!/usr/bin/env sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
namespace=${NAMESPACE:-containerlab}
release=${RELEASE_NAME:-containerlab-studio}
context=${KUBE_CONTEXT:-$(kubectl config current-context)}
host=${STUDIO_HOST:-$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')}
image_repository=${IMAGE_REPOSITORY:-ghcr.io/mohandontineni/containerlab-studio}
image_tag=${IMAGE_TAG:-main}
admin_username=${ADMIN_USERNAME:-admin}
clabernetes_version=${CLABERNETES_VERSION:-0.8.0}

for tool in kubectl helm openssl; do command -v "$tool" >/dev/null 2>&1 || { echo "Missing required tool: $tool" >&2; exit 1; }; done
[ -n "$context" ] || { echo "No current Kubernetes context is selected." >&2; exit 2; }
[ -n "$host" ] || { echo "Could not discover a node IP; set STUDIO_HOST." >&2; exit 2; }

tmp=$(mktemp -d "${TMPDIR:-/tmp}/containerlab-studio.XXXXXX")
trap 'rm -rf "$tmp"' EXIT HUP INT TERM
umask 077

kubectl --context "$context" get nodes >/dev/null
kubectl --context "$context" create namespace "$namespace" --dry-run=client -o yaml | kubectl --context "$context" apply -f - >/dev/null

if ! kubectl --context "$context" get crd topologies.c9s.run >/dev/null 2>&1; then
  echo "Installing Clabernetes ${clabernetes_version}..."
  helm --kube-context "$context" upgrade --install clabernetes \
    oci://ghcr.io/clabernetes/clabernetes/clabernetes \
    --version "$clabernetes_version" --namespace "$namespace" --wait --timeout 10m
fi

if [ -n "${ADMIN_PASSWORD_FILE:-}" ]; then cp "$ADMIN_PASSWORD_FILE" "$tmp/admin-password"; else openssl rand -base64 30 | tr -d '\n' >"$tmp/admin-password"; fi
printf '%s' "$admin_username" >"$tmp/admin-username"
openssl rand -hex 32 >"$tmp/django-key"
openssl rand -base64 36 | tr -d '\n' >"$tmp/postgres-password"

if ! kubectl --context "$context" -n "$namespace" get secret containerlab-studio-secrets >/dev/null 2>&1; then
  kubectl --context "$context" -n "$namespace" create secret generic containerlab-studio-secrets \
    --from-file=DJANGO_SECRET_KEY="$tmp/django-key" --from-file=POSTGRES_PASSWORD="$tmp/postgres-password" \
    --from-file=postgres-password="$tmp/postgres-password" >/dev/null
fi
if ! kubectl --context "$context" -n "$namespace" get secret containerlab-studio-initial-admin >/dev/null 2>&1; then
  kubectl --context "$context" -n "$namespace" create secret generic containerlab-studio-initial-admin \
    --from-file=username="$tmp/admin-username" --from-file=password="$tmp/admin-password" >/dev/null
fi
if ! kubectl --context "$context" -n "$namespace" get secret containerlab-studio-tls >/dev/null 2>&1; then
  case "$host" in *:*) san="IP:$host" ;; *[!0-9.]*) san="DNS:$host" ;; *) san="IP:$host" ;; esac
  openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes -subj "/CN=$host" \
    -addext "subjectAltName=$san" -keyout "$tmp/tls.key" -out "$tmp/tls.crt" >/dev/null 2>&1
  kubectl --context "$context" -n "$namespace" create secret tls containerlab-studio-tls \
    --key "$tmp/tls.key" --cert "$tmp/tls.crt" >/dev/null
fi

echo "Installing ContainerLab Studio into namespace '$namespace' on context '$context'..."
KUBE_CONTEXT="$context" NAMESPACE="$namespace" RELEASE_NAME="$release" STUDIO_HOST="$host" \
IMAGE_REPOSITORY="$image_repository" IMAGE_TAG="$image_tag" STORAGE_CLASS="${STORAGE_CLASS:-}" \
REQUIRE_METRICS_API="${REQUIRE_METRICS_API:-false}" "$root/scripts/install.sh" apply

cat <<EOF

ContainerLab Studio is ready at https://${host}:30444/
Retrieve the generated first-login credentials with: ./studioctl credentials
The administrator must change the generated password after signing in.
EOF
