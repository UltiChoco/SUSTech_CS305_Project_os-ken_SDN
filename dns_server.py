import struct

from os_ken.lib import addrconv
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ipv4
from os_ken.lib.packet import packet
from os_ken.lib.packet import udp
from os_ken.ofproto import ether
from os_ken.ofproto import inet


class DNSServer:
    DNS_SERVER_IP = "192.168.1.1"
    DNS_SERVER_MAC = "7e:49:b3:f0:f9:99"
    DNS_TABLE = {
        "h1.local": "192.168.1.2",
        "h2.local": "192.168.1.3",
        "web.local": "192.168.1.3",
    }
    DNS_AAAA_TABLE = {
        "h1.local": "fd00::2",
        "h2.local": "fd00::3",
        "web.local": "fd00::3",
    }
    DNS_CNAME_TABLE = {
        "www.local": "web.local",
    }
    DEFAULT_TTL = 60

    @classmethod
    def handle_dns(cls, datapath, in_port, pkt):
        eth = pkt.get_protocol(ethernet.ethernet)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        udp_pkt = pkt.get_protocol(udp.udp)
        if eth is None or ip_pkt is None or udp_pkt is None:
            return

        payload = cls._extract_udp_payload(pkt, udp_pkt)
        if not payload:
            return

        response_payload = cls._build_response(payload)
        if response_payload is None:
            return

        resp_pkt = packet.Packet()
        resp_pkt.add_protocol(ethernet.ethernet(
            ethertype=ether.ETH_TYPE_IP,
            dst=eth.src,
            src=cls.DNS_SERVER_MAC
        ))
        resp_pkt.add_protocol(ipv4.ipv4(
            src=cls.DNS_SERVER_IP,
            dst=ip_pkt.src,
            proto=inet.IPPROTO_UDP
        ))
        resp_pkt.add_protocol(udp.udp(
            src_port=53,
            dst_port=udp_pkt.src_port
        ))
        resp_pkt.add_protocol(response_payload)

        cls._send_packet(datapath, in_port, resp_pkt)

    @classmethod
    def _extract_udp_payload(cls, pkt, udp_pkt):
        payload = None
        for i, proto in enumerate(pkt.protocols):
            if proto == udp_pkt:
                if i + 1 < len(pkt.protocols):
                    next_proto = pkt.protocols[i + 1]
                    if isinstance(next_proto, (bytes, bytearray)):
                        payload = bytes(next_proto)
                break

        if payload is None:
            for proto in pkt.protocols:
                if isinstance(proto, (bytes, bytearray)):
                    payload = bytes(proto)
        return payload

    @classmethod
    def _build_response(cls, data):
        header = cls._parse_header(data)
        if header is None:
            return None

        transaction_id, flags, qdcount = header
        rd_flag = flags & 0x0100

        if qdcount < 1:
            return cls._build_error_response(transaction_id, rd_flag, 1)

        query = cls._parse_query(data, 12)
        if query is None:
            return cls._build_error_response(transaction_id, rd_flag, 1)

        qname, qtype, qclass, query_bytes = query

        if qclass != 1 or qtype not in {1, 5, 28}:
            return cls._build_error_response(transaction_id, rd_flag, 4, query_bytes=query_bytes)

        normalized = qname.rstrip(".").lower()
        answers = []

        if qtype == 1:
            cname_target = cls.DNS_CNAME_TABLE.get(normalized)
            if cname_target:
                answers.append(cls._build_cname_answer(cname_target))
                target_key = cname_target.rstrip(".").lower()
                ip_addr = cls.DNS_TABLE.get(target_key)
                if ip_addr:
                    answers.append(cls._build_a_answer(ip_addr))
                return cls._build_response_with_answers(
                    transaction_id, rd_flag, query_bytes, answers
                )

            ip_addr = cls.DNS_TABLE.get(normalized)
            if ip_addr:
                answers.append(cls._build_a_answer(ip_addr))
                return cls._build_response_with_answers(
                    transaction_id, rd_flag, query_bytes, answers
                )

            if cls._name_exists(normalized):
                return cls._build_response_with_answers(
                    transaction_id, rd_flag, query_bytes, answers
                )

            return cls._build_error_response(transaction_id, rd_flag, 3, query_bytes=query_bytes)

        if qtype == 28:
            cname_target = cls.DNS_CNAME_TABLE.get(normalized)
            if cname_target:
                answers.append(cls._build_cname_answer(cname_target))
                target_key = cname_target.rstrip(".").lower()
                ip_addr = cls.DNS_AAAA_TABLE.get(target_key)
                if ip_addr:
                    answers.append(cls._build_aaaa_answer(ip_addr))
                return cls._build_response_with_answers(
                    transaction_id, rd_flag, query_bytes, answers
                )

            ip_addr = cls.DNS_AAAA_TABLE.get(normalized)
            if ip_addr:
                answers.append(cls._build_aaaa_answer(ip_addr))
                return cls._build_response_with_answers(
                    transaction_id, rd_flag, query_bytes, answers
                )

            if cls._name_exists(normalized):
                return cls._build_response_with_answers(
                    transaction_id, rd_flag, query_bytes, answers
                )

            return cls._build_error_response(transaction_id, rd_flag, 3, query_bytes=query_bytes)

        cname_target = cls.DNS_CNAME_TABLE.get(normalized)
        if cname_target:
            answers.append(cls._build_cname_answer(cname_target))
            return cls._build_response_with_answers(
                transaction_id, rd_flag, query_bytes, answers
            )

        if cls._name_exists(normalized):
            return cls._build_response_with_answers(
                transaction_id, rd_flag, query_bytes, answers
            )

        return cls._build_error_response(transaction_id, rd_flag, 3, query_bytes=query_bytes)

    @classmethod
    def _parse_header(cls, data):
        if len(data) < 12:
            return None
        try:
            transaction_id, flags, qdcount, _, _, _ = struct.unpack("!HHHHHH", data[:12])
        except struct.error:
            return None
        return transaction_id, flags, qdcount

    @classmethod
    def _parse_query(cls, data, offset):
        qname, new_offset = cls._decode_qname(data, offset, 0)
        if qname is None:
            return None
        if new_offset + 4 > len(data):
            return None
        try:
            qtype, qclass = struct.unpack("!HH", data[new_offset:new_offset + 4])
        except struct.error:
            return None
        query_bytes = data[offset:new_offset + 4]
        return qname, qtype, qclass, query_bytes

    @classmethod
    def _decode_qname(cls, data, offset, depth):
        if depth > 10:
            return None, offset

        labels = []
        start_offset = offset
        while True:
            if offset >= len(data):
                return None, offset
            length = data[offset]
            if length == 0:
                offset += 1
                break
            if length & 0xC0 == 0xC0:
                if offset + 1 >= len(data):
                    return None, offset
                pointer = ((length & 0x3F) << 8) | data[offset + 1]
                pointed_name, _ = cls._decode_qname(data, pointer, depth + 1)
                if pointed_name is None:
                    return None, offset
                labels.append(pointed_name)
                offset += 2
                break

            offset += 1
            if offset + length > len(data):
                return None, offset
            label_bytes = data[offset:offset + length]
            try:
                label = label_bytes.decode("ascii")
            except UnicodeDecodeError:
                label = label_bytes.decode("ascii", "ignore")
            labels.append(label)
            offset += length

        name = ".".join([label for label in labels if label])
        if not name and offset == start_offset + 1:
            return "", offset
        return name, offset

    @classmethod
    def _encode_qname(cls, name):
        if name == "":
            return b"\x00"
        parts = name.rstrip(".").split(".")
        out = bytearray()
        for part in parts:
            if not part:
                continue
            part_bytes = part.encode("ascii", "ignore")
            out.append(len(part_bytes))
            out.extend(part_bytes)
        out.append(0)
        return bytes(out)

    @classmethod
    def _build_a_answer(cls, ip_addr):
        name_ptr = b"\xc0\x0c"
        rr_header = struct.pack("!HHIH", 1, 1, cls.DEFAULT_TTL, 4)
        rdata = addrconv.ipv4.text_to_bin(ip_addr)
        return name_ptr + rr_header + rdata

    @classmethod
    def _build_aaaa_answer(cls, ip_addr):
        name_ptr = b"\xc0\x0c"
        rr_header = struct.pack("!HHIH", 28, 1, cls.DEFAULT_TTL, 16)
        rdata = addrconv.ipv6.text_to_bin(ip_addr)
        return name_ptr + rr_header + rdata

    @classmethod
    def _build_cname_answer(cls, target_name):
        name_ptr = b"\xc0\x0c"
        rdata = cls._encode_qname(target_name)
        rr_header = struct.pack("!HHIH", 5, 1, cls.DEFAULT_TTL, len(rdata))
        return name_ptr + rr_header + rdata

    @classmethod
    def _build_response_with_answers(cls, transaction_id, rd_flag, query_bytes, answers):
        flags_resp = 0x8000 | 0x0400 | rd_flag
        header_bytes = struct.pack(
            "!HHHHHH",
            transaction_id,
            flags_resp,
            1,
            len(answers),
            0,
            0,
        )
        return header_bytes + query_bytes + b"".join(answers)

    @classmethod
    def _name_exists(cls, normalized_name):
        return (
            normalized_name in cls.DNS_TABLE
            or normalized_name in cls.DNS_AAAA_TABLE
            or normalized_name in cls.DNS_CNAME_TABLE
        )

    @classmethod
    def _build_error_response(cls, transaction_id, rd_flag, rcode, query_bytes=b""):
        flags_resp = 0x8000 | rd_flag | (rcode & 0x000F)
        qdcount = 1 if query_bytes else 0
        header_bytes = struct.pack("!HHHHHH", transaction_id, flags_resp, qdcount, 0, 0, 0)
        return header_bytes + query_bytes

    @classmethod
    def _send_packet(cls, datapath, port, pkt):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        pkt.serialize()
        actions = [parser.OFPActionOutput(port=port)]
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=pkt.data
        )
        datapath.send_msg(out)
