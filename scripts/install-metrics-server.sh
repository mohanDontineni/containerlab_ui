#!/usr/bin/env sh
set -eu
version=${METRICS_SERVER_VERSION:-v0.7.2}
[ "$version" = "v0.7.2" ] || { echo "Only the platform-verified metrics-server v0.7.2 is accepted." >&2; exit 2; }
kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/${version}/components.yaml"
if [ "${KUBELET_INSECURE_TLS:-false}" = "true" ]; then
  kubectl get deployment metrics-server -n kube-system -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -q -- '--kubelet-insecure-tls' ||
    kubectl patch deployment metrics-server -n kube-system --type=json -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
fi
kubectl rollout status deployment/metrics-server -n kube-system --timeout=3m
kubectl wait --for=condition=Available apiservice/v1beta1.metrics.k8s.io --timeout=2m
kubectl top node >/dev/null
echo "metrics.k8s.io is available and serving node samples."
