"""Integration test for Bellman-Ford switching.

Simple star topology (1 switch, 2 hosts) — verifies end-to-end
controller operation: host learning, ARP proxy, forwarding with Bellman-Ford.

Multi-hop routing tested separately in unit test (bellman_ford_unit_test.py).

Requires: sudo, Mininet, running controller_bf.py
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


class StarTopo(Topo):
    def __init__(self, **opts):
        Topo.__init__(self, **opts)
        h1 = self.addHost('h1', ip='192.168.1.10/24')
        h2 = self.addHost('h2', ip='192.168.1.11/24')
        s1 = self.addSwitch('s1')
        self.addLink(h1, s1)
        self.addLink(h2, s1)


def run_test():
    topo = StarTopo()

    net = Mininet(topo=topo, autoSetMacs=True, controller=RemoteController)

    for h in net.hosts:
        disable_ipv6(h)
    for s in net.switches:
        disable_ipv6(s)

    net.start()
    time.sleep(2)

    h1 = net.get('h1')
    h2 = net.get('h2')

    send_arp(h1)
    send_arp(h2)
    time.sleep(4)

    h1_ip = h1.IP()
    h2_ip = h2.IP()

    tests = []
    for name, src, dst in [('h1->h2', h1, h2), ('h2->h1', h2, h1)]:
        info('\n=== %s (%s -> %s) ===\n' % (name, src.IP(), dst.IP()))
        result = src.cmd('ping -c 3 -W 2 %s' % dst.IP())
        info(result)
        tests.append(' 0% packet loss' in result)

    net.stop()

    passed = sum(tests)
    info('\n=== Results: %d/%d tests passed ===\n' % (passed, len(tests)))
    info('Bellman-Ford controller: %s\n' % ('PASS' if passed == len(tests) else 'FAIL'))

    return passed == len(tests)


if __name__ == '__main__':
    setLogLevel('info')
    success = run_test()
    sys.exit(0 if success else 1)
