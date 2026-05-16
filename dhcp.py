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
import time


class Config():
    controller_macAddr = '7e:49:b3:f0:f9:99'  # don't modify, a dummy mac address for fill the mac enrty
    dns = '8.8.8.8'  # don't modify, just for the dns entry
    start_ip = '192.168.1.2'  # can be modified
    end_ip = '192.168.1.99'  # can be modified
    netmask = '255.255.255.0'  # can be modified

    server_ip = '192.168.1.1'
    lease_duration = 8       # demo: 8 / 3600
    offer_timeout = 4          # demo: 4 / 60
    decline_timeout = 6       # demo: 6 / 300

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
    offer_timeout = Config.offer_timeout
    decline_timeout = Config.decline_timeout

    # Some os-ken versions may not expose all DHCP message constants directly.
    DHCP_MSG_DECLINE = getattr(dhcp, 'DHCP_DECLINE', 4)
    DHCP_MSG_NAK = getattr(dhcp, 'DHCP_NAK', 6)
    DHCP_MSG_RELEASE = getattr(dhcp, 'DHCP_RELEASE', 7)

    # Formal lease tables.
    mac_to_ip = {}
    ip_to_mac = {}
    lease_expire_time = {}

    # Temporary OFFER reservation tables.
    offered_ip_by_mac = {}  # "h1_mac": "192.168.1.2"
    offered_mac_by_ip = {}  # "192.168.1.2": "h1_mac"
    offer_expire_time = {}  # "192.168.1.2": expire_timestamp

    # Declined IP quarantine table.
    declined_ip_until = {}  # "192.168.1.2": current_time + decline_timeout

#-------------------------------------
# helpers: ip pool, parsing, validation, build packet, sending
#--------------------------------------

    @classmethod
    def _iter_pool_ips(cls):    # generate all candidate IPs in the pool range
        start = int(ipaddress.ip_address(cls.start_ip))
        end = int(ipaddress.ip_address(cls.end_ip))
        for ip_int in range(start, end + 1):
            yield str(ipaddress.ip_address(ip_int))

    @classmethod
    def _is_in_pool(cls, ip_addr):
        ip_int = int(ipaddress.ip_address(ip_addr))
        return int(ipaddress.ip_address(cls.start_ip)) <= ip_int <= int(ipaddress.ip_address(cls.end_ip))

    @classmethod
    def _decode_option_ipv4(cls, dhcp_pkt, tag):    # decode IPv4 address option from DHCP packet by tag
        if dhcp_pkt.options is None:
            return None
        for opt in dhcp_pkt.options.option_list:
            if isinstance(opt, dhcp.option) and opt.tag == tag and len(opt.value) == 4:
                return addrconv.ipv4.bin_to_text(opt.value)
        return None

    @classmethod
    def _decode_msg_type(cls, dhcp_pkt):   # decode DHCP message type from DHCP packet, return None if not found or invalid
        if dhcp_pkt.options is None:
            return None
        for opt in dhcp_pkt.options.option_list:
            if isinstance(opt, dhcp.option) and opt.tag == dhcp.DHCP_MESSAGE_TYPE_OPT and len(opt.value) >= 1:
                return opt.value[0]
        return None

    @classmethod
    def _build_dhcp_response_packet(cls, xid, client_mac, yiaddr, msg_type, flags=0, include_network_options=True):
        option_list = [
            # OFFER/ACK/NAK
            dhcp.option(tag=dhcp.DHCP_MESSAGE_TYPE_OPT, value=struct.pack('!B', msg_type)),
            # server identifier
            dhcp.option(tag=dhcp.DHCP_SERVER_IDENTIFIER_OPT, value=addrconv.ipv4.text_to_bin(cls.server_ip)),
        ]

        if include_network_options:
            option_list.extend([
                dhcp.option(tag=dhcp.DHCP_SUBNET_MASK_OPT, value=addrconv.ipv4.text_to_bin(cls.netmask)),
                dhcp.option(tag=dhcp.DHCP_DNS_SERVER_ADDR_OPT, value=addrconv.ipv4.text_to_bin(cls.dns)),
                # BONUS: tell the client the lease duration in OFFER/ACK
                dhcp.option(tag=dhcp.DHCP_IP_ADDR_LEASE_TIME_OPT, value=struct.pack('!I', cls.lease_duration)),
            ])

        pkt = packet.Packet()

        # ethernet, broadcast
        pkt.add_protocol(ethernet.ethernet(
            ethertype=ether_types.ETH_TYPE_IP,
            dst='ff:ff:ff:ff:ff:ff',
            src=cls.hardware_addr
        ))

        # IPV4, broadcast
        pkt.add_protocol(ipv4.ipv4(
            src=cls.server_ip,
            dst='255.255.255.255',
            proto=inet.IPPROTO_UDP
        ))

        # UDP
        pkt.add_protocol(udp.udp(
            src_port=67,
            dst_port=68
        ))

        # DHCP
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

    @classmethod
    def _get_ip_unavailable_reason(cls, ip_addr, mac):
        try:
            if not cls._is_in_pool(ip_addr):
                return f'requested IP {ip_addr} out of pool'
        except ValueError:
            return f'invalid IP address {ip_addr}'

        # declined
        if cls.declined_ip_until.get(ip_addr, 0) > time.time():
            return f'requested IP {ip_addr} is temporarily blocked after DECLINE'

        # leased
        lease_owner = cls.ip_to_mac.get(ip_addr)
        if lease_owner not in (None, mac):
            return f'requested IP {ip_addr} is already leased to {lease_owner}'

        # offered
        offered_owner = cls.offered_mac_by_ip.get(ip_addr)
        if offered_owner not in (None, mac):
            return f'requested IP {ip_addr} is already offered to {offered_owner}'

        return None

    @classmethod
    def _is_ip_available_for_mac(cls, ip_addr, mac):
        return cls._get_ip_unavailable_reason(ip_addr, mac) is None

#-------------------------------------
#cleanup
#--------------------------------------
    @classmethod
    def _cleanup_expired_offers(cls):  
        now = time.time()
        expired_ips = [ip for ip, expire_at in cls.offer_expire_time.items() if expire_at <= now]
        for ip_addr in expired_ips:
            owner_mac = cls.offered_mac_by_ip.get(ip_addr)
            cls._release_offer_for_ip(ip_addr)
            print(f'[DHCP] OFFER expired -> client={owner_mac}, ip={ip_addr}')

    @classmethod
    def _cleanup_expired_leases(cls):  
        now = time.time()
        expired_ips = [ip for ip, expire_at in cls.lease_expire_time.items() if expire_at <= now]
        for ip_addr in expired_ips:
            owner_mac = cls.ip_to_mac.get(ip_addr)
            cls._release_lease_for_ip(ip_addr)
            print(f'[DHCP] Lease expired -> client={owner_mac}, ip={ip_addr}')

    @classmethod
    def _cleanup_expired_declines(cls):
        now = time.time()
        expired_ips = [ip for ip, until in cls.declined_ip_until.items() if until <= now]
        for ip_addr in expired_ips:
            cls.declined_ip_until.pop(ip_addr, None)
            print(f'[DHCP] DECLINE timeout released ip={ip_addr}')

    @classmethod
    def _cleanup_expired_state(cls):    # use before processing each DHCP packet
        cls._cleanup_expired_leases()
        cls._cleanup_expired_offers()
        cls._cleanup_expired_declines()


#-------------------------------------
# DISCOVER
#--------------------------------------
    @classmethod
    def _handle_discover(cls, datapath, port, pkt):
        offer_pkt = cls.assemble_offer(pkt, datapath)
        if offer_pkt is not None:
            cls._send_packet(datapath, port, offer_pkt)

#-------------------------------------
# OFFER
#--------------------------------------

    @classmethod
    def _reserve_offer_ip(cls, mac, ip_addr):
        # MAC has been offered a IP -> remove old offer
        old_offer = cls.offered_ip_by_mac.get(mac)
        if old_offer and old_offer != ip_addr:
            cls.offered_mac_by_ip.pop(old_offer, None)
            cls.offer_expire_time.pop(old_offer, None)

        # The IP has been offered to another MAC -> remove old offer
        old_owner = cls.offered_mac_by_ip.get(ip_addr)
        if old_owner and old_owner != mac:
            cls.offered_ip_by_mac.pop(old_owner, None)

        cls.offered_ip_by_mac[mac] = ip_addr
        cls.offered_mac_by_ip[ip_addr] = mac
        cls.offer_expire_time[ip_addr] = time.time() + cls.offer_timeout

    @classmethod
    def _release_offer_for_ip(cls, ip_addr):    # release the OFFER reservation for the given IP, if any
        owner_mac = cls.offered_mac_by_ip.pop(ip_addr, None)
        if owner_mac and cls.offered_ip_by_mac.get(owner_mac) == ip_addr:
            cls.offered_ip_by_mac.pop(owner_mac, None)
        cls.offer_expire_time.pop(ip_addr, None)

    @classmethod
    def _release_offer_for_mac(cls, mac):   # release the OFFER reservation for the given MAC, if any
        offered_ip = cls.offered_ip_by_mac.pop(mac, None)
        if offered_ip:
            cls.offered_mac_by_ip.pop(offered_ip, None)
            cls.offer_expire_time.pop(offered_ip, None)

    @classmethod
    def _pick_offer_ip(cls, mac):
        # have a lease, use old lease
        existing_lease = cls.mac_to_ip.get(mac)
        if existing_lease and cls._is_ip_available_for_mac(existing_lease, mac):
            return existing_lease
        
        # has offer
        offered_ip = cls.offered_ip_by_mac.get(mac)
        if offered_ip and cls._is_ip_available_for_mac(offered_ip, mac):
            cls.offer_expire_time[offered_ip] = time.time() + cls.offer_timeout
            return offered_ip

        # find ip from ip pool
        cls._release_offer_for_mac(mac)
        for candidate in cls._iter_pool_ips():
            if cls._is_ip_available_for_mac(candidate, mac):
                cls._reserve_offer_ip(mac, candidate)
                return candidate
        return None

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
 
#-------------------------------------
# REQUEST
#-------------------------------------
    @classmethod
    def _extract_requested_ip(cls, dhcp_pkt):
        requested_ip = cls._decode_option_ipv4(dhcp_pkt, dhcp.DHCP_REQUESTED_IP_ADDR_OPT)

        if requested_ip is None and dhcp_pkt.ciaddr != '0.0.0.0':
            requested_ip = dhcp_pkt.ciaddr

        return requested_ip

    
    @classmethod
    def _validate_request_for_ack(cls, mac, requested_ip):
        if requested_ip is None:
            return False, 'missing requested IP/ciaddr'

        unavailable_reason = cls._get_ip_unavailable_reason(requested_ip, mac)
        if unavailable_reason:
            return False, unavailable_reason

        offered_ip = cls.offered_ip_by_mac.get(mac)
        if offered_ip and offered_ip != requested_ip:
            return False, f'requested IP {requested_ip} does not match OFFER {offered_ip}'

        return True, None

    @classmethod
    def _release_lease_for_ip(cls, ip_addr):
        owner_mac = cls.ip_to_mac.pop(ip_addr, None)
        if owner_mac and cls.mac_to_ip.get(owner_mac) == ip_addr:
            cls.mac_to_ip.pop(owner_mac, None)
        cls.lease_expire_time.pop(ip_addr, None)
        return owner_mac

    @classmethod
    def assemble_nak(cls, pkt, reason):
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        if pkt_dhcp is None:
            print('[DHCP] NAK build failed: no DHCP protocol in packet')
            return None

        client_mac = pkt_dhcp.chaddr
        print(f'[DHCP] NAK -> client={client_mac}, reason={reason}')
        return cls._build_dhcp_response_packet(
            xid=pkt_dhcp.xid,
            client_mac=client_mac,
            yiaddr='0.0.0.0',
            msg_type=cls.DHCP_MSG_NAK,
            flags=pkt_dhcp.flags,
            include_network_options=False
        )

    @classmethod
    def _handle_request(cls, datapath, port, pkt, pkt_dhcp):
        server_id = cls._decode_option_ipv4(pkt_dhcp, dhcp.DHCP_SERVER_IDENTIFIER_OPT)

        if server_id and server_id != cls.server_ip:
            print(f'[DHCP] REQUEST ignored: target server={server_id}, this server={cls.server_ip}')
            return

        client_mac = pkt_dhcp.chaddr
        requested_ip = cls._extract_requested_ip(pkt_dhcp)
        valid, reason = cls._validate_request_for_ack(client_mac, requested_ip)

        # NAK
        if not valid:
            nak_pkt = cls.assemble_nak(pkt, reason)
            if nak_pkt is not None:
                cls._send_packet(datapath, port, nak_pkt)
            return

        # ACK
        ack_pkt = cls.assemble_ack(pkt, datapath, port, requested_ip)
        if ack_pkt is not None:
            cls._send_packet(datapath, port, ack_pkt)

 
#--------------------------------------
# ACK
#--------------------------------------
    @classmethod
    def _commit_lease(cls, mac, ip_addr):
        # remove old lease
        old_lease = cls.mac_to_ip.get(mac)
        if old_lease and old_lease != ip_addr:
            cls.ip_to_mac.pop(old_lease, None)
            cls.lease_expire_time.pop(old_lease, None)

        old_owner = cls.ip_to_mac.get(ip_addr)
        if old_owner and old_owner != mac:
            cls.mac_to_ip.pop(old_owner, None)

        old_offer_owner = cls.offered_mac_by_ip.get(ip_addr)
        if old_offer_owner:
            cls.offered_ip_by_mac.pop(old_offer_owner, None)
            cls.offered_mac_by_ip.pop(ip_addr, None)
            cls.offer_expire_time.pop(ip_addr, None)

        cls._release_offer_for_mac(mac)

        cls.mac_to_ip[mac] = ip_addr
        cls.ip_to_mac[ip_addr] = mac
        cls.lease_expire_time[ip_addr] = time.time() + cls.lease_duration

    @classmethod
    def assemble_ack(cls, pkt, datapath, port, requested_ip):
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        if pkt_dhcp is None:
            print('[DHCP] ACK build failed: no DHCP protocol in packet')
            return None

        client_mac = pkt_dhcp.chaddr

        cls._commit_lease(client_mac, requested_ip)

        lease_expire = int(cls.lease_expire_time.get(requested_ip, 0))
        print(f'[DHCP] ACK -> client={client_mac}, ip={requested_ip}, lease_until={lease_expire}')
        return cls._build_dhcp_response_packet(
            xid=pkt_dhcp.xid,
            client_mac=client_mac,
            yiaddr=requested_ip,
            msg_type=dhcp.DHCP_ACK,
            flags=pkt_dhcp.flags
        )

    
#----------------------------
# RELEASE
#----------------------------
    
    @classmethod
    def _handle_release(cls, pkt_dhcp):
        client_mac = pkt_dhcp.chaddr
        release_ip = None
        released_ip = None

        if pkt_dhcp.ciaddr != '0.0.0.0':
            release_ip = pkt_dhcp.ciaddr
        elif client_mac in cls.mac_to_ip:
            release_ip = cls.mac_to_ip[client_mac]
        
        if release_ip and cls.ip_to_mac.get(release_ip) == client_mac:
            cls._release_lease_for_ip(release_ip)
            cls._release_offer_for_ip(release_ip)
            released_ip = release_ip
        elif client_mac in cls.mac_to_ip:
            fallback_ip = cls.mac_to_ip[client_mac]
            cls._release_lease_for_ip(fallback_ip)
            cls._release_offer_for_ip(fallback_ip)
            released_ip = fallback_ip

        cls._release_offer_for_mac(client_mac)

        if released_ip:
            print(f'[DHCP] RELEASE -> client={client_mac}, ip={released_ip}')
        else:
            print(f'[DHCP] RELEASE ignored -> client={client_mac}, no active lease')

#-----------------------------------
# DECLINE
#-----------------------------------
    @classmethod
    def _handle_decline(cls, pkt_dhcp):
        client_mac = pkt_dhcp.chaddr
        declined_ip = cls._decode_option_ipv4(pkt_dhcp, dhcp.DHCP_REQUESTED_IP_ADDR_OPT)

        cls._release_offer_for_mac(client_mac)

        if declined_ip and cls._is_in_pool(declined_ip):
            cls._release_offer_for_ip(declined_ip)

            cls.declined_ip_until[declined_ip] = time.time() + cls.decline_timeout
            
            decline_until = int(cls.declined_ip_until[declined_ip])
            print(f'[DHCP] DECLINE -> client={client_mac}, ip={declined_ip}, blocked_until={decline_until}')
            return

        print(f'[DHCP] DECLINE ignored -> client={client_mac}, invalid requested IP')

#-------------------------------------
# DHCP packet handler entry
#-------------------------------------
    @classmethod
    def handle_dhcp(cls, datapath, port, pkt):
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        if pkt_dhcp is None:
            print('[DHCP] Ignored packet without DHCP payload')
            return

        cls._cleanup_expired_state()

        msg_type = cls._decode_msg_type(pkt_dhcp)
        client_mac = pkt_dhcp.chaddr
        print(f'[DHCP] Received type={msg_type} from client={client_mac}')

        if msg_type == dhcp.DHCP_DISCOVER:
            cls._handle_discover(datapath, port, pkt)
            return

        if msg_type == dhcp.DHCP_REQUEST:
            cls._handle_request(datapath, port, pkt, pkt_dhcp)
            return

        if msg_type == cls.DHCP_MSG_RELEASE:
            cls._handle_release(pkt_dhcp)
            return

        if msg_type == cls.DHCP_MSG_DECLINE:
            cls._handle_decline(pkt_dhcp)
            return

        print(f'[DHCP] Unsupported DHCP message type: {msg_type}')
