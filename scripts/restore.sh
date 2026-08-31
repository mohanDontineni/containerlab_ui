#!/usr/bin/env sh
set -eu
dump=${1:?usage: restore.sh DUMP_FILE}
[ -f "$dump" ] || { echo "Dump does not exist" >&2; exit 2; }
kubectl -n containerlab exec -i containerlab-studio-postgres-0 -- pg_restore -U containerlab -d containerlab --clean --if-exists < "$dump"

