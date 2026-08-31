#!/usr/bin/env sh
set -eu
out=${1:?usage: backup.sh OUTPUT_DIRECTORY}
mkdir -p "$out"
kubectl -n containerlab exec containerlab-studio-postgres-0 -- pg_dump -U containerlab -Fc containerlab > "$out/containerlab.dump"
echo "Database backup written to $out/containerlab.dump; back up retained artifact PV data separately."

