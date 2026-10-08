# 三节点 Kubernetes 拓扑与阶段验收

> 最后更新：2026-10-08。本文记录三节点集群、受控部署模板及实际验收边界。

## 当前结论

- [已验证] 三台 Ubuntu 22.04.5 VMware VM 均可通过 Tailscale SSH 访问，并已组成一个 k3s `v1.30.14+k3s1` 集群。
- [已验证] 三台 VM 使用 VMware `192.168.244.0/24` 地址作为 Kubernetes InternalIP；Tailscale 只用于远程管理。
- [已验证] VM2 的旧独立 k3s Server 已卸载，现以 Agent 身份作为 `worker-upf` 加入 VM1；VM3 以 Agent 身份作为 `ran-test` 加入 VM1。
- [已验证] VM2 已加载 `gtp5g v0.9.5`，并通过 `/etc/modules-load.d/gtp5g.conf` 配置开机加载。VM3 尚未安装/加载 `gtp5g`，首轮仅承担 UERANSIM 测试角色。
- [已验证] 从 VM2、VM3 调度的普通 Pod 均能访问 VM1 的 CoreDNS Pod、解析 Kubernetes DNS，并连接 `kubernetes.default.svc` 的 `10.43.0.1:443`。
- [已验证] Docker Hub 曾在 VM2 被阻断；调整 VPN 后，VM2 与 VM3 访问 `https://registry-1.docker.io/v2/` 均返回预期的 `HTTP 401`，可用于拉取公开镜像。
- [已验证] 固定提交、受控补丁、单节点基线 Values 与多节点放置覆盖组合后，free5GC 和 UERANSIM Chart 均通过 `helm lint` 与 `helm template`。
- [已验证] 已真实完成多节点迁移：控制面 NF 与 MongoDB 位于 VM1，UPF 位于 VM2，gNB/UE 位于 VM3；`free5gc-helm` 为 Revision 11、`ueransim` 为 Revision 3，均为 `deployed`。
- [已验证] UE 已完成注册、鉴权和 PDU Session，获得 `uesimtun0 (10.60.0.4/16)`；绑定该接口对 `1.1.1.1` 的 10 包测试为 10/10 成功（平均 RTT 270.617 ms）。
- [已验证] 三节点功能基线已完成 20 轮 UE 重启测量：注册、PDU Session 与 `uesimtun0` 均为 20/20 成功；详细口径和原始数据见[功能基线测量](08-multinode-functional-baseline.md)。公网 Ping 不作为平台时延结论。
- [已测，吞吐未完成] 2026-10-08 已安装 VM1 iperf3 服务与 UE 持久化客户端镜像，UERANSIM 升级到 Revision 4；受控 RTT 300/300 成功、均值 1.354 ms。TCP 压测触发 UPF 1Gi 容器内存限制，服务已恢复，完整吞吐批次暂停；详见[数据面基线](10-controlled-data-plane-baseline.md)。上述 Revision 3 为三节点迁移验收时的版本。
- [边界] Tailscale只用于远程管理。k3s节点间通信优先使用VMware局域网，避免把Kubernetes覆盖网络再次套入Tailscale隧道。
- [边界] 三台VM位于同一宿主机和虚拟交换网络，天然时延几乎相同；后续必须使用独立VMnet或`tc netem`构造受控的core/edge路径差异。
- [已验证] 三节点 Profile 已从干净 Chart 重建；两次渲染一致、实际 Helm post-renderer 检查通过。2026-10-08 已完成一次完整受控重部署及严格业务验收。

## 受控可重复部署模板

固定入口为 [free5gc-3node-no-multus-v1.json](../infra/profiles/free5gc-3node-no-multus-v1.json)，执行工具为 [free5gc-profile.py](../scripts/free5gc-profile.py)。它把当前基线变成“固定输入→检查→重部署→业务验收”，不是自由生成 YAML 的工具。

### 固定内容与前提

- 上游提交、整个修补后 `charts/` 内容及 9 个输入文件均校验 SHA256（统一 LF）；配置变化需人工审核并更新 Profile。
- 17 个镜像均锁定 Digest，包含 initContainer；15 个工作负载、单副本、Single UPF、无 HPA/Multus。角色标签选出 3 个不同的 Linux amd64 Ready 节点。
- 控制面和 MongoDB 在 VM1、判空修复 UPF 在 VM2、gNB/UE 在 VM3。UERANSIM 镜像 Tag 为 `v4.0.1`，实际日志版本为 `v3.2.7`，不是原计划的 `v3.3.0`。
- 需已有三节点 k3s、Helm 3、Python 3.10+、PyYAML、`local-path` 存储，以及现有两个 Release、MongoDB 与匹配的测试订阅。**本入口不是从空白 VM 安装集群或创建订阅的工具。**
- UPF 宿主须加载 `gtp5g v0.9.5` 并允许转发；SCTP、资源容量与跨节点网络须预先确认。标签检查不替代能力验证。
- 两个本地镜像不在公共仓库；节点须先导入验证过的镜像归档并建立 Digest 别名，使用 `Never`。`image-commands --role user`（或 `--role ran`）仅打印别名命令，不执行或下载镜像。
- 不删除 PVC、清空 MongoDB或覆盖订阅；`local-path` 数据仍与原节点绑定，不支持数据库跨节点迁移。重部署前保留快照/数据备份。

### 四步入口（在 VM1 的仓库根目录）

```bash
export KUBECONFIG=/home/lhm/.kube/config
chmod +x scripts/free5gc-profile.py
python3 scripts/free5gc-profile.py render    # 固定 Chart + 补丁 + 清单；不访问集群
python3 scripts/free5gc-profile.py check     # 渲染 + 节点角色/Ready/平台检查
python3 scripts/free5gc-profile.py apply --confirm-disruption
python3 scripts/free5gc-profile.py verify    # Digest、放置、Ready、业务日志、TUN Ping
```

默认 Chart 和生成报告放在 `.work/free5gc-3node-no-multus-v1/`。可用 `--chart-dir` 指定独立副本、`--out` 保存唯一实验目录；离线时 `--chart-source <已有Chart Git仓库>` 只用于克隆，仍检出固定提交并应用补丁，不修改源目录。

升级前可执行 `compare-current` 比较现有 Helm manifest；只允许镜像 Digest/拉取策略规范化，其他差异一律拒绝。当前仍是原 Tag 部署，可用 `verify --allow-tag-baseline` 核对实际 imageID；这不代表 Digest 模板已部署。正式 `verify` 不接受 Tag。

### 重部署语义与验证结果

`apply` 保存旧 Values/history，依次停止 UE、gNB、UPF，升级核心网；待 NRF/MongoDB 就绪后，按 AUSF→UDR→UDM→PCF→NSSF→NEF→CHF→SMF→AMF 的顺序重启 NF，使其重新注册到 NRF；最后升级 RAN并验收。**会中断旧会话并重新注册、建会话，不是无损迁移。** 每个 Release 使用 `--atomic`，但两个 Release 不是跨 Release 原子事务；业务验收失败时只恢复人工 scale 的副本数，不自动宣称整套回滚成功。旧 Values/history 用于人工恢复，且恢复时仍需匹配 Chart、post-renderer 和镜像缓存。

2026-10-08 证据见 [模板验证结果](../results/20261008-profile-template-validation/)：

- 20 项单元测试及 1 项真实 Helm 集成测试通过；从干净提交应用修正补丁、连续两次渲染及真实 post-renderer 输出均一致。
- 生成 66 个资源、15 个工作负载；固定模板与当前 Release 经镜像规范化后语义完全一致。
- 原 Tag 环境的实际镜像 Digest、三节点放置、Pod Ready、gNB/UE 成功日志与 `gtp5g` 均通过检查；TUN Ping VM1 为 5/5 成功，仅作为连通性检查。
- 本次首次执行暴露两项可复现前提：VM3 的本地 UE 镜像需先建立 Digest 别名；且 Pod Ready 不等于 NF 已完成 NRF 注册。前者已人工补齐，后者已固化为 `apply` 的稳定化步骤。
- **2026-10-08 完整 `apply` 已通过。** 核心网为 Revision 14、UERANSIM 为 Revision 6；严格 `verify` 检查到 15 个工作负载均使用锁定 Digest、放置正确、`gtp5g`已加载，UE 注册和 PDU Session 成功。`uesimtun0` 至 VM1 的 5 包 Ping 为 0% 丢包，平均 RTT 1.093 ms。证据见[重部署结果](../results/20261008-profile-template-redeploy/)。空白 VM 初始化路径仍未验收。

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
8. 已新增多节点放置覆盖文件并完成固定 Chart 提交的渲染预演：控制面/数据库渲染到 VM1，UPF 渲染到 VM2，gNB/UE 渲染到 VM3。
9. 已执行真实 Helm 升级：控制面与 MongoDB 固定在 VM1，UPF 在 VM2，UERANSIM gNB/UE 在 VM3。SMF 与 UPF 的 PFCP Association、UE 注册、PDU Session 和数据面均通过。
10. 迁移中发现 MongoDB 默认 `install-tini` init container 依赖 Debian 软件源，已用 Values 禁用；同时发现 CHF 默认 CGF FTP 导出会阻塞会话创建，已保留 CHF API 但关闭该实验范围外的 CGF 导出。二者均已固化为可复现配置。
11. UPF Pod 重建后，SMF 曾在启动时缓存旧的 Headless Service Pod IP；重启 SMF 使其重新解析 UPF IP 后恢复。该现象说明当前动态 Pod-IP 方案不具备 UPF 无损迁移能力，后续应以重新建会为验收边界。

## 实施顺序

1. [完成] 对 VM1、VM2 创建 VMware 快照，并保留 VM1 的单节点业务基线。
2. [完成] 保留 VM1 k3s Server 与订阅数据；VM2 退出独立集群并以 Agent 加入 VM1。
3. [部分完成] VM2 已安装并验证 `gtp5g v0.9.5`；VM3 暂不需要该模块，转为 edge-UPF 前再完成安装验证。
4. [完成] 使用 VM1 的 VMware 局域网地址作为 Server 地址，将 VM2、VM3 加入集群；join token 未进入仓库。
5. [完成] 设置节点标签，并完成跨节点 Pod、DNS 和 Kubernetes Service 连通性验证。
6. [完成] 已使用原子 Helm 升级完成真实多节点部署：控制面与 MongoDB 固定 VM1、UPF 固定 VM2、UERANSIM 固定 VM3；端到端复验通过。
7. [后续] 完成端到端业务验收后，将 UPF 改放 VM3；此前需先为 VM3 安装 `gtp5g`。
8. [后续] 跨节点单网络基线稳定后，再增加 Multus、第二 vNIC 和独立 N6 网络。

`scripts/bootstrap-k3s-node.sh`提供Server/Agent初始化入口，但目前仍为待验证辅助工具。使用时必须以VMware局域网地址设置`NODE_IP`、`K3S_URL`，Tailscale只用于SSH管理；K3s令牌只通过运行时环境变量传入，禁止写入仓库。现有独立k3s节点的迁移必须先做快照，不能直接以初始化脚本覆盖。

## 第一轮验收标准

```text
集群层：[已通过] 3个Node均Ready，节点IP使用VMware局域网，系统Pod稳定
放置层：[已通过] 控制面NF在VM1，UPF在VM2，UERANSIM在VM3
内核层：[已通过] VM2已加载gtp5g；VM3转为UPF候选前仍需安装验证
控制面：[已通过] NF注册成功，SMF与UPF完成PFCP Association
接入层：[已通过] gNB与AMF完成SCTP和NG Setup
业务层：[已通过] UE完成鉴权、注册和PDU Session，创建uesimtun0
用户面：[已通过] 绑定uesimtun0 Ping 1.1.1.1为10/10成功
可重复性：[后续] UPF放置从core切到edge后，重新完成同一套验收；不以无损迁移作为当前目标
```

## 网络后续工作

当前三台VM只有`ens33`，适合先跑通Flannel单网络的跨节点基线。Multus阶段至少需要明确：

- VMware第二张vNIC连接到哪个VMnet；
- N2、N3、N4、N6各自使用的子网和路由；
- N6数据网络由哪个节点或测试服务承载；
- core与edge路径分别施加多少固定时延、抖动和带宽限制；
- `tc netem`规则的添加、查询和清理方法。

在这些条件确定前，不使用当前同宿主机局域网RTT声称edge UPF具有低时延收益。
