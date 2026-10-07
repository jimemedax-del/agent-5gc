# 三节点 Kubernetes 拓扑与阶段验收

> 记录日期：2026-10-07。本文记录三节点集群已完成后的实际状态、验证证据和下一阶段边界。

## 当前结论

- [已验证] 三台 Ubuntu 22.04.5 VMware VM 均可通过 Tailscale SSH 访问，并已组成一个 k3s `v1.30.14+k3s1` 集群。
- [已验证] 三台 VM 使用 VMware `192.168.244.0/24` 地址作为 Kubernetes InternalIP；Tailscale 只用于远程管理。
- [已验证] VM2 的旧独立 k3s Server 已卸载，现以 Agent 身份作为 `worker-upf` 加入 VM1；VM3 以 Agent 身份作为 `ran-test` 加入 VM1。
- [已验证] VM2 已加载 `gtp5g v0.9.5`，并通过 `/etc/modules-load.d/gtp5g.conf` 配置开机加载。VM3 尚未安装/加载 `gtp5g`，首轮仅承担 UERANSIM 测试角色。
- [已验证] 从 VM2、VM3 调度的普通 Pod 均能访问 VM1 的 CoreDNS Pod、解析 Kubernetes DNS，并连接 `kubernetes.default.svc` 的 `10.43.0.1:443`。
- [已验证] Docker Hub 曾在 VM2 被阻断；调整 VPN 后，VM2 与 VM3 访问 `https://registry-1.docker.io/v2/` 均返回预期的 `HTTP 401`，可用于拉取公开镜像。
- [未迁移] 现有 `free5gc-helm` 与 `ueransim` Release 仍运行在 VM1；尚未执行多节点 Helm upgrade。
- [边界] Tailscale只用于远程管理。k3s节点间通信优先使用VMware局域网，避免把Kubernetes覆盖网络再次套入Tailscale隧道。
- [边界] 三台VM位于同一宿主机和虚拟交换网络，天然时延几乎相同；后续必须使用独立VMnet或`tc netem`构造受控的core/edge路径差异。

## 主机清单

以下地址是2026-10-07的当前观测值。Tailscale地址用于SSH别名，VMware地址由DHCP获得；在加入集群前应确认地址稳定或设置DHCP保留，不能直接当作长期Chart配置。

| VM | SSH别名 | 当前Tailscale地址 | 当前VMware地址 | 当前状态 | 计划角色 |
|---|---|---|---|---|---|
| VM1 | `free5gc-vm-1` | `100.118.123.44` | `192.168.244.128/24` | k3s Server，`Ready` | MongoDB与控制面NF |
| VM2 | `free5gc-vm-2` | `100.111.54.35` | `192.168.244.129/24` | k3s Agent `worker-upf`，`Ready`；`gtp5g`已加载 | 首轮UPF节点（`core`） |
| VM3 | `free5gc-vm-3` | `100.112.197.4` | `192.168.244.130/24` | k3s Agent `ran-test`，`Ready` | 首轮UERANSIM节点（`edge`）；后续edge-UPF候选 |

VM3密钥登录已验证：

```bash
ssh -o BatchMode=yes free5gc-vm-3 hostname
# lhm3-virtual-machine
```

## 目标拓扑

```text
VM1: k3s Server
  MongoDB + NRF/AMF/SMF/AUSF/UDM/UDR/PCF/NSSF 等控制面组件
                 │ N4/PFCP
        ┌────────┴────────┐
        │                 │
VM2: worker-upf        VM3: ran-test
  首轮UPF + gtp5g        首轮UERANSIM
  zone=core              zone=edge；后续edge-UPF候选
```

首轮实验仍只部署一个UPF：先在VM2运行，再通过受控Helm配置迁移至VM3。双UPF同时运行、ULCL和会话无损迁移属于后续实验。

## 节点标签约定

```text
VM1:
  5gc.free5gc.org/plane=control

VM2:
  5gc.free5gc.org/plane=user
  5gc.free5gc.org/gtp5g=true
  topology.kubernetes.io/zone=core

VM3:
  topology.kubernetes.io/zone=edge
  5gc.free5gc.org/role=ran-test
```

只有完成内核模块加载验证的节点才能获得`gtp5g=true`标签。VM3当前没有该标签；当其未来承担edge-UPF时，须先安装、加载并验证模块。Agent后续只能选择`core`或`edge`等受控放置类别，由确定性代码映射为这些标签。

## 本次执行记录（2026-10-07）

1. VM1 单节点基线重启后复验通过：free5GC Pod 全部 Ready，gNB NG Setup 成功，UE 通过 `uesimtun0` Ping `8.8.8.8` 为 0% 丢包。
2. VM2 卸载独立 k3s 后加入 VM1；节点名固定为 `worker-upf`，InternalIP 为 `192.168.244.129`。
3. VM2 加载 `gtp5g` 并设置持久化加载后，授予用户面与 `gtp5g` 标签。
4. 在 VM2 调度探针 Pod，获得 `10.42.1.2`；到 VM1 CoreDNS Pod 的两次 Ping 均成功，DNS 解析成功，`10.43.0.1:443` TCP 可达。
5. VM3 加入 VM1；节点名固定为 `ran-test`，InternalIP 为 `192.168.244.130`，并授予 RAN 测试和 `edge` 标签。
6. 在 VM3 调度探针 Pod，跨节点 Ping、DNS 与 Kubernetes Service TCP 连通性均通过。
7. 加入过程中使用的 join token、临时二进制文件和临时 HTTP 文件服务均已清理，token 未写入仓库。

## 实施顺序

1. [完成] 对 VM1、VM2 创建 VMware 快照，并保留 VM1 的单节点业务基线。
2. [完成] 保留 VM1 k3s Server 与订阅数据；VM2 退出独立集群并以 Agent 加入 VM1。
3. [部分完成] VM2 已安装并验证 `gtp5g v0.9.5`；VM3 暂不需要该模块，转为 edge-UPF 前再完成安装验证。
4. [完成] 使用 VM1 的 VMware 局域网地址作为 Server 地址，将 VM2、VM3 加入集群；join token 未进入仓库。
5. [完成] 设置节点标签，并完成跨节点 Pod、DNS 和 Kubernetes Service 连通性验证。
6. [下一步] 导出当前 Helm Values，创建并渲染多节点覆盖文件：控制面与 MongoDB 固定 VM1、UPF 固定 VM2、UERANSIM 固定 VM3。
7. [后续] 完成端到端业务验收后，将 UPF 改放 VM3；此前需先为 VM3 安装 `gtp5g`。
8. [后续] 跨节点单网络基线稳定后，再增加 Multus、第二 vNIC 和独立 N6 网络。

`scripts/bootstrap-k3s-node.sh`提供Server/Agent初始化入口，但目前仍为待验证辅助工具。使用时必须以VMware局域网地址设置`NODE_IP`、`K3S_URL`，Tailscale只用于SSH管理；K3s令牌只通过运行时环境变量传入，禁止写入仓库。现有独立k3s节点的迁移必须先做快照，不能直接以初始化脚本覆盖。

## 第一轮验收标准

```text
集群层：[已通过] 3个Node均Ready，节点IP使用VMware局域网，系统Pod稳定
放置层：控制面NF在VM1，UPF只在指定的VM2或VM3
内核层：UPF所在节点已加载gtp5g，另一候选节点也具备该能力
控制面：NF注册成功，SMF与UPF完成PFCP Association
接入层：gNB与AMF完成SCTP和NG Setup
业务层：UE完成鉴权、注册和PDU Session，创建uesimtun0
用户面：绑定uesimtun0访问测试数据网络成功
可重复性：UPF放置从core切到edge后，能够重新完成同一套验收
```

## 网络后续工作

当前三台VM只有`ens33`，适合先跑通Flannel单网络的跨节点基线。Multus阶段至少需要明确：

- VMware第二张vNIC连接到哪个VMnet；
- N2、N3、N4、N6各自使用的子网和路由；
- N6数据网络由哪个节点或测试服务承载；
- core与edge路径分别施加多少固定时延、抖动和带宽限制；
- `tc netem`规则的添加、查询和清理方法。

在这些条件确定前，不使用当前同宿主机局域网RTT声称edge UPF具有低时延收益。
