from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.topology import event
from os_ken.topology.switches import Switch, Host, HostState, Port, PortState, PortData, PortDataState, Link, LinkState
from os_ken.topology.switches import Switches
from os_ken.ofproto import ofproto_v1_0, ether, inet
from os_ken.lib.packet import packet, ethernet, ether_types, arp
from os_ken.lib.packet import dhcp
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ipv4
from os_ken.lib.packet import packet
from os_ken.lib.packet import udp
from dhcp import DHCPServer
from dns_server import DNSServer
from collections import defaultdict
import time
from ofctl_utilis import OfCtl, OfCtl_v1_0, OfCtl_after_v1_2, VLANID_NONE
import logging
import copy
import heapq
from firewall import Firewall


class ControllerApp(app_manager.OSKenApp):
    """SDN Controller Main Application

    Implements shortest-path L2 switching based on a global topology view:
    1. Build full-network topology graph (switch adjacency) via event listeners
    2. Learn host locations via ARP packets (MAC -> switch + port)
    3. Calculate shortest path and install forwarding flow entries for IP packets using Dijkstra
    4. Provide proxy ARP replies for ARP requests to reduce broadcast flooding
    """

    OFP_VERSIONS = [ofproto_v1_0.OFP_VERSION]


    FORWARDING_COOKIE = 0x1000
    FORWARDING_PRIORITY = 1000

    ROUTING_ALGORITHM = "dijkstra"  # "dijkstra" or "bellman_ford"

    def __init__(self, *args, **kwargs):
        super(ControllerApp, self).__init__(*args, **kwargs)
        self._init_topology()
        self.firewall = Firewall()

    def _init_topology(self):
        """Initialize network topology data structures

        Maintains four core data structures:
        1. self.graph -- Switch-level adjacency table {dpid: {neighbor_dpid: local_port}}
           Records link relationships between switches in the entire network for shortest path calculation.
        2. self.dpid_to_dp -- DPID to Datapath object mapping {dpid: datapath}
           Datapath objects are needed to send OpenFlow messages when installing flow entries.
        3. self.mac_to_loc -- Host MAC to physical location mapping {mac: (dpid, port)}
           Records which switch and port each host is connected to.
        4. self.ip_to_mac -- IP to MAC mapping {ip: mac}
           Acts as the controller's ARP cache table for proxy ARP replies.
        """
        self.graph = defaultdict(dict)
        self.dpid_to_dp = {}
        self.mac_to_loc = {}
        self.ip_to_mac = {}
        self.ip_to_mac["192.168.1.1"] = "7e:49:b3:f0:f9:99"

    @set_ev_cls(event.EventSwitchEnter)
    def _handle_switch_add(self, ev):
        """Handle switch up event -- EventSwitchEnter

        Triggered when a switch first establishes an OpenFlow connection with the controller.
        Performs two operations:
        (1) Record switch info into the topology data structures
        (2) Install table-miss flow entry (lowest priority),
            ensuring unmatched packets are sent to the controller.
        """
        dp = ev.switch.dp
        dpid = dp.id

        self.dpid_to_dp[dpid] = dp

        if dpid not in self.graph:
            self.graph[dpid] = {}

        ofctl = OfCtl.factory(dp, self.logger)
        ofctl.set_packetin_flow(cookie=0, priority=0)
        self.firewall.install_rules({dpid: ofctl})

        self.logger.info('Switch joined: dpid=%016x', dpid)

    @set_ev_cls(event.EventSwitchLeave)
    def handle_switch_delete(self, ev):
        """Handle switch down event -- EventSwitchLeave

        Cleans up all topology state related to the departing switch:
        - Removes the switch node from the adjacency graph
        - Removes edges pointing to this switch from all neighbours
        - Removes the Datapath object record
        """
        dp = ev.switch.dp
        dpid = dp.id

        if dpid in self.graph:
            del self.graph[dpid]

        for neighbor_dpid in list(self.graph.keys()):
            if dpid in self.graph[neighbor_dpid]:
                del self.graph[neighbor_dpid][dpid]

        if dpid in self.dpid_to_dp:
            del self.dpid_to_dp[dpid]

        self.logger.info('Switch left: dpid=%016x', dpid)

    @set_ev_cls(event.EventHostAdd)
    def handle_host_add(self, ev):
        """Handle host joining the network event -- EventHostAdd

        The os-ken topology detection module automatically triggers this event
        when it detects an active MAC address on a switch port. Host info includes
        MAC address, IP address list, and the switch DPID and port number it is
        connected to.

        We record this info into mac_to_loc and ip_to_mac mapping tables to
        provide a basis for subsequent ARP proxy and shortest-path forwarding.
        """
        host = ev.host
        mac = host.mac
        dpid = host.port.dpid
        port = host.port.port_no

        self.mac_to_loc[mac] = (dpid, port)

        for ip in host.ipv4:
            self.ip_to_mac[ip] = mac
            self.logger.info('Host joined: mac=%s, ip=%s, dpid=%016x, port=%d',
                             mac, ip, dpid, port)

    @set_ev_cls(event.EventLinkAdd)
    def handle_link_add(self, ev):
        """Handle inter-switch link addition event -- EventLinkAdd

        Triggered when the controller discovers physical connections between
        switches via LLDP (Link Layer Discovery Protocol).

        Record the link bidirectionally in the adjacency graph self.graph:
        - self.graph[src_dpid][dst_dpid] = port number used on the src side
        - self.graph[dst_dpid][src_dpid] = port number used on the dst side

        Note: Each link is bidirectional, with each end having its own local port number.
        """
        link = ev.link
        src_dpid = link.src.dpid
        dst_dpid = link.dst.dpid
        src_port = link.src.port_no
        dst_port = link.dst.port_no

        if src_dpid not in self.graph:
            self.graph[src_dpid] = {}
        if dst_dpid not in self.graph:
            self.graph[dst_dpid] = {}

        self.graph[src_dpid][dst_dpid] = src_port
        self.graph[dst_dpid][src_dpid] = dst_port

        self.logger.info('Link added: %016x:%d <-> %016x:%d',
                         src_dpid, src_port, dst_dpid, dst_port)

    @set_ev_cls(event.EventLinkDelete)
    def handle_link_delete(self, ev):
        """Handle inter-switch link deletion event -- EventLinkDelete

        Triggered when the controller detects a link disconnection between switches.
        Bidirectionally remove the link record from the adjacency graph.
        """
        link = ev.link
        src_dpid = link.src.dpid
        dst_dpid = link.dst.dpid

        if src_dpid in self.graph and dst_dpid in self.graph[src_dpid]:
            del self.graph[src_dpid][dst_dpid]
        if dst_dpid in self.graph and src_dpid in self.graph[dst_dpid]:
            del self.graph[dst_dpid][src_dpid]

        self.logger.info('Link deleted: %016x <-> %016x', src_dpid, dst_dpid)

    @set_ev_cls(event.EventPortModify)
    def handle_port_modify(self, ev):
        """Handle switch port status change event -- EventPortModify

        Triggered when a switch port status (UP / DOWN) changes.
        Includes ports connected to hosts and ports interconnecting switches.

        Current implementation: Log only. Can be extended to respond to port DOWN
        events (e.g., clearing location info for affected hosts).
        """
        port = ev.port
        dpid = port.dpid
        port_no = port.port_no
        self.logger.info('Port status changed: dpid=%016x, port=%d', dpid, port_no)

    def _dijkstra(self, src_dpid, dst_dpid):
        """Dijkstra Shortest Path Algorithm

        Calculates the shortest path from source switch to destination switch
        on the switch adjacency graph self.graph. All edge weights are 1 (hop count),
        optimized with a priority queue (min-heap).

        Args:
            src_dpid: Source switch DPID (int)
            dst_dpid: Destination switch DPID (int)

        Returns:
            Success: list of (dpid, out_port) -- Forwarding rules for intermediate
                     switches on the path, each element represents "exit from
                     out_port on switch dpid"
            Failure: None -- No path exists from src to dst
            Same switch: [] -- Empty list means src and dst are on the same switch

        Note:
            The returned path does not include the host port (dst_port) on the
            destination switch. Callers need to handle host port output separately
            on the destination switch.
        """
        if src_dpid == dst_dpid:
            return []

        if src_dpid not in self.graph or dst_dpid not in self.graph:
            self.logger.warning('Dijkstra: Node not in graph, src=%016x, dst=%016x',
                                src_dpid, dst_dpid)
            return None

        dist = {node: float('inf') for node in self.graph}
        dist[src_dpid] = 0

        prev_node = {}
        prev_port = {}

        pq = [(0, src_dpid)]

        while pq:
            d, u = heapq.heappop(pq)

            if d > dist[u]:
                continue

            if u == dst_dpid:
                break

            for v, port in self.graph[u].items():
                alt = d + 1
                if alt < dist[v]:
                    dist[v] = alt
                    prev_node[v] = u
                    prev_port[v] = port
                    heapq.heappush(pq, (alt, v))

        if dist[dst_dpid] == float('inf'):
            self.logger.warning('Dijkstra: No path found, src=%016x, dst=%016x',
                                src_dpid, dst_dpid)
            return None

        path = []
        curr = dst_dpid
        while curr != src_dpid:
            prev = prev_node[curr]
            port = prev_port[curr]
            path.append((prev, port))
            curr = prev

        path.reverse()
        return path

    def _bellman_ford(self, src_dpid, dst_dpid):
        """Bellman-Ford Shortest Path Algorithm

        Calculates the shortest path from source switch to destination switch
        on the switch adjacency graph self.graph. All edge weights are 1 (hop count),
        using the classic relaxation approach.

        Args:
            src_dpid: Source switch DPID (int)
            dst_dpid: Destination switch DPID (int)

        Returns:
            Success: list of (dpid, out_port) -- Forwarding rules for intermediate
                     switches on the path
            Failure: None -- No path exists from src to dst
            Same switch: [] -- Empty list means src and dst are on the same switch
        """
        if src_dpid == dst_dpid:
            return []

        if src_dpid not in self.graph or dst_dpid not in self.graph:
            self.logger.warning('Bellman-Ford: Node not in graph, src=%016x, dst=%016x',
                                src_dpid, dst_dpid)
            return None

        nodes = list(self.graph.keys())
        dist = {node: float('inf') for node in nodes}
        dist[src_dpid] = 0

        prev_node = {}
        prev_port = {}

        for _ in range(len(nodes) - 1):
            updated = False
            for u in nodes:
                if dist[u] == float('inf'):
                    continue
                for v, port in self.graph[u].items():
                    alt = dist[u] + 1
                    if alt < dist[v]:
                        dist[v] = alt
                        prev_node[v] = u
                        prev_port[v] = port
                        updated = True
            if not updated:
                break

        if dist[dst_dpid] == float('inf'):
            self.logger.warning('Bellman-Ford: No path found, src=%016x, dst=%016x',
                                src_dpid, dst_dpid)
            return None

        path = []
        curr = dst_dpid
        while curr != src_dpid:
            prev = prev_node[curr]
            port = prev_port[curr]
            path.append((prev, port))
            curr = prev

        path.reverse()
        return path

    def _shortest_path(self, src_dpid, dst_dpid):
        """Dispatch to the configured shortest-path algorithm."""
        if self.ROUTING_ALGORITHM == "bellman_ford":
            return self._bellman_ford(src_dpid, dst_dpid)
        return self._dijkstra(src_dpid, dst_dpid)

    def _install_path_flows(self, src_dpid, dst_dpid, dst_mac, dst_port):
        """Install L2 forwarding flow entries on each switch along the shortest path

        Install flow rules for each hop on the path from source switch to destination switch:
            Match: Ethernet destination MAC = dst_mac
            Action: Output to the port specified by the path (next hop)

        Args:
            src_dpid: Source switch DPID
            dst_dpid: Destination switch DPID
            dst_mac:  Destination host MAC address
            dst_port: Port on destination switch connected to destination host

        Returns:
            True:  Flow installation successful
            False: Path calculation failed
        """
        path = self._shortest_path(src_dpid, dst_dpid)
        if path is None:
            self.logger.error('Cannot compute shortest path for %016x -> %016x, skipping flow install',
                               src_dpid, dst_dpid)
            return False

        if src_dpid == dst_dpid:
            dp = self.dpid_to_dp.get(dst_dpid)
            if dp is None:
                return False
            ofctl = OfCtl.factory(dp, self.logger)
            actions = [dp.ofproto_parser.OFPActionOutput(dst_port, 0)]
            ofctl.set_flow(
                cookie=self.FORWARDING_COOKIE,
                priority=self.FORWARDING_PRIORITY,
                dl_dst=dst_mac,
                actions=actions
            )
            self.logger.info('Installed same-switch flow: dpid=%016x, mac=%s -> port=%d',
                             dst_dpid, dst_mac, dst_port)
            return True

        for i, (dpid, out_port) in enumerate(path):
            dp = self.dpid_to_dp.get(dpid)
            if dp is None:
                self.logger.warning('Switch %016x not in dpid_to_dp', dpid)
                continue
            ofctl = OfCtl.factory(dp, self.logger)
            actions = [dp.ofproto_parser.OFPActionOutput(out_port, 0)]
            ofctl.set_flow(
                cookie=self.FORWARDING_COOKIE,
                priority=self.FORWARDING_PRIORITY,
                dl_dst=dst_mac,
                actions=actions
            )
            self.logger.info('Installed path flow[%d/%d]: dpid=%016x, mac=%s -> port=%d',
                             i + 1, len(path) + 1, dpid, dst_mac, out_port)

        dp = self.dpid_to_dp.get(dst_dpid)
        if dp is not None:
            ofctl = OfCtl.factory(dp, self.logger)
            actions = [dp.ofproto_parser.OFPActionOutput(dst_port, 0)]
            ofctl.set_flow(
                cookie=self.FORWARDING_COOKIE,
                priority=self.FORWARDING_PRIORITY,
                dl_dst=dst_mac,
                actions=actions
            )
            self.logger.info('Installed destination switch flow: dpid=%016x, mac=%s -> port=%d',
                             dst_dpid, dst_mac, dst_port)

        return True

    def _handle_arp(self, datapath, in_port, pkt):
        """Handle ARP packets

        Core functions:
        (1) Learning -- Extract sender's IP-MAC mapping and physical location from ARP packets
        (2) Proxy Reply -- For ARP requests, if the controller has cached the target IP's MAC,
            directly construct an ARP reply packet back to the requester to avoid full-network broadcast
        (3) Flooding -- If target IP's MAC is unknown, flood the ARP request to all ports
        (4) Forwarding -- For ARP replies, forward them to the actual target host

        Args:
            datapath: Switch Datapath object that received the packet
            in_port:  Input port number of the packet
            pkt:      Parsed packet object
        """
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt is None:
            return

        src_mac = arp_pkt.src_mac
        src_ip = arp_pkt.src_ip
        dst_ip = arp_pkt.dst_ip
        dpid = datapath.id

        self.ip_to_mac[src_ip] = src_mac
        self.mac_to_loc[src_mac] = (dpid, in_port)

        if arp_pkt.opcode == arp.ARP_REQUEST:
            self.logger.info('Received ARP request: who-has %s tell %s', dst_ip, src_ip)

            target_mac = self.ip_to_mac.get(dst_ip)
            if target_mac:
                self.logger.info('Proxy ARP reply: %s is-at %s (to %s)',
                                 dst_ip, target_mac, src_ip)
                ofctl = OfCtl.factory(datapath, self.logger)
                ofctl.send_arp(
                    arp_opcode=arp.ARP_REPLY,
                    vlan_id=VLANID_NONE,
                    dst_mac=src_mac,
                    sender_mac=target_mac,
                    sender_ip=dst_ip,
                    target_mac=src_mac,
                    target_ip=src_ip,
                    src_port=datapath.ofproto.OFPP_CONTROLLER,
                    output_port=in_port
                )
            else:
                self.logger.info('Flooding ARP request: target %s unknown', dst_ip)
                ofctl = OfCtl.factory(datapath, self.logger)
                ofctl.send_packet_out(
                    in_port=in_port,
                    output=datapath.ofproto.OFPP_FLOOD,
                    data=pkt.data
                )
            return

        if arp_pkt.opcode == arp.ARP_REPLY:
            self.logger.info('Received ARP reply: %s is-at %s', src_ip, src_mac)

            target_ip = arp_pkt.dst_ip
            target_mac = arp_pkt.dst_mac
            self.ip_to_mac[target_ip] = target_mac

            dest_info = self.mac_to_loc.get(target_mac)
            if dest_info is None:
                self.logger.info('Unknown target host location, flooding ARP reply')
                ofctl = OfCtl.factory(datapath, self.logger)
                ofctl.send_packet_out(
                    in_port=in_port,
                    output=datapath.ofproto.OFPP_FLOOD,
                    data=pkt.data
                )
                return

            dest_dpid, dest_port = dest_info

            if dest_dpid == dpid:
                ofctl = OfCtl.factory(datapath, self.logger)
                ofctl.send_packet_out(
                    in_port=in_port,
                    output=dest_port,
                    data=pkt.data
                )
            else:
                self._forward_packet(
                    dpid, dest_dpid, target_mac, dest_port,
                    pkt.data, datapath, in_port
                )

    def _forward_packet(self, src_dpid, dst_dpid, dst_mac, dst_port,
                        pkt_data, cur_datapath, in_port):
        """Forward packets to destination host

        Performs two steps:
        (1) Install flow entries along the shortest path (subsequent similar packets
            forwarded directly by switch hardware)
        (2) Immediately send the current packet to the next hop (first packet cannot
            wait for flow table to take effect)

        Args:
            src_dpid:       Source switch DPID
            dst_dpid:       Destination switch DPID
            dst_mac:        Destination host MAC address
            dst_port:       Port on destination switch connected to destination host
            pkt_data:       Raw packet data (binary)
            cur_datapath:   Current packet's Datapath object
            in_port:        Current packet's input port number
        """
        self._install_path_flows(src_dpid, dst_dpid, dst_mac, dst_port)

        if src_dpid == dst_dpid:
            ofctl = OfCtl.factory(cur_datapath, self.logger)
            ofctl.send_packet_out(
                in_port=in_port,
                output=dst_port,
                data=pkt_data
            )
        else:
            path = self._shortest_path(src_dpid, dst_dpid)
            if path:
                first_hop_port = path[0][1]
                ofctl = OfCtl.factory(cur_datapath, self.logger)
                ofctl.send_packet_out(
                    in_port=in_port,
                    output=first_hop_port,
                    data=pkt_data
                )
            else:
                self.logger.warning('No path, dropping packet: %016x -> %016x',
                                    src_dpid, dst_dpid)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """Handle Packet-In event -- Controller entry point

        When a switch receives a packet that cannot match any flow table entry,
        the packet is sent to the controller via OpenFlow message. This method
        dispatches by protocol type:
        - DHCP packets -> DHCPServer (IP address allocation)
        - ARP packets  -> _handle_arp() (learning + proxy reply)
        - IP packets   -> Lookup destination host location, calculate shortest path and forward
        - Other packets -> Flood (to avoid network connectivity issues)
        """
        try:
            msg = ev.msg
            datapath = msg.datapath
            pkt = packet.Packet(data=msg.data)
            in_port = msg.in_port

            pkt_dhcp = pkt.get_protocols(dhcp.dhcp)
            if pkt_dhcp:
                DHCPServer.handle_dhcp(datapath, in_port, pkt)
                return

            pkt_arp = pkt.get_protocol(arp.arp)
            if pkt_arp:
                self._handle_arp(datapath, in_port, pkt)
                return

            pkt_ip = pkt.get_protocol(ipv4.ipv4)
            pkt_udp = pkt.get_protocol(udp.udp)
            if pkt_ip and pkt_udp and pkt_udp.dst_port == 53:
                DNSServer.handle_dns(datapath, in_port, pkt)
                return

            pkt_eth = pkt.get_protocol(ethernet.ethernet)
            if pkt_ip and pkt_eth:
                dst_mac = pkt_eth.dst
                dst_ip = pkt_ip.dst
                src_mac = pkt_eth.src
                src_ip = pkt_ip.src

                self.mac_to_loc[src_mac] = (datapath.id, in_port)
                self.ip_to_mac[src_ip] = src_mac

                dest_info = self.mac_to_loc.get(dst_mac)
                if dest_info:
                    dest_dpid, dest_port = dest_info
                    self._forward_packet(
                        datapath.id, dest_dpid, dst_mac, dest_port,
                        msg.data, datapath, in_port
                    )
                    self.logger.info('IP forwarding: %s (%s) -> %s (%s)',
                                     src_ip, src_mac, dst_ip, dst_mac)
                else:
                    self.logger.info('Unknown destination MAC %s, flooding IP packet', dst_mac)
                    ofctl = OfCtl.factory(datapath, self.logger)
                    ofctl.send_packet_out(
                        in_port=in_port,
                        output=datapath.ofproto.OFPP_FLOOD,
                        data=msg.data
                    )
                return

            ofctl = OfCtl.factory(datapath, self.logger)
            ofctl.send_packet_out(
                in_port=in_port,
                output=datapath.ofproto.OFPP_FLOOD,
                data=msg.data
            )

        except Exception as e:
            self.logger.error('packet_in handler exception: %s', e)
