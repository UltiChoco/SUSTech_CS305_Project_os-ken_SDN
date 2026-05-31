import time
from collections import OrderedDict

from os_ken.lib.packet import ethernet, ether_types, ipv4, icmp, packet

from os_ken.ofproto import inet


class NATConfig:
    internal_subnet = "192.168.1.0/24"
    server_ip = "192.168.1.1"
    server_mac = "7e:49:b3:f0:f9:99"

    icmp_timeout = 30
    tcp_timeout = 300
    udp_timeout = 60


class NATServer:
    _icmp_map = OrderedDict()
    _tcp_map = OrderedDict()
    _udp_map = OrderedDict()
    _next_ext_port = 50000

    @staticmethod
    def _ip_in_subnet(ip_str, subnet_cidr):
        import ipaddress
        try:
            return ipaddress.ip_address(ip_str) in ipaddress.ip_network(subnet_cidr)
        except ValueError:
            return False

    @classmethod
    def is_internal(cls, ip_str):
        return cls._ip_in_subnet(ip_str, NATConfig.internal_subnet)

    @classmethod
    def is_external(cls, ip_str):
        return not cls.is_internal(ip_str)

    @classmethod
    def _cleanup_expired(cls, mapping, timeout):
        now = time.time()
        expired = [k for k, (_, exp) in mapping.items() if exp <= now]
        for k in expired:
            mapping.pop(k, None)

    @classmethod
    def _build_snat_icmp_packet(cls, pkt_data, nat_ip, nat_mac, dst_mac):
        pkt = packet.Packet(data=pkt_data)
        pkt.serialize()

        eth_pkt = pkt.get_protocol(ethernet.ethernet)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        icmp_pkt = pkt.get_protocol(icmp.icmp)
        if eth_pkt is None or ip_pkt is None or icmp_pkt is None:
            return None

        internal_ip = ip_pkt.src
        icmp_data = icmp_pkt.data

        icmp_id = icmp_data.id if isinstance(icmp_data, icmp.echo) else 0
        icmp_seq = icmp_data.seq if isinstance(icmp_data, icmp.echo) else 0
        icmp_payload = icmp_data.data if isinstance(icmp_data, icmp.echo) else b''

        key = (ip_pkt.dst, icmp_id)
        cls._cleanup_expired(cls._icmp_map, NATConfig.icmp_timeout)
        cls._icmp_map[key] = (internal_ip, time.time() + NATConfig.icmp_timeout)

        new_eth = ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_IP,
            dst=dst_mac,
            src=nat_mac,
        )

        new_ip = ipv4.ipv4(
            version=ip_pkt.version,
            header_length=ip_pkt.header_length,
            tos=ip_pkt.tos,
            total_length=0,
            identification=ip_pkt.identification,
            flags=ip_pkt.flags,
            offset=ip_pkt.offset,
            ttl=ip_pkt.ttl - 1 if ip_pkt.ttl > 1 else 1,
            proto=inet.IPPROTO_ICMP,
            csum=0,
            src=nat_ip,
            dst=ip_pkt.dst,
        )

        new_echo = icmp.echo(id_=icmp_id, seq=icmp_seq, data=icmp_payload)
        new_icmp = icmp.icmp(
            type_=icmp_pkt.type,
            code=icmp_pkt.code,
            csum=0,
            data=new_echo,
        )

        new_pkt = packet.Packet()
        new_pkt.add_protocol(new_eth)
        new_pkt.add_protocol(new_ip)
        new_pkt.add_protocol(new_icmp)
        new_pkt.serialize()
        return new_pkt

    @classmethod
    def _build_dnat_icmp_packet(cls, pkt_data, nat_ip, nat_mac, controller):
        pkt = packet.Packet(data=pkt_data)
        pkt.serialize()

        eth_pkt = pkt.get_protocol(ethernet.ethernet)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        icmp_pkt = pkt.get_protocol(icmp.icmp)
        if eth_pkt is None or ip_pkt is None or icmp_pkt is None:
            return None, None

        icmp_data = icmp_pkt.data
        icmp_id = icmp_data.id if isinstance(icmp_data, icmp.echo) else 0

        cls._cleanup_expired(cls._icmp_map, NATConfig.icmp_timeout)

        key = (ip_pkt.src, icmp_id)
        entry = cls._icmp_map.get(key)
        if entry is None:
            return None, None

        internal_ip = entry[0]
        del cls._icmp_map[key]

        internal_mac = controller.ip_to_mac.get(internal_ip)
        if internal_mac is None:
            return None, None

        icmp_seq = icmp_data.seq if isinstance(icmp_data, icmp.echo) else 0
        icmp_payload = icmp_data.data if isinstance(icmp_data, icmp.echo) else b''

        new_eth = ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_IP,
            dst=internal_mac,
            src=nat_mac,
        )

        new_ip = ipv4.ipv4(
            version=ip_pkt.version,
            header_length=ip_pkt.header_length,
            tos=ip_pkt.tos,
            total_length=0,
            identification=ip_pkt.identification,
            flags=ip_pkt.flags,
            offset=ip_pkt.offset,
            ttl=ip_pkt.ttl - 1 if ip_pkt.ttl > 1 else 1,
            proto=inet.IPPROTO_ICMP,
            csum=0,
            src=ip_pkt.src,
            dst=internal_ip,
        )

        new_echo = icmp.echo(id_=icmp_id, seq=icmp_seq, data=icmp_payload)
        new_icmp = icmp.icmp(
            type_=icmp_pkt.type,
            code=icmp_pkt.code,
            csum=0,
            data=new_echo,
        )

        new_pkt = packet.Packet()
        new_pkt.add_protocol(new_eth)
        new_pkt.add_protocol(new_ip)
        new_pkt.add_protocol(new_icmp)
        new_pkt.serialize()
        return new_pkt, internal_mac

    @classmethod
    def handle_nat(cls, datapath, in_port, pkt, controller):
        eth_pkt = pkt.get_protocol(ethernet.ethernet)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if eth_pkt is None or ip_pkt is None:
            return False

        src_ip = ip_pkt.src
        dst_ip = ip_pkt.dst
        proto = ip_pkt.proto

        if cls.is_internal(src_ip) and cls.is_external(dst_ip):
            if proto == inet.IPPROTO_ICMP:
                dst_mac = controller.ip_to_mac.get(dst_ip)
                if dst_mac is None:
                    controller.logger.info(
                        'NAT-SNAT: unknown external IP %s, dropping', dst_ip
                    )
                    return False

                dest_info = controller.mac_to_loc.get(dst_mac)
                if dest_info is None:
                    controller.logger.info(
                        'NAT-SNAT: unknown external host %s, dropping', dst_mac
                    )
                    return False

                new_pkt = cls._build_snat_icmp_packet(
                    pkt.data, NATConfig.server_ip, NATConfig.server_mac, dst_mac
                )
                if new_pkt is None:
                    return False

                dest_dpid, dest_port = dest_info
                dest_dp = controller.dpid_to_dp.get(dest_dpid)
                if dest_dp is None:
                    return False
                cls._send_packet(dest_dp, dest_port, new_pkt)
                controller.logger.info(
                    'NAT-SNAT: %s -> %s (ICMP)', src_ip, dst_ip
                )
                return True

        if dst_ip == NATConfig.server_ip:
            if proto == inet.IPPROTO_ICMP:
                new_pkt, internal_mac = cls._build_dnat_icmp_packet(
                    pkt.data, NATConfig.server_ip, NATConfig.server_mac, controller
                )
                if new_pkt is None:
                    return False

                dest_info = controller.mac_to_loc.get(internal_mac)
                if dest_info is None:
                    controller.logger.info(
                        'NAT-DNAT: unknown internal host %s, dropping', internal_mac
                    )
                    return False

                dest_dpid, dest_port = dest_info
                dest_dp = controller.dpid_to_dp.get(dest_dpid)
                if dest_dp is None:
                    return False
                cls._send_packet(dest_dp, dest_port, new_pkt)
                controller.logger.info(
                    'NAT-DNAT: %s -> %s (ICMP)', ip_pkt.src, internal_mac
                )
                return True

        return False

    @staticmethod
    def _send_packet(datapath, port, pkt):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        pkt.serialize()
        data = pkt.data
        actions = [parser.OFPActionOutput(port=port)]
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=ofproto.OFP_NO_BUFFER,
            in_port=ofproto.OFPP_CONTROLLER,
            actions=actions,
            data=data,
        )
        datapath.send_msg(out)
