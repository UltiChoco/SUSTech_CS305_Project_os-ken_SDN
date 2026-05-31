# DNS Bonus (Tier 1)

This document describes the controller-based DNS responder and how to test it.

## Overview
- A simple DNS responder runs inside the controller.
 - It handles UDP/53 A, AAAA, and CNAME record queries.
 - Answers are generated from static tables in `dns_server.py`.
- Unknown names return NXDOMAIN (or a formatted failure response).
- No recursion, no external DNS, and no TCP DNS are supported.

## Implementation Summary
- Added `dns_server.py` with a `DNSServer` class.
- `controller.py` now routes UDP/53 packets to the DNS handler before normal IP forwarding.
- The controller ARP cache preloads `192.168.1.1 -> 7e:49:b3:f0:f9:99` so hosts can ARP the DNS server IP.
- A Mininet test is added under `tests/dns_test/test_network.py`.

## DNS Packet Flow
1. PacketIn arrives at the controller.
2. DHCP packets are handled first, ARP packets second.
3. If IPv4 + UDP + dst_port == 53, the packet is sent to `DNSServer.handle_dns()`.
4. The DNS handler parses the question and accepts A/AAAA/CNAME with class IN.
5. If the name exists in the DNS tables, the controller builds a response with TTL=60.
6. If the name does not exist, the controller returns NXDOMAIN (or a formatted failure response).
7. The response is sent back with PacketOut on the original input port.

## DNS Response Encapsulation
- Ethernet: src = 7e:49:b3:f0:f9:99, dst = client MAC
- IPv4: src = 192.168.1.1, dst = client IP
- UDP: src_port = 53, dst_port = client UDP source port

## Testing
### Start the controller
```
conda activate cs305
osken-manager --observe-links controller.py
```

### Start Mininet
```
cd tests/dns_test
sudo env "PATH=$PATH" python test_network.py
```

### Run commands in Mininet CLI
```
h1 ifconfig
h2 ifconfig
h1 nslookup web.local 192.168.1.1
h1 nslookup h2.local 192.168.1.1
h1 nslookup unknown.local 192.168.1.1
h1 nslookup -type=AAAA web.local 192.168.1.1
h1 nslookup -type=CNAME www.local 192.168.1.1
```

