import json
import os
import sys
import tempfile
import types
import unittest


os_ken = types.ModuleType("os_ken")
ofproto = types.ModuleType("os_ken.ofproto")
ofproto.ether = types.SimpleNamespace(ETH_TYPE_IP=0x0800)
ofproto.inet = types.SimpleNamespace(
    IPPROTO_ICMP=1,
    IPPROTO_TCP=6,
    IPPROTO_UDP=17,
)
os_ken.ofproto = ofproto
sys.modules.setdefault("os_ken", os_ken)
sys.modules.setdefault("os_ken.ofproto", ofproto)

from firewall import Firewall


class FakeOfCtl:
    def __init__(self):
        self.flows = []

    def set_flow(self, **kwargs):
        self.flows.append(kwargs)


class FirewallTest(unittest.TestCase):
    def write_rules(self, rules):
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as fp:
            json.dump({"rules": rules}, fp)
        return path

    def test_installs_deny_rules_as_high_priority_drop_flows(self):
        path = self.write_rules([
            {
                "src_ip": "192.168.117.2",
                "dst_ip": "192.168.117.3",
                "proto": "icmp",
                "src_port": "*",
                "dst_port": "*",
                "action": "deny",
            },
            {
                "src_ip": "192.168.117.2",
                "dst_ip": "192.168.117.3",
                "proto": "tcp",
                "dst_port": 80,
                "action": "deny",
            },
            {
                "src_ip": "192.168.117.2",
                "dst_ip": "192.168.117.3",
                "proto": "tcp",
                "dst_port": 8080,
                "action": "allow",
            },
        ])
        try:
            firewall = Firewall(path)
            ofctl = FakeOfCtl()

            firewall.install_rules({1: ofctl})
            firewall.install_rules({1: ofctl})

            self.assertEqual(2, len(ofctl.flows))
            self.assertEqual([], ofctl.flows[0]["actions"])
            self.assertEqual(Firewall.PRIORITY, ofctl.flows[0]["priority"])
            self.assertEqual(1, ofctl.flows[0]["nw_proto"])
            self.assertEqual(0, ofctl.flows[0]["tp_dst"])
            self.assertEqual(6, ofctl.flows[1]["nw_proto"])
            self.assertEqual(80, ofctl.flows[1]["tp_dst"])
        finally:
            os.remove(path)

    def test_reset_switch_allows_rules_to_be_reinstalled_after_reconnect(self):
        path = self.write_rules([
            {
                "src_ip": "192.168.117.2",
                "dst_ip": "192.168.117.3",
                "proto": "icmp",
                "action": "deny",
            },
        ])
        try:
            firewall = Firewall(path)
            first_ofctl = FakeOfCtl()
            second_ofctl = FakeOfCtl()

            firewall.install_rules({1: first_ofctl})
            firewall.reset_switch(1)
            firewall.install_rules({1: second_ofctl})

            self.assertEqual(1, len(first_ofctl.flows))
            self.assertEqual(1, len(second_ofctl.flows))
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
