#!/usr/bin/env sh
set -eu
helm uninstall containerlab-studio -n containerlab
echo "Application release removed. Retained PVs, data, Clabernetes, namespace, and lab resources were preserved."

