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

        # TODO: read rule_file
        # TODO: parse JSON rules
        # TODO: create FirewallRule objects
        # TODO: append them into rules

        return rules

    def install_rules(self, ofctls):
        """
        Install firewall rules to all switches.
        """
        for dpid, ofctl in ofctls.items():
            for rule in self.rules:

                # TODO: only handle deny rules

                # TODO: convert protocol name to protocol number

                # TODO: normalize source and destination ports

                # TODO: skip invalid port rules

                # TODO: avoid duplicated flow installation

                # TODO: use ofctl.set_flow() to install a high-priority drop flow

                pass