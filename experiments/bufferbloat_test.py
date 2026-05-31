"""
实验二: Bufferbloat 现象验证

拓扑:
  h1 --- s1 ========== s2 --- h2
  h3 ---/

  瓶颈链路 s1-s2: bw=10Mbps, delay=10ms
  变量: s1 队列大小 (20 vs 200 包)

采集数据:
  1. h3 ping h2 的延迟时间序列
  2. h1-h2 iperf 吞吐量

显示:
  - 大缓冲区: 延迟飙升 (bufferbloat)
  - 小缓冲区: 延迟可控
"""

import os
import re
import time

from mininet.log import setLogLevel, info
from mininet.net import Mininet
from mininet.link import TCLink
from mininet.topo import Topo


class BufferbloatTopo(Topo):
    """Bufferbloat test topology."""

    def __init__(self, queue_size=200):
        self.qsize = queue_size
        super(BufferbloatTopo, self).__init__()

    def build(self):
        h1 = self.addHost('h1', ip='10.0.0.1/24')
        h2 = self.addHost('h2', ip='10.0.0.2/24')
        h3 = self.addHost('h3', ip='10.0.0.3/24')

        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')

        self.addLink(h1, s1, bw=100)
        self.addLink(h3, s1, bw=100)
        self.addLink(h2, s2, bw=100)
        self.addLink(s1, s2, bw=10, delay='10ms', max_queue_size=self.qsize)


def run_bufferbloat_test(qsize, output_dir):
    """Run bufferbloat experiment with given queue size."""
    label = 'q%d' % qsize
    info('\n=== Bufferbloat test: queue_size=%d ===\n' % qsize)

    topo = BufferbloatTopo(queue_size=qsize)
    net = Mininet(topo=topo, link=TCLink, controller=None)
    net.start()
    time.sleep(1)

    h1 = net.get('h1')
    h2 = net.get('h2')
    h3 = net.get('h3')

    for sw in net.switches:
        sw.cmd('ovs-vsctl set-fail-mode %s standalone' % sw.name)
        sw.cmd('ovs-ofctl add-flow %s priority=1,actions=NORMAL' % sw.name)

    disable_offload(net)

    for h in net.hosts:
        h.cmd('arping -c 2 -A -I %s-eth0 %s > /dev/null 2>&1 &' % (h.name, h.IP()))
    time.sleep(2)

    result = h3.cmd('ping -c 1 -W 1 10.0.0.2')
    info('  Connectivity: %s\n' % ('OK' if ' 0%' in result else 'FAILED'))

    ping_log = os.path.join(output_dir, 'ping_%s.txt' % label)
    iperf_log = os.path.join(output_dir, 'iperf_%s.txt' % label)

    h2.cmd('killall iperf 2>/dev/null || true')
    h2.cmd('iperf -s -p 5001 > /tmp/iperf_server.log 2>&1 &')
    time.sleep(0.5)

    info('  Starting ping from h3 to h2 (interval=0.2s, count=75)...\n')
    h3.cmd('ping -n -i 0.2 -c 75 %s > %s 2>&1 &' % (h2.IP(), ping_log))
    time.sleep(2)

    info('  Starting iperf from h1 to h2 (20s)...\n')
    h1.cmd('iperf -c %s -p 5001 -t 20 -i 1 > %s 2>&1 &' % (h2.IP(), iperf_log))
    time.sleep(2)

    h1.cmd('wait')
    h3.cmd('wait')

    time.sleep(2)

    info('  Ping min/avg/max from h3:\n')
    ping_output = h3.cmd('cat %s' % ping_log)
    for line in ping_output.strip().split('\n')[-3:]:
        info('  %s\n' % line)

    h2.cmd('killall iperf 2>/dev/null || true')
    net.stop()
    time.sleep(1)


def disable_offload(net):
    """Disable TSO/GSO/GRO offloading for accurate measurement."""
    for h in net.hosts:
        for iface in h.intfNames():
            h.cmd('ethtool -K %s tso off gso off gro off 2>/dev/null || true' % iface)


def run():
    output_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(output_dir, exist_ok=True)

    for qsize in [20, 200]:
        run_bufferbloat_test(qsize, output_dir)
        time.sleep(2)

    info('\nExperiments complete. Data saved to: %s\n' % output_dir)


if __name__ == '__main__':
    setLogLevel('info')
    run()
