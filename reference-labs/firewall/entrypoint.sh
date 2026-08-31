#!/bin/sh
set -eu

# Containerlab creates data-plane links after the container starts. Wait for
# both firewall ports before applying the versioned Studio configuration.
attempt=0
until ip link show eth1 >/dev/null 2>&1 && ip link show eth2 >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        echo "Timed out waiting for eth1 and eth2" >&2
        exit 1
    fi
    sleep 1
done

if [ ! -s /etc/studio/firewall.sh ]; then
    echo "Missing /etc/studio/firewall.sh" >&2
    exit 1
fi

/bin/sh /etc/studio/firewall.sh
exec "$@"
