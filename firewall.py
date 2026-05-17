# firewall.py

import json
import os
from dataclasses import dataclass

from os_ken.ofproto import ether, inet


@dataclass(frozen=True)
class FirewallRule:
    src_ip: str = None
    dst_ip: str = None
    proto: str = None
    src_port: object = None
    dst_port: object = None
    action: str = "deny"


class Firewall:
    COOKIE = 0x305F
    PRIORITY = 60000
    DEFAULT_RULES = [
        FirewallRule(
            src_ip="192.168.117.2",
            dst_ip="192.168.117.3",
            proto="icmp",
            action="deny",
        ),
        FirewallRule(
            src_ip="192.168.117.2",
            dst_ip="192.168.117.3",
            proto="tcp",
            dst_port=80,
            action="deny",
        ),
    ]

    PROTO_MAP = {
        None: 0,
        "": 0,
        "*": 0,
        "any": 0,
        "icmp": inet.IPPROTO_ICMP,
        "tcp": inet.IPPROTO_TCP,
        "udp": inet.IPPROTO_UDP,
    }

    def __init__(self, rule_file="firewall_rules.json"):
        self.rule_file = rule_file
        self.rules = self._load_rules(rule_file)
        self.installed = set()

    # Some helper functions that may be useful
    def _normalize_any(self, value):
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in ["", "*", "any"]:
            return None
        return value

    def _normalize_proto(self, proto):
        proto = self._normalize_any(proto)
        if proto is None:
            return None
        return str(proto).lower()

    def _proto_to_number(self, proto):
        proto = self._normalize_proto(proto)
        return self.PROTO_MAP.get(proto, 0)

    def _normalize_port(self, value):
        value = self._normalize_any(value)
        if value is None:
            return 0
        return int(value)

    def _load_rules(self, rule_file):
        """
        Load firewall rules from firewall_rules.json and return a list of FirewallRule.
        """
        rules = []

        path = rule_file
        if not os.path.exists(path) and rule_file == "firewall_rules.json":
            fallback = "firewall_rule.json"
            if os.path.exists(fallback):
                path = fallback

        if not os.path.exists(path):
            return list(self.DEFAULT_RULES)

        with open(path, "r") as fp:
            data = json.load(fp)

        for item in data.get("rules", []):
            rules.append(FirewallRule(
                src_ip=self._normalize_any(item.get("src_ip")),
                dst_ip=self._normalize_any(item.get("dst_ip")),
                proto=self._normalize_proto(item.get("proto")),
                src_port=self._normalize_any(item.get("src_port")),
                dst_port=self._normalize_any(item.get("dst_port")),
                action=str(item.get("action", "deny")).lower(),
            ))

        return rules

    def _is_valid_port(self, port):
        return 0 <= port <= 65535

    def _flow_key(self, dpid, rule, proto_num, src_port, dst_port):
        return (
            dpid,
            rule.src_ip,
            rule.dst_ip,
            proto_num,
            src_port,
            dst_port,
            rule.action,
        )

    def install_rules(self, ofctls):
        """
        Install firewall rules to all switches.
        """
        for dpid, ofctl in ofctls.items():
            for rule in self.rules:
                if rule.action != "deny":
                    continue

                proto = self._normalize_proto(rule.proto)
                if proto not in self.PROTO_MAP:
                    continue

                proto_num = self._proto_to_number(proto)
                try:
                    src_port = self._normalize_port(rule.src_port)
                    dst_port = self._normalize_port(rule.dst_port)
                except (TypeError, ValueError):
                    continue

                if not self._is_valid_port(src_port) or not self._is_valid_port(dst_port):
                    continue

                if (src_port or dst_port) and proto_num not in (inet.IPPROTO_TCP, inet.IPPROTO_UDP):
                    continue

                key = self._flow_key(dpid, rule, proto_num, src_port, dst_port)
                if key in self.installed:
                    continue

                ofctl.set_flow(
                    cookie=self.COOKIE,
                    priority=self.PRIORITY,
                    dl_type=ether.ETH_TYPE_IP,
                    nw_src=rule.src_ip or 0,
                    nw_dst=rule.dst_ip or 0,
                    nw_proto=proto_num,
                    tp_src=src_port,
                    tp_dst=dst_port,
                    actions=[],
                )
                self.installed.add(key)
