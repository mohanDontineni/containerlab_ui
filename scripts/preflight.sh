#!/usr/bin/env sh
set -eu
namespace=${NAMESPACE:-containerlab}
[ "$(kubectl config current-context)" = "${KUBE_CONTEXT:-kubernetes-admin@kubernetes}" ] || { echo "Unexpected Kubernetes context" >&2; exit 2; }
kubectl get nodes
if kubectl get svc -A -o jsonpath='{range .items[*].spec.ports[*]}{.nodePort}{"\n"}{end}' | grep -qx 30444; then
  kubectl get svc containerlab-studio-gateway -n "$namespace" -o jsonpath='{range .spec.ports[*]}{.nodePort}{"\n"}{end}' 2>/dev/null | grep -qx 30444 || {
    echo "NodePort 30444 is occupied by a service outside this Studio release" >&2; exit 3;
  }
fi
kubectl auth can-i create deployments -n "$namespace" | grep -qx yes
if [ "${REQUIRE_METRICS_API:-true}" = "true" ]; then
  kubectl get apiservice v1beta1.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null | grep -qx True || {
    echo "Kubernetes resource metrics API is unavailable; run scripts/install-metrics-server.sh or set REQUIRE_METRICS_API=false for a degraded installation." >&2
    exit 4
  }
fi
