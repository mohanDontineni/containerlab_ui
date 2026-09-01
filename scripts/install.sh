#!/usr/bin/env sh
set -eu

mode=${1:-plan};case "$mode" in plan|--plan)mode=plan;;apply|--apply)mode=apply;;*)echo "Usage: scripts/install.sh [plan|apply]" >&2;exit 64;;esac
namespace=${NAMESPACE:-containerlab};release=${RELEASE_NAME:-containerlab-studio};root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
chart="$root/helm/containerlab-studio";values=${VALUES_FILE:-};image_repository=${IMAGE_REPOSITORY:-containerlab-studio};image_tag=${IMAGE_TAG:-latest};storage_class=${STORAGE_CLASS:-}
context=$(kubectl config current-context);expected=${KUBE_CONTEXT:-kubernetes-admin@kubernetes}
[ "$context" = "$expected" ]||{ echo "Refusing $mode: current context '$context' does not match KUBE_CONTEXT '$expected'." >&2;exit 2; }

render(){
  if [ -n "$values" ];then helm template "$release" "$chart" --namespace "$namespace" -f "$values" --set "image.repository=$image_repository" --set "image.tag=$image_tag" --set "storageClass=$storage_class";
  else helm template "$release" "$chart" --namespace "$namespace" --set "image.repository=$image_repository" --set "image.tag=$image_tag" --set "storageClass=$storage_class";fi
}
lint(){
  if [ -n "$values" ];then helm lint "$chart" -f "$values" --set "image.repository=$image_repository" --set "image.tag=$image_tag" --set "storageClass=$storage_class";
  else helm lint "$chart" --set "image.repository=$image_repository" --set "image.tag=$image_tag" --set "storageClass=$storage_class";fi
}
upgrade(){
  if [ -n "$values" ];then helm upgrade --install "$release" "$chart" --namespace "$namespace" --create-namespace -f "$values" --set "image.repository=$image_repository" --set "image.tag=$image_tag" --set "storageClass=$storage_class" --wait --timeout 10m;
  else helm upgrade --install "$release" "$chart" --namespace "$namespace" --create-namespace --set "image.repository=$image_repository" --set "image.tag=$image_tag" --set "storageClass=$storage_class" --wait --timeout 10m;fi
}

metrics_required=${REQUIRE_METRICS_API:-true};[ "${INSTALL_METRICS_SERVER:-false}" = true ]&&metrics_required=false
REQUIRE_METRICS_API="$metrics_required" NAMESPACE="$namespace" KUBE_CONTEXT="$expected" "$root/scripts/preflight.sh"
lint
if [ "$mode" = plan ];then
  output=${PLAN_OUTPUT:-/tmp/containerlab-studio-install-plan.yaml};render >"$output"
  echo "PLAN ONLY — no cluster resources were changed."
  echo "Context: $context";echo "Release: $release";echo "Namespace: $namespace";echo "Image: $image_repository:$image_tag";echo "Rendered manifest: $output"
  echo "Apply requires existing Secrets containerlab-studio-tls and containerlab-studio-secrets plus a non-latest immutable IMAGE_TAG."
  exit 0
fi
[ "$image_tag" != latest ]&&[ -n "$image_tag" ]||{ echo "Apply requires an immutable IMAGE_TAG; 'latest' is refused." >&2;exit 5; }
if [ "${INSTALL_METRICS_SERVER:-false}" = true ];then "$root/scripts/install-metrics-server.sh";REQUIRE_METRICS_API=true NAMESPACE="$namespace" KUBE_CONTEXT="$expected" "$root/scripts/preflight.sh";fi
kubectl get secret containerlab-studio-tls -n "$namespace" >/dev/null
kubectl get secret containerlab-studio-secrets -n "$namespace" >/dev/null
upgrade
lock_checksum=$(cksum "$root/versions.lock.yaml"|awk '{print $1":"$2}')
kubectl -n "$namespace" create configmap containerlab-studio-installation --from-literal=release="$release" --from-literal=selected-context="$context" --from-literal=image="$image_repository:$image_tag" --from-literal=version-lock-checksum="$lock_checksum" --dry-run=client -o yaml|kubectl apply -f -
NAMESPACE="$namespace" HOST=${HOST:-192.168.1.148} "$root/scripts/smoke-test.sh"
echo "Installed $release from $context. GUI: https://${HOST:-192.168.1.148}:30444/"
