from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_firewall_appliance_is_pinned_and_fails_closed():
    dockerfile = (ROOT / "reference-labs/firewall/Dockerfile").read_text()
    entrypoint = (ROOT / "reference-labs/firewall/entrypoint.sh").read_text()
    assert "FROM alpine:3.22.5" in dockerfile
    assert "nftables" in dockerfile and "busybox-extras" in dockerfile
    assert "until ip link show eth1" in entrypoint
    assert 'if [ ! -s /etc/studio/firewall.sh ]' in entrypoint
    assert "/bin/sh /etc/studio/firewall.sh" in entrypoint


def test_firewall_reference_policy_is_default_deny_with_auditable_counters():
    policy = (ROOT / "reference-labs/firewall/firewall.sh").read_text()
    assert "net.ipv4.ip_forward=1" in policy
    assert "policy drop" in policy
    assert "ct state established,related accept" in policy
    assert "counter allowed_icmp" in policy
    assert "counter denied_tcp" in policy
    assert 'tcp dport 8080 counter name denied_tcp drop' in policy
