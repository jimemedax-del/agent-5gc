# free5GC 单 UPF 基线：环境、验证与复现

> 最后更新：2026-10-07
> 范围：Docker Compose 单机基线，不是 Kubernetes 或多节点最终实验环境。

## 当前结论

- [已验证] 单 UPF 场景可完成 UE 注册、5G AKA 鉴权、PDU Session 建立和公网数据访问。
- [已验证] 最近一次检查中，`uesimtun0` 为 `10.61.0.5/16`；对 `1.1.1.1` 的 `ping -I uesimtun0 -c 4` 为 4/4 成功、0% 丢包、平均约 207 ms。
- [已验证] UPF 的 `gtp5g` 规则表中可看到当前 UE 地址对应的动态 PDR/FAR/QER 条目。
- [注意] UE 的地址是会话动态分配结果，不能将某一次的 `10.61.0.5` 当作固定配置。

## 环境快照

| 项目 | 已知状态 |
|---|---|
| 运行位置 | 远程 VMware 虚拟机 |
| 操作系统 | Ubuntu 22.04.5 LTS |
| 虚拟机规格 | 8 vCPU、约 7.7 GiB 内存、约 49 GiB 根分区 |
| 容器运行时 | Docker 29.1.3、Docker Compose 2.40.3 |
| 5GC 编排 | free5gc-compose v4.2.2（实际 NF 镜像标签为 v4.2.1） |
| UE/gNB 模拟 | UERANSIM v3.3.0 |
| 内核用户面模块 | gtp5g v0.9.5，已编译、加载并配置为开机加载 |
| Docker 私网 | `free5gc-compose_privnet`，`10.100.200.0/24` |
| 访问方式 | Tailscale SSH；具体地址不作为项目配置记录 |

Compose 工作目录位于远程 VM：`/home/lhm/free5gc-lab/free5gc-compose`。

## 容器与角色

### 基础 5GC 闭环

| 容器 | 角色 |
|---|---|
| `nrf` | Network Repository Function，NF 注册和服务发现 |
| `amf` | 接入、移动性与 NAS 控制协调 |
| `ausf` | 鉴权服务 |
| `udm`、`udr`、`mongodb` | 用户身份、订阅数据与最终存储 |
| `smf` | PDU Session 控制、IP/规则协调、UPF 选择 |
| `upf` | 用户面数据转发 |

### 已部署的扩展或辅助组件

| 容器 | 说明 |
|---|---|
| `nssf` | 网络切片选择 |
| `pcf` | 策略控制 |
| `chf` | 计费功能 |
| `nef` | 对外能力开放接口 |
| `webui` | 用户与订阅配置管理界面，不在实时数据路径 |
| `n3iwf`、`n3iwue` | 非 3GPP（例如 Wi-Fi）接入扩展；不在当前 UERANSIM 3GPP 接入路径 |
| `ueransim` | 模拟 UE 与 gNB，不是 5GC NF |

## 已验证证据

### 控制面和用户面

```text
UE 注册请求 → gNB → AMF
AMF → AUSF → UDM → UDR/MongoDB：完成 5G AKA 与订阅数据查询
UE PDU Session 请求 → gNB → AMF → SMF
SMF → UPF：通过 PFCP 下发用户面规则
UE ⇄ gNB ⇄ UPF ⇄ Internet：用户数据双向传输
```

最近一次数据面检查的关键输出：

```text
PING 1.1.1.1 from 10.61.0.5 uesimtun0
4 packets transmitted, 4 received, 0% packet loss
round-trip average ≈ 207 ms
```

该时延包含校园/公网出口与到目标地址的路径，**不能**直接作为 5GC 内部时延或 edge UPF 优势的指标。

### 动态 PFCP/gtp5g 规则

已从 UPF 内读取：

```bash
docker exec upf /free5gc/gtp5g-tunnel list pdr
docker exec upf /free5gc/gtp5g-tunnel list far
docker exec upf /free5gc/gtp5g-tunnel list qer
```

当前会话对应 `SEID = 3`。可观察到：

- 上行：识别来自 UE `10.61.0.5` 的流量，按规则去除 GTP-U 封装并转向数据网络；
- 下行：识别目的地址为该 UE 的流量，按规则封装为 GTP-U 并发向 gNB；
- QER 中存在上下行速率控制条目。

此前为教学演示直接终止过 UE 进程，UPF 中可能保留旧会话规则。这是非优雅释放造成的运行时残留，后续 Task 生命周期设计必须处理创建、释放、超时和清理。

## 常用检查命令

```bash
# 查看运行中的容器
docker ps

# 查看 UE 是否仍在运行及其会话地址
docker exec ueransim pgrep -af nr-ue
docker exec ueransim ip -br addr show dev uesimtun0
docker exec ueransim ip route

# 验证用户面；源地址应与上一条显示的 uesimtun0 地址一致
docker exec ueransim ping -I uesimtun0 -c 4 1.1.1.1

# 查看 UE 运行日志（文件名取决于启动方式）
docker exec ueransim tail -n 80 /tmp/ue-pdu-trace.log

# 查看 UPF 实际生效的动态规则
docker exec upf /free5gc/gtp5g-tunnel list pdr
docker exec upf /free5gc/gtp5g-tunnel list far
docker exec upf /free5gc/gtp5g-tunnel list qer
```

## 待验证问题

- [待验证] UE/会话的优雅释放方式，以及 UPF 旧规则的可靠清理策略。
- [待验证] 现有 Compose 的全部扩展 NF 是否都能通过独立业务场景实际调用。

默认切片`1-010203`的失败已在PS-002中定位为PCF策略数据不完整；补齐FlowRule与ChargingData后，`1-010203`和`1-112233`两条会话均已验证成功。空白VM上的k3s/Helm重建也已完成，见[重建记录](06-clean-vm-rebuild.md)。

## 下一步

Compose基线作为业务流程和故障排查对照保留；Kubernetes单节点基线已经完成。下一阶段进入三节点Flannel单网络基线，随后才引入Multus、双网卡和可测量的edge/core路径差异。
