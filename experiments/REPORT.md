# Mininet 网络特性研究报告

## 概述

本报告基于 CS305 SDN 项目 bonus 模块，利用 Mininet 搭建可控网络环境，研究两项核心网络特性：
1. **TCP 拥塞控制算法**（Reno vs Cubic）的性能对比
2. **Bufferbloat 现象**——大缓冲区导致的延迟灾难

实验环境：Mininet 2.3.0 + iperf + ping，数据分析基于 Python/matplotlib/seaborn。

---

## 实验一：TCP 拥塞控制算法对比

### 1.1 背景

TCP 拥塞控制是互联网可靠传输的基石。Reno (1990) 和 Cubic (2008, Linux 默认) 是两种经典算法：
- **Reno**: 慢启动 + 拥塞避免 + 快重传/快恢复。AIMD 规则（Additive Increase Multiplicative Decrease），丢包后 cwnd 减半。
- **Cubic**: 用三次函数代替 AIMD 的线性增长，丢包后能快速恢复到原窗口附近，更适合高 BDP 网络。

### 1.2 实验拓扑

```
h1 (sender) --- s1 ========== s2 --- h2 (receiver)
                 瓶颈: 10Mbps, 20ms
```

- 瓶颈链路 BDP ≈ 10Mbps × 0.02s ÷ (1448×8 bits) ≈ 17 segments
- 其余链路: 100Mbps, 1ms（无瓶颈）

### 1.3 方法

1. 在发送端 h1 分别设置 `sysctl net.ipv4.tcp_congestion_control=reno` / `cubic`
2. 运行 `iperf -c <h2> -t 30 -i 1` 采集每秒吞吐量
3. 通过数学模型模拟拥塞窗口演变（AIMD 锯形 vs 三次曲线）

### 1.4 结果

| 指标 | TCP Reno | TCP Cubic |
|---|---|---|
| 平均吞吐量 | **10.81 Mbps** | **9.85 Mbps** |
| 最大吞吐量 | 49.30 Mbps | 16.80 Mbps |
| 最小吞吐量 | 4.69 Mbps | 7.81 Mbps |
| 标准差 | 7.31 Mbps | 1.56 Mbps |
| 瓶颈利用率 | ~108% * | ~99% |

> \* Reno 平均超 10Mbps 是因为慢启动阶段第一秒吞吐量暴增至 49.30 Mbps（burst），拉高了整体平均。排除首秒后稳定在 9.49 Mbps（利用率 ~95%），标准差降至 1.55 Mbps。

![吞吐量对比](charts/tcp_throughput_comparison.png)

### 1.5 拥塞窗口分析

Reno 呈现典型 **AIMD 锯形波**：cwnd 线性增长至 BDP 上限后因丢包减半，重复此过程。Cubic 则在丢包后快速爬升（三次函数），在瓶颈附近平稳运行，窗口利用率更高。

![拥塞窗口](charts/cwnd_evolution.png)

### 1.6 结论

- 在单流、10Mbps/20ms 瓶颈下，Reno (10.81 Mbps) 与 Cubic (9.85 Mbps) 吞吐量相近。Reno 初始 burst (49.30 Mbps) 拉高平均后约 10.81 Mbps，排除首秒后稳定在 9.49 Mbps 左右
- Cubic 吞吐量更稳定（标准差 1.56 vs 7.31），波动更小
- 单流场景两者均能充分饱和瓶颈链路；差异会在多流竞争或高 BDP 场景更明显

---

## 实验二：Bufferbloat 现象验证

### 2.1 背景

Bufferbloat 是指网络设备缓冲区过大导致的数据包长时间排队现象。大缓冲区在 TCP 流满载时会造成毫秒级 RTT 变成秒级延迟，严重影响交互式应用（VoIP、游戏、Web）。

核心机制：TCP 依赖丢包作为拥塞信号。大缓冲区延迟丢包发生，使发送端继续增加 cwnd，进一步填满缓冲区，造成延迟持续高企。

### 2.2 实验拓扑

```
h1 (iperf) --- s1 ========== s2 --- h2
h3 (ping) ---/    10Mbps, 10ms
```

- h1 → h2: iperf TCP 长流，打满瓶颈带宽
- h3 → h2: 并发 ping（0.2s 间隔），测量排队延迟
- **变量**: 瓶颈 s1 出口队列大小（20 vs 200 包）

### 2.3 方法

1. 先用 `max_queue_size=20` 运行，再用 `200` 重复
2. 每次启动 h1→h2 iperf 长流（20s），同时 h3 持续 ping
3. 记录 ping RTT 时间序列，对比两种缓冲区下的延迟变化

### 2.4 结果

| 指标 | 小缓冲 (20 pkts) | 大缓冲 (200 pkts) |
|---|---|---|
| 平均 RTT | **28.2 ms** | **177.6 ms** |
| 最小 RTT | 20.1 ms | 20.1 ms |
| 最大 RTT | 34.1 ms | **251.0 ms** |
| 丢包率 | 9.3% | 0% |

![Bufferbloat 对比](charts/bufferbloat_comparison.png)

### 2.5 分析

- **小缓冲**：iperf 长流打满瓶颈后产生 9.3% 丢包（缓冲区不足丢弃），RTT 稳定在 28ms 左右，延迟可控
- **大缓冲**：丢包完全消除（0%），但代价惊人——平均 RTT 飙升至 177.6ms（**6.3 倍**），峰值达 251ms（**7.4 倍**）。TCP 发送端长时间未感知丢包，持续填满 200 包队列，形成 bufferbloat 恶性循环

### 2.6 结论

- Bufferbloat 是真实且严重的问题：大缓冲可使延迟恶化 **12.5 倍**（20ms → 251ms）
- 解决方向：AQM 算法（CoDel、FQ-CoDel）、适当减小缓冲区、ECN 标记

---

## 实验总结

通过两个 Mininet 实验，验证了：

1. **TCP 拥塞控制算法对比**——在 10Mbps/20ms 单流场景下，Reno (10.81 Mbps) 与 Cubic (9.85 Mbps) 均能饱和瓶颈，Cubic 吞吐量更稳定（标准差 1.56 vs 7.31）
2. **Bufferbloat 是延迟杀手**——200 包缓冲使平均 RTT 从 28ms 飙升至 177.6ms（**6.3 倍**），峰值达 251ms，以延迟为代价换取了零丢包

这些实验展示了 Mininet 作为网络教学和研究工具的能力：可精确控制拓扑、带宽、延迟和缓冲区，在软件层面复现实际网络行为。

---

## 运行指南

```bash
# 真实硬件实验 (需 sudo)
sudo env "PATH=$PATH" python experiments/tcp_cc_test.py
sudo env "PATH=$PATH" python experiments/bufferbloat_test.py

# 生成图表和报告
cd experiments
uv run python analyze.py
```

## 文件清单

| 文件 | 说明 |
|---|---|
| `EXPERIMENT.md` | 实验方案设计 |
| `tcp_cc_test.py` | TCP 拥塞控制实验脚本 |
| `bufferbloat_test.py` | Bufferbloat 实验脚本 |
| `analyze.py` | 数据分析与图表生成 |
| `data/*.txt` | 实验原始数据 |
| `charts/*.png` | 结果图表（3 张） |
| `REPORT.md` | 本报告 |
