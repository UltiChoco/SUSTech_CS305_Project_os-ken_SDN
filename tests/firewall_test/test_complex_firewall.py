# Complex firewall demo topology.
#
# Run the controller first:
#   osken-manager --observe-links controller.py
#
# Then run this script:
#   sudo env "PATH=$PATH" python test_complex_firewall.py

import sys
import time
from collections import deque

from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.topo import Topo


HOST_IPS = {
    "h1": "192.168.117.2",
    "h2": "192.168.117.3",
    "h3": "192.168.117.4",
    "h4": "192.168.117.5",
    "h5": "192.168.117.6",
    "h6": "192.168.117.7",
    "h7": "192.168.117.8",
}

SWITCH_LINKS = [
    ("s1", "s2"),
    ("s1", "s3"),
    ("s2", "s4"),
    ("s2", "s5"),
    ("s3", "s6"),
    ("s3", "s7"),
]

RUNTIME_FIREWALL_COOKIE = "0x305e"


class ComplexFirewallTopo(Topo):
    def build(self):
        hosts = {}
        switches = {}

        for i in range(1, 8):
            host_name = "h%s" % i
            switch_name = "s%s" % i
            hosts[host_name] = self.addHost(host_name, ip="no ip defined/8")
            switches[switch_name] = self.addSwitch(switch_name)
            self.addLink(hosts[host_name], switches[switch_name])

        for left, right in SWITCH_LINKS:
            self.addLink(switches[left], switches[right])


def disable_ipv6(node):
    node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.default.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.lo.disable_ipv6=1")


def configure_host_ips(net):
    for host_name, ip in HOST_IPS.items():
        host = net.get(host_name)
        host.cmd("ifconfig %s-eth0 %s/24 up" % (host_name, ip))


def wait_for_switch_controllers(net, timeout=30, interval=1):
    deadline = time.time() + timeout

    while time.time() < deadline:
        disconnected = []
        for switch in net.switches:
            output = switch.cmd("ovs-vsctl get Controller %s is_connected" %
                                switch.name)
            if "true" not in output.lower():
                disconnected.append(switch.name)

        if not disconnected:
            return

        time.sleep(interval)

    raise RuntimeError("Switches not connected to controller: %s" %
                       ", ".join(disconnected))


def send_arp(host, count=1):
    intf = "%s-eth0" % host.name
    if "does not exist" in host.cmd("ip link show %s 2>&1" % intf):
        return
    host.cmd("arping -c %s -A -I %s %s" %
             (count, intf, HOST_IPS[host.name]))


def do_arp_all(net):
    for host in net.hosts:
        send_arp(host)


def ping(host, dst_ip, count=2, timeout=1):
    return host.cmd("ping -c %s -W %s %s" % (count, timeout, dst_ip))


def ping_succeeded(output):
    return " 0% packet loss" in output or ", 0% packet loss" in output


def assert_ping(host, dst_ip, should_pass, label):
    output = ping(host, dst_ip)
    passed = ping_succeeded(output)
    result = "PASS" if passed == should_pass else "FAIL"
    expectation = "reachable" if should_pass else "blocked"

    print("\n[%s] %s expected %s" % (result, label, expectation))
    print(output)

    if passed != should_pass:
        raise AssertionError("%s expected %s" % (label, expectation))


def wait_for_ping(net, host, dst_ip, should_pass, label,
                  timeout=45, interval=3):
    deadline = time.time() + timeout
    last_output = ""

    while time.time() < deadline:
        do_arp_all(net)
        time.sleep(1)
        last_output = ping(host, dst_ip)
        passed = ping_succeeded(last_output)

        if passed == should_pass:
            print("\n[PASS] %s expected %s" %
                  (label, "reachable" if should_pass else "blocked"))
            print(last_output)
            return

        time.sleep(interval)

    print("\n[FAIL] %s expected %s" %
          (label, "reachable" if should_pass else "blocked"))
    print(last_output)
    print_diagnostics(net)
    raise AssertionError("%s expected %s" %
                         (label, "reachable" if should_pass else "blocked"))


def print_diagnostics(net):
    print("\nDiagnostics:")
    print("  Mininet net:")
    print(net)
    for host in net.hosts:
        print("\n  %s addresses:" % host.name)
        print(host.cmd("ip addr show %s-eth0" % host.name))
        print("  %s ARP table:" % host.name)
        print(host.cmd("arp -n"))

    for switch in net.switches:
        print("\n  %s flows:" % switch.name)
        print(switch.cmd("ovs-ofctl -O OpenFlow10 dump-flows %s" %
                         switch.name))


def assert_firewall_flows_installed(switch):
    flows = switch.cmd("ovs-ofctl -O OpenFlow10 dump-flows %s" %
                       switch.name)
    print("\n[check] %s firewall flows after restart:" % switch.name)
    print(flows)

    has_icmp_rule = (
        "priority=60000" in flows and
        "icmp" in flows and
        "nw_src=192.168.117.2" in flows and
        "nw_dst=192.168.117.3" in flows and
        "actions=drop" in flows
    )
    if not has_icmp_rule:
        raise AssertionError("%s missing reinstalled firewall flow" %
                             switch.name)


def install_runtime_icmp_drop(net, src_ip, dst_ip):
    flow = (
        "cookie=%s,priority=61000,icmp,nw_src=%s,nw_dst=%s,actions=drop" %
        (RUNTIME_FIREWALL_COOKIE, src_ip, dst_ip)
    )
    for switch in net.switches:
        switch.cmd("ovs-ofctl -O OpenFlow10 add-flow %s '%s'" %
                   (switch.name, flow))


def clear_runtime_firewall(net, src_ip=HOST_IPS["h1"], dst_ip=HOST_IPS["h7"]):
    for switch in net.switches:
        switch.cmd("ovs-ofctl -O OpenFlow10 del-flows %s "
                   "'icmp,nw_src=%s,nw_dst=%s'" %
                   (switch.name, src_ip, dst_ip))


def restart_switch(net, switch_name):
    switch = net.get(switch_name)
    print("\n[demo] Restarting %s to verify firewall reinstall on reconnect" %
          switch_name)
    switch.stop()
    time.sleep(1)
    switch.start(net.controllers)
    time.sleep(3)
    do_arp_all(net)


def build_switch_graph():
    graph = {("s%s" % i): set() for i in range(1, 8)}
    for left, right in SWITCH_LINKS:
        graph[left].add(right)
        graph[right].add(left)
    return graph


def shortest_path(graph, src, dst):
    queue = deque([(src, [src])])
    visited = set([src])

    while queue:
        node, path = queue.popleft()
        if node == dst:
            return path
        for neighbor in sorted(graph[node]):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return None


def print_expected_topology():
    print("""
Complex firewall topology:

Host links:
  h1-s1  h2-s2  h3-s3  h4-s4  h5-s5  h6-s6  h7-s7

Switch graph:
  s4--s2--s1--s3--s6
      |       |
      s5      s7

Mermaid figure:
  graph LR
    h1---s1
    h2---s2
    h3---s3
    h4---s4
    h5---s5
    h6---s6
    h7---s7
    s1---s2
    s1---s3
    s2---s4
    s2---s5
    s3---s6
    s3---s7
""")

    graph = build_switch_graph()
    print("Expected switch shortest paths:")
    switches = sorted(graph)
    for index, src in enumerate(switches):
        for dst in switches[index + 1:]:
            path = shortest_path(graph, src, dst)
            print("  %s to %s: %s, %s edges" %
                  (src, dst, " -> ".join(path), len(path) - 1))

    print("Expected host shortest paths:")
    hosts = sorted(HOST_IPS)
    for index, src_host in enumerate(hosts):
        src_switch = "s%s" % src_host[1:]
        for dst_host in hosts[index + 1:]:
            dst_switch = "s%s" % dst_host[1:]
            switch_path = shortest_path(graph, src_switch, dst_switch)
            host_path = [src_host] + switch_path + [dst_host]
            print("  %s to %s: %s, %s edges" %
                  (src_host, dst_host, " -> ".join(host_path),
                   len(host_path) - 1))


def print_dynamic_cli_checklist():
    print("""
Dynamic CLI checks for the demo:

  net
  pingall
  switch s7 stop
  switch s7 start
  link s2 s5 down
  link s2 s5 up
  sh ovs-ofctl -O OpenFlow10 mod-port s3 4 down
  sh ovs-ofctl -O OpenFlow10 mod-port s3 4 up

Expected controller events covered:
  handle_host_add: initial h1..h7 ARP announcements
  handle_switch_add: initial s1..s7 startup and switch start
  handle_switch_delete: switch stop
  handle_link_add: initial links and link up
  handle_link_delete: link down
  handle_port_modify: mod-port down/up
""")


def run_mininet():
    print_expected_topology()

    topo = ComplexFirewallTopo()
    net = Mininet(topo=topo, autoSetMacs=True, controller=RemoteController)

    try:
        for node in net.hosts + net.switches:
            disable_ipv6(node)

        net.start()
        wait_for_switch_controllers(net)
        time.sleep(2)
        configure_host_ips(net)

        for _ in range(3):
            do_arp_all(net)
            time.sleep(1)

        h1 = net.get("h1")
        h2_ip = HOST_IPS["h2"]
        h7_ip = HOST_IPS["h7"]

        wait_for_ping(net, h1, h7_ip, True,
                      "baseline h1 -> h7 before runtime firewall rule")
        assert_ping(h1, h2_ip, False,
                    "default firewall h1 -> h2 ICMP deny rule")

        install_runtime_icmp_drop(net, HOST_IPS["h1"], h7_ip)
        time.sleep(1)
        assert_ping(h1, h7_ip, False,
                    "runtime firewall makes prior h1 -> h7 path unreachable")

        clear_runtime_firewall(net)
        wait_for_ping(net, h1, h7_ip, True,
                      "h1 -> h7 after clearing runtime firewall rule")

        restart_switch(net, "s5")
        assert_firewall_flows_installed(net.get("s5"))
        assert_ping(h1, h2_ip, False,
                    "default firewall still blocks h1 -> h2 after s5 restart")
        wait_for_ping(net, h1, h7_ip, True,
                      "unrelated h1 -> h7 traffic still works after s5 restart")

        print_dynamic_cli_checklist()
        CLI(net)
    finally:
        clear_runtime_firewall(net)
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    try:
        run_mininet()
    except AssertionError as exc:
        print("\n[FAIL] %s" % exc)
        sys.exit(1)
