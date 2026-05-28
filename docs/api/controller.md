# ControllerApp

`controller.py` 中的主 os-ken 应用类。

```python
class ControllerApp(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_0.OFP_VERSION]
    FORWARDING_COOKIE = 0x1000
    FORWARDING_PRIORITY = 1000
```

## 拓扑数据结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `graph` | `defaultdict[dict]` | 交换机邻接表：`{dpid: {neighbor_dpid: local_port}}` |
| `dpid_to_dp` | `dict` | DPID 到 Datapath 对象的映射 |
| `mac_to_loc` | `dict` | 主机 MAC 到 `(dpid, port)` 位置映射 |
| `ip_to_mac` | `dict` | IP 到 MAC 映射（ARP 缓存） |

## 事件处理

### `_handle_switch_add(ev)`

交换机加入事件。记录 DPID，安装 table-miss 流并安装防火墙规则。

### `handle_switch_delete(ev)`

交换机离开事件。清理拓扑图、DPID 映射以及邻居引用。

### `handle_host_add(ev)`

主机发现事件。通过 gratuitous ARP 学习 MAC→位置 和 IP→MAC 映射。

### `handle_link_add(ev)` / `handle_link_delete(ev)`

链路发现事件。更新双向邻接图。

### `packet_in_handler(ev)`

主数据包处理入口。分派到 DHCP、DNS、ARP 代理或 IP 转发。

## 核心方法

### `_dijkstra(src_dpid, dst_dpid)`

在交换机邻接图上使用堆优化的 Dijkstra 算法计算最短路径。
返回 `(dpid, out_port)` 元组列表。

- 源 == 目标：返回 `[]`（同一交换机）
- 无路径：返回 `None`
- 正常：返回中间交换机转发规则列表

### `_install_path(path, src_mac, dst_mac, datapath, in_port)`

沿计算出的路径安装转发流表项。

### `_handle_arp(pkt_dst, pkt_arp, datapath, in_port)`

代理 ARP 处理器。若已知目标 MAC 则回复，否则泛洪到所有交换机端口。
