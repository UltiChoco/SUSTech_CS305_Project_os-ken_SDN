from os_ken.lib import addrconv
from os_ken.lib.packet import packet
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ether_types
from os_ken.lib.packet import ipv4
from os_ken.lib.packet import udp
from os_ken.lib.packet import dhcp
from os_ken.ofproto import inet
import ipaddress
import struct


class Config():
    controller_macAddr = '7e:49:b3:f0:f9:99' # don't modify, a dummy mac address for fill the mac enrty
    dns = '8.8.8.8' # don't modify, just for the dns entry
    start_ip = '192.168.1.2' # can be modified
    end_ip = '192.168.1.99' # can be modified
    netmask = '255.255.255.0' # can be modified
    
    server_ip = '192.168.1.1'
    lease_duration = 3600

    # You may use above attributes to configure your DHCP server.
    # You can also add more attributes like "lease_time" to support bonus function.


class DHCPServer():
    hardware_addr = Config.controller_macAddr
    start_ip = Config.start_ip
    end_ip = Config.end_ip
    netmask = Config.netmask
    dns = Config.dns
    server_ip = Config.server_ip
    lease_duration = Config.lease_duration


    # avoid duplicated IP assignment
    # formal
    mac_to_ip = {}
    ip_to_mac = {}
    # temp
    offered_ip_by_mac = {}
    offered_mac_by_ip = {}

    @classmethod
    def _iter_pool_ips(cls):
        start = int(ipaddress.ip_address(cls.start_ip))
        end = int(ipaddress.ip_address(cls.end_ip))
        for ip_int in range(start, end + 1):
            yield str(ipaddress.ip_address(ip_int))

    @classmethod
    def _is_in_pool(cls, ip_addr):
        ip_int = int(ipaddress.ip_address(ip_addr))
        return int(ipaddress.ip_address(cls.start_ip)) <= ip_int <= int(ipaddress.ip_address(cls.end_ip))

    @classmethod
    def _decode_option_ipv4(cls, dhcp_pkt, tag):
        if dhcp_pkt.options is None:
            return None
        for opt in dhcp_pkt.options.option_list:
            if isinstance(opt, dhcp.option) and opt.tag == tag and len(opt.value) == 4:
                return addrconv.ipv4.bin_to_text(opt.value)
        return None

    @classmethod
    def _decode_msg_type(cls, dhcp_pkt):
        if dhcp_pkt.options is None:
            return None
        for opt in dhcp_pkt.options.option_list:
            if isinstance(opt, dhcp.option) and opt.tag == dhcp.DHCP_MESSAGE_TYPE_OPT and len(opt.value) >= 1:
                return opt.value[0]
        return None

    @classmethod
    def _release_offer_for_mac(cls, mac):
        old_offer = cls.offered_ip_by_mac.pop(mac, None)
        if old_offer:
            cls.offered_mac_by_ip.pop(old_offer, None)

    @classmethod
    def _reserve_offer_ip(cls, mac, ip_addr):
        old_offer = cls.offered_ip_by_mac.get(mac)
        if old_offer and old_offer != ip_addr:
            cls.offered_mac_by_ip.pop(old_offer, None)
        cls.offered_ip_by_mac[mac] = ip_addr
        cls.offered_mac_by_ip[ip_addr] = mac

    @classmethod
    def _pick_offer_ip(cls, mac):
        existing = cls.mac_to_ip.get(mac)
        if existing:
            return existing

        offered = cls.offered_ip_by_mac.get(mac)
        if offered:
            lease_owner = cls.ip_to_mac.get(offered)
            offered_owner = cls.offered_mac_by_ip.get(offered)
            if lease_owner in (None, mac) and offered_owner in (None, mac):
                return offered
            cls._release_offer_for_mac(mac)

        for candidate in cls._iter_pool_ips():
            lease_owner = cls.ip_to_mac.get(candidate)
            offered_owner = cls.offered_mac_by_ip.get(candidate)
            if lease_owner is None and offered_owner is None:
                cls._reserve_offer_ip(mac, candidate)
                return candidate
        return None


    # ACK
    @classmethod
    def _commit_lease(cls, mac, ip_addr):
        # delete old formal
        old_lease = cls.mac_to_ip.get(mac)
        if old_lease and old_lease != ip_addr:
            cls.ip_to_mac.pop(old_lease, None)

        # delete old offer
        old_offer_owner = cls.offered_mac_by_ip.get(ip_addr)
        if old_offer_owner:
            cls.offered_ip_by_mac.pop(old_offer_owner, None)
            cls.offered_mac_by_ip.pop(ip_addr, None)
        cls._release_offer_for_mac(mac)

        cls.mac_to_ip[mac] = ip_addr
        cls.ip_to_mac[ip_addr] = mac

    @classmethod
    def _select_ack_ip(cls, mac, requested_ip):
        # requested ip not occupied by other lease or offer, and is in pool
        if requested_ip and cls._is_in_pool(requested_ip):
            lease_owner = cls.ip_to_mac.get(requested_ip)
            offered_owner = cls.offered_mac_by_ip.get(requested_ip)
            if lease_owner in (None, mac) and offered_owner in (None, mac):
                return requested_ip

        # has existing lease
        existing = cls.mac_to_ip.get(mac)
        if existing and cls._is_in_pool(existing):
            return existing

        # has temp offer
        offered = cls.offered_ip_by_mac.get(mac)
        if offered and cls._is_in_pool(offered):
            lease_owner = cls.ip_to_mac.get(offered)
            offered_owner = cls.offered_mac_by_ip.get(offered)
            if lease_owner in (None, mac) and offered_owner in (None, mac):
                return offered

        # pick new offer
        return cls._pick_offer_ip(mac)

    @classmethod
    def _build_dhcp_response_packet(cls, xid, client_mac, yiaddr, msg_type, flags=0):
        option_list = [
            dhcp.option(tag=dhcp.DHCP_MESSAGE_TYPE_OPT, value=struct.pack('!B', msg_type)),
            dhcp.option(tag=dhcp.DHCP_SERVER_IDENTIFIER_OPT, value=addrconv.ipv4.text_to_bin(cls.server_ip)),
            dhcp.option(tag=dhcp.DHCP_SUBNET_MASK_OPT, value=addrconv.ipv4.text_to_bin(cls.netmask)),
            dhcp.option(tag=dhcp.DHCP_DNS_SERVER_ADDR_OPT, value=addrconv.ipv4.text_to_bin(cls.dns)),
            dhcp.option(tag=dhcp.DHCP_IP_ADDR_LEASE_TIME_OPT, value=struct.pack('!I', cls.lease_duration)),
        ]

        pkt = packet.Packet()
        pkt.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_IP,
            dst='ff:ff:ff:ff:ff:ff',
            src=cls.hardware_addr
        ))
        pkt.add_protocol(ipv4.ipv4(
            src=cls.server_ip,
            dst='255.255.255.255',
            proto=inet.IPPROTO_UDP
        ))
        pkt.add_protocol(udp.udp(
            src_port=67,
            dst_port=68
        ))
        pkt.add_protocol(dhcp.dhcp(
            op=dhcp.DHCP_BOOT_REPLY,
            chaddr=client_mac,
            xid=xid,
            yiaddr=yiaddr,
            siaddr=cls.server_ip,
            flags=flags,
            options=dhcp.options(option_list=option_list)
        ))
        return pkt

    @classmethod
    def assemble_ack(cls, pkt, datapath, port):
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        if pkt_dhcp is None:
            print('[DHCP] ACK build failed: no DHCP protocol in packet')
            return None

        client_mac = pkt_dhcp.chaddr
        requested_ip = cls._decode_option_ipv4(pkt_dhcp, dhcp.DHCP_REQUESTED_IP_ADDR_OPT)
        if requested_ip is None and pkt_dhcp.ciaddr != '0.0.0.0':
            requested_ip = pkt_dhcp.ciaddr

        ack_ip = cls._select_ack_ip(client_mac, requested_ip)
        if ack_ip is None:
            print(f'[DHCP] IP pool exhausted, cannot ACK for client {client_mac}')
            return None

        cls._commit_lease(client_mac, ack_ip)
        print(f'[DHCP] ACK -> client={client_mac}, ip={ack_ip}')
        return cls._build_dhcp_response_packet(
            xid=pkt_dhcp.xid,
            client_mac=client_mac,
            yiaddr=ack_ip,
            msg_type=dhcp.DHCP_ACK,
            flags=pkt_dhcp.flags
        )

    @classmethod
    def assemble_offer(cls, pkt, datapath):
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        if pkt_dhcp is None:
            print('[DHCP] OFFER build failed: no DHCP protocol in packet')
            return None

        client_mac = pkt_dhcp.chaddr
        offered_ip = cls._pick_offer_ip(client_mac)
        if offered_ip is None:
            print(f'[DHCP] IP pool exhausted, cannot OFFER for client {client_mac}')
            return None

        print(f'[DHCP] OFFER -> client={client_mac}, ip={offered_ip}')
        return cls._build_dhcp_response_packet(
            xid=pkt_dhcp.xid,
            client_mac=client_mac,
            yiaddr=offered_ip,
            msg_type=dhcp.DHCP_OFFER,
            flags=pkt_dhcp.flags
        )

    @classmethod
    def handle_dhcp(cls, datapath, port, pkt):
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        if pkt_dhcp is None:
            print('[DHCP] Ignored packet without DHCP payload')
            return

        msg_type = cls._decode_msg_type(pkt_dhcp)
        client_mac = pkt_dhcp.chaddr
        print(f'[DHCP] Received type={msg_type} from client={client_mac}')

        if msg_type == dhcp.DHCP_DISCOVER:
            offer_pkt = cls.assemble_offer(pkt, datapath)
            if offer_pkt is not None:
                cls._send_packet(datapath, port, offer_pkt)
            return

        if msg_type == dhcp.DHCP_REQUEST:
            ack_pkt = cls.assemble_ack(pkt, datapath, port)
            if ack_pkt is not None:
                cls._send_packet(datapath, port, ack_pkt)
            return

        print(f'[DHCP] Unsupported DHCP message type: {msg_type}')

    @classmethod
    def _send_packet(cls, datapath, port, pkt):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        if isinstance(pkt, str):
            pkt = pkt.encode()
        pkt.serialize()
        data = pkt.data
        actions = [parser.OFPActionOutput(port=port)]
        out = parser.OFPPacketOut(datapath=datapath,
                                  buffer_id=ofproto.OFP_NO_BUFFER,
                                  in_port=ofproto.OFPP_CONTROLLER,
                                  actions=actions,
                                  data=data)
        datapath.send_msg(out)
