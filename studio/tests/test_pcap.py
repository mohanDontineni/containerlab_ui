import struct

import pytest

from studio.pcap import PcapError, analyze_pcap


def packet(frame, seconds=1):
    return struct.pack("<IIII", seconds, 0, len(frame), len(frame)) + frame


def capture(*frames):
    header = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHiiii", 2, 4, 0, 0, 65535, 1)
    return header + b"".join(packet(frame, index + 1) for index, frame in enumerate(frames))


def test_analyzes_ethernet_ipv4_icmp_and_tcp():
    ethernet = bytes.fromhex("00112233445566778899aabb0800")
    ipv4_icmp = bytes.fromhex("4500001c00000000400100000a0000010a0000020800000000000000")
    ipv4_tcp = bytes.fromhex("4500002800000000400600000a0000020a000001") + struct.pack("!HH", 179, 49152) + b"\x00" * 16
    result = analyze_pcap(capture(ethernet + ipv4_icmp, ethernet + ipv4_tcp))
    assert result["packets"] == 2
    assert [item["protocol"] for item in result["protocols"]] == ["ICMP", "TCP"]
    assert result["rows"][0]["source"] == "10.0.0.1"
    assert result["rows"][1]["summary"] == "TCP 179 → 49152"
    assert len(result["conversations"]) == 2


def test_rejects_malformed_and_bounds_packet_rows():
    frame = bytes.fromhex("00112233445566778899aabb0806") + b"\x00" * 28
    result = analyze_pcap(capture(frame, frame), row_limit=1)
    assert result["displayed_packets"] == 1 and result["truncated"] is True
    with pytest.raises(PcapError, match="record length"):
        analyze_pcap(capture(frame)[:-1])
    with pytest.raises(PcapError, match="packet analysis limit"):
        analyze_pcap(capture(frame, frame), packet_limit=1)
