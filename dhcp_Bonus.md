# DHCP Bonus 实现说明

## 1. DHCP bonus 功能

1.  **lease duration**（正式租约过期与回收）
2.  **Temporary OFFER timeout**（临时 OFFER 超时回收）
3. 增加了更清晰的 **RFC 风格 IP 可分配性判断**，防止重复分配。
4. **DHCP NAK**（非法 REQUEST 回复 NAK）
5. **DHCP RELEASE**（客户端主动释放租约）
6. **DHCP DECLINE**（客户端拒绝地址后临时隔离该 IP）



## 2. lease duration 实现

1. ACK 成功时，在 `_commit_lease` 中写入：
   - `mac_to_ip`
   - `ip_to_mac`
   - `lease_expire_time[ip] = time.time() + lease_duration`
2. 每次处理 DHCP 报文前，在 `handle_dhcp` 中统一调用 `_cleanup_expired_state()`。
3. `_cleanup_expired_leases()` 会清理到期租约并输出日志：
   - 删除 `mac_to_ip / ip_to_mac / lease_expire_time`
4. 到期后该 IP 自动重新回到可分配池。

## 3. 如何避免重复 IP 分配

核心检查函数：`_is_ip_available_for_mac(ip, mac)`，统一判断：

1. IP 是否在地址池内。
2. IP 是否处于 DECLINE 隔离期。
3. IP 是否已被其他 MAC 正式租约占用。
4. IP 是否已被其他 MAC 临时 OFFER 占用。

IP 分配（OFFER）和 REQUEST 校验（ACK 前）都走这套检查，避免双重标准。

## 4. RFC 风格改进点

### REQUEST 校验

- 在 `DHCP_REQUEST` 时解析 requested IP（option 50 或 ciaddr）。
- 越界、无效、已被占用、与当前 OFFER 不匹配（同客户端有活跃 OFFER 时）都视为非法。

### NAK

- 非法 REQUEST 会回 `DHCP NAK`（message type=NAK + server identifier）。
- 不再对非法 REQUEST 自动改配其他 IP 并 ACK。

### RELEASE

- 识别 `DHCP_RELEASE`（option 53 = RELEASE）。
- 根据 `ciaddr` 或 `mac_to_ip` 释放租约并清理相关 OFFER。
- 输出日志：`[DHCP] RELEASE -> client=..., ip=...`

### DECLINE

- 识别 `DHCP_DECLINE`（option 53 = DECLINE）。
- 清理该客户端的 OFFER，并将被 DECLINE 的 IP 放入 `declined_ip_until` 临时隔离。
- 到达 `decline_timeout` 后自动恢复可分配。

## 5. bonus测试

> 重要！运行测试脚本前更改dhcp.py中的Config参数为（lease_duration=8s，offer_timeout=4s，decline_timeout=6s）


终端 1：

```bash
cd /home/mininet/CS305-2026Spring-Project
osken-manager --observe-links controller.py
```

终端 2：

```bash
cd /home/mininet/CS305-2026Spring-Project/tests/dhcp_test
sudo env "PATH=$PATH" python test_dhcp_bonus.py
```

流程：

1. 启动 5 主机 Mininet 拓扑（`h1`~`h5`），并清空初始地址。
2. 验证 `h1` 正常 DORA，且 ACK 中包含 `lease_time`（option 51）。
3. 验证 `h2` 分配到的地址与 `h1` 不重复。
4. 验证 `h3` 非法请求已占用 IP 时收到 NAK。
5. 验证 `h2` 发送 RELEASE 后，`h4` 可以复用该 IP。
6. 验证 `h3` DECLINE 后，下一次 DISCOVER 不会立即拿到同一 IP。
7. 当 `lease_duration <= 30` 时，额外等待租约过期并验证过期地址可被 `h5` 重新分配；否则跳过等待演示。



