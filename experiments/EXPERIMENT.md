# Mininet 网络特性研究 — 实验方案

## 实验一：TCP 拥塞控制算法比较 (Reno vs Cubic)

### 目的
通过对比 TCP Reno 和 TCP Cubic 在相同网络条件下的吞吐量和拥塞窗口行为，理解不同拥塞控制算法的特性差异。

### 拓扑
```
h1 --- s1 ------------ s2 --- h2
        |               |
        h3              h4
```
- h1-h2: 主数据流路径
- s1-s2: 瓶颈链路 (bw=10Mbps, delay=20ms)
- 其余链路: 100Mbps, 1ms

### 方法
1. 分别设置发送端 h1 的拥塞控制算法为 reno 和 cubic
2. 使用 iperf 在 h1-h2 间建立单个 TCP 连接，持续 30 秒
3. 用 tcpdump 在 h1 出口抓包，记录所有 TCP 包
4. 提取 TCP 序列号-时间数据，绘制拥塞窗口变化曲线
5. 对比两种算法的吞吐量、收敛速度、丢包恢复行为

### 预期结果
- Reno: 加性增乘性减 (AIMD)，丢包后 cwnd 减半，恢复较慢
- Cubic: 三次函数增长，丢包后快速恢复到接近原窗口，带宽利用率更高

### 数据采集
- h1 出口 pcap 文件
- iperf 吞吐量报告

---

## 实验二：Bufferbloat 现象验证

### 目的
展示大缓冲区导致的 bufferbloat 现象：长流打满瓶颈缓冲后，短流延迟剧增。

### 拓扑
```
h1 --- s1 ========== s2 --- h2
h3 ---/
```
- h1-h2: 长流路径 (iperf TCP)
- h3-h2: 短流路径 (ping 延迟探测)
- s1-s2: 瓶颈链路 (bw=10Mbps, delay=10ms)
- **关键变量**: s1 队列大小 (对比 20 vs 200 包)

### 方法
1. 设置瓶颈链路缓冲区为不同大小 (20 / 100 / 200 包)
2. 启动 h1→h2 的 iperf 长流 (30 秒)
3. 同时 h3 持续 ping h2，记录 RTT
4. 绘制 ping 延迟随时间变化曲线
5. 对比不同缓冲区大小下的延迟行为

### 预期结果
- 小缓冲区 (20 包): 延迟增加轻微，但可能有丢包
- 大缓冲区 (200 包): 延迟显著增加 (秒级)，典型的 bufferbloat 现象
- 中等缓冲区 (100 包): 介于两者之间

### 数据采集
- h3 ping 输出 (时间 + RTT)
- 可选: s1 队列长度统计 (tc qdisc)

---

## 工具需求

| 工具 | 用途 |
|---|---|
| `iperf` | TCP 流量生成 |
| `tcpdump` | 抓包 |
| `python3` + `matplotlib` + `numpy` | 数据分析与绘图 |
| `mininet` | 拓扑搭建 (已安装) |

## 运行步骤

```bash
# 1. 安装 Python 依赖
source ~/.venvs/cs305-uv/bin/activate
uv pip install matplotlib numpy

# 2. 运行实验 (需要 sudo)
sudo env "PATH=$PATH" python experiments/tcp_cc_test.py
sudo env "PATH=$PATH" python experiments/bufferbloat_test.py

# 3. 生成图表
source ~/.venvs/cs305-uv/bin/activate
python experiments/analyze.py
```

> 注: 若无 sudo 权限，`analyze.py` 会自动使用模拟数据生成示例图表。真实实验数据在 `experiments/data/` 目录中会覆盖模拟数据。

---

## 文件产出

```
experiments/
├── EXPERIMENT.md           # 本文件：实验方案
├── tcp_cc_test.py          # 实验一脚本
├── bufferbloat_test.py     # 实验二脚本
├── requirements.txt        # Python 依赖 (matplotlib, numpy)
├── data/                   # 采集的原始数据
│   ├── reno.pcap
│   ├── cubic.pcap
│   ├── ping_small_buf.txt
│   ├── ping_large_buf.txt
│   └── ...
├── charts/                 # 生成的图表
│   ├── reno_cwnd.png
│   ├── cubic_cwnd.png
│   └── bufferbloat_rtt.png
└── analyze.py              # 数据分析与绘图脚本
```
