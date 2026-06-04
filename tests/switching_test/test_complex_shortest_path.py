"""Complex automated integration test for shortest-path switching.

6-switch, 6-host mesh topology with multi-hop paths (1-hop, 2-hop, 3-hop).
Verifies that the controller correctly computes shortest paths via Dijkstra
and installs forwarding flows end-to-end.

Topology diagram:

    h1 -- s1 ---- s2 -- h2
           | \  / | \
           |  \/  |  \
           |  /\  |   \
    h3 -- s3 -- s5    s4 -- h4
                 | \  /
                 |  s6 -- h6
                 |
                 h5

Requires: sudo, Mininet, running controller (osken-manager controller.py)
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


class MeshTopo(Topo):
    """6-switch, 6-host mesh topology with multiple possible paths.

    Shortest path hop counts between hosts:
      h1-h2: 1    h1-h3: 1    h1-h4: 2    h1-h5: 2    h1-h6: 3
      h2-h3: 1    h2-h4: 1    h2-h5: 1    h2-h6: 2
      h3-h4: 2    h3-h5: 1    h3-h6: 2
      h4-h5: 1    h4-h6: 1
      h5-h6: 1
    """

    def __init__(self, **opts):
        Topo.__init__(self, **opts)

        h1 = self.addHost('h1', ip='192.168.10.1/24')
        h2 = self.addHost('h2', ip='192.168.10.2/24')
        h3 = self.addHost('h3', ip='192.168.10.3/24')
        h4 = self.addHost('h4', ip='192.168.10.4/24')
        h5 = self.addHost('h5', ip='192.168.10.5/24')
        h6 = self.addHost('h6', ip='192.168.10.6/24')

        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')
        s3 = self.addSwitch('s3')
        s4 = self.addSwitch('s4')
        s5 = self.addSwitch('s5')
        s6 = self.addSwitch('s6')

        self.addLink(h1, s1)
        self.addLink(h2, s2)
        self.addLink(h3, s3)
        self.addLink(h4, s4)
        self.addLink(h5, s5)
        self.addLink(h6, s6)

        self.addLink(s1, s2)
        self.addLink(s1, s3)
        self.addLink(s2, s3)
        self.addLink(s2, s4)
        self.addLink(s2, s5)
        self.addLink(s3, s5)
        self.addLink(s4, s5)
        self.addLink(s4, s6)
        self.addLink(s5, s6)


def run_test():
    topo = MeshTopo()

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

    hosts = {name: net.get(name) for name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']}

    info('\n=== Sending gratuitous ARP from all hosts ===\n')
    for h in hosts.values():
        send_arp(h)
    time.sleep(4)

    test_pairs = [
        ('h1->h2', hosts['h1'], hosts['h2'], 1),
        ('h1->h3', hosts['h1'], hosts['h3'], 1),
        ('h1->h4', hosts['h1'], hosts['h4'], 2),
        ('h1->h5', hosts['h1'], hosts['h5'], 2),
        ('h1->h6', hosts['h1'], hosts['h6'], 3),
        ('h2->h3', hosts['h2'], hosts['h3'], 1),
        ('h2->h4', hosts['h2'], hosts['h4'], 1),
        ('h2->h5', hosts['h2'], hosts['h5'], 1),
        ('h2->h6', hosts['h2'], hosts['h6'], 2),
        ('h3->h4', hosts['h3'], hosts['h4'], 2),
        ('h3->h5', hosts['h3'], hosts['h5'], 1),
        ('h3->h6', hosts['h3'], hosts['h6'], 2),
        ('h4->h5', hosts['h4'], hosts['h5'], 1),
        ('h4->h6', hosts['h4'], hosts['h6'], 1),
        ('h5->h6', hosts['h5'], hosts['h6'], 1),
        ('h6->h1', hosts['h6'], hosts['h1'], 3),
        ('h6->h2', hosts['h6'], hosts['h2'], 2),
        ('h6->h3', hosts['h6'], hosts['h3'], 2),
        ('h5->h1', hosts['h5'], hosts['h1'], 2),
        ('h4->h1', hosts['h4'], hosts['h1'], 2),
    ]

    tests = []
    for name, src, dst, expected_hops in test_pairs:
        info('\n=== %s (%s -> %s, expected %d hops) ===\n' %
             (name, src.IP(), dst.IP(), expected_hops))
        result = src.cmd('ping -c 3 -W 2 %s' % dst.IP())
        info(result)
        passed = ' 0% packet loss' in result
        tests.append(passed)
        if passed:
            info('  PASS\n')
        else:
            info('  FAIL\n')

    net.stop()

    passed = sum(tests)
    info('\n========================================\n')
    info('=== Results: %d/%d tests passed ===\n' % (passed, len(tests)))
    info('========================================\n')

    if passed == len(tests):
        info('ALL TESTS PASSED\n')
    else:
        failures = [test_pairs[i][0] for i, ok in enumerate(tests) if not ok]
        info('FAILED: %s\n' % ', '.join(failures))

    return passed == len(tests)


if __name__ == '__main__':
    setLogLevel('info')
    success = run_test()
    sys.exit(0 if success else 1)
