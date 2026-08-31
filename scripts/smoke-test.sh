#!/usr/bin/env sh
set -eu
host=${STUDIO_HOST:-192.168.1.148}
code=$(curl -ksS -o /dev/null -w '%{http_code}' "https://${host}:30444/admin/login/")
[ "$code" = 200 ] || { echo "GUI returned $code" >&2; exit 1; }
kubectl -n containerlab get topology.c9s.run/studio-smoke -o jsonpath='{.status.topologyReady}' | grep -qx true
echo "GUI and runtime smoke checks passed"

