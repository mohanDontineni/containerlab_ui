#!/usr/bin/env sh
set -eu
mode=${1:---plan}
namespace=${NAMESPACE:-containerlab}
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
if [ "${INSTALL_METRICS_SERVER:-false}" = "true" ]; then
  REQUIRE_METRICS_API=false "$root/scripts/preflight.sh"
  "$root/scripts/install-metrics-server.sh"
fi
"$root/scripts/preflight.sh"
[ "$mode" = "--apply" ] || { helm template containerlab-studio "$root/helm/containerlab-studio" -n "$namespace"; echo "Plan only; rerun with --apply after supplying TLS and application Secrets, PVs, and a pullable image."; exit 0; }
kubectl get secret containerlab-studio-tls -n "$namespace" >/dev/null
kubectl get secret containerlab-studio-secrets -n "$namespace" >/dev/null
helm upgrade --install containerlab-studio "$root/helm/containerlab-studio" -n "$namespace" --wait --timeout 10m
"$root/scripts/smoke-test.sh"
