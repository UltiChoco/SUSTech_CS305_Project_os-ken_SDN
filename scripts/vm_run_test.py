#!/usr/bin/env python3
"""Run complex shortest-path test on Mininet VM via SSH."""
import sys
import paramiko

HOST = "192.168.148.129"
USER = "mininet"
PASSWORD = "mininet"
PROJECT = "/home/mininet/CS305-2026Spring-Project"
CONDA_BIN = "/home/mininet/software/miniconda3/envs/cs305/bin"
PATH = (
    f"{CONDA_BIN}:/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin"
)


def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=15)

    cleanup_cmd = (
        "sudo mn -c >/dev/null 2>&1 || true; "
        "for sig in osken-manager test_complex_shortest_path; do "
        "  pgrep -f \"$sig\" | while read -r pid; do "
        "    case \"$(ps -p \"$pid\" -o args= 2>/dev/null)\" in "
        f"      *{CONDA_BIN}*) kill -9 \"$pid\" 2>/dev/null ;; "
        "    esac; "
        "  done; "
        "done; "
        "sleep 1"
    )
    test_cmd = (
        f"cd {PROJECT} && "
        f"nohup {CONDA_BIN}/osken-manager --observe-links controller.py "
        f">/tmp/osken.log 2>&1 & sleep 4; "
        f"cd {PROJECT}/tests/switching_test && "
        f'sudo env PATH="{PATH}" {CONDA_BIN}/python test_complex_shortest_path.py'
    )

    print("Cleaning up VM...")
    client.exec_command(cleanup_cmd, timeout=120)[1].read()

    print("Running test on VM (may take several minutes)...")
    stdin, stdout, stderr = client.exec_command(test_cmd, timeout=900)
    out = stdout.read().decode()
    err = stderr.read().decode()
    code = stdout.channel.recv_exit_status()

    print("REMOTE_EXIT:", code)
    print(out[-35000:] if len(out) > 35000 else out)
    if err:
        print("STDERR:", err[-2000:])

    _, o2, _ = client.exec_command("tail -50 /tmp/osken.log")
    print("=== controller log (tail) ===")
    print(o2.read().decode()[-6000:])

    client.close()
    return 0 if "ALL TESTS PASSED" in out else 1


if __name__ == "__main__":
    sys.exit(main())
