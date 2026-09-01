import ipaddress
import struct
from collections import Counter, defaultdict


class PcapError(ValueError):
    pass


MAGIC = {
    b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
    b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
    b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
    b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
}


def _mac(raw):
    return ":".join(f"{part:02x}" for part in raw)


def _decode(frame):
    if len(frame) < 14:
        return "Other", "—", "—", "Truncated Ethernet frame"
    destination, source = _mac(frame[:6]), _mac(frame[6:12])
    ether_type = struct.unpack("!H", frame[12:14])[0]
    offset = 14
    vlan = ""
    if ether_type in (0x8100, 0x88A8) and len(frame) >= 18:
        tag, ether_type = struct.unpack("!HH", frame[14:18])
        vlan, offset = f" · VLAN {tag & 0x0fff}", 18
    if ether_type == 0x0806 and len(frame) >= offset + 28:
        operation = struct.unpack("!H", frame[offset + 6:offset + 8])[0]
        sender = str(ipaddress.ip_address(frame[offset + 14:offset + 18]))
        target = str(ipaddress.ip_address(frame[offset + 24:offset + 28]))
        return "ARP", sender, target, f"ARP {'request' if operation == 1 else 'reply' if operation == 2 else operation}{vlan}"
    if ether_type == 0x0800 and len(frame) >= offset + 20:
        ihl = (frame[offset] & 0x0f) * 4
        if ihl < 20 or len(frame) < offset + ihl:
            return "IPv4", source, destination, "Malformed IPv4 header"
        protocol = frame[offset + 9]
        src = str(ipaddress.ip_address(frame[offset + 12:offset + 16]))
        dst = str(ipaddress.ip_address(frame[offset + 16:offset + 20]))
        transport = offset + ihl
        return _transport(protocol, frame, transport, src, dst, vlan, ipv6=False)
    if ether_type == 0x86DD and len(frame) >= offset + 40:
        protocol = frame[offset + 6]
        src = str(ipaddress.ip_address(frame[offset + 8:offset + 24]))
        dst = str(ipaddress.ip_address(frame[offset + 24:offset + 40]))
        return _transport(protocol, frame, offset + 40, src, dst, vlan, ipv6=True)
    return "Other", source, destination, f"EtherType 0x{ether_type:04x}{vlan}"


def _transport(protocol, frame, offset, src, dst, vlan, ipv6):
    labels = {1: "ICMP", 6: "TCP", 17: "UDP", 58: "ICMPv6"}
    label = labels.get(protocol, "IPv6" if ipv6 else "IPv4")
    if protocol in (6, 17) and len(frame) >= offset + 4:
        source_port, destination_port = struct.unpack("!HH", frame[offset:offset + 4])
        return label, f"{src}:{source_port}", f"{dst}:{destination_port}", f"{label} {source_port} → {destination_port}{vlan}"
    if protocol in (1, 58) and len(frame) >= offset + 2:
        return label, src, dst, f"{label} type {frame[offset]} code {frame[offset + 1]}{vlan}"
    return label, src, dst, f"IP protocol {protocol}{vlan}"


def analyze_pcap(payload, packet_limit=5000, row_limit=500, conversation_limit=100):
    if len(payload) < 24:
        raise PcapError("Capture header is truncated.")
    layout = MAGIC.get(payload[:4])
    if not layout:
        raise PcapError("Only classic PCAP captures are supported.")
    endian, resolution = layout
    major, minor, _, _, snaplen, network = struct.unpack(endian + "HHiiii", payload[4:24])
    if major != 2 or network != 1 or snaplen <= 0:
        raise PcapError("Capture format or link type is unsupported.")
    cursor, first_time, total_packets, total_bytes = 24, None, 0, 0
    protocols, protocol_bytes, conversations, rows = Counter(), Counter(), defaultdict(lambda: [0, 0]) , []
    while cursor < len(payload):
        if len(payload) - cursor < 16:
            raise PcapError("Packet record header is truncated.")
        seconds, fraction, included, original = struct.unpack(endian + "IIII", payload[cursor:cursor + 16])
        cursor += 16
        if included > snaplen or included > len(payload) - cursor:
            raise PcapError("Packet record length is invalid.")
        frame = payload[cursor:cursor + included]
        cursor += included
        total_packets += 1
        if total_packets > packet_limit:
            raise PcapError(f"Capture exceeds the {packet_limit} packet analysis limit.")
        total_bytes += original
        timestamp = seconds + fraction / resolution
        first_time = timestamp if first_time is None else first_time
        protocol, source, destination, summary = _decode(frame)
        protocols[protocol] += 1
        protocol_bytes[protocol] += original
        pair = tuple(sorted((source, destination)))
        conversation = (protocol, *pair)
        conversations[conversation][0] += 1
        conversations[conversation][1] += original
        if len(rows) < row_limit:
            rows.append({"number": total_packets, "relative_ms": round((timestamp - first_time) * 1000, 3), "length": original,
                         "protocol": protocol, "source": source, "destination": destination, "summary": summary})
    mix = [{"protocol": name, "packets": count, "bytes": protocol_bytes[name]} for name, count in protocols.most_common()]
    top = sorted(conversations.items(), key=lambda item: (-item[1][0], -item[1][1]))[:conversation_limit]
    return {"format": f"PCAP {major}.{minor}", "packets": total_packets, "bytes": total_bytes,
            "displayed_packets": len(rows), "protocols": mix,
            "conversations": [{"protocol": key[0], "endpoint_a": key[1], "endpoint_b": key[2], "packets": value[0], "bytes": value[1]} for key, value in top],
            "rows": rows, "truncated": total_packets > len(rows)}
