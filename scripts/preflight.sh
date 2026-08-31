#!/usr/bin/env sh
set -eu
namespace=${NAMESPACE:-containerlab}
[ "$(kubectl config current-context)" = "${KUBE_CONTEXT:-kubernetes-admin@kubernetes}" ] || { echo "Unexpected Kubernetes context" >&2; exit 2; }
kubectl get nodes
kubectl get svc -A -o jsonpath='{range .items[*].spec.ports[*]}{.nodePort}{"\n"}{end}' | grep -qx 30444 && { echo "NodePort 30444 is occupied" >&2; exit 3; } || true
kubectl auth can-i create deployments -n "$namespace" | grep -qx yes

