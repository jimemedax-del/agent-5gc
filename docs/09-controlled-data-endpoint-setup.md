# 受控数据面测试端点安装记录

> 2026-10-08：本文保留安装阶段记录。随后已按用户授权开始测试；最新测量与 UPF 故障见[受控数据面基线](10-controlled-data-plane-baseline.md)。

## 当前配置

| 项目 | 实际配置 |
|---|---|
| 测试服务器 | VM1，`192.168.244.128` |
| 服务 | `free5gc-iperf3.service`，active、enabled，开机自启 |
| 监听 | `192.168.244.128:5201/TCP`；UDP socket 在后续 UDP 测试时建立 |
| 运行用户 | systemd DynamicUser 非特权用户 |
| 服务端版本 | iperf3 3.9，Ubuntu 包 `3.9-1+deb11u1ubuntu0.1` |
| 客户端 | VM3 的 UE 容器内，iperf3 3.9，Debian 包 `3.9-1+deb11u3` |
| 客户端镜像 | `local/ueransim-iperf3:free5gc-v4.0.1-20261008`；覆盖文件请求 `Never`，实际 Pod 为 `IfNotPresent` |
| UERANSIM Release | Revision 4，deployed；UE Pod 1/1 Running |
| 核心网 | 保留原 VM1 控制面、VM2 UPF 拓扑 |
| 防火墙 | VM1 UFW 仍为 inactive；已添加 worker LAN 地址与 Pod 子网的 TCP/UDP 5201 规则 |

客户端不是临时安装在旧容器里：它已写入新镜像并导入 VM3 的 containerd，UE 在同一节点重建时仍可使用。VM3 重装或镜像被清理后，须重新导入镜像。

## 镜像与复现记录

- 基础 UERANSIM digest：`sha256:4a6745b0c9f0c60173833f8bef89816324e84636e220917bdc682555a299e8ba`。
- 新镜像 ID：`sha256:2c464fbf9eccafe187e2a2565e6ee3cdb4c3e256f76d223bf520103dc30e803b`。
- 本次 gzip 镜像导出文件 SHA256：`375c8c0663ebd8c849211a3934cb765e7379724285757aa0ad328727399c225a`。两端一致。
- VM1 导出文件：`/tmp/ueransim-iperf3-20261008.tar.gz`；另保留在 `/home/lhm/.work/iperf3-transfer-20261008/ueransim-iperf3.tar.gz`。
- VM3 文件：`/tmp/ueransim-iperf3-20261008-lan.tar.gz`；解压为 `.tar` 后导入 k3s。
- 安装过程遇到 UE 基础镜像缺少 CA 包、Debian 11 包迁到归档源；Dockerfile 已加入公开 CA 包与签名归档源的处理。
- 镜像通过 VMware LAN 传输，临时 HTTP 文件服务已关闭。镜像未推送到外部仓库。

配置和复现入口见 [infra/iperf3](../infra/iperf3/README.md)、[UE 镜像覆盖 Values](../infra/ueransim-iperf3-values.yaml) 和 [服务安装脚本](../scripts/configure-iperf3-server.sh)。

## 本次核验范围

已检查服务启动、自启配置、监听地址、客户端版本、镜像导入与 Helm 部署状态。
安装时尚未验证业务路径和性能；随后已完成路径检查和 300 包 RTT，TCP 压测暴露 UPF OOM。完整结果以[测试记录](10-controlled-data-plane-baseline.md)为准，安装状态不能替代业务测量。

后续路径应为 `UE 的 uesimtun0 → gNB → VM2 UPF → VM1 测试服务器`。正式测试前需读取当时的 UE 隧道 IP，检查选路并观察 UPF 转发，确认流量走该路径。测试服务器与控制面共享 VM1，后续记录 CPU 负载并使用固定测试条件；当前未加入人工链路时延。
