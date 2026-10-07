# 三节点 Kubernetes 拓扑与阶段验收

> 记录日期：2026-10-07。本文记录已经验证的基础条件和下一阶段实施边界；多节点集群尚未完成前，不把计划项标为已验证。

## 当前结论

- [已验证] 三台 Ubuntu 22.04.5 VMware VM 均可通过 Tailscale SSH 访问。
- [已验证] 三台 VM 位于同一 VMware `192.168.244.0/24` 网络；VM1、VM2 到 VM3 的 ICMP 测试均为 2/2 成功、0% 丢包。
- [已验证] VM3 为干净环境：8 vCPU、约 7.7 GiB 内存、2 GiB swap、49 GiB 根分区；尚未安装 Docker、k3s、Helm 或 `gtp5g`。
- [边界] Tailscale只用于远程管理。k3s节点间通信优先使用VMware局域网，避免把Kubernetes覆盖网络再次套入Tailscale隧道。
- [边界] 三台VM位于同一宿主机和虚拟交换网络，天然时延几乎相同；后续必须使用独立VMnet或`tc netem`构造受控的core/edge路径差异。

## 主机清单

以下地址是2026-10-07的当前观测值。Tailscale地址用于SSH别名，VMware地址由DHCP获得；在加入集群前应确认地址稳定或设置DHCP保留，不能直接当作长期Chart配置。

| VM | SSH别名 | 当前Tailscale地址 | 当前VMware地址 | 当前状态 | 计划角色 |
|---|---|---|---|---|---|
| VM1 | `free5gc-vm-1` | `100.118.123.44` | `192.168.244.128/24` | 已验证的k3s Server与Single UPF基线 | k3s Server、MongoDB与控制面NF |
| VM2 | `free5gc-vm-2` | `100.111.54.35` | `192.168.244.129/24` | 独立单节点k3s基线 | `core`候选UPF Agent |
| VM3 | `free5gc-vm-3` | `100.112.197.4` | `192.168.244.130/24` | 干净Ubuntu；SSH已验证 | `edge`候选UPF Agent与UERANSIM候选节点 |

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
VM2: core候选节点     VM3: edge候选节点
  UPF + gtp5g          UPF + gtp5g
                       UERANSIM候选位置
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
  5gc.free5gc.org/plane=user
  5gc.free5gc.org/gtp5g=true
  topology.kubernetes.io/zone=edge
```

只有完成内核模块加载验证的节点才能获得`gtp5g=true`标签。Agent后续只能选择`core`或`edge`等受控放置类别，由确定性代码映射为这些标签。

## 实施顺序

1. 对VM1、VM2创建VMware快照，并导出当前节点、Release、Values和业务验收结果。
2. 保留VM1现有k3s Server和订阅数据；VM2经快照保护后退出其独立集群，再作为Agent加入VM1。
3. 在VM2、VM3安装并验证与当前内核匹配的`gtp5g v0.9.5`。
4. 使用VM1的VMware局域网地址作为k3s Server地址，将VM2、VM3加入集群；集群令牌不得写入仓库。
5. 设置节点标签，并先验证普通Pod能够跨节点通信、DNS和Storage正常。
6. 通过受控Values将控制面NF固定到VM1、UPF固定到VM2，UERANSIM按实验设计放置。
7. 完成端到端业务验收后，将UPF改放VM3并重复同一组测试。
8. 跨节点单网络基线稳定后，再增加Multus、第二vNIC和独立N6网络。

`scripts/bootstrap-k3s-node.sh`提供Server/Agent初始化入口，但目前仍为待验证辅助工具。使用时必须以VMware局域网地址设置`NODE_IP`、`K3S_URL`，Tailscale只用于SSH管理；K3s令牌只通过运行时环境变量传入，禁止写入仓库。现有独立k3s节点的迁移必须先做快照，不能直接以初始化脚本覆盖。

## 第一轮验收标准

```text
集群层：3个Node均Ready，节点IP使用VMware局域网，系统Pod稳定
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
