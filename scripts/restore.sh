#!/usr/bin/env sh
set -eu

bundle=${1:-};mode=${2:-};namespace=${STUDIO_NAMESPACE:-containerlab}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
[ -d "$bundle" ] || { echo "Backup bundle does not exist" >&2; exit 2; }
python3 "$script_dir/platform_backup_bundle.py" verify "$bundle" "$namespace"
[ "$mode" = "--verify-only" ] && exit 0
[ "$mode" = "--confirm-stopped" ] || { echo "Use --verify-only or --confirm-stopped" >&2; exit 2; }

for name in containerlab-studio-web containerlab-studio-gateway containerlab-studio-worker containerlab-studio-scheduler containerlab-studio-console; do
  replicas=$(kubectl -n "$namespace" get deployment "$name" -o 'jsonpath={.spec.replicas}')
  [ "$replicas" = 0 ] || { echo "Restore refused: deployment $name is not stopped" >&2; exit 3; }
done

storage_stopped=false;helper_created=false
cleanup() {
  status=$?;trap - EXIT INT TERM
  if [ "$helper_created" = true ]; then kubectl -n "$namespace" delete pod containerlab-studio-restore-storage --ignore-not-found --wait=true >/dev/null || true; fi
  if [ "$storage_stopped" = true ]; then kubectl -n "$namespace" scale statefulset containerlab-studio-redis containerlab-studio-registry --replicas=1 >/dev/null || true; fi
  exit "$status"
}
trap cleanup EXIT INT TERM

kubectl -n "$namespace" exec -i containerlab-studio-postgres-0 -- pg_restore -U containerlab -d containerlab --clean --if-exists --exit-on-error < "$bundle/database.dump"
kubectl -n "$namespace" scale statefulset containerlab-studio-redis containerlab-studio-registry --replicas=0 >/dev/null
storage_stopped=true
kubectl -n "$namespace" wait --for=delete pod/containerlab-studio-redis-0 pod/containerlab-studio-registry-0 --timeout=180s
image=$(python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); print(next(item["spec"]["template"]["spec"]["containers"][0]["image"] for item in data["items"] if item["metadata"]["name"]=="containerlab-studio-web"))' "$bundle/platform-deployments.json")
kubectl -n "$namespace" delete pod containerlab-studio-restore-storage --ignore-not-found >/dev/null
kubectl -n "$namespace" run containerlab-studio-restore-storage --restart=Never --image="$image" --overrides='{"spec":{"automountServiceAccountToken":false,"containers":[{"name":"containerlab-studio-restore-storage","image":"'"$image"'","command":["sleep","600"],"volumeMounts":[{"name":"artifacts","mountPath":"/restore/artifacts"},{"name":"registry","mountPath":"/restore/registry"},{"name":"redis","mountPath":"/restore/redis"}]}],"volumes":[{"name":"artifacts","persistentVolumeClaim":{"claimName":"containerlab-studio-artifacts"}},{"name":"registry","persistentVolumeClaim":{"claimName":"containerlab-studio-registry"}},{"name":"redis","persistentVolumeClaim":{"claimName":"containerlab-studio-redis"}}]}}' >/dev/null
helper_created=true
kubectl -n "$namespace" wait --for=condition=Ready pod/containerlab-studio-restore-storage --timeout=180s
kubectl -n "$namespace" exec containerlab-studio-restore-storage -- sh -c 'find /restore/artifacts /restore/registry /restore/redis -mindepth 1 -delete'
kubectl -n "$namespace" exec -i containerlab-studio-restore-storage -- tar -C /restore/artifacts -xzf - < "$bundle/artifacts.tar.gz"
kubectl -n "$namespace" exec -i containerlab-studio-restore-storage -- tar -C /restore/registry -xzf - < "$bundle/registry.tar.gz"
kubectl -n "$namespace" exec -i containerlab-studio-restore-storage -- tar -C /restore/redis -xzf - < "$bundle/redis.tar.gz"
kubectl -n "$namespace" delete pod containerlab-studio-restore-storage --wait=true >/dev/null
helper_created=false
kubectl apply -f "$bundle/platform-secret.json" >/dev/null
kubectl apply -f "$bundle/gateway-tls-secret.json" >/dev/null
kubectl -n "$namespace" scale statefulset containerlab-studio-redis containerlab-studio-registry --replicas=1 >/dev/null
storage_stopped=false
kubectl -n "$namespace" rollout status statefulset/containerlab-studio-redis --timeout=180s
kubectl -n "$namespace" rollout status statefulset/containerlab-studio-registry --timeout=180s
trap - EXIT INT TERM
echo "Restore completed. Run migrations and explicitly restart application deployments after validation."
