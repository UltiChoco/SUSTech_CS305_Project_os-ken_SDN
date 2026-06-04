#!/usr/bin/env python3
import paramiko
import sys
import time

LOG = "/home/mininet/test_complex_run.log"

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect("192.168.148.129", username="mininet", password="mininet")

for i in range(100):
    _, stdout, _ = client.exec_command(
        f"grep -E 'TEST_EXIT|Results:|ALL TESTS|FAILED:' {LOG} 2>/dev/null | tail -5; "
        f"tail -6 {LOG} 2>/dev/null; "
        f"wc -c {LOG} 2>/dev/null"
    )
    text = stdout.read().decode()
    if "TEST_EXIT=" in text:
        _, stdout2, _ = client.exec_command(f"cat {LOG}")
        print(stdout2.read().decode()[-25000:])
        client.close()
        sys.exit(0 if "ALL TESTS PASSED" in text else 1)
    if i % 5 == 0:
        print(f"--- poll {i} ---\n{text}")
    time.sleep(6)

_, stdout, _ = client.exec_command(f"tail -80 {LOG} 2>/dev/null; cat /home/mininet/vm_runner.out 2>/dev/null")
print("TIMEOUT\n", stdout.read().decode())
client.close()
sys.exit(2)
