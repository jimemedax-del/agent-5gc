# 三节点功能基线测量

> 测量日期：2026-10-08。拓扑为 VM1 控制面、VM2 UPF、VM3 UERANSIM；未注入 `tc netem` 时延，也未启用 Multus 或第二张 vNIC。

## 目的与边界

本测量验证三节点迁移后的可重复业务能力，不比较 core-UPF 与 edge-UPF 性能。每轮重启 UE，而核心网、UPF 与 gNB 保持运行；成功定义为 UE 注册成功、PDU Session 成功并创建 `uesimtun0`。

注册耗时定义为 UERANSIM 日志中 `Sending Initial Registration` 到 `Initial Registration is successful` 的时间差；PDU Session 耗时定义为 `Sending PDU Session Establishment Request` 到 `PDU Session establishment is successful` 的时间差。P50/P95 使用线性插值分位数。

## 结果

共 20 轮，全部成功：

| 指标 | 结果 |
|---|---:|
| UE 注册成功率 | 20/20（100%） |
| PDU Session 成功率 | 20/20（100%） |
| `uesimtun0` 创建成功率 | 20/20（100%） |
| 注册耗时 | P50 24.5 ms；P95 32.05 ms；范围 23–33 ms |
| PDU Session 建立耗时 | P50 311.5 ms；P95 313 ms；范围 306–313 ms |
| UE→`1.1.1.1` 外部 Ping | 100 发/91 收，9% 丢包（每轮 5 包） |

原始逐轮数据见 [2026-10-08-multinode-functional-baseline.tsv](../results/2026-10-08-multinode-functional-baseline.tsv)。

## 正确解读

- 20 轮全成功证明当前 VM1→VM2→VM3 的三节点业务链路稳定，能够作为后续放置实验的功能基线。
- 注册和会话耗时来自同宿主机 VMware LAN，不代表真实运营商网络时延。
- `1.1.1.1` 位于不可控公网路径；9% ICMP 丢包只记录外部出网现象，不能归因给 5GC/UPF，也不能作为用户面性能指标。
- 后续需要在受控 N6 测试服务上用 `ping`、`iperf3`，并在隔离实验路径注入 `tc netem`，才能比较 core-UPF 与 edge-UPF。
