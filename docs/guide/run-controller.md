# 运行控制器

## 启动控制器

```bash
osken-manager --observe-links controller.py
```

`--observe-links` 参数启用基于 LLDP 的链路发现，实现自动拓扑检测。

## 运行测试网络

打开第二个终端，执行对应测试脚本：

```bash
# DHCP 测试
cd tests/dhcp_test/
sudo env "PATH=$PATH" python test_network.py

# 交换测试
cd tests/switching_test/
sudo env "PATH=$PATH" python test_network.py

# 防火墙测试
cd tests/firewall_test/
sudo env "PATH=$PATH" python test_network.py
```

!!! note
    Mininet 需要 `sudo` 权限。`env "PATH=$PATH"` 用于在 sudo 环境中保留 conda 路径。

## 常用 Mininet 命令

| 命令 | 说明 |
|------|------|
| `pingall` | 测试所有主机之间的连通性 |
| `arping_all` | 从所有主机发送 ARP 报文 |
| `dpctl dump-flows` | 查看所有交换机的流表 |
| `net` | 查看当前网络拓扑 |
| `h1 ifconfig` | 查看主机 h1 的 IP 配置 |
| `sudo mn -c` | 清理之前配置的网络 |
