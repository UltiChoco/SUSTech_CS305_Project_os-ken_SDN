import time

from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import RemoteController
from mininet.topo import Topo


def disable_ipv6(node):
    node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.default.disable_ipv6=1")
    node.cmd("sysctl -w net.ipv6.conf.lo.disable_ipv6=1")


def send_arp(node, count=1):
    node.cmd('arping -c %s -A -I %s-eth0 %s' % (count, node.name, node.IP()))


def send_dhcp(node):
    print('Sending DHCP request: dhclient -v %s-eth0' % node.name)
    node.cmd('dhclient -v %s-eth0' % node.name)


class NATTopo(Topo):
    def __init__(self, **opts):
        Topo.__init__(self, **opts)

        h1 = self.addHost('h1')
        h2 = self.addHost('h2', ip='10.0.0.2/8')

        s1 = self.addSwitch('s1')

        self.addLink(h1, s1)
        self.addLink(h2, s1)


def run_mininet():
    topo = NATTopo()

    net = Mininet(
        topo=topo,
        autoSetMacs=True,
        controller=RemoteController
    )

    for h in net.hosts:
        disable_ipv6(h)
    for s in net.switches:
        disable_ipv6(s)

    net.start()
    time.sleep(1)

    h1 = net.get('h1')
    h2 = net.get('h2')

    send_dhcp(h1)
    time.sleep(3)

    h1_ip = h1.IP()
    print('h1 IP (DHCP): %s' % h1_ip)

    h2.cmd('ping -c 1 -W 1 10.0.0.2 2>&1 || true')
    time.sleep(1)

    print('\n=== h2 sends gratuitous ARP (controller learns h2 location) ===')
    send_arp(h2)
    time.sleep(1)

    print('\n=== Test 1: h1 (%s) -> h2 (10.0.0.2) ping (NAT should translate) ===' % h1_ip)
    result = h1.cmd('ping -c 2 -W 2 10.0.0.2')
    print(result)
    if ' 0% packet loss' in result:
        print('PASS: NAT translation successful')
    else:
        print('FAIL: ping failed')

    print('\n=== Test 2: h2 sees reply from NAT IP (not h1 IP) ===')
    print('(Check controller logs for NAT-SNAT / NAT-DNAT messages)')

    print('\n=== Entering CLI for manual testing ===')
    print('  h1 ping 10.0.0.2')
    print('  h2 tcpdump -n -i h2-eth0 icmp')
    CLI(net)

    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run_mininet()
