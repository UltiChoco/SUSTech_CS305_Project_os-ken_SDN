"""Complex automated integration test for shortest-path switching.

8-switch, 8-host cyclic topology with redundant paths. The test validates
shortest-path forwarding and dynamic recovery after link, switch, and switch
port changes.

Switch-level topology:

        s4 -------- s7
        |           |
        s2 -- s5 -- s6 -- s8
        |           | \   |
        s1 -------- s3 ---

Every switch has one directly attached host with the same number, e.g. h1-s1.
The s1-s2-s4-s7-s6-s3-s1 outer ring plus inner shortcuts create multiple loops.

Requires: sudo, Mininet, running controller (osken-manager controller.py)
"""

import sys
import time
from collections import deque

from mininet.log import setLogLevel, info
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.topo import Topo


HOST_COUNT = 8
SWITCH_COUNT = 8

SWITCH_LINKS = [
    ('s1', 's2'),
    ('s2', 's4'),
    ('s4', 's7'),
    ('s7', 's6'),
    ('s6', 's3'),
    ('s3', 's1'),
    ('s2', 's5'),
    ('s5', 's6'),
    ('s3', 's8'),
    ('s8', 's6'),
]


def disable_ipv6(node):
    node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.default.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.lo.disable_ipv6=1")


def switch_id(name):
    return int(name[1:])


def normalize_link(a, b):
    return tuple(sorted((a, b), key=switch_id))


def host_switch(host_name):
    return 's%s' % host_name[1:]


def active_links(disabled_links=None, down_switches=None):
    disabled_links = set(disabled_links or [])
    down_switches = set(down_switches or [])
    links = []

    for a, b in SWITCH_LINKS:
        if a in down_switches or b in down_switches:
            continue
        if normalize_link(a, b) in disabled_links:
            continue
        links.append((a, b))

    return links


def build_switch_graph(links):
    graph = {}
    for i in range(1, SWITCH_COUNT + 1):
        graph['s%s' % i] = set()
    for a, b in links:
        graph[a].add(b)
        graph[b].add(a)
    return graph


def shortest_switch_path(src_switch, dst_switch, disabled_links=None,
                         down_switches=None):
    down_switches = set(down_switches or [])
    if src_switch in down_switches or dst_switch in down_switches:
        return None
    if src_switch == dst_switch:
        return [src_switch]

    graph = build_switch_graph(active_links(disabled_links, down_switches))
    queue = deque([src_switch])
    parent = {src_switch: None}

    while queue:
        current = queue.popleft()
        for neighbor in sorted(graph[current], key=switch_id):
            if neighbor in parent:
                continue
            parent[neighbor] = current
            if neighbor == dst_switch:
                queue.clear()
                break
            queue.append(neighbor)

    if dst_switch not in parent:
        return None

    path = []
    current = dst_switch
    while current is not None:
        path.append(current)
        current = parent[current]
    path.reverse()
    return path


def expected_path_string(src_host, dst_host, disabled_links=None,
                         down_switches=None):
    switch_path = shortest_switch_path(
        host_switch(src_host),
        host_switch(dst_host),
        disabled_links,
        down_switches,
    )
    if switch_path is None:
        return None, None

    full_path = [src_host] + switch_path + [dst_host]
    return ' -> '.join(full_path), len(switch_path) - 1


def send_arp(node, count=1):
    node.cmd('arping -c %s -w 2 -A -I %s-eth0 %s' %
             (count, node.name, node.IP()))
    time.sleep(0.3)


def wait_for_switch_controllers(net, timeout=30, interval=1):
    deadline = time.time() + timeout

    while time.time() < deadline:
        disconnected = []
        for switch in net.switches:
            output = switch.cmd('ovs-vsctl get Controller %s is_connected' %
                                switch.name)
            if 'true' not in output.lower():
                disconnected.append(switch.name)

        if not disconnected:
            return

        time.sleep(interval)

    raise RuntimeError('Switches not connected to controller: %s' %
                       ', '.join(disconnected))


def install_static_arp(hosts):
    for src in hosts.values():
        for dst in hosts.values():
            if src.name == dst.name:
                continue
            src.cmd('arp -s %s %s' % (dst.IP(), dst.MAC()))


def do_arp_all(hosts):
    for host in hosts.values():
        send_arp(host)


def wait_for_reconvergence(hosts, seconds=6):
    time.sleep(seconds)
    do_arp_all(hosts)
    time.sleep(1)


def ping_until_success(hosts, src, dst, attempts=5):
    last_result = ''

    for attempt in range(attempts):
        do_arp_all(hosts)
        time.sleep(1)
        last_result = src.cmd('ping -c 3 -W 2 %s' % dst.IP())
        if ' 0% packet loss' in last_result:
            return True, last_result
        if attempt + 1 < attempts:
            time.sleep(2)

    return False, last_result


def switch_port_for_peer(net, switch_name, peer_name):
    switch = net.get(switch_name)

    for intf in switch.intfList():
        link = getattr(intf, 'link', None)
        if link is None:
            continue

        if link.intf1.node == switch:
            other_intf = link.intf2
        elif link.intf2.node == switch:
            other_intf = link.intf1
        else:
            continue

        if other_intf.node.name == peer_name:
            return switch.ports[intf]

    raise RuntimeError('Cannot find %s port connected to %s' %
                       (switch_name, peer_name))


class ComplexLoopTopo(Topo):
    """8-switch, 8-host topology with an outer ring and inner shortcuts."""

    def __init__(self, **opts):
        Topo.__init__(self, **opts)

        hosts = {}
        switches = {}

        for i in range(1, HOST_COUNT + 1):
            hosts[i] = self.addHost('h%s' % i,
                                    ip='192.168.10.%s/24' % i)

        for i in range(1, SWITCH_COUNT + 1):
            switches[i] = self.addSwitch('s%s' % i)

        for i in range(1, HOST_COUNT + 1):
            self.addLink(hosts[i], switches[i])

        for a, b in SWITCH_LINKS:
            self.addLink(switches[switch_id(a)], switches[switch_id(b)])


def run_ping_case(hosts, name, src_name, dst_name, disabled_links=None,
                  down_switches=None):
    expected_path, expected_hops = expected_path_string(
        src_name,
        dst_name,
        disabled_links,
        down_switches,
    )
    src = hosts[src_name]
    dst = hosts[dst_name]

    info('\n=== %s (%s -> %s) ===\n' % (name, src.IP(), dst.IP()))
    if expected_path is None:
        info('Expected shortest path: no available path\n')
    else:
        info('Expected shortest path: %s (%d switch hops)\n' %
             (expected_path, expected_hops))

    passed, result = ping_until_success(hosts, src, dst)
    info(result)
    if passed:
        info('  PASS\n')
    else:
        info('  FAIL\n')
    return passed


def run_baseline_tests(hosts):
    test_pairs = [
        ('baseline h1->h2', 'h1', 'h2'),
        ('baseline h1->h4', 'h1', 'h4'),
        ('baseline h1->h7', 'h1', 'h7'),
        ('baseline h1->h8', 'h1', 'h8'),
        ('baseline h2->h5', 'h2', 'h5'),
        ('baseline h2->h6', 'h2', 'h6'),
        ('baseline h3->h8', 'h3', 'h8'),
        ('baseline h4->h6', 'h4', 'h6'),
        ('baseline h5->h7', 'h5', 'h7'),
        ('baseline h6->h8', 'h6', 'h8'),
        ('baseline h7->h3', 'h7', 'h3'),
        ('baseline h8->h1', 'h8', 'h1'),
    ]

    results = []
    for name, src_name, dst_name in test_pairs:
        results.append((name, run_ping_case(hosts, name, src_name, dst_name)))
    return results


def run_dynamic_tests(net, hosts):
    results = []

    disabled_s2_s4 = {normalize_link('s2', 's4')}
    info('\n=== Link modification: bring s2-s4 down ===\n')
    net.configLinkStatus('s2', 's4', 'down')
    wait_for_reconvergence(hosts)
    results.append((
        'link down s2-s4 h1->h7',
        run_ping_case(hosts, 'link down s2-s4', 'h1', 'h7',
                      disabled_links=disabled_s2_s4),
    ))

    info('\n=== Link modification: restore s2-s4 ===\n')
    net.configLinkStatus('s2', 's4', 'up')
    wait_for_reconvergence(hosts, seconds=8)
    results.append((
        'link restored s2-s4 h1->h7',
        run_ping_case(hosts, 'link restored s2-s4', 'h1', 'h7'),
    ))

    info('\n=== Switch modification: stop s5 ===\n')
    s5 = net.get('s5')
    s5.stop(deleteIntfs=False)
    wait_for_reconvergence(hosts, seconds=8)
    results.append((
        'switch s5 stopped h2->h6',
        run_ping_case(hosts, 'switch s5 stopped', 'h2', 'h6',
                      down_switches={'s5'}),
    ))

    info('\n=== Switch modification: restart s5 ===\n')
    s5.start(net.controllers)
    wait_for_switch_controllers(net)
    wait_for_reconvergence(hosts, seconds=8)
    results.append((
        'switch s5 restarted h2->h6',
        run_ping_case(hosts, 'switch s5 restarted', 'h2', 'h6'),
    ))

    info('\n=== Port modification: bring s3 port toward s1 down ===\n')
    s3_to_s1_port = switch_port_for_peer(net, 's3', 's1')
    net.get('s3').cmd('ovs-ofctl -O OpenFlow10 mod-port s3 %s down' %
                      s3_to_s1_port)
    disabled_s1_s3 = {normalize_link('s1', 's3')}
    wait_for_reconvergence(hosts, seconds=8)
    results.append((
        'port down s3-s1 h1->h8',
        run_ping_case(hosts, 'port down s3-s1', 'h1', 'h8',
                      disabled_links=disabled_s1_s3),
    ))

    info('\n=== Port modification: restore s3 port toward s1 ===\n')
    net.get('s3').cmd('ovs-ofctl -O OpenFlow10 mod-port s3 %s up' %
                      s3_to_s1_port)
    wait_for_reconvergence(hosts, seconds=8)
    results.append((
        'port restored s3-s1 h1->h8',
        run_ping_case(hosts, 'port restored s3-s1', 'h1', 'h8'),
    ))

    return results


def run_test():
    topo = ComplexLoopTopo()
    net = Mininet(
        topo=topo,
        autoSetMacs=True,
        controller=RemoteController,
    )

    results = []

    try:
        for h in net.hosts:
            disable_ipv6(h)
        for s in net.switches:
            disable_ipv6(s)

        net.start()
        wait_for_switch_controllers(net)
        time.sleep(3)

        hosts = {
            'h%s' % i: net.get('h%s' % i)
            for i in range(1, HOST_COUNT + 1)
        }
        install_static_arp(hosts)

        info('\n=== Sending gratuitous ARP from all hosts ===\n')
        do_arp_all(hosts)
        time.sleep(5)

        results.extend(run_baseline_tests(hosts))
        results.extend(run_dynamic_tests(net, hosts))
    finally:
        net.stop()

    passed = sum(1 for _, ok in results if ok)
    info('\n========================================\n')
    info('=== Results: %d/%d tests passed ===\n' % (passed, len(results)))
    info('========================================\n')

    if passed == len(results):
        info('ALL TESTS PASSED\n')
    else:
        failures = [name for name, ok in results if not ok]
        info('FAILED: %s\n' % ', '.join(failures))

    return passed == len(results)


if __name__ == '__main__':
    setLogLevel('info')
    success = run_test()
    sys.exit(0 if success else 1)
