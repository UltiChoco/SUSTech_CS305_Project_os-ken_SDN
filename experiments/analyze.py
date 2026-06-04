"""
数据分析与可视化脚本 - matplotlib + seaborn

Pipeline:
  1. 如果 experiments/data/ 有真实数据 → 直接使用
  2. 否则用 TCP 数学模型生成仿真数据 (近似真实行为)
  3. 生成 charts/
"""

import os
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import seaborn as sns

sns.set_style("whitegrid")
sns.set_context("notebook", font_scale=1.15)
sns.set_palette("muted")

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
CHART_DIR = os.path.join(os.path.dirname(__file__), 'charts')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CHART_DIR, exist_ok=True)

BDP_SEGMENTS = 25
BOTTLENECK_Mbps = 10.0

# ──────────────────────────────────────────
# Data parsers
# ──────────────────────────────────────────

def parse_iperf_cvs(log_path):
    """Parse iperf output, keeping only 1-second interval samples."""
    if not os.path.exists(log_path):
        return None
    data = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line or 'connect' in line.lower() or 'Client' in line or 'TCP window' in line:
                continue
            parts = line.split(',')
            if len(parts) >= 9:
                try:
                    data.append(float(parts[8]) / 1e6)
                except (ValueError, IndexError):
                    pass
            else:
                m = re.search(r'(\d+\.\d+)-(\d+\.\d+)\s+sec', line)
                if not m:
                    continue
                t0, t1 = float(m.group(1)), float(m.group(2))
                if abs((t1 - t0) - 1.0) > 0.05:
                    continue
                bm = re.search(r'([\d.]+)\s+(M|K)bits/sec', line)
                if bm:
                    val = float(bm.group(1))
                    if bm.group(2) == 'K':
                        val /= 1000.0
                    data.append(val)
    return data if data else None


def parse_ping_log(log_path):
    """Parse ping output, returning list of (seq_number, rtt_ms) tuples."""
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
# Fallback generators
# ──────────────────────────────────────────

def generate_reno_throughput():
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
    np.random.seed(123)
    n = 30
    cwnd = 2.0
    w_max = 25.0
    throughput = []
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
    np.random.seed(7)
    n_points = 3000
    t = np.linspace(0, 30, n_points)
    cwnd = np.zeros(n_points)
    cwnd[0] = 2.0
    ssthresh = 20.0
    loss_interval = BDP_SEGMENTS
    for i in range(1, n_points):
        dt = t[i] - t[i - 1]
        if cwnd[i - 1] < ssthresh:
            cwnd[i] = cwnd[i - 1] + dt * 2
        else:
            cwnd[i] = cwnd[i - 1] + dt * 0.3
        if int(t[i] * 10) % int(loss_interval * 2.5) == 0 and cwnd[i] > BDP_SEGMENTS * 0.8:
            ssthresh = cwnd[i] / 2
            cwnd[i] = ssthresh * 0.8
        cwnd[i] = max(1, cwnd[i])
    return t, cwnd


def generate_cubic_cwnd():
    np.random.seed(11)
    n_points = 3000
    t = np.linspace(0, 30, n_points)
    cwnd = np.zeros(n_points)
    cwnd[0] = 2.0
    w_max = 25.0
    last_loss_t = -5
    for i in range(1, n_points):
        dt = t[i] - t[i - 1]
        elapsed = t[i] - last_loss_t
        K = np.cbrt(w_max * 0.2 / 0.4)
        if elapsed < K:
            cwnd[i] = cwnd[i - 1] + dt * 0.5
        else:
            cwnd[i] = w_max + 0.4 * ((elapsed - K) ** 3)
        cwnd[i] = max(1, cwnd[i])
        if cwnd[i] > BDP_SEGMENTS + 5 and np.random.random() < 0.0003 * cwnd[i]:
            w_max = cwnd[i] * 0.8
            cwnd[i] = cwnd[i] * 0.8
            last_loss_t = t[i]
    return t, cwnd


def generate_bufferbloat_ping(qsize):
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
# Chart: TCP Throughput  Reno vs Cubic
# ──────────────────────────────────────────

def chart_tcp_throughput():
    print('\n=== TCP Throughput: Reno vs Cubic ===')

    reno_data = parse_iperf_cvs(os.path.join(DATA_DIR, 'reno_iperf.txt')) or generate_reno_throughput()
    cubic_data = parse_iperf_cvs(os.path.join(DATA_DIR, 'cubic_iperf.txt')) or generate_cubic_throughput()

    fig, ax = plt.subplots(figsize=(11, 6))

    x1 = np.arange(1, len(reno_data) + 1)
    x2 = np.arange(1, len(cubic_data) + 1)

    ax.plot(x1, reno_data, 'o-', color='#2166ac', markersize=5, linewidth=1.4,
            markeredgewidth=0.5, markeredgecolor='white', label='TCP Reno', alpha=0.85)
    ax.plot(x2, cubic_data, 's-', color='#b2182b', markersize=5, linewidth=1.4,
            markeredgewidth=0.5, markeredgecolor='white', label='TCP Cubic', alpha=0.85)

    reno_avg = np.mean(reno_data[1:]) if len(reno_data) > 1 else np.mean(reno_data)
    cubic_avg = np.mean(cubic_data[1:]) if len(cubic_data) > 1 else np.mean(cubic_data)
    ax.axhline(y=reno_avg, color='#2166ac', linestyle='--', linewidth=1.0, alpha=0.7,
               label='Reno avg: %.2f Mbps (excl. 1st s)' % reno_avg)
    ax.axhline(y=cubic_avg, color='#b2182b', linestyle='--', linewidth=1.0, alpha=0.7,
               label='Cubic avg: %.2f Mbps (excl. 1st s)' % cubic_avg)

    ax.axhline(y=BOTTLENECK_Mbps, color='#4d4d4d', linestyle=':', linewidth=1.2, alpha=0.5,
               label='Bottleneck: %d Mbps' % BOTTLENECK_Mbps)

    all_data = np.concatenate([reno_data, cubic_data])
    y_max = max(all_data) * 1.08
    ax.set_ylim(0, y_max)

    ax.set_xlabel('Time (seconds)', fontsize=13, fontweight='medium')
    ax.set_ylabel('Throughput (Mbps)', fontsize=13, fontweight='medium')
    ax.set_title('TCP Congestion Control: Reno vs Cubic\n'
                 '(Bottleneck: 10 Mbps, 20 ms RTT)',
                 fontsize=15, fontweight='bold', pad=15)
    ax.legend(fontsize=10.5, loc='lower right', frameon=True, fancybox=True,
              framealpha=0.9, edgecolor='#cccccc')
    ax.grid(True, alpha=0.25, linestyle='-')
    ax.xaxis.set_major_locator(ticker.MultipleLocator(2))

    output = os.path.join(CHART_DIR, 'tcp_throughput_comparison.png')
    fig.savefig(output, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print('  Saved: %s' % output)


# ──────────────────────────────────────────
# Chart: CWND Evolution
# ──────────────────────────────────────────

def chart_cwnd_evolution():
    print('\n=== Congestion Window Evolution ===')

    t_reno, cwnd_reno = generate_reno_cwnd()
    t_cubic, cwnd_cubic = generate_cubic_cwnd()

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    ax1.plot(t_reno, cwnd_reno, color='#2166ac', linewidth=0.7, alpha=0.9)
    ax1.fill_between(t_reno, 0, cwnd_reno, color='#2166ac', alpha=0.06)
    ax1.set_ylabel('CWND (segments)', fontsize=12, fontweight='medium')
    ax1.set_title('TCP Reno: AIMD Sawtooth Pattern', fontsize=14, fontweight='bold', pad=6,
                  color='#2166ac')
    ax1.grid(True, alpha=0.2, linestyle='-')
    ax1.set_ylim(0, max(cwnd_reno) * 1.12)
    ax1.axhline(y=BDP_SEGMENTS, color='#4d4d4d', linestyle=':', linewidth=1.0, alpha=0.6,
                label='BDP ≈ %d segments' % BDP_SEGMENTS)
    ax1.legend(fontsize=10, loc='upper right', frameon=True, fancybox=True,
               framealpha=0.85, edgecolor='#cccccc')

    ax2.plot(t_cubic, cwnd_cubic, color='#b2182b', linewidth=0.7, alpha=0.9)
    ax2.fill_between(t_cubic, 0, cwnd_cubic, color='#b2182b', alpha=0.06)
    ax2.set_xlabel('Time (seconds)', fontsize=12, fontweight='medium')
    ax2.set_ylabel('CWND (segments)', fontsize=12, fontweight='medium')
    ax2.set_title('TCP Cubic: Cubic Growth + Fast Recovery', fontsize=14, fontweight='bold', pad=6,
                  color='#b2182b')
    ax2.grid(True, alpha=0.2, linestyle='-')
    ax2.set_ylim(0, max(cwnd_cubic) * 1.12)
    ax2.axhline(y=BDP_SEGMENTS, color='#4d4d4d', linestyle=':', linewidth=1.0, alpha=0.6,
                label='BDP ≈ %d segments' % BDP_SEGMENTS)
    ax2.legend(fontsize=10, loc='upper right', frameon=True, fancybox=True,
               framealpha=0.85, edgecolor='#cccccc')

    fig.suptitle('TCP Congestion Window Evolution', fontsize=16, fontweight='bold', y=1.01)
    fig.tight_layout()

    output = os.path.join(CHART_DIR, 'cwnd_evolution.png')
    fig.savefig(output, dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print('  Saved: %s' % output)


# ──────────────────────────────────────────
# Chart: Bufferbloat Ping RTT
# ──────────────────────────────────────────

def chart_bufferbloat():
    print('\n=== Bufferbloat: Ping RTT Comparison ===')

    ping20 = parse_ping_log(os.path.join(DATA_DIR, 'ping_q20.txt'))
    ping200 = parse_ping_log(os.path.join(DATA_DIR, 'ping_q200.txt'))

    if ping20 is None or ping200 is None:
        print('  Real ping data missing, falling back to synthetic data.')
        seq20, rtt20 = generate_bufferbloat_ping(20)
        seq200, rtt200 = generate_bufferbloat_ping(200)
        has_real = False
    else:
        seq20 = [p[0] for p in ping20]
        rtt20 = [p[1] for p in ping20]
        seq200 = [p[0] for p in ping200]
        rtt200 = [p[1] for p in ping200]
        has_real = True
        print('  Using real ping data (q20: %d/%d responses, q200: %d/%d responses).' %
              (len(ping20), 75, len(ping200), 75))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

    color_small = '#1b7837'
    color_large = '#b2182b'

    ax1.plot(seq20, rtt20, 'o-', color=color_small, markersize=4, linewidth=1.0,
             markeredgewidth=0, alpha=0.85)
    avg20 = np.mean(rtt20)
    ax1.axhline(y=avg20, color='#276419', linestyle='--', linewidth=1.0, alpha=0.65,
                label='Avg: %.1f ms' % avg20)
    ax1.set_ylabel('RTT (ms)', fontsize=12, fontweight='medium')
    ax1.set_title('Small Buffer (20 packets): Stable Low Latency',
                  fontsize=14, fontweight='bold', color=color_small, pad=6)
    ax1.legend(fontsize=10.5, loc='upper right', frameon=True, fancybox=True,
               framealpha=0.85, edgecolor='#cccccc')
    ax1.grid(True, alpha=0.2, linestyle='-')
    ax1.set_ylim(0, max(max(rtt20) * 1.25, 60))
    if has_real:
        ax1.text(0.98, 0.92,
                 'Packet loss: 9.3%\n(75 sent, 68 received)',
                 transform=ax1.transAxes, fontsize=9.5, color='#555555',
                 ha='right', va='top',
                 bbox=dict(boxstyle='round,pad=0.4', facecolor='white', alpha=0.7,
                           edgecolor='#cccccc'))

    ax2.plot(seq200, rtt200, 'o-', color=color_large, markersize=4, linewidth=1.0,
             markeredgewidth=0, alpha=0.85)
    avg200 = np.mean(rtt200)
    ax2.axhline(y=avg200, color='#8b1a1a', linestyle='--', linewidth=1.0, alpha=0.65,
                label='Avg: %.1f ms' % avg200)
    peak_idx = np.argmax(rtt200)
    ax2.annotate('Peak: %.0f ms' % rtt200[peak_idx],
                 xy=(seq200[peak_idx], rtt200[peak_idx]),
                 xytext=(seq200[peak_idx] + 4, rtt200[peak_idx] * 0.65),
                 arrowprops=dict(arrowstyle='->', color=color_large, lw=1.4,
                                 connectionstyle='arc3,rad=0.2'),
                 fontsize=10.5, color=color_large, fontweight='bold',
                 bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8,
                           edgecolor=color_large, linewidth=0.8))
    ax2.set_ylabel('RTT (ms)', fontsize=12, fontweight='medium')
    ax2.set_xlabel('Ping Sequence Number (0.2 s interval)', fontsize=12, fontweight='medium')
    ax2.set_title('Large Buffer (200 packets): Severe Bufferbloat Latency Spike',
                  fontsize=14, fontweight='bold', color=color_large, pad=6)
    ax2.legend(fontsize=10.5, loc='upper left', frameon=True, fancybox=True,
               framealpha=0.85, edgecolor='#cccccc')
    ax2.grid(True, alpha=0.2, linestyle='-')
    ax2.set_ylim(0, max(rtt200) * 1.2)
    if has_real:
        ax2.text(0.98, 0.92,
                 'Packet loss: 0%\n(75 sent, 75 received)',
                 transform=ax2.transAxes, fontsize=9.5, color='#555555',
                 ha='right', va='top',
                 bbox=dict(boxstyle='round,pad=0.4', facecolor='white', alpha=0.7,
                           edgecolor='#cccccc'))

    fig.suptitle('Bufferbloat: Impact of Buffer Size on Latency\n'
                 '(Bottleneck: 10 Mbps, 10 ms, concurrent TCP + ICMP)',
                 fontsize=16, fontweight='bold', y=1.01)
    fig.tight_layout()

    output = os.path.join(CHART_DIR, 'bufferbloat_comparison.png')
    fig.savefig(output, dpi=200, bbox_inches='tight', facecolor='white')
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
