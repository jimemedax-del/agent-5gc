# 受控数据面基线：延迟通过，吞吐测试暴露 UPF 故障

> 2026-10-08。测试未全部完成；保留失败，不把短时探测当作完整吞吐基线。

## 测试路径与条件

`VM3 UE(uesimtun0) → VM3 gNB → VM2 UPF → VM1 iperf3(192.168.244.128:5201)`。

测试前检查了 UE 源地址选路、绑定 TUN 的 Ping、VM2 上双向 GTP-U 抓包以及 UPF NAT 新流计数。该路径不经过公网测速服务；Tailscale 仅用于管理。

- 同一 VMware 宿主机、同一 LAN，未添加人工链路时延，未启用 Multus。
- UPF：请求 500m CPU/512Mi，限制 1 CPU/1Gi；本次未修改资源限制、gtp5g 或 URR 参数。
- RTT：10 轮，每轮 30 包，间隔 0.2 秒；汇总 300 个逐包 RTT，P50/P95 采用线性插值。
- 原定 TCP 上/下行和 UDP 上行各 10 轮，每轮计量 30 秒、预热 3 秒；TCP 单流，UDP 20 Mbps/1200 字节。原定批次因故障中止。
- 节点指标每 10 秒读取 metrics-server，按指标时间戳去重；不是 UPF 内存增长的高频剖析数据。

## 实际结果

| 测试 | 实际结果 | 解释 |
|---|---|---|
| 受控 RTT | 300/300 收到，0% 丢包 | 10 轮均完成 |
| RTT 均值/P50/P95 | 1.354 / 1.310 / 1.681 ms | 范围 1.090–3.690 ms |
| TCP 上行 | 第 1 轮超时；没有完整吞吐结果 | 压测期间 UPF 被 OOM 杀掉 |
| TCP 下行、正式 UDP 批次 | 未执行 | 不填零、不推算 |
| 恢复后 UDP 短探测 | 1 轮 5 秒、无预热，接收 19.996 Mbps | 仅诊断，不并入正式批次 |
| 短探测 UDP 丢包/抖动 | 0/10415；0.718 ms | 不能证明长期稳定或最大吞吐量 |

这组 RTT 是当前虚拟实验网络的基线，不是运营商网络时延，也不能证明 edge 放置收益。20 Mbps 是 UDP 的设定发送速率，不是测得的最大容量。

## 故障证据与恢复

北京时间 11:19:43，VM2 内核报告 `CONSTRAINT_MEMCG` 和 `Killed process ... (upf)`，UPF 匿名驻留内存约 1 GiB，与容器限制一致。节点仍 Ready；证据指向容器内存限额，不是宿主机总内存耗尽。

随后 UPF 容器重启报告 `open Gtp5g: ... create: file exists`，原 Pod 网络命名空间内残留 `upfgtp`，进入反复失败。删除该 UPF Pod 后重新创建网络命名空间可以恢复启动。

恢复顺序：停止 UE → 重建 UPF Pod → 等待 UPF Ready → 重启 SMF，让启动 wrapper 重新解析新的 UPF Pod IP → 启动 UE。随后 UE 注册、PDU Session 和 TUN Ping 5/5 成功；UDP 短探测后 UPF 无重启，新容器 `memory.events` 的 OOM 计数为 0。

压测同时出现大量 URR quota 报告及 `URR ... is not in ChargingInfo map!`。当前 `urrThreshold=1000`、`urrPeriod=10`；这只是排查线索，尚未证明它造成内存增长，不能直接宣称内存泄漏或报告风暴根因已定位。

旧采集脚本在首轮故障后仍尝试第 2–5 轮，均失败；第 6 轮只留下命令，采集被人工停止。这些不是 5 次独立的 OOM 实验。现已增加失败即停止及超时客户端清理，未悄悄补测或替换失败数据。

另一个前置问题：gNB/UE 同时滚动升级可能留下不一致的 AMF/gNB 会话。已通过先停止二者、先启动 gNB 并确认 NG Setup、再启动 UE 恢复。因此 Pod Running 或 TUN 存在不能代替业务验证。

## 数据与下一步

- [原始批次](../results/20261008-core-upf/)：RTT 文本、命令、CSV、资源与部署快照、汇总及内核 OOM 证据。该批次被人工终止，metadata 未包含结束时间；summary 是停止后补算，不是重新跑测试。
- [恢复后短探测](../results/20261008-udp-short-diagnostic/)：独立参数、UDP JSON 和汇总。
- [采集脚本](../scripts/measure-controlled-data-plane.py)：支持 `--modes` 分项运行，首个失败后停止。

下一步先用限速、短时、逐级负载与 UPF 高频内存采样定位增长条件，并核对 UPF/SMF/gtp5g 兼容性和 URR 行为。任何参数或版本变更单独建实验目录；不要仅扩大内存后宣布问题解决。稳定性通过后，再完成正式吞吐批次和 core/edge 放置对照。
