#!/bin/sh
set -eu

ip address replace 10.10.0.1/24 dev eth1
ip address replace 10.20.0.1/24 dev eth2
ip link set eth1 up
ip link set eth2 up
sysctl -w net.ipv4.ip_forward=1

nft -f - <<'EOF'
flush ruleset
table inet studio_filter {
  counter allowed_icmp {}
  counter denied_tcp {}

  chain forward {
    type filter hook forward priority filter; policy drop;
    ct state established,related accept
    iifname "eth1" oifname "eth2" ip protocol icmp counter name allowed_icmp accept
    iifname "eth1" oifname "eth2" tcp dport 8080 counter name denied_tcp drop
  }
}
EOF
