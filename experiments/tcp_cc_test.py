"""
实验一: TCP 拥塞控制算法比较 (Reno vs Cubic)

拓扑:
  h1 --- s1 ========== s2 --- h2
  (10Mbps, 20ms 瓶颈)

采集数据:
  1. iperf 吞吐量报告 (每秒)
  2. tcpdump 抓包 (用于 packet-level 分析)
"""

import os
import time
import sys

from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.net import Mininet
from mininet.node import CPULimitedHost
from mininet.topolib import TreeTopo
from mininet.topo import Topo
from mininet.link import TCLink


class CCTestTopo(Topo):
    """TCP congestion control test topology."""

    def build(self):
        h1 = self.addHost('h1', ip='10.0.0.1/24')
        h2 = self.addHost('h2', ip='10.0.0.2/24')

        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')

        self.addLink(h1, s1, bw=100, delay='1ms')
        self.addLink(h2, s2, bw=100, delay='1ms')
        self.addLink(s1, s2, bw=10, delay='20ms', max_queue_size=100)


def set_cc_algorithm(host, algo):
    """Set TCP congestion control algorithm on host."""
    host.cmd('sysctl -w net.ipv4.tcp_congestion_control=%s' % algo)
    result = host.cmd('sysctl net.ipv4.tcp_congestion_control').strip()
    info('  %s CC algorithm: %s\n' % (host.name, result))


def run_cc_test(net, algo, output_dir):
    """Run iperf test with specified congestion control algorithm."""
    h1 = net.get('h1')
    h2 = net.get('h2')

    info('\n=== Testing %s ===\n' % algo)
    set_cc_algorithm(h1, algo)

    pcap_file = os.path.join(output_dir, '%s.pcap' % algo)
    iperf_log = os.path.join(output_dir, '%s_iperf.txt' % algo)

    h2.cmd('killall iperf 2>/dev/null; sleep 0.5; killall iperf 2>/dev/null || true')
    time.sleep(1)
    h2.cmd('iperf -s -p 5001 > /tmp/iperf_server.log 2>&1 &')
    time.sleep(2)

    h1.cmd('tcpdump -i %s-eth0 -w %s tcp and port 5001 &' % (h1.name, pcap_file))
    time.sleep(0.5)

    info('  Starting iperf client (30s)...\n')
    result = h1.cmd('iperf -c %s -p 5001 -t 30 -i 1 > %s 2>&1' % (h2.IP(), iperf_log))

    time.sleep(1)
    h1.cmd('killall tcpdump 2>/dev/null || true')
    h2.cmd('killall iperf 2>/dev/null || true')


def run():
    output_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(output_dir, exist_ok=True)

    topo = CCTestTopo()
    net = Mininet(topo=topo, link=TCLink, controller=None)
    net.start()

    h1 = net.get('h1')
    h2 = net.get('h2')

    # Configure OVS for standalone forwarding (no controller needed)
    for sw in net.switches:
        sw.cmd('ovs-vsctl set-fail-mode %s standalone' % sw.name)
        sw.cmd('ovs-ofctl add-flow %s priority=1,actions=NORMAL' % sw.name)

    h1.cmd('sysctl -w net.ipv4.tcp_congestion_control=reno')
    h2.cmd('sysctl -w net.ipv4.tcp_congestion_control=reno')

    info('\nWaiting for network convergence...\n')
    time.sleep(1)

    h2.cmd('iperf -s -p 5001 > /tmp/iperf_server.log 2>&1 &')
    time.sleep(1)
    result = h1.cmd('ping -c 2 -W 2 10.0.0.2')
    info('  Connectivity test: %s\n' % ('OK' if ' 0%' in result else 'FAILED'))
    h2.cmd('killall iperf 2>/dev/null || true')
    time.sleep(1)

    run_cc_test(net, 'reno', output_dir)
    time.sleep(2)

    run_cc_test(net, 'cubic', output_dir)

    info('\nExperiments complete. Data saved to: %s\n' % output_dir)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()
