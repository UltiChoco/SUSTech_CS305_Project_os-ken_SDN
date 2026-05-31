"""Automated integration test for NAT (ICMP SNAT/DNAT).

Topology: 1 switch, 2 hosts (static IPs, no DHCP dependency).
- h1: static IP 192.168.1.2/24 (internal host behind NAT)
- h2: static IP 10.0.0.2/8 (external host)
- NAT IP: 192.168.1.1

Requires: sudo, Mininet, running controller
"""
import sys
import time

from mininet.log import setLogLevel, info
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.topo import Topo


def disable_ipv6(node):
    node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.default.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.lo.disable_ipv6=1")


def send_arp(node, count=1):
    node.cmd('arping -c %s -A -I %s-eth0 %s' % (count, node.name, node.IP()))
    time.sleep(0.5)


class NATTopo(Topo):
    def __init__(self, **opts):
        Topo.__init__(self, **opts)
        h1 = self.addHost('h1', ip='192.168.1.2/24')
        h2 = self.addHost('h2', ip='10.0.0.2/8')
        s1 = self.addSwitch('s1')
        self.addLink(h1, s1)
        self.addLink(h2, s1)


def run_test():
    topo = NATTopo()

    net = Mininet(
        topo=topo,
        autoSetMacs=True,
        controller=RemoteController,
    )

    for h in net.hosts:
        disable_ipv6(h)
    for s in net.switches:
        disable_ipv6(s)

    net.start()
    time.sleep(2)

    h1 = net.get('h1')
    h2 = net.get('h2')

    h1.cmd('ip route add 10.0.0.0/8 dev h1-eth0 2>/dev/null || true')
    h2.cmd('ip route add 192.168.1.0/24 dev h2-eth0 2>/dev/null || true')

    h1_ip = h1.IP()
    h2_ip = h2.IP()
    info('h1 IP: %s\n' % h1_ip)
    info('h2 IP: %s\n' % h2_ip)

    send_arp(h1)
    send_arp(h2)
    time.sleep(4)

    info('\n=== Test 1: h1 (%s) -> h2 (%s) ping (SNAT outbound) ===\n' % (h1_ip, h2_ip))
    result = h1.cmd('ping -c 3 -W 2 %s' % h2_ip)
    info(result)
    t1 = ' 0% packet loss' in result

    info('\n=== Test 2: rerun to confirm connectivity ===\n')
    result = h1.cmd('ping -c 3 -W 2 %s' % h2_ip)
    info(result)
    t2 = ' 0% packet loss' in result

    net.stop()

    passed = sum([t1, t2])
    total = 2
    info('\n=== Results: %d/%d tests passed ===\n' % (passed, total))

    if passed == total:
        info('ALL TESTS PASSED\n')
    else:
        failures = []
        if not t1:
            failures.append('h1->h2 (SNAT outbound)')
        if not t2:
            failures.append('h1->h2 (rerun)')
        info('FAILED: %s\n' % ', '.join(failures))

    return passed == total


if __name__ == '__main__':
    setLogLevel('info')
    success = run_test()
    sys.exit(0 if success else 1)
