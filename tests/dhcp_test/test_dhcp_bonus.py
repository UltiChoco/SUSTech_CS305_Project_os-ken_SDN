#!/usr/bin/env python3
"""
DHCP bonus demonstration test for CS305 project.

It demonstrates:
1. DHCP lease duration option is included in ACK.
2. Different hosts do not receive duplicate IP addresses.
3. Invalid REQUEST for an occupied IP receives DHCP NAK.
4. DHCP RELEASE makes the IP reusable.
5. DHCP DECLINE quarantines the declined IP temporarily.
6. If lease_duration is short enough, expired leases are reclaimed.

Recommended demo setting in dhcp.py:
    lease_duration = 8
    offer_timeout = 4
    decline_timeout = 6

Run with controller in another terminal:
    osken-manager --observe-links controller.py

Then run this script:
    sudo env "PATH=$PATH" python test_dhcp_bonus.py
"""

import json
import os
import sys
import time
import subprocess

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.topo import Topo
from mininet.log import setLogLevel
from mininet.clean import cleanup


SERVER_IP = '192.168.1.1'

# If ACK lease time is larger than this, the script will still prove the lease option,
# but it will skip the waiting-based expiry demo to avoid waiting for one hour.
MAX_WAITABLE_LEASE = 30
EXTRA_WAIT_AFTER_LEASE = 3


class DHCPBonusTopo(Topo):
    def build(self):
        s1 = self.addSwitch('s1')
        for i in range(1, 6):
            h = self.addHost(f'h{i}', ip='0.0.0.0/0')
            self.addLink(h, s1)


HOST_DHCP_TOOL = r'''
import json
import socket
import struct
import sys
import time

from os_ken.lib import addrconv
from os_ken.lib.packet import packet
from os_ken.lib.packet import ethernet
from os_ken.lib.packet import ether_types
from os_ken.lib.packet import ipv4
from os_ken.lib.packet import udp
from os_ken.lib.packet import dhcp
from os_ken.ofproto import inet

IFACE = sys.argv[1]
MAC = sys.argv[2]
ACTION = sys.argv[3]
SERVER_IP = sys.argv[4]

DISCOVER = getattr(dhcp, 'DHCP_DISCOVER', 1)
OFFER = getattr(dhcp, 'DHCP_OFFER', 2)
REQUEST = getattr(dhcp, 'DHCP_REQUEST', 3)
DECLINE = getattr(dhcp, 'DHCP_DECLINE', 4)
ACK = getattr(dhcp, 'DHCP_ACK', 5)
NAK = getattr(dhcp, 'DHCP_NAK', 6)
RELEASE = getattr(dhcp, 'DHCP_RELEASE', 7)
BOOT_REQUEST = getattr(dhcp, 'DHCP_BOOT_REQUEST', 1)


def _option_u8(tag, value):
    return dhcp.option(tag=tag, value=struct.pack('!B', value))


def _option_ip(tag, ip):
    return dhcp.option(tag=tag, value=addrconv.ipv4.text_to_bin(ip))


def _msg_name(msg_type):
    names = {
        DISCOVER: 'DISCOVER',
        OFFER: 'OFFER',
        REQUEST: 'REQUEST',
        DECLINE: 'DECLINE',
        ACK: 'ACK',
        NAK: 'NAK',
        RELEASE: 'RELEASE',
    }
    return names.get(msg_type, str(msg_type))


def _decode_msg_type(dhcp_pkt):
    if dhcp_pkt is None or dhcp_pkt.options is None:
        return None
    for opt in dhcp_pkt.options.option_list:
        if isinstance(opt, dhcp.option) and opt.tag == dhcp.DHCP_MESSAGE_TYPE_OPT and len(opt.value) >= 1:
            return opt.value[0]
    return None


def _decode_lease_time(dhcp_pkt):
    if dhcp_pkt is None or dhcp_pkt.options is None:
        return None
    for opt in dhcp_pkt.options.option_list:
        if isinstance(opt, dhcp.option) and opt.tag == dhcp.DHCP_IP_ADDR_LEASE_TIME_OPT and len(opt.value) == 4:
            return struct.unpack('!I', opt.value)[0]
    return None


def _build_dhcp_packet(msg_type, xid, requested_ip=None, server_id=None, ciaddr='0.0.0.0'):
    option_list = [
        _option_u8(dhcp.DHCP_MESSAGE_TYPE_OPT, msg_type),
    ]

    if requested_ip:
        option_list.append(_option_ip(dhcp.DHCP_REQUESTED_IP_ADDR_OPT, requested_ip))

    if server_id:
        option_list.append(_option_ip(dhcp.DHCP_SERVER_IDENTIFIER_OPT, server_id))

    pkt = packet.Packet()
    pkt.add_protocol(ethernet.ethernet(
        ethertype=ether_types.ETH_TYPE_IP,
        dst='ff:ff:ff:ff:ff:ff',
        src=MAC,
    ))
    pkt.add_protocol(ipv4.ipv4(
        src='0.0.0.0' if ciaddr == '0.0.0.0' else ciaddr,
        dst='255.255.255.255',
        proto=inet.IPPROTO_UDP,
    ))
    pkt.add_protocol(udp.udp(src_port=68, dst_port=67))
    pkt.add_protocol(dhcp.dhcp(
        op=BOOT_REQUEST,
        chaddr=MAC,
        xid=xid,
        ciaddr=ciaddr,
        flags=0x8000,
        options=dhcp.options(option_list=option_list),
    ))
    pkt.serialize()
    return pkt.data


def _open_socket():
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
    s.bind((IFACE, 0))
    s.settimeout(0.2)
    return s


def _recv_dhcp_reply(sock, xid, timeout=3.0):
    end_time = time.time() + timeout
    while time.time() < end_time:
        try:
            data = sock.recv(4096)
        except socket.timeout:
            continue

        pkt = packet.Packet(data=data)
        pkt_dhcp = pkt.get_protocol(dhcp.dhcp)
        pkt_udp = pkt.get_protocol(udp.udp)
        if pkt_dhcp is None or pkt_udp is None:
            continue
        if pkt_udp.src_port != 67 or pkt_udp.dst_port != 68:
            continue
        if pkt_dhcp.xid != xid:
            continue

        msg_type = _decode_msg_type(pkt_dhcp)
        return {
            'timeout': False,
            'xid': xid,
            'msg_type': msg_type,
            'msg_name': _msg_name(msg_type),
            'yiaddr': pkt_dhcp.yiaddr,
            'siaddr': pkt_dhcp.siaddr,
            'lease_time': _decode_lease_time(pkt_dhcp),
        }

    return {
        'timeout': True,
        'xid': xid,
        'msg_type': None,
        'msg_name': 'TIMEOUT',
        'yiaddr': None,
        'siaddr': None,
        'lease_time': None,
    }


def _send_and_maybe_recv(msg_type, xid, requested_ip=None, server_id=None, ciaddr='0.0.0.0', expect_reply=True):
    sock = _open_socket()
    # Let old packets drain a little.
    drain_end = time.time() + 0.15
    while time.time() < drain_end:
        try:
            sock.recv(4096)
        except socket.timeout:
            break

    data = _build_dhcp_packet(
        msg_type=msg_type,
        xid=xid,
        requested_ip=requested_ip,
        server_id=server_id,
        ciaddr=ciaddr,
    )
    sock.send(data)

    if not expect_reply:
        sock.close()
        return {
            'sent': _msg_name(msg_type),
            'xid': xid,
            'requested_ip': requested_ip,
            'ciaddr': ciaddr,
            'reply_expected': False,
        }

    reply = _recv_dhcp_reply(sock, xid=xid, timeout=3.0)
    sock.close()
    reply['sent'] = _msg_name(msg_type)
    reply['requested_ip'] = requested_ip
    reply['ciaddr'] = ciaddr
    return reply


def dora(xid_base):
    offer = _send_and_maybe_recv(DISCOVER, xid_base)
    if offer.get('timeout'):
        return {'ok': False, 'step': 'DISCOVER/OFFER', 'offer': offer}

    offered_ip = offer['yiaddr']
    ack = _send_and_maybe_recv(
        REQUEST,
        xid_base + 1,
        requested_ip=offered_ip,
        server_id=SERVER_IP,
    )
    ok = (not ack.get('timeout')) and ack.get('msg_type') == ACK and ack.get('yiaddr') == offered_ip
    return {'ok': ok, 'offer': offer, 'ack': ack, 'ip': offered_ip, 'lease_time': ack.get('lease_time')}


def bad_request(xid, requested_ip):
    reply = _send_and_maybe_recv(
        REQUEST,
        xid,
        requested_ip=requested_ip,
        server_id=SERVER_IP,
    )
    ok = (not reply.get('timeout')) and reply.get('msg_type') == NAK
    return {'ok': ok, 'reply': reply}


def release(xid, ip):
    result = _send_and_maybe_recv(
        RELEASE,
        xid,
        server_id=SERVER_IP,
        ciaddr=ip,
        expect_reply=False,
    )
    result['ok'] = True
    return result


def decline_then_discover_again(xid_base):
    offer1 = _send_and_maybe_recv(DISCOVER, xid_base)
    if offer1.get('timeout'):
        return {'ok': False, 'step': 'first DISCOVER', 'first_offer': offer1}

    declined_ip = offer1['yiaddr']
    decline_reply = _send_and_maybe_recv(
        DECLINE,
        xid_base + 1,
        requested_ip=declined_ip,
        server_id=SERVER_IP,
        expect_reply=False,
    )

    # Same client asks again. A correct RFC-like server should not immediately re-offer the declined IP.
    offer2 = _send_and_maybe_recv(DISCOVER, xid_base + 2)
    ok = (not offer2.get('timeout')) and offer2.get('yiaddr') != declined_ip
    return {
        'ok': ok,
        'declined_ip': declined_ip,
        'first_offer': offer1,
        'decline': decline_reply,
        'second_offer': offer2,
    }


def main():
    if ACTION == 'dora':
        result = dora(int(sys.argv[5]))
    elif ACTION == 'bad_request':
        result = bad_request(int(sys.argv[5]), sys.argv[6])
    elif ACTION == 'release':
        result = release(int(sys.argv[5]), sys.argv[6])
    elif ACTION == 'decline_demo':
        result = decline_then_discover_again(int(sys.argv[5]))
    else:
        result = {'ok': False, 'error': 'unknown action: ' + ACTION}

    print(json.dumps(result, sort_keys=True))


main()
'''


def run_host_tool(host, action, *args, timeout=8):
    iface = host.defaultIntf().name
    mac = host.MAC()
    cmd = [sys.executable, '-c', HOST_DHCP_TOOL, iface, mac, action, SERVER_IP] + [str(a) for a in args]
    proc = host.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return {'ok': False, 'timeout': True, 'stdout': out, 'stderr': err}

    if err.strip():
        print(f'[{host.name} stderr]\n{err.strip()}')

    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines:
        return {'ok': False, 'error': 'no output from host tool', 'stderr': err}

    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return {'ok': False, 'error': 'invalid json output', 'stdout': out, 'stderr': err}


def show_result(title, result, must_pass=True):
    status = 'PASS' if result.get('ok') else ('WARN' if not must_pass else 'FAIL')
    details = []
    for key in ('ip', 'lease_time', 'declined_ip', 'reason', 'suggestion'):
        if result.get(key) is not None:
            details.append(f'{key}={result[key]}')

    ack = result.get('ack')
    if isinstance(ack, dict):
        if ack.get('msg_name'):
            details.append(f'ack={ack["msg_name"]}')
        if ack.get('yiaddr'):
            details.append(f'ack_yiaddr={ack["yiaddr"]}')

    reply = result.get('reply')
    if isinstance(reply, dict):
        if reply.get('msg_name'):
            details.append(f'reply={reply["msg_name"]}')
        if reply.get('yiaddr'):
            details.append(f'reply_yiaddr={reply["yiaddr"]}')

    offer = result.get('second_offer')
    if isinstance(offer, dict) and offer.get('yiaddr'):
        details.append(f'second_offer_yiaddr={offer["yiaddr"]}')

    detail_text = ', '.join(details) if details else 'no extra details'
    print(f'\n[{status}] {title} | {detail_text}', flush=True)
    if must_pass and not result.get('ok'):
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        raise RuntimeError(f'Test failed: {title}')


def announce_step(title, **params):
    fields = ', '.join(f'{k}={v}' for k, v in params.items())
    if fields:
        print(f'\n[STEP] {title} ({fields})', flush=True)
    else:
        print(f'\n[STEP] {title}', flush=True)


def run_quiet_cleanup(stage):
    print(f'\n[CLEAN] {stage}: start', flush=True)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    stdout_fd = os.dup(1)
    stderr_fd = os.dup(2)
    try:
        os.dup2(devnull_fd, 1)
        os.dup2(devnull_fd, 2)
        cleanup()
    finally:
        os.dup2(stdout_fd, 1)
        os.dup2(stderr_fd, 2)
        os.close(stdout_fd)
        os.close(stderr_fd)
        os.close(devnull_fd)
    print(f'[CLEAN] {stage}: done', flush=True)


def main():
    setLogLevel('info')
    run_quiet_cleanup('before test')

    topo = DHCPBonusTopo()
    net = Mininet(
        topo=topo,
        controller=None,
        switch=OVSKernelSwitch,
        autoSetMacs=True,
        autoStaticArp=False,
    )
    net.addController('c0', controller=RemoteController, ip='127.0.0.1', port=6633)

    try:
        net.start()
        time.sleep(1)

        h1, h2, h3, h4, h5 = [net.get(f'h{i}') for i in range(1, 6)]

        # Make sure hosts start without configured IP addresses.
        for h in [h1, h2, h3, h4, h5]:
            h.cmd(f'ip addr flush dev {h.defaultIntf().name}')
            h.cmd(f'ip link set {h.defaultIntf().name} up')

        print('\n================ DHCP BONUS DEMO ================', flush=True)
        print('Controller terminal should show OFFER / ACK / NAK / RELEASE / DECLINE / Lease expired.', flush=True)

        # 1. Normal DORA + lease duration option.
        announce_step('DORA for h1 and validate lease option', host='h1', xid=1000)
        r1 = run_host_tool(h1, 'dora', 1000)
        show_result('DORA for h1, ACK contains lease_time', r1)
        ip1 = r1['ip']
        lease_time = r1.get('lease_time')
        if lease_time is None:
            raise RuntimeError('ACK did not contain DHCP lease time option')

        # 2. Another host should not get duplicate IP.
        announce_step('DORA for h2 and check no duplicate with h1', host='h2', xid=2000, h1_ip=ip1)
        r2 = run_host_tool(h2, 'dora', 2000)
        show_result('DORA for h2, no duplicate IP with h1', r2)
        ip2 = r2['ip']
        if ip2 == ip1:
            raise RuntimeError(f'Duplicate allocation detected: h1 and h2 both got {ip1}')

        # 3. RFC-like NAK: h3 intentionally requests h1's occupied IP.
        announce_step('Send invalid REQUEST and expect NAK', host='h3', xid=3000, requested_ip=ip1)
        r3 = run_host_tool(h3, 'bad_request', 3000, ip1)
        show_result('RFC-like NAK when h3 requests h1 occupied IP', r3)

        # 4. RELEASE: h2 releases IP, h4 should be able to receive that IP.
        announce_step('Release h2 address', host='h2', xid=4000, ciaddr=ip2)
        r4_release = run_host_tool(h2, 'release', 4000, ip2)
        show_result('h2 sends DHCP RELEASE', r4_release)
        time.sleep(0.5)
        announce_step('Verify released IP can be reused', host='h4', xid=5000, expected_ip=ip2)
        r4_reuse = run_host_tool(h4, 'dora', 5000)
        show_result('Released IP can be reused by h4', r4_reuse)
        if r4_reuse['ip'] != ip2:
            raise RuntimeError(f'Expected h4 to reuse released IP {ip2}, but got {r4_reuse["ip"]}')

        # 5. DECLINE quarantine: h3 declines an offered IP, then should not immediately get same IP again.
        announce_step('Run DECLINE quarantine check', host='h3', xid_base=6000)
        r5 = run_host_tool(h3, 'decline_demo', 6000)
        show_result('RFC-like DECLINE quarantine blocks immediate re-offer', r5)

        # 6. Lease expiry demo. This only makes sense when lease_duration is short for demo.
        if lease_time is not None and lease_time <= MAX_WAITABLE_LEASE:
            wait_seconds = lease_time + EXTRA_WAIT_AFTER_LEASE
            announce_step('Wait lease expiry window', lease_time=lease_time, extra_wait=EXTRA_WAIT_AFTER_LEASE, total_wait=wait_seconds)
            time.sleep(lease_time + EXTRA_WAIT_AFTER_LEASE)
            announce_step('Check expired lease is reclaimable', host='h5', xid=7000, expected_ip=ip1)
            r6 = run_host_tool(h5, 'dora', 7000, timeout=8)
            show_result('Expired lease is reclaimed and can be assigned to h5', r6)
            if r6['ip'] != ip1:
                raise RuntimeError(f'Expected expired IP {ip1} to be reclaimed first, but h5 got {r6["ip"]}')
        else:
            show_result(
                'Lease expiry waiting demo skipped',
                {
                    'ok': True,
                    'reason': f'ACK lease_time={lease_time}; too long to wait in demo',
                    'suggestion': 'For demo, temporarily set Config.lease_duration = 8 in dhcp.py and restart controller.',
                },
                must_pass=False,
            )

        print('\n================ ALL DHCP BONUS CHECKS FINISHED ================', flush=True)

    finally:
        net.stop()
        run_quiet_cleanup('after test')


if __name__ == '__main__':
    main()
