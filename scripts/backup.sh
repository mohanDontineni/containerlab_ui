#!/usr/bin/env sh
set -eu

root=${1:-};mode=${2:-};namespace=${STUDIO_NAMESPACE:-containerlab}
[ -n "$root" ] && [ "$mode" = "--quiesce" ] || { echo "usage: backup.sh OUTPUT_DIRECTORY --quiesce" >&2; exit 2; }
mkdir -p "$root"
stamp=$(date -u +%Y%m%dT%H%M%SZ);final="$root/containerlab-studio-$stamp"
[ ! -e "$final" ] || { echo "Backup destination already exists: $final" >&2; exit 2; }
work=$(mktemp -d "$root/.containerlab-studio-backup.XXXXXX");chmod 700 "$work"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd);quiesced=false

restore_replicas() {
  [ "$quiesced" = true ] || return 0
  while read -r name replicas; do kubectl -n "$namespace" scale deployment "$name" --replicas="$replicas" >/dev/null; done < "$work/replicas.txt"
  quiesced=false
}
cleanup() {
  status=$?;trap - EXIT INT TERM;restore_replicas || true
  [ "$status" -eq 0 ] || rm -rf "$work";exit "$status"
}
trap cleanup EXIT INT TERM

deployments="containerlab-studio-gateway containerlab-studio-worker containerlab-studio-scheduler containerlab-studio-console"
kubectl -n "$namespace" get deployment $deployments -o 'jsonpath={range .items[*]}{.metadata.name}{" "}{.spec.replicas}{"\n"}{end}' > "$work/replicas.txt"
kubectl -n "$namespace" get deployment containerlab-studio-web $deployments -o json > "$work/platform-deployments.json"
for name in $deployments; do kubectl -n "$namespace" scale deployment "$name" --replicas=0 >/dev/null; done
quiesced=true;attempt=0;count=1
while [ "$attempt" -lt 90 ]; do
  count=$(kubectl -n "$namespace" get pods -l 'app in (containerlab-studio-gateway,containerlab-studio-worker,containerlab-studio-scheduler,containerlab-studio-console)' --no-headers 2>/dev/null | wc -l | tr -d ' ')
  [ "$count" = 0 ] && break
  attempt=$((attempt+1));sleep 2
done
[ "$count" = 0 ] || { echo "Application workloads did not quiesce" >&2; exit 1; }

kubectl -n "$namespace" get secret containerlab-studio-secrets -o json | python3 "$script_dir/platform_backup_bundle.py" sanitize-secret > "$work/platform-secret.json"
kubectl -n "$namespace" get secret containerlab-studio-tls -o json | python3 "$script_dir/platform_backup_bundle.py" sanitize-secret > "$work/gateway-tls-secret.json"
chmod 600 "$work/platform-secret.json" "$work/gateway-tls-secret.json"
kubectl -n "$namespace" exec containerlab-studio-postgres-0 -- pg_dump -U containerlab -Fc containerlab > "$work/database.dump"
kubectl -n "$namespace" exec deploy/containerlab-studio-web -- tar -C /artifacts -czf - . > "$work/artifacts.tar.gz"
kubectl -n "$namespace" exec containerlab-studio-registry-0 -- tar -C /var/lib/registry -czf - . > "$work/registry.tar.gz"
kubectl -n "$namespace" exec containerlab-studio-redis-0 -- sh -c 'redis-cli SAVE >/dev/null && tar -C /data -czf - .' > "$work/redis.tar.gz"
python3 "$script_dir/platform_backup_bundle.py" create "$work" "$namespace" >/dev/null
restore_replicas;mv "$work" "$final";trap - EXIT INT TERM
echo "$final"
