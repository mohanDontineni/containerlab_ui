#!/usr/bin/env sh
set -eu

mode=${1:-plan};case "$mode" in plan|--plan)mode=plan;;remove|--remove|--apply)mode=remove;;purge|--purge)mode=purge;;*)echo "Usage: scripts/uninstall.sh [plan|remove|purge]" >&2;exit 64;;esac
namespace=${NAMESPACE:-containerlab};release=${RELEASE_NAME:-containerlab-studio};expected=${KUBE_CONTEXT:-kubernetes-admin@kubernetes};context=$(kubectl config current-context)
[ "$context" = "$expected" ]||{ echo "Refusing $mode: current context '$context' does not match KUBE_CONTEXT '$expected'." >&2;exit 2; }
runtime_namespaces=$(kubectl get namespaces -l app.kubernetes.io/managed-by=containerlab-studio -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null||true)
echo "Context: $context";echo "Release: $release";echo "Application namespace: $namespace";echo "Owned runtime namespaces:";printf '%s\n' "${runtime_namespaces:-  (none)}"
echo "Retained data: containerlab-studio-postgres, containerlab-studio-redis, containerlab-studio-artifacts, containerlab-studio-registry, containerlab-studio-registry-pv"
if [ "$mode" = plan ];then echo "PLAN ONLY — no cluster resources were changed. Use 'remove' to uninstall workloads and owned runtimes; use 'purge' only for explicit persistent-data deletion.";exit 0;fi
[ "$mode" != purge ]||[ "${PURGE_CONFIRM:-}" = "purge:$namespace" ]||{ echo "Purge refused. Set PURGE_CONFIRM='purge:$namespace' after verifying a backup." >&2;exit 6; }

if helm status "$release" -n "$namespace" >/dev/null 2>&1;then helm uninstall "$release" -n "$namespace";else echo "Helm release is already absent.";fi
for runtime_namespace in $runtime_namespaces;do [ "$runtime_namespace" = "$namespace" ]||kubectl delete namespace "$runtime_namespace" --wait=true;done
if [ "$mode" = remove ];then
  echo "Application workloads and owned runtime namespaces removed. Persistent claims/PV and externally supplied Secrets were preserved."
  exit 0
fi
kubectl -n "$namespace" delete pvc containerlab-studio-postgres containerlab-studio-redis containerlab-studio-artifacts containerlab-studio-registry --ignore-not-found --wait=true
kubectl delete pv containerlab-studio-registry-pv --ignore-not-found --wait=true
echo "Persistent Kubernetes claims and the owned registry PV were deleted. Secrets, the namespace, and node-local registry files remain for deliberate administrator handling."
