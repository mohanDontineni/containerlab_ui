#!/usr/bin/env sh
set -eu
host=${STUDIO_HOST:-192.168.1.148}
namespace=${NAMESPACE:-containerlab}
code=$(curl -ksS -o /dev/null -w '%{http_code}' "https://${host}:30444/admin/login/")
[ "$code" = 200 ] || { echo "GUI returned $code" >&2; exit 1; }
for deployment in web worker scheduler console;do kubectl -n "$namespace" get deployment "containerlab-studio-$deployment" -o jsonpath='{.status.availableReplicas}'|grep -Eq '^[1-9][0-9]*$';done
kubectl get crd topologies.c9s.run >/dev/null
if kubectl -n "$namespace" get topology.c9s.run/studio-smoke >/dev/null 2>&1;then
  kubectl -n "$namespace" get topology.c9s.run/studio-smoke -o jsonpath='{.status.topologyReady}'|grep -qx true
  echo "GUI, application, Clabernetes API, and runtime fixture smoke checks passed."
else
  echo "GUI, application, and Clabernetes API smoke checks passed; no optional studio-smoke runtime fixture is installed."
fi
