#!/usr/bin/env python3
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.148.129", username="mininet", password="mininet")

cmds = [
    "wc -l /home/mininet/CS305-2026Spring-Project/tests/switching_test/test_complex_shortest_path.py",
    "grep -n run_baseline /home/mininet/CS305-2026Spring-Project/tests/switching_test/test_complex_shortest_path.py",
    "grep -n pingAll /home/mininet/CS305-2026Spring-Project/tests/switching_test/test_complex_shortest_path.py",
    "ps -o etime,cmd -p $(pgrep -f 'python test_complex_shortest_path.py' | head -1) 2>/dev/null",
    "sudo mn -c 2>&1 | tail -3",
]
for cmd in cmds:
    _, o, _ = c.exec_command(cmd)
    print("===", cmd, "===")
    print(o.read().decode())
c.close()
