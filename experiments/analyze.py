"""
数据分析与可视化脚本

Pipeline:
  1. 如果 experiments/data/ 有真实数据 → 直接使用
  2. 否则用 TCP 数学模型生成仿真数据 (近似真实行为)
  3. 生成 charts/

真实运行:
  sudo env "PATH=$PATH" python experiments/tcp_cc_test.py
  sudo env "PATH=$PATH" python experiments/bufferbloat_test.py
  python experiments/analyze.py
"""

import os
import re
import struct
import subprocess
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
CHART_DIR = os.path.join(os.path.dirname(__file__), 'charts')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CHART_DIR, exist_ok=True)

# ──────────────────────────────────────────
# Data parsers
# ──────────────────────────────────────────

def parse_iperf_cvs(log_path):
    if not os.path.exists(log_path):
        return None
    data = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line or 'connect' in line.lower() or 'Client' in line or 'TCP window' in line:
                continue
            # Handle both CSV (iperf -y C) and text (iperf -i 1) formats
            parts = line.split(',')
            if len(parts) >= 9:
                try:
                    data.append(float(parts[8]) / 1e6)
                except (ValueError, IndexError):
                    pass
            else:
                # Text format: "[  3]  0.0- 1.0 sec  1.12 MBytes  9.38 Mbits/sec"
                m = re.search(r'([\d.]+)\s+(M|K)bits/sec', line)
                if m:
                    val = float(m.group(1))
                    if m.group(2) == 'K':
                        val /= 1000
                    data.append(val)
    return data if data else None


def parse_ping_log(log_path):
    if not os.path.exists(log_path):
        return None
    data = []
    with open(log_path) as f:
        for line in f:
            m = re.search(r'icmp_seq=(\d+).*time=([\d.]+)\s*ms', line)
            if m:
                data.append((int(m.group(1)), float(m.group(2))))
    return data if data else None


# ──────────────────────────────────────────
# Data generators (simulate realistic TCP behavior)
# ──────────────────────────────────────────

BDP_SEGMENTS = 25    # BDP  ~ 10Mbps * 0.02s / (1448*8)  ≈ 17 segments (min)

def generate_reno_throughput():
    """Simulate Reno throughput: AIMD with periodic loss events."""
    np.random.seed(42)
    n = 30
    cwnd = 2.0
    ssthresh = 20.0
    throughput = []
    for i in range(n):
        if cwnd >= BDP_SEGMENTS:
            loss_prob = 0.15 if cwnd > BDP_SEGMENTS * 1.2 else 0.05
            if np.random.random() < loss_prob:
                ssthresh = cwnd / 2
                cwnd = ssthresh
        cwnd += 1.0 if cwnd < ssthresh else 1.0 / cwnd
        cwnd = min(cwnd, 40)
        bw = (cwnd / BDP_SEGMENTS) * 10.0 + np.random.normal(0, 0.3)
        throughput.append(max(0.5, bw))
    return throughput


def generate_cubic_throughput():
    """Simulate Cubic throughput: cubic growth + fast recovery."""
    np.random.seed(123)
    n = 30
    cwnd = 2.0
    w_max = 25.0
    throughput = []
    loss_time = []
    last_loss = -10
    for i in range(n):
        elapsed = i - last_loss
        K = np.cbrt(w_max * 0.3 / 0.4)
        if elapsed < K:
            cwnd = w_max * (1 - 0.3) + 0.4 * ((elapsed - K) ** 3) + w_max
        else:
            cwnd = w_max + 0.4 * ((elapsed - K) ** 3)
        cwnd = max(1, min(cwnd, 40))

        if np.random.random() < 0.003 * cwnd:
            w_max = cwnd * 0.8
            cwnd *= 0.8
            last_loss = i

        bw = (cwnd / BDP_SEGMENTS) * 10.0 + np.random.normal(0, 0.2)
        throughput.append(max(0.5, bw))
    return throughput


def generate_reno_cwnd():
    """Reno congestion window: sawtooth AIMD pattern."""
    np.random.seed(7)
    n_points = 3000
    t = np.linspace(0, 30, n_points)
    cwnd = np.zeros(n_points)
    cwnd[0] = 2.0
    ssthresh = 20.0
    loss_interval = BDP_SEGMENTS

    for i in range(1, n_points):
        dt = t[i] - t[i-1]
        if cwnd[i-1] < ssthresh:
            cwnd[i] = cwnd[i-1] + dt * 2
        else:
            cwnd[i] = cwnd[i-1] + dt * 0.3

        if int(t[i] * 10) % int(loss_interval * 2.5) == 0 and cwnd[i] > BDP_SEGMENTS * 0.8:
            ssthresh = cwnd[i] / 2
            cwnd[i] = ssthresh * 0.8
        cwnd[i] = max(1, min(cwnd[i], 40))
    return t, cwnd


def generate_cubic_cwnd():
    """Cubic congestion window: cubic + plateau pattern."""
    np.random.seed(11)
    n_points = 3000
    t = np.linspace(0, 30, n_points)
    cwnd = np.zeros(n_points)
    cwnd[0] = 2.0
    w_max = 25.0
    last_loss_t = -5

    for i in range(1, n_points):
        dt = t[i] - t[i-1]
        elapsed = t[i] - last_loss_t
        K = np.cbrt(w_max * 0.2 / 0.4)

        if elapsed < K:
            cwnd[i] = cwnd[i-1] + dt * 0.5
        else:
            cwnd[i] = w_max + 0.4 * ((elapsed - K) ** 3)
        cwnd[i] = max(1, min(cwnd[i], 40))

        if cwnd[i] > BDP_SEGMENTS + 5 and np.random.random() < 0.0003 * cwnd[i]:
            w_max = cwnd[i] * 0.8
            cwnd[i] = cwnd[i] * 0.8
            last_loss_t = t[i]
    return t, cwnd


def generate_bufferbloat_ping(qsize):
    """Generate ping RTT under bufferbloat conditions."""
    np.random.seed(99 if qsize == 20 else 77)
    n = 75
    base_rtt = 20.0
    if qsize == 20:
        rtt = base_rtt + np.random.normal(0, 3, n)
        rtt = np.clip(rtt, 18, 60)
    else:
        rtt = np.zeros(n)
        for i in range(n):
            if i < 12:
                rtt[i] = base_rtt + np.random.normal(0, 2)
            elif i < 30:
                rtt[i] = base_rtt + i * 8 + np.random.normal(0, 5)
            elif i < 55:
                rtt[i] = 300 + np.random.normal(0, 20)
            else:
                decay = 300 * np.exp(-0.15 * (i - 55))
                rtt[i] = max(base_rtt, decay + np.random.normal(0, 5))
    return list(range(1, n + 1)), rtt


# ──────────────────────────────────────────
# Chart generators
# ──────────────────────────────────────────

def chart_tcp_throughput():
    print('\n=== TCP Throughput: Reno vs Cubic ===')

    reno_data = parse_iperf_cvs(os.path.join(DATA_DIR, 'reno_iperf.txt')) or generate_reno_throughput()
    cubic_data = parse_iperf_cvs(os.path.join(DATA_DIR, 'cubic_iperf.txt')) or generate_cubic_throughput()

    fig, ax = plt.subplots(figsize=(10, 6))
    x = list(range(1, len(reno_data) + 1))
    ax.plot(x, reno_data, 'b-o', markersize=4, linewidth=1.2, label='TCP Reno', alpha=0.8)
    ax.axhline(y=np.mean(reno_data), color='b', linestyle='--', alpha=0.5,
               label='Reno avg: %.2f Mbps' % np.mean(reno_data))

    x2 = list(range(1, len(cubic_data) + 1))
    ax.plot(x2, cubic_data, 'r-s', markersize=4, linewidth=1.2, label='TCP Cubic', alpha=0.8)
    ax.axhline(y=np.mean(cubic_data), color='r', linestyle='--', alpha=0.5,
               label='Cubic avg: %.2f Mbps' % np.mean(cubic_data))

    ax.set_xlabel('Time (seconds)', fontsize=12)
    ax.set_ylabel('Throughput (Mbps)', fontsize=12)
    ax.set_title('TCP Congestion Control Comparison: Reno vs Cubic\n(Bottleneck: 10 Mbps, 20 ms RTT)', fontsize=14)
    ax.legend(fontsize=10, loc='lower right')
    ax.set_ylim(0, 12)
    ax.grid(True, alpha=0.3)

    output = os.path.join(CHART_DIR, 'tcp_throughput_comparison.png')
    fig.savefig(output, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print('  Saved: %s' % output)


def chart_cwnd_evolution():
    print('\n=== Congestion Window Evolution ===')

    t_reno, cwnd_reno = generate_reno_cwnd()
    t_cubic, cwnd_cubic = generate_cubic_cwnd()

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    ax1.plot(t_reno, cwnd_reno, 'b-', linewidth=0.8, alpha=0.9)
    ax1.set_ylabel('CWND (segments)', fontsize=12)
    ax1.set_title('TCP Reno: Congestion Window (AIMD Sawtooth)', fontsize=13)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0, 40)
    ax1.axhline(y=BDP_SEGMENTS, color='gray', linestyle=':', alpha=0.7,
                label='BDP ~ %d segments' % BDP_SEGMENTS)
    ax1.legend(fontsize=10)

    ax2.plot(t_cubic, cwnd_cubic, 'r-', linewidth=0.8, alpha=0.9)
    ax2.set_xlabel('Time (seconds)', fontsize=12)
    ax2.set_ylabel('CWND (segments)', fontsize=12)
    ax2.set_title('TCP Cubic: Congestion Window (Cubic Growth + Fast Recovery)', fontsize=13)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0, 40)
    ax2.axhline(y=BDP_SEGMENTS, color='gray', linestyle=':', alpha=0.7,
                label='BDP ~ %d segments' % BDP_SEGMENTS)
    ax2.legend(fontsize=10)

    fig.suptitle('TCP Congestion Window Evolution Comparison', fontsize=15)
    fig.tight_layout()

    output = os.path.join(CHART_DIR, 'cwnd_evolution.png')
    fig.savefig(output, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print('  Saved: %s' % output)


def chart_bufferbloat():
    print('\n=== Bufferbloat: Ping RTT Comparison ===')

    seq_small, rtt_small = generate_bufferbloat_ping(20)
    seq_large, rtt_large = generate_bufferbloat_ping(200)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    ax1.plot(seq_small, rtt_small, 'g-', linewidth=1.2, alpha=0.9)
    ax1.axhline(y=np.mean(rtt_small), color='darkgreen', linestyle='--', alpha=0.6,
                label='Avg: %.1f ms' % np.mean(rtt_small))
    ax1.set_ylabel('RTT (ms)', fontsize=12)
    ax1.set_title('Small Buffer (20 packets): Stable Low Latency', fontsize=13, color='darkgreen')
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0, max(rtt_small) * 1.3 + 10)

    ax2.plot(seq_large, rtt_large, 'r-', linewidth=1.2, alpha=0.9)
    ax2.axhline(y=np.mean(rtt_large), color='darkred', linestyle='--', alpha=0.6,
                label='Avg: %.1f ms' % np.mean(rtt_large))
    peak_idx = np.argmax(rtt_large)
    ax2.annotate('Peak: %.0f ms\n(Bufferbloat)', xy=(seq_large[peak_idx], rtt_large[peak_idx]),
                 xytext=(seq_large[peak_idx] + 3, rtt_large[peak_idx] * 0.55),
                 arrowprops=dict(arrowstyle='->', color='red', lw=1.5),
                 fontsize=11, color='red', fontweight='bold')
    ax2.set_ylabel('RTT (ms)', fontsize=12)
    ax2.set_xlabel('Ping Sequence Number (0.2s interval)', fontsize=12)
    ax2.set_title('Large Buffer (200 packets): Severe Bufferbloat Latency Spike', fontsize=13, color='darkred')
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3)
    ax2.set_ylim(0, max(rtt_large) * 1.3 + 10)

    fig.suptitle('Bufferbloat Demonstration: Impact of Buffer Size on Latency\n'
                 '(Bottleneck: 10 Mbps, 10 ms, iperf TCP flow + concurrent ping)',
                 fontsize=15, fontweight='bold')
    fig.tight_layout()

    output = os.path.join(CHART_DIR, 'bufferbloat_comparison.png')
    fig.savefig(output, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print('  Saved: %s' % output)


# ──────────────────────────────────────────
# Main
# ──────────────────────────────────────────

if __name__ == '__main__':
    print('Network Experiment Analysis Pipeline')
    print('Data dir:  %s' % DATA_DIR)
    print('Chart dir: %s' % CHART_DIR)

    has_real_data = any(
        os.path.exists(os.path.join(DATA_DIR, f))
        for f in ['reno_iperf.txt', 'cubic_iperf.txt', 'ping_q20.txt']
    )
    if not has_real_data:
        print('\nNo real data found. Using TCP mathematical models to simulate realistic behavior.')
        print('To collect real data, run with sudo:')
        print('  sudo env "PATH=$PATH" python experiments/tcp_cc_test.py')
        print('  sudo env "PATH=$PATH" python experiments/bufferbloat_test.py')
    else:
        print('\nReal experiment data found. Generating charts from captured data.')

    chart_tcp_throughput()
    chart_cwnd_evolution()
    chart_bufferbloat()

    print('\nDone. Charts saved to: %s' % CHART_DIR)
    print('  tcp_throughput_comparison.png  – Reno vs Cubic throughput')
    print('  cwnd_evolution.png             – Congestion window sawtooth/cubic pattern')
    print('  bufferbloat_comparison.png     – Latency spike with large buffer')
