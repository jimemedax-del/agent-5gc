# 面向业务需求的 CNF 服务链编排

> 最后更新：2026-10-20
> 最近实机验收配置：原 `cnf-service-chain-v1/protected` 加 `cnf-multi-ue-v1` 专用双 UE 夹具；A 的正式结果与证据见下文。
> 适用路线/阶段：R2.S1—R2.S3 的历史能力与证据，以及 R2.S4—R2.S5 的任务设计和 Agent 接入契约。阶段状态统一见 [研究路线](03-research-roadmap.md#r2-当前阶段顺序)。

## 目标与边界

项目以已验证的 free5GC、UERANSIM 与 Kubernetes 三节点环境为接入和用户面底座，进一步构建可编排的网络功能（CNF）环境。目标是：面对业务需求，选择并配置流量识别、QoS 控制、代理转发、流量引导和边缘服务接入等功能，使 UE 业务流量经过可观测、可验证的服务链。

```text
业务需求
→ 识别业务与约束
→ 选择/配置 CNF 服务链
→ free5GC 提供 UE 接入与用户面基础转发
→ 代理、引导与边缘服务处理实际业务流量
→ 测量端到端效果并反馈
```

本模块不把所有 5GC 网元视为可任意串联的数据面功能。AMF、AUSF、UDM/UDR、NRF、NSSF、PCF、SMF 和 UPF 仍按其 5GC 职责协作；业务数据的基础路径为 `UE → gNB → UPF → 数据网络`。Agent 是后续的受约束决策层，不是当前最小实验的前提。

### 已验证能力与后续目标的区别

- [已验证] 单 UE 注册、鉴权、PDU Session、隧道 IP 及经 UPF/Envoy 到 core/edge 的双向业务；显式 TCP 端口分类和顺序单流整形的证据见历史实验章节。
- [已验证，A] 两个独立用户的低负载同时接入与业务；[待验证] 共享瓶颈竞争、用户独立保底、优先级、时延/丢包目标、更多用户规模、长期稳定性及 PCF/SMF 策略到数据面的真实效果。
- [已确认目标，未实现] 识别业务需求类型。当前 `core/edge` 是预设转发目标，日志 `business_class` 是配置标签，均不能作为系统已自主理解业务需求的证据。
- 后续任务的依赖、进入条件和验收标准见 [后续任务、验收与 Agent 接入](#后续任务验收与-agent-接入)，最终交付形态见 [Agent 接入与最终交付](#agent-接入与最终交付)。

## 与既有平台的关系

| 范围 | 当前结论 | 本模块中的作用 |
|---|---|---|
| UE、gNB、AMF、SMF、UPF | 已完成注册、PDU Session 与用户面连通验证 | 提供真实 UE 接入和基础用户面 |
| PCF | 已部署，QoS 策略是否产生可测用户面效果尚未验证 | 候选 QoS 策略控制能力 |
| NEF | 已部署，源码含 Traffic Influence 接口 | 候选应用/业务需求入口，需验证端到端效果 |
| Kubernetes 三节点与固定 Profile | 已验证可重复部署 | 承载既有 5GC 与后续新增 CNF |
| 显式端口分类、代理转发、可观测流量引导 | 限定范围能力与固定 Profile 的部署/恢复、重复测量已验收；需求类型识别未实现 | 后续补齐需求模型与识别，再接确定性编排 |

已有 5GC 基线不需要重做。此前以 SMF/UPF 实例选择为中心的设想保留为后续扩展决策维度，但不再单独构成当前主线。

## 调研材料、复核方法与纠错

R2.S1 输入包括《容器化实现七大网络功能调研》《容器化网络功能调研》和《容器化网络核心功能实现研究报告》。三份材料用于发现候选项目和技术路线，不作为项目操作指令或最终事实来源。二次验证优先采用 Kubernetes、K3s、Cilium、Envoy、Gateway API 与 free5GC 官方文档；博客中的性能百分比、成熟度判断和示例命令未得到同条件复现前不进入项目结论。

### 三份材料的可用性分级

| 材料 | 候选发现价值 | 可直接作为证据的可信度 | 本项目采用方式 |
|---|---|---|---|
| 《容器化实现七大网络功能调研》 | 高：七项能力结构清楚，项目官方文档和仓库较多 | 中：对 Cilium、Istio 等完整平台的能力描述大体可用于建候选集，但未约束到本项目 Flannel、Single-UPF 和三节点资源条件 | 作为候选目录；每个入选能力仍回到官方文档和实机实验确认 |
| 《容器化网络功能调研》 | 中：覆盖工具广，适合补充检索关键词 | 低：共识别到 79 个外部链接，其中 30 个指向 CSDN，另含知乎、百度等二手/三手来源；不少比较缺少版本和测试条件 | 只保留候选线索，不采用其中的性能排序和迁移建议 |
| 《容器化网络核心功能实现研究报告》 | 中高：通信原理映射、研究问题和实验指标框架较完整 | 低：存在 RFC 主题错配、已废止规范、无法解析的 DOI 和无测试条件的百分比结论 | 只吸收问题框架；文献、标准与量化结论必须逐条重查 |

这里的“可信度”评价针对材料能否直接支撑本项目决策，不评价候选技术本身的质量。三份材料都没有结合当前固定 Profile 做恢复风险和单变量实验设计，因此不能直接导出首版部署栈。

对三份材料中影响选型的关键表述，复核结果如下：

- **Cilium/Hubble 的 L7 识别不是“全部在 eBPF 中完成”。** Cilium 官方说明，启用 L7 策略或 L7 可见性会把流量重定向到 Envoy 代理；因此它会改变实际数据路径，不能把 Hubble 当作纯旁路 DPI。[Cilium L7 可见性](https://docs.cilium.io/en/stable/observability/visibility/)
- **Cilium Bandwidth Manager 不是可直接叠加在当前 Flannel 上的独立插件。** 官方明确它不使用 chained bandwidth CNI plugin，而是原生集成于 Cilium，并强烈建议配合 BPF Host Routing；在当前集群采用它意味着先部署/迁移 Cilium 数据面。[Cilium Bandwidth Manager](https://docs.cilium.io/en/stable/network/kubernetes/bandwidth-manager/)
- **当前 k3s 不需要为了 L3/L4 NetworkPolicy 更换 CNI。** k3s 默认集成 Flannel，并内置基于 kube-router netpol 库的网络策略控制器；改用 Cilium、Calico 或 Kube-OVN 属于数据面迁移，而不是新增一个普通 CNF。[K3s 网络服务](https://docs.k3s.io/networking/networking-services)
- **free5GC 的“支持 QoS”不等于当前环境已提供可测用户面保障。** 通用功能表把 5QI、ARP、GBR、MBR 列在 `Control Plane only` 下；本项目固定的 gtp5g v0.9.5 README 另声明支持 Session-AMBR 与 MFBR，并注明 QoS 默认关闭。两者粒度不同，不能推导为“用户面完全不支持”或“保底已实现”；实际开关、PDR/QER 与收发效果须由能力探针确认。[free5GC 功能表](https://free5gc.org/guide/features/) [固定 gtp5g 版本说明](https://github.com/free5gc/gtp5g/blob/v0.9.5/README.md)
- **free5GC Traffic Influence 与当前 Single-UPF 路径不等价。** 官方实验使用 ULCL、I-UPF 和多个 PSA-UPF；本项目当前 Profile 明确为 Single UPF，因此 NEF Traffic Influence 不能替代首版代理引导实验。[free5GC Traffic Influence](https://free5gc.org/guide/8-traffic-influence/)
- **Gateway API 不能只靠创建 CRD 就获得网关能力。** 它需要具体控制器/数据面实现；且 TCPRoute、UDPRoute 仍属于 Experimental Channel，不作为首个 iperf3/TCP 实验的必需依赖。[Gateway API 概览](https://gateway-api.sigs.k8s.io/docs/concepts/api-overview/)
- **Ingress-NGINX 已于 2026 年 3 月退役，但这不等于 NGINX 开源代理本身停止维护。** 三份材料混用了二者的范围；本项目不以 Ingress-NGINX 为候选。[Kubernetes 官方退役公告](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/)
- **第三份报告把 RFC 8296 错标为“流量测量架构”。** RFC 8296 的主题实际是 BIER 在 MPLS/非 MPLS 网络中的封装；标题为 *Traffic Flow Measurement: Architecture* 的文档是 1999 年的 Informational RFC 2722。该旧文档可用于概念溯源，但不能替代当前项目的实现依据。[RFC 8296](https://datatracker.ietf.org/doc/rfc8296/) [RFC 2722](https://datatracker.ietf.org/doc/rfc2722/)
- **第三份报告引用的 RFC 2460 已不是当前 IPv6 基础规范。** IETF 已由 RFC 8200 取代 RFC 2460；后续双栈研究应从 RFC 8200 及其更新文档开始。[RFC 8200](https://datatracker.ietf.org/doc/rfc8200/)
- **第三份报告的两条 ACM DOI 无法在 DOI 官方解析器中解析。** `10.1145/3544217.3544236` 与 `10.1145/3484266.3484271` 截至 2026-10-15 均返回 `DOI Not Found`，因此对应的“eBPF-based Network Telemetry”和“eBPF Forwarding Performance”不能列为已核实论文。[DOI 1](https://doi.org/10.1145/3544217.3544236) [DOI 2](https://doi.org/10.1145/3484266.3484271)
- **无实验条件的性能百分比不进入选型评分。** “eBPF 识别损耗小于 5%”“Bandwidth Manager 比 HTB 高 30%”“Cilium 转发比 IPVS 高 20%”等表述没有给出可复现的硬件、内核、流量模型和统计口径，本项目只在相同三节点环境中用自己的数据做量化比较。

## 选型标准与候选评分

评分只回答“哪个方案最适合当前三节点实验”，不是通用产品排名。各项按 1—5 分计，5 分最好；权重来自本项目已经验证的固定 Profile 和小步实验要求。分数是可追溯的工程适配判断，不是性能实测结果；M0—M4 证据若推翻假设，须重新评分和选型。

| 评价项 | 权重 | 项目含义 |
|---|---:|---|
| 现有基线兼容性 | 30% | 不更换 Flannel、kube-proxy、Single UPF、gtp5g 与现有 Helm Profile |
| 变更/恢复风险 | 20% | 能独立部署和删除，不要求重建集群或迁移 5GC 数据面 |
| 可测量与可归因 | 20% | 能形成路径、分类、限速、后端命中和资源证据 |
| 确定性与后续编排接口 | 15% | 配置可校验，后续可由白名单目录和控制面下发 |
| 首版实现复杂度 | 10% | 依赖少、资源开销和排障面可控；分数越高越简单 |
| 研究扩展性 | 5% | 可继续扩展到动态配置、优化算法和对照实验 |

| 候选组合 | 兼容 | 风险 | 可测量 | 确定性 | 复杂度 | 扩展性 | 加权分 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **独立 Envoy + Linux `tc` + k3s 原生网络** | 5 | 5 | 5 | 5 | 4 | 4 | **4.85** |
| NGINX + Linux `tc` + k3s 原生网络 | 5 | 5 | 5 | 3 | 5 | 3 | 4.60 |
| Istio + Envoy 服务网格 | 3 | 2 | 4 | 4 | 1 | 5 | 3.05 |
| Cilium/Hubble/Bandwidth Manager | 1 | 1 | 5 | 4 | 2 | 5 | 2.55 |
| Kube-OVN/OVS 一体化数据面 | 1 | 1 | 4 | 4 | 1 | 4 | 2.20 |

结论是首版采用**独立 Envoy + Linux `tc` + k3s 原生 Service/NetworkPolicy**。Envoy 可先使用完全静态的 Listener/Cluster 配置，后续平滑演进到 xDS；其 TCP proxy 原生提供上下游统计和访问日志，适合复用当前 UE 镜像中的 iperf3。[Envoy TCP proxy](https://www.envoyproxy.io/docs/envoy/latest/configuration/listeners/network_filters/tcp_proxy_filter) [Envoy xDS 配置](https://www.envoyproxy.io/docs/envoy/latest/intro/arch_overview/operations/dynamic_configuration)

Cilium、Istio、Kube-OVN、KubeEdge 和 Multus 均保留为后续对照或扩展候选，不进入 R2.S2。其原因不是能力不足，而是当前阶段会同时改变过多实验变量，破坏已验证基线和故障归因。

## 功能目录与首版选型

七项能力分为“服务链处理功能”和“平台保障能力”。NAT/转发、NetworkPolicy 不伪装成必须串行经过的 CNF；它们分别提供连通底座和隔离边界。

| 逻辑功能 | R2.S2 首版选择 | 在路径中的职责 | 状态与边界 |
|---|---|---|---|
| 流量识别 | Envoy 两个受控 TCP Listener，按目的端口区分两类 iperf3 业务；访问日志记录 Listener、目标 Cluster、字节数与连接结果 | 链内分类 | [已验证，M2] 两类各 20/20；仅 L4 端口分类，HTTP Path/Header 留到 UE HTTP 客户端镜像固定后 |
| QoS 控制 | Linux `tc` TBF；由仅授予 `NET_ADMIN` 的辅助容器在后端 Pod 共享网络命名空间内配置和读取 qdisc | 链内整形 | [已验证，M3] 5/15 Mbps 各 5×15 秒下行，中位吞吐比 2.981；限于顺序单流，不解释为 5G 无线 QoS |
| 代理转发 | 单副本独立 Envoy Deployment，静态 v3 API 配置；镜像锁定版本与 Digest | 链内 L4 代理 | [已验证，M1] 单 Listener、单后端的受控上行通过；不安装 Istio、不接管其他 Pod 流量 |
| 流量引导 | Envoy Listener/Cluster 映射到两个 ClusterIP 后端；外部只暴露 Envoy NodePort | 链内选择后端 | [已验证，M2] 手工静态映射、错误路由 0；R2.S4 再接 xDS/受控规划器 |
| 边缘服务接入 | 同一 iperf3 服务镜像分别放置到 VM1 与 VM3，使用现有节点标签固定位置 | 链终点与放置变量 | [已验证，M4] 去整形后跨节点业务通过；三台 VM 同宿主机，不宣称真实 MEC 时延优势 |
| NAT/地址转发 | 复用 UPF N6、Flannel、kube-proxy 与固定 NodePort；保存路由和转发表证据 | 平台连通底座 | [已验证，M1 计数关联] 不迁移 Cilium/Calico，不新增第二 CNI |
| 网络策略控制 | 后端 Ingress 仅同 Namespace 的 Envoy TCP 5201；Envoy Ingress 保留 15201/15202，Egress 限后端 5201 与 CoreDNS UDP/TCP 53 | 平台隔离护栏 | [已验证] VM1/VM3 普通 Pod 正反例与撤销/重加对照通过；非全来源绕行防护 |

PCF 是策略决策功能，不是通用流量识别器或应用代理；UPF 提供基础用户面转发，不自动保证业务经过新增 CNF。NEF/PCF 能力探针与上述首版链路分离：先保持只读或隔离测试，不把未验证的 Traffic Influence、5QI 或 GBR 配置写入主实验路径。free5GC v4.2.2 修复了此前 NEF Traffic Influence 鉴权缺陷，但 NEF 仍不应暴露为外部 NodePort。[free5GC 安全公告](https://github.com/free5gc/free5gc/security/advisories/GHSA-3p28-73q7-45xp)

## 最小实验

首个实验聚焦手工服务链，不引入 Agent，也不修改现有固定 5GC Profile：

```text
VM3 UE(uesimtun0) → VM3 gNB → VM2 UPF
→ VM1 NodeIP:NodePort → VM1 Envoy
→ core 后端(VM1) / edge 后端(VM3)
```

NodePort 是当前阶段的显式入口：Kubernetes 会在节点端口把流量转发到 Service 的 Ready Endpoint，适合复用已经验证的 `UE → UPF → VM1` N6 路径。[Kubernetes NodePort](https://kubernetes.io/docs/concepts/services-networking/service/#type-nodeport) 后端不暴露 NodePort，避免 UE 绕过代理直连。

为避免把放置、QoS 和代理开销混为一个结果，实验按单变量顺序推进：

1. **M0 基线复查**：部署前运行固定 Profile 的严格 `verify`，记录 Pod UID、镜像、放置、重启数、UE 地址与路由。
2. **M1 代理路径**：一个 Listener、一个未限速后端；UE 绑定 `uesimtun0` 发起 iperf3，要求 UPF 路径、Envoy 访问日志和后端连接三处证据闭合。
3. **M2 分类与引导**：两个 Listener 分别映射 core/edge Cluster；每类 20 次短连接，目标后端命中率均须为 20/20，错误路由为 0。
4. **M3 QoS 隔离实验**：两个后端使用相同镜像、相同节点和相同资源，仅将 `tc` 上限设为 5/15 Mbps；各做 5 轮、每轮 15 秒 TCP 下行。要求全部完成、qdisc 计数增长、两组中位吞吐至少相差 2 倍，且任一组中位值不得高于配置上限 15% 以上。
5. **M4 放置实验**：撤销 QoS 差异后，再把一个后端放到 VM3；只验证放置和路径可控。当前同宿主机环境不以 RTT 差异证明 edge 优势。
6. **收尾复查**：再次执行 5GC 业务验收；任一 UPF/SMF/Envoy 重启、OOM、采集失效、路由绕过或日志链断裂都判该轮失败，不补写为成功数据。

NetworkPolicy 另做正反例：Envoy 到后端应允许；非 Envoy 测试 Pod 到后端应拒绝。Kubernetes 官方明确 NetworkPolicy 只有在执行引擎生效时才有意义，因此必须以数据包结果而不是对象 `Created` 作为验收。[Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)

UERANSIM 不模拟真实无线调度，因此首版的 QoS 结论限定为当前虚拟网络与服务链中的可测效果，不能宣称无线侧 QoS 保障。

## M0/M1 执行入口与验证记录

配置为 [`infra/cnfs/m1-proxy.yaml`](../infra/cnfs/m1-proxy.yaml)，镜像来源和 Digest 为
[`m1-images.json`](../infra/cnfs/m1-images.json)，入口为 [`scripts/cnf-m1.py`](../scripts/cnf-m1.py)。
仅面向 M0/M1 的固定实验，不是 R2.S4 通用编排器。

M1 验收配置为 `cnf-m1-v3`；首轮部署和失败诊断的 v1/v2 配置随原始结果保留。当前集群已扩展到下节 M2，M1 固定项如下：

| 项目 | 配置与边界 |
|---|---|
| 新增资源 | `cnf-lab` 内一个 ConfigMap、两个单副本 Deployment、两个 Service |
| 入口 | VM1 当前 InternalIP 的 TCP `31201` → Envoy `15201`；`externalTrafficPolicy: Local`，Envoy 固定 VM1 |
| 后端 | VM1 上单个 iperf3 3.9，ClusterIP `5201`；复用已验证的 UE 镜像并覆盖启动命令 |
| Envoy | `v1.39.4`，锁定 Linux amd64 子清单 Digest；静态 TCP proxy，仅监听一个业务端口 |
| 权限与资源 | 新增容器为非 root、只读根文件系统、移除全部 capabilities；各请求 100m CPU/64Mi，限制 500m/256Mi |
| 临时文件与 DNS | 后端单独挂载 16Mi 内存型 `/tmp`，`fsGroup=65532`；Envoy 使用末尾带点号的绝对 Service 域名 |
| 业务探测 | UE 绑定动态读取的 TUN IP，TCP 单流、5 秒、发送端设为 5 Mbps；只验证兼容性与路径，不作为 QoS 或最大吞吐结果 |
| 当前保障边界 | 无 `tc`、无 NetworkPolicy 效果声明；ClusterIP 不代表已阻断绕过代理，隔离正反例仍须后续实测 |

进程型 readiness 探针避免向 iperf 协议端口发送无效连接；Ready 只作为启动条件，不代替业务验收。
Envoy 的管理接口只绑定容器回环地址 `127.0.0.1:9901`，不通过 Service 暴露。

### VM1 镜像准备

VM1 的 Docker 中已有 UE 测试镜像，但 Docker 与 k3s containerd 的缓存独立；VM3 中的镜像也不能直接供 VM1 Pod 使用。
本次已将现存完整归档解压到 `/home/lhm/.work/cnf-m1-20261015/ueransim-iperf3.tar`。
需由可交互执行 sudo 的操作者在 VM1 运行：

```bash
sudo k3s ctr images import /home/lhm/.work/cnf-m1-20261015/ueransim-iperf3.tar
sudo k3s ctr images tag \
  docker.io/local/ueransim-iperf3:free5gc-v4.0.1-20261008 \
  docker.io/local/ueransim-iperf3@sha256:2c464fbf9eccafe187e2a2565e6ee3cdb4c3e256f76d223bf520103dc30e803b
```

2026-10-15 已完成上述导入与别名创建。镜像未导入时，`deploy` 会在创建 Namespace 前拒绝继续；脚本不自动提权或修改 containerd 配置，凭据不写入仓库。
Envoy 由 k3s 按固定 Digest 拉取；Docker 中的校验镜像不等于已进入 k3s 缓存。

### 固定执行顺序

在 VM1 的本次源码副本 `/home/lhm/.work/cnf-m1-20261015/repo` 中执行；之后复现可使用同版本仓库副本。
Python 3.10+、PyYAML 和原固定 Profile 的依赖仍须满足。每次 `--out` 使用新的目录，禁止覆盖失败轮次。

```bash
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/cnf-m1.py render
python3 scripts/cnf-m1.py m0 --out /home/lhm/.work/cnf-m1-20261015/m0-new
python3 scripts/cnf-m1.py deploy --out /home/lhm/.work/cnf-m1-20261015/deploy-new
python3 scripts/cnf-m1.py probe --out /home/lhm/.work/cnf-m1-20261015/probe-new
```

`deploy` 先严格复验 5GC、检查 VM1 镜像和 NodePort 冲突，要求 `cnf-lab` 尚不存在；创建独立 Namespace 后再执行服务端 dry-run、apply 与 rollout 检查。
失败时保留现场和结果，不自动删除 Namespace、不替换已有资源。检查失败原因后人工处理，不能不加检查地重复执行。

`probe` 保存命令、镜像/配置哈希、Pod UID/容器标识/重启数、UE 地址与路由、UPF 接口和 NAT 计数、iperf JSON、Envoy 日志和后端日志。
要求 iperf 完成、至少两条正确 Cluster/上游地址的 Envoy 控制/数据连接日志、后端观察到 Envoy Pod 地址、UPF `upfgtp` 双向字节数增长；最后再次严格复验 5GC。
Envoy 日志在连接关闭后仍可能缓冲约 8 秒；采集入口允许 15 秒等待（另加 API 调用耗时），保持两条连接证据要求，超时仍判失败。声明镜像从 Pod spec 读取，实际镜像核对 `imageID` Digest，不能用运行时显示名称替代。
路径证据是绑定选路与多处观察的关联，UPF 计数为接口聚合值，不等于逐流抓包。本轮 UPF 镜像没有 tcpdump，未向现有 UPF 容器临时安装工具。

采样检查 Pod/容器标识与重启数变化，并保留 UPF 512MiB 停止线；采集异常或探测失败即停止，不自动重启 NF。
采样间隔包含 Kubernetes API 与 exec 耗时，不能保证捕获瞬时峰值；5 秒、5 Mbps 也不证明长期稳定性。

只撤除本次五个命名资源、保留 Namespace 的清理命令为：

```bash
kubectl -n cnf-lab delete deployment/cnf-m1-envoy deployment/cnf-m1-backend \
  service/cnf-m1-envoy service/cnf-m1-backend configmap/cnf-m1-envoy
python3 scripts/free5gc-profile.py verify
```

### 2026-10-15 验证范围

- [已验证] Tailscale/SSH 恢复后，三节点 Ready，15 个既有工作负载 Running、0 次重启；固定 Profile 严格 `verify` 通过，UE 注册、PDU Session、TUN Ping 通过。
- [已验证] M0 入口在 VM1 执行通过并保存 Pod 身份、镜像、UE 地址与选路；证据见 [`results/20261015-cnf-service-chain-m0/`](../results/20261015-cnf-service-chain-m0/)。Ping 仅用于连通验收，不与历史 RTT 直接作性能比较。
- [已验证] 固定 Envoy 镜像在 VM1 Docker 中以非 root、只读文件系统和无 capabilities 执行 `--mode validate`，配置校验通过；此项没有部署 Kubernetes 工作负载。
- [已验证] 10 项证据判定单测在 Windows 和 VM1 通过，覆盖路径证据缺失、Pod 替换、声明/实际镜像不符及只有一条代理日志等情况。
- [已验证] 已导入 VM1 后端镜像，执行服务端 dry-run、真实部署与受控配置修正；`probe-06` 的单流 TCP 上行和收尾严格验收通过。完整记录见 [`results/20261015-cnf-service-chain-m1/`](../results/20261015-cnf-service-chain-m1/)。
- [后续进展] M2 分类引导、M3 `tc`、M4 放置及 NetworkPolicy 正反例均已完成，证据见后续章节。M1 单次短时探测本身不等于 R2.S2 全阶段验收。

| `probe-06` 观察 | 结果与口径 |
|---|---|
| 业务入口与后端 | `192.168.244.128:31201` → Envoy `10.42.0.178` → Service `10.43.108.155:5201` → 后端 `10.42.0.177`；地址为本次观测值 |
| 业务传输 | 发送/接收均为 3,252,220 字节；Envoy 两条成功日志，后端日志确认代理来源 |
| UPF 路径 | `upfgtp` RX/TX 增加 3,380,248/47,189 字节；NAT 新流计数从 19 到 21；代理观察到 UPF NAT 后的 `192.168.244.129` |
| 运行状态 | 原 15 个 5GC Pod UID 从 M0 至收尾均未改变，重启数为 0；新增两 Pod 在本轮身份不变、0 重启；UPF 12 次采样峰值 10.105MiB |
| 日志等待 | 8.713 秒后证据齐全，未以第一条日志代替控制/数据两条证据 |
| 吞吐限制 | 发送统计窗口 5.000095 秒、约 5.203Mbps；接收窗口 15.040593 秒、约 1.730Mbps。窗口不一致沿用原始结果，不解释为丢包、不作为 `tc` 或容量结论 |

`probe-01` 至 `probe-05` 的失败 summary 和原始材料均保留，`probe-04/05` 虽有业务字节也没有回填为成功。
配置变化只滚动更新新增 CNF；镜像显示名误判、DNS 搜索域、只读临时目录和日志缓冲问题的完整处理记录见 [PS-007](../problem%20solve.md#ps-007cnf-首轮代理实验的镜像核验dns临时目录与日志采集问题)。

## M2 分类与引导实验

M2 在已验收的 M1 上扩展，输入为 [`m2-experiment.json`](../infra/cnfs/m2-experiment.json)，入口为
[`scripts/cnf-m2.py`](../scripts/cnf-m2.py)。固定渲染器继承 M1 的镜像、资源和权限配置，生成 M2 ConfigMap、
一个新增后端 Deployment/Service，以及现有 Envoy Deployment/Service 的覆盖；不重施加原后端或 5GC 清单。
M1 已固化为本地提交 `9629c67`，M2 在 `codex/cnf-m2-steering` 分支继续，旧配置和结果不覆盖。

### 固定变量与验收口径

| 逻辑业务 | UE 目标 NodePort | Envoy Listener / Cluster | 后端 |
|---|---:|---|---|
| core | 31201 | `m2_core` / `m2_core`，容器端口 15201 | 既有 `cnf-m1-backend:5201` |
| edge | 31202 | `m2_edge` / `m2_edge`，容器端口 15202 | 新增 `cnf-m2-edge:5201` |

两个后端都先放在 VM1 的 control-role 节点，使用相同 Digest、100m/64Mi 请求、500m/256Mi 限制和 16Mi 临时卷；
`core/edge` 在 M2 仅为逻辑业务标签。Envoy 复用 M1 的单实例，原 31201 入口与原后端保留。
没有增加 `tc`、NetworkPolicy 或业务识别模型；本轮分类依据是显式目的端口。

当前 `cnf-m2-v4` 正式测量交错执行 core/edge 各 20 次独立 iperf3 会话，每次单流、计时 1 秒、块长 16,384 字节，
客户端发送速率参数为 1 Mbps，绑定动态 UE TUN IP。逐轮记录实际字节，要求非空且收发一致，不将速率参数解释为 `tc` 效果。
每次包含控制和数据两条 TCP 连接；“20 次”计业务会话而非 TCP socket 数。
先各做一次 smoke 验证，另存目录且不计入正式 40 次。

每次会话要求：

1. iperf 收发字节完全一致且至少 16,384 字节，使用客户端生成的唯一 Cookie 标识该次会话；两端均为 0 也不能算成功。
2. 代理 JSON 日志的连接开始时间位于本次发起/结束时间窗内；恰有两条成功记录，业务类、Listener、本地端口、Cluster 和上游 Service 地址全部符合映射。
3. 预期后端日志包含该 Cookie 和 Envoy Pod 来源，另一后端日志不含该 Cookie；不能只凭 iperf 成功或代理配置推定命中。
4. UPF `upfgtp` 双向字节增长，Pod/容器标识与重启数稳定；保留 512MiB UPF 停止线及会话超时保护。

日志仍使用 M1 的有界等待，最多 15 秒另加 API 耗时。任一会话失败即停止，保留逐轮原始输出和失败状态；
每轮输出目录包含命令、iperf JSON、两后端日志、代理日志、UPF 观察、资源采样与判定。
测量前后运行固定 5GC Profile 的严格业务验收。总体目标为两类各 20/20、错误路由 0，共 80 条代理 TCP 连接记录。

### VM1 执行与恢复

本次源码副本位于 `/home/lhm/.work/cnf-m2-20261015/repo`。每次使用新的结果目录：

```bash
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/cnf-m2.py render --out /home/lhm/.work/cnf-m2-20261015/render-new
python3 scripts/cnf-m2.py deploy --out /home/lhm/.work/cnf-m2-20261015/deploy-new
python3 scripts/cnf-m2.py measure --smoke --out /home/lhm/.work/cnf-m2-20261015/smoke-new
python3 scripts/cnf-m2.py measure --out /home/lhm/.work/cnf-m2-20261015/measure-new
```

部署先确认 M1 基线、检查新增名称与 NodePort 冲突，执行服务端 dry-run 和受控 apply，只更新新增 CNF。
已有 M2 资源时 `deploy` 拒绝再次覆盖；正常复测直接使用 `measure`，不重复部署。
当前运行 M2 后，M1 的旧 `probe` 不适用于新的日志和 Listener 配置；5GC 的 `free5gc-profile.py verify` 仍可独立执行。

需要恢复 M1 时，先确认没有运行中的测量，再执行下列命令并重新进行 M1 业务验收；本次成功路径不执行恢复：

```bash
kubectl apply -f infra/cnfs/m1-proxy.yaml
kubectl -n cnf-lab rollout status deployment/cnf-m1-envoy --timeout=120s
kubectl -n cnf-lab delete deployment/cnf-m2-edge service/cnf-m2-edge configmap/cnf-m2-envoy
python3 scripts/cnf-m1.py probe --out /home/lhm/.work/cnf-m2-20261015/restored-m1-new
```

### 实施与证据

- [已验证] M1 在变更前再次完成端到端探测与收尾严格验收。
- [已验证] `cnf-m2-v4` 配置通过实际 Envoy 镜像校验、服务端 dry-run 与受控更新；两类各一次固定时长 smoke 验证通过，不计正式验收。
- [已验证] 8 项路由证据单测在 Windows 和 VM1 通过，覆盖错误后端 Cookie、错误 Cluster、旧会话日志、缺少连接、不完整字节和全零统计等情况。
- [已记录，未接受] 初版日志刷新字段不被 Envoy 接受，预校验失败后未部署；v2 固定 16KiB 测量在两类各完成 6 次后，第 7 次 core 的接收统计为 0，整批停止。v3 的 256KiB 多块探测又少统计最后 4KiB，代理数据连接为 `UR`，仍判失败。没有合并这些成功片段凑足 20 次。具体观察与根因边界见 [PS-008](../problem%20solve.md#ps-008m2-固定字节数短会话的接收统计不完整)。
- [已验证] v4 固定 1 秒的正式 `measure-02` 完成两类各 20/20，错误路由 0，收尾严格业务验收通过。数据见 [`results/20261015-cnf-service-chain-m2/`](../results/20261015-cnf-service-chain-m2/)，`measure-01` 与 `smoke-02` 的失败状态未改写，也未拼入本轮。

| 正式 `measure-02` 指标 | 结果与边界 |
|---|---|
| 分类引导 | core 20/20，edge 20/20，错误路由 0；40 个独立 Cookie、80 条成功代理 TCP 连接 |
| 业务字节 | 每会话收发各 131,072 字节，共 5,242,880 字节；计时 1 秒是客户端参数，不等于整轮采集耗时 |
| 后端观察 | core Service `10.43.108.155` → Pod `10.42.0.177`；edge Service `10.43.151.195` → Pod `10.42.0.179`；Envoy Pod `10.42.0.182`，均为本次动态观测值 |
| 运行状态 | 从部署前到收尾，原 15 个 5GC Pod/容器身份未变；正式测量中的三个 CNF Pod/容器身份未变，最终均为 0 重启 |
| 资源采样 | 40 个 UPF 内存采样点，采样峰值 10.219MiB；采样不代表瞬时最大值 |
| 校验 | M1/M2 共 18 项单测在 Windows 和 VM1 通过；离线复核 75 项输入哈希匹配，40 个 Cookie 唯一，另一后端均未出现该 Cookie |

本轮只验收显式 TCP 目的端口分类和静态后端引导。两后端仍同放 VM1，不能称为实际 edge 时延收益；
M2 本身没有 `tc`、NetworkPolicy、最大容量或长时间稳定性结论。后续 M3 在同镜像、同节点、同资源条件下验证了 5/15 Mbps 下行整形，见下节。

## M3 同节点下行整形实验

M3 配置为 [`m3-experiment.json`](../infra/cnfs/m3-experiment.json)，部署与采集入口为
[`cnf-m3.py`](../scripts/cnf-m3.py)，离线复核为 [`audit-cnf-m3.py`](../scripts/audit-cnf-m3.py)。
M2 已固化为本地提交 `748d025`，M3 从该提交在 `codex/cnf-m3-shaping` 分支继续。
仍适用 R2.S2，不新增路线版本。

### 配置与测量边界

| 固定项 | `cnf-m3-v1` 配置 |
|---|---|
| 路由 | 沿用 `cnf-m2-v4` 的 Listener、Cluster、Service 和 31201/31202 端口；Envoy 不重建，日志中的 `experiment` 仍为 M2 路由版本 |
| 后端 | core、edge 都在 VM1；`iperf3` 主容器和 `tc` 辅助容器均复用 M1 的固定 Digest 镜像，实际 `tc` 为 iproute2 5.9.0 |
| 主容器资源 | 两侧相同：request 100m/64Mi，limit 500m/256Mi |
| 辅助容器资源 | 两侧相同：request 10m/32Mi，limit 100m/64Mi；每个 Pod 合计 request 110m/96Mi，limit 600m/320Mi |
| 整形位置 | 仅后端 Pod 共享网络命名空间的 `eth0` 出口；root TBF `1:`，core 5 Mbps、edge 15 Mbps，burst 32kb、latency 100ms |
| 权限 | 主容器保持 UID 65532、drop ALL；辅助容器 UID 0、drop ALL 后仅加 `NET_ADMIN`，只读根文件系统、禁止提权、RuntimeDefault seccomp；不使用 privileged、hostNetwork 或宿主机 qdisc |
| 业务 | UE 绑定动态读取的 `uesimtun0` 地址，通过 UPF、Envoy 到后端，`iperf3 -R -P 1 -t 15 -b 20M -l 16384`；交错执行各 5 轮 |
| 统计 | 使用 UE 接收端 `sum_received` 的原始字节数/窗口计算吞吐；保留 sender/receiver 的原始字节、时长和速率，不要求两端停止时缓冲区中的字节完全一致 |
| 护栏 | 逐会话监测所有原有 5GC 与 CNF Pod/容器身份、Ready 和重启数，采样 UPF 内存；512MiB、身份变化、重启、超时或采集失效即停，失败目录不重用 |

20 Mbps 是两组一致、且高于两个 TBF 上限的发送端负载上界；它不作为整形结果。
每轮要求 qdisc 字节增长覆盖本轮接收载荷、packet 和 overlimit 增长，并保存 drops、requeues、backlog。
代理证据检查下行的 `bytes_sent`；每轮仍需两条正确的代理连接、目标后端 Cookie、另一后端不误收和 UPF 双向计数。

本实验验证顺序单流条件下的两档下行控制效果，不代表并发业务资源隔离、最大容量、无线调度 QoS、PCF 策略生效或物理 edge 优势。
TBF 队列可能丢包，TCP 也可能存在重传和停止时的在途缓冲；不能将成功完成解释为零丢包。

### 执行与恢复

在 VM1 独立源码目录执行，所有结果目录必须是新目录：

```bash
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/cnf-m3.py preflight --out <新目录>/preflight
python3 scripts/cnf-m3.py deploy --out <新目录>/deploy
python3 scripts/cnf-m3.py measure --smoke --out <新目录>/smoke
python3 scripts/cnf-m3.py measure --out <新目录>/measure
python3 -m unittest discover -s tests -p 'test_cnf_m*.py' -v
```

`preflight` 只接受未增加辅助容器的 M2 基线，各做一次 5 秒、5 Mbps 下行兼容性探测。
`deploy` 校验该基线，只修改两个后端 Deployment；Recreate 策略避免部署期同时存在新旧后端。
它在无测试流量时对两个 Pod 各执行一次 TBF 删除、读取 `noqueue`、按原参数重建并复查，保留恢复证据。
`measure --smoke` 每侧只测一轮，始终不算完整 M3 验收；`measure` 才按固定参数执行各 5 轮。
正式批次不拼接探测或失败批次的数据。

运行 M3 配置时，M1 的 `probe`、M2 的 `measure` 不适合作为 M3 验收入口；严格 5GC `verify` 仍可独立执行。
重复测量直接运行 M3 `measure`；不要重复执行要求原 M2 状态的 `deploy`。

M4 前须先撤销两侧整形差异。可生成原 M2 的两个后端清单，再审阅并应用；此路径会重建两个后端，
M3 当轮实测的恢复范围仅为 Pod 内 qdisc 删除/重建，**以下手工回退命令未直接执行**；后续 M4 使用固定 `normalize` 入口完成了两侧后端去整形及复验：

```bash
python3 - <<'PY'
import importlib.util
from pathlib import Path
import yaml
module = importlib.util.spec_from_file_location('m2', 'scripts/cnf-m2.py')
m2 = importlib.util.module_from_spec(module)
module.loader.exec_module(m2)
core = next(d for d in m2.m1.config()[0]
            if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m1-backend')
edge = next(d for d in m2.render()[2]
            if d['kind'] == 'Deployment' and d['metadata']['name'] == 'cnf-m2-edge')
with Path('cnf-restore-m2.yaml').open('x', encoding='utf-8') as f:
    yaml.safe_dump_all([core, edge], f, sort_keys=False)
PY
kubectl apply --dry-run=server -f cnf-restore-m2.yaml
kubectl apply -f cnf-restore-m2.yaml
kubectl -n cnf-lab rollout status deployment/cnf-m1-backend --timeout=120s
kubectl -n cnf-lab rollout status deployment/cnf-m2-edge --timeout=120s
python3 scripts/cnf-m2.py measure --smoke --out <新的恢复验证目录>
```

恢复后还应确认两侧无 TBF、5GC 严格验收通过，再实施 M4 的节点放置变化。

### 2026-10-15 验收结果

证据集中于 [`results/20261015-cnf-service-chain-m3/`](../results/20261015-cnf-service-chain-m3/)。
[`preflight-01`](../results/20261015-cnf-service-chain-m3/preflight-01/summary.json) 的两条未整形下行路径均通过，
[`deploy-01`](../results/20261015-cnf-service-chain-m3/deploy-01/summary.json) 保存 TBF 撤销/重建记录，
[`smoke-01`](../results/20261015-cnf-service-chain-m3/smoke-01/summary.json) 只作探测；
正式数据仅取 [`measure-01`](../results/20261015-cnf-service-chain-m3/measure-01/summary.json)。

| 轮次 | core：5 Mbps 上限，接收 Mbps | edge：15 Mbps 上限，接收 Mbps |
|---|---:|---:|
| 1 | 4.827 | 14.376 |
| 2 | 4.822 | 14.376 |
| 3 | 4.823 | 14.374 |
| 4 | 4.822 | 14.376 |
| 5 | 4.823 | 14.377 |
| 中位数 | **4.822877** | **14.376206** |

中位吞吐比 **2.980836**，满足至少 2 倍；两组中位数均低于各自配置上限，也低于验收允许的 5.75/17.25 Mbps。
10/10 会话完成，10 个 Cookie 唯一、20 条正确代理连接、错误路由为 0；每轮 TBF 字节/包/overlimit 计数增长、UPF 双向计数增长。
正式批次接收载荷共 180,092,928 字节。原有 15 个 5GC Pod 从部署前至收尾身份不变，Envoy 未重建；
两个后端只在部署辅助容器时重建，测量期间全部 Pod/容器身份不变，最终重启数为 0。
119 次 UPF 内存采样的峰值为 16.1328125 MiB；这是采样峰值，不是连续监控的绝对峰值。
正式批次前后均通过严格 5GC 验收。

Windows 与 VM1 的 M1—M3 共 **31 项单测通过**。独立离线审计从原始接收字节/时长重算吞吐和中位数，
核对 qdisc、Cookie、日志方向、资源对等、实例身份及 **36 项输入哈希**，结果见
[`audit.json`](../results/20261015-cnf-service-chain-m3/audit.json)。审计器对零接收载荷、错误限速、Pod 替换、
被改写的中位数比值和缺失内存采样这 5 类损坏副本均拒绝通过，原始证据不变，见
[`audit-negative-checks.json`](../results/20261015-cnf-service-chain-m3/audit-negative-checks.json)。
[`runtime-inspection.txt`](../results/20261015-cnf-service-chain-m3/runtime-inspection.txt) 还记录两侧辅助容器的
实际 `CapEff=0x1000`（仅 `NET_ADMIN`）、主容器能力为 0，以及 Pod `eth0` 的 1450 MTU/TBF 状态。
复核此固定数据集可运行：

```bash
python scripts/audit-cnf-m3.py results/20261015-cnf-service-chain-m3 --out <新的审计JSON路径>
```

M3 验收结束时保留两侧 5/15 Mbps TBF 配置；随后 M4 已通过固定入口重建无整形后端并完成跨节点放置，见下节。
这些后续操作不计入 M3 当轮证据。NetworkPolicy 的后续验收见独立章节。

## M4 后端放置与跨节点路径实验

M4 从 M3 提交 `ca0d064` 继续，分支为 `codex/cnf-m4-placement`。配置为
[`m4-experiment.json`](../infra/cnfs/m4-experiment.json)，部署与测量入口为
[`cnf-m4.py`](../scripts/cnf-m4.py)，离线审计为 [`audit-cnf-m4.py`](../scripts/audit-cnf-m4.py)。
适用路线/阶段仍为 R2.S2；本实验只验证放置和路径可控。

### 单变量设置与证据

先撤销两个后端的 TBF，并移除整形辅助容器，恢复同节点、同镜像、同资源的无整形基线。
通过两类业务的上下行探测后，再只对 edge Deployment 的 Pod `nodeSelector` 做以下修改：

| 项目 | 去整形后的对照 | 放置实验 |
|---|---|---|
| core | VM1，`5gc.free5gc.org/plane=control` | 不变 |
| edge | VM1，`5gc.free5gc.org/plane=control` | VM3，`5gc.free5gc.org/role=ran-test` |
| 后端镜像与容器 | 单个 `iperf3`，复用 M1 固定 Digest，`imagePullPolicy: Never` | 不变；VM3 复用已有 UE 镜像缓存 |
| 资源/权限 | 每侧 request 100m/64Mi，limit 500m/256Mi；UID 65532、drop ALL、只读根文件系统与可写 `/tmp` | 不变；不再保留 `NET_ADMIN` 辅助容器 |
| qdisc | 两侧 `eth0` 为 `noqueue`，无 TBF/HTB | 每轮前后继续核对 |
| 代理/Service | VM1 Envoy，M2 的 31201/31202 → core/edge，`externalTrafficPolicy: Local` | 不变；只更新 edge 后端 Pod 和端点 |
| 测量 | 单流 TCP、5 秒、1 Mbps、16KiB 块长；UE 源地址绑定 `uesimtun0` | 相同参数；每类上下行各 5 次，计 20 次正式会话 |

业务入口固定为 VM1 NodePort，因此 UE 与 edge 同在 VM3 也不将业务改为直连后端。
证据包括源地址绑定及 UE 路由、UPF `upfgtp` 双向计数、Envoy 的两条控制/数据连接、后端 Cookie 和 Envoy 来源地址，
以及 Endpoints/EndpointSlice 中的实际 Pod IP、UID、节点、Ready 与端口。
保存节点 PodCIDR、后端到 Envoy 的路由和镜像 Digest，以关联跨节点放置；节点记录不保存任意注解或凭据。

部署检查会拒绝不唯一、不可调度、非 Ready、架构不符或缺少固定本地镜像的 VM3；
测量时继续监测原有 5GC 和 CNF Pod/容器身份、重启及 UPF 内存，停止阈值仍为 512MiB。
上下行分别检查代理 `bytes_received` / `bytes_sent`。上行沿用 M2 的完整载荷核对，下行保留 M3 的接收端统计和原始发送端窗口。
任何路由错误、身份变化、采集失效或超时都停止，不在同一结果目录补测。

### 执行与恢复边界

在 VM1 独立源码目录执行；每个输出目录必须是新目录：

```bash
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/cnf-m4.py normalize --out <新的实验目录>/normalize-01
python3 scripts/cnf-m4.py measure-neutral --out <新的实验目录>/neutral-01
python3 scripts/cnf-m4.py place --out <新的实验目录>/place-01
python3 scripts/cnf-m4.py measure --smoke --out <新的实验目录>/smoke-01
python3 scripts/cnf-m4.py measure --out <新的实验目录>/measure-01
```

`normalize` 只接受已验证的 M3 起点，重建两侧后端以去除整形辅助容器；它不修改 5GC、Envoy 或 Service。
`measure-neutral` 验证两侧仍在 VM1，各做一次上下行探测；`place` 只接受该无整形放置配置，
实际只应用 edge Deployment。采用 Recreate，切换期间会中断 edge 服务，不是在线无损迁移。
`measure --smoke` 在 VM3 放置后各做一次上下行；正式 `measure` 才执行各 5 次。
所有部署和测量前后均执行严格 5GC 验收。重复测量直接运行 M4 `measure`，不能重跑要求 M3 起点的 `normalize`。

若需回到 VM1 对照，可从 `normalize-01/manifest.yaml` 提取 edge Deployment，服务端 dry-run 后再应用并等待 rollout，
随后运行 `measure-neutral`；这是保留的恢复方法，**本次未执行 edge 从 VM3 回到 VM1 的反向迁移**。
旧的 M1—M3 业务入口依赖各自放置/容器配置，不适用于当前 M4 实验验收。

三台 VM 同属一个宿主机，本实验不设置 RTT/吞吐优势阈值，不宣称真实 MEC、5G 无线 QoS、并发隔离或最大容量。
UPF 计数属于聚合关联证据，未开展逐流抓包；NetworkPolicy 正反例须单独验证。

### 2026-10-15 验收结果

原始证据见 [`results/20261015-cnf-service-chain-m4/`](../results/20261015-cnf-service-chain-m4/)。
[`normalize-01`](../results/20261015-cnf-service-chain-m4/normalize-01/summary.json) 完成去整形，
[`neutral-01`](../results/20261015-cnf-service-chain-m4/neutral-01/summary.json) 的同节点上下行 4 次探测通过；
[`place-01`](../results/20261015-cnf-service-chain-m4/place-01/summary.json) 只应用 edge Deployment，
[`smoke-01`](../results/20261015-cnf-service-chain-m4/smoke-01/summary.json) 的迁移后 4 次探测通过。
正式数据仅取 [`measure-01`](../results/20261015-cnf-service-chain-m4/measure-01/summary.json)，不混入上述探测数据。

| 业务 | 实际后端节点 / 本轮 Pod IP | 上行 | 下行 | qdisc |
|---|---|---:|---:|---|
| core | VM1 `lhm-virtual-machine` / `10.42.0.186` | 5/5 | 5/5 | `noqueue` |
| edge | VM3 `ran-test` / `10.42.2.73` | 5/5 | 5/5 | `noqueue` |

20/20 正式会话完成，20 个 Cookie 唯一、40 条正确代理连接，错误路由为 0；接收载荷共 12,779,520 字节。
原有 15 个 5GC Pod 从去整形前到收尾身份不变；Envoy 未重建/重启，去整形后 core 在 edge 迁移期间身份不变。
两个后端只在预定部署步骤重建；正式批次所有 Pod/容器身份稳定，最终重启数为 0。
80 次 UPF 内存采样的峰值为 11.0078125 MiB，这是离散采样峰值；批次前后严格 5GC 验收均通过。

Windows 与 VM1 的 M1—M4 共 **41 项单测通过**，包含旧 Pod UID、错误节点、过期/非 Ready/重复 EndpointSlice、
残留 TBF 与单变量清单检查。独立离线审计核对原始路径、实际端点与 PodCIDR、后端返回路由、资源/权限不变、身份、qdisc 及
**55 项输入哈希**，见 [`audit.json`](../results/20261015-cnf-service-chain-m4/audit.json)。复核命令：

```bash
python scripts/audit-cnf-m4.py results/20261015-cnf-service-chain-m4 --out <新的审计JSON路径>
```

审计器对旧 Pod 端点、残留 TBF 和护栏中 Pod 被替换的三个损坏副本均拒绝通过，原始证据未改写，见
[`audit-negative-checks.json`](../results/20261015-cnf-service-chain-m4/audit-negative-checks.json)。

M4 验收后保留 core 在 VM1、edge 在 VM3 的无整形配置，后续 NetworkPolicy 在此基础上完成，见下节。

## NetworkPolicy 隔离正反例与阶段收尾

配置为 [`networkpolicy.yaml`](../infra/cnfs/networkpolicy.yaml) 与
[`networkpolicy-experiment.json`](../infra/cnfs/networkpolicy-experiment.json)，入口为
[`cnf-networkpolicy.py`](../scripts/cnf-networkpolicy.py)，独立离线审计为
[`audit-cnf-networkpolicy.py`](../scripts/audit-cnf-networkpolicy.py)。
配置版本 `cnf-netpol-v1` 从 M4 提交 `790fd4f` 继续，分支 `codex/cnf-networkpolicy`；路线/阶段仍为 R2.S2。

### 策略边界

| 对象 | 规则与边界 |
|---|---|
| `cnf-backends-from-envoy` | 选中 core/edge 两后端，仅允许同 `cnf-lab` 内 `app=cnf-m1-envoy` 的 TCP 5201 入站；不限制后端 Egress |
| `cnf-envoy-chain-and-dns` | 选中 Envoy；入站保留公开业务端口 TCP 15201/15202；出站仅同 Namespace 的两个后端 TCP 5201，以及 `kube-system` 内 `k8s-app=kube-dns` 的 UDP/TCP 53 |
| DNS peer | Namespace 与 Pod selector 放在同一个 peer，按 AND 匹配；不是两个可分别命中的宽松 peer |
| 临时测试 Pod | VM1/VM3 各一台，标签不匹配 Envoy/后端；固定 M1 Digest 镜像、非 root、drop ALL、无服务账号令牌；不被上述策略选为隔离源，以便单独验证后端 Ingress |
| 保持项 | M4 的节点、镜像、资源、无整形状态、Envoy ConfigMap 与 Service 均不变；不修改 free5gc Namespace 或既有 MongoDB 策略 |

VM1、VM3 的宿主机 `iptables-save -c -t filter` 已观察到 kube-router netpol 规则链；
同时保留策略生效后的规则记录。没有更换 CNI、重启 k3s 或使用 privileged/hostNetwork 测试 Pod。
判定以实际连接结果为主，不以规则链存在或 Policy 对象创建代替验收。

### 对照与测量方法

所有 TCP 探测都创建新连接，避免复用已有连接的状态。探测固定源 Pod、目标 IP 与端口，顺序为：

1. 无策略：两测试 Pod 到两后端的 Service/Pod IP，以及 Envoy 到后端/测试 Pod 均可达。
2. 加策略并等待 5 秒收敛：期望后端拒绝非 Envoy 来源，Envoy 出站拒绝测试 Pod；允许后端与 DNS。
3. 仅撤销本实验两条策略，等待 5 秒：同一源/目标恢复可达，排除服务未监听、错误 IP 或测试工具失效。
4. 重加同一策略，等待 5 秒后执行 3 轮正式隔离矩阵；随后运行两类上下行各 5 次、每次 5 秒/1 Mbps 的 UE 回归。

每轮正式矩阵如下；Service IP 和 Pod IP 都覆盖同节点与跨节点：

| 路径 | 每轮 | 3 轮合计 | 期望 |
|---|---:|---:|---|
| 两测试 Pod → 两后端，各 Service/Pod IP | 8 | 24 | 拒绝 |
| Envoy → 两后端，各 Service/Pod IP | 4 | 12 | 允许 |
| Envoy → 两测试 Pod IP | 2 | 6 | 拒绝 |
| Envoy → CoreDNS Service TCP 53 | 1 | 3 | 允许 |

另在 Envoy 容器中逐轮执行两个后端 FQDN 的 `getent ahostsv4`，共 6 次解析，要求返回正确 Service IP；
TCP 53 测的是连接建立，不宣称完成 DNS over TCP 查询。普通 TCP 探测会连上后立即关闭，
可能使 iperf3 服务器记录协议未完成消息；这些连接只作为连通性证据，业务成功另用完整 iperf3 会话核对。
允许要求退出码 0；拒绝只接受超时或明确网络拒绝，命令缺失和 kubectl 错误均不能算拒绝。

在策略切换、连接矩阵和 UE 测量中监测原有工作负载与临时 Pod 的 UID、容器身份、Ready 和重启；
既有 Deployment/Service/ConfigMap 的运行配置必须不变。UE 测量继续使用 512MiB UPF 内存停止阈值，
关联 UPF 双向计数、代理方向字节、目标 Cookie 和另一后端不误收，失败立即停止并保留原始目录。

### 执行、恢复与复测

在 VM1 独立源码目录执行。开始时必须是没有本实验策略、没有同名测试 Pod 的 M4 配置：

```bash
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/cnf-networkpolicy.py render --out <新的渲染目录>
python3 scripts/cnf-networkpolicy.py run --engine-dir <引擎证据目录> --out <新的实验目录>/run-01
python3 -m unittest discover -s tests -p 'test_cnf*.py' -v
```

`--engine-dir` 应含 VM1/VM3 分别采集的 `engine-before-vm1.txt`、`engine-before-vm3.txt`：
在对应主机执行 `sudo iptables-save -c -t filter > <新文件路径>`，再将两文件放到 VM1 同一证据目录。
采集只读规则，不把密码、服务环境变量或 k3s token 写入文件。

成功后保留两条策略，删除两个临时测试 Pod，并再次严格验收 5GC。
若应用策略后的测试失败，脚本尝试保留预期的两条策略并记录该动作；失败现场和测试 Pod 留待检查，不能将失败目录重用为成功数据。
正常运行状态下不要直接重跑该完整对照：复测前须明确执行
`kubectl delete -f infra/cnfs/networkpolicy.yaml`，恢复无策略 M4 起点后再选择新输出目录运行。
这只删除本实验两条策略；撤销/重加及连接恢复已实测，不会重建后端或 Envoy。
M4 的旧 `measure` 要求无策略，不能直接用于当前保护状态；严格 5GC `verify` 仍可独立执行。

### 2026-10-15 验收结果

原始证据见 [`results/20261015-cnf-service-chain-networkpolicy/`](../results/20261015-cnf-service-chain-networkpolicy/)，
正式批次为 [`run-01`](../results/20261015-cnf-service-chain-networkpolicy/run-01/summary.json)。
无策略对照 15/15 可达；初次隔离 5 次允许、10 次拒绝均正确；撤销后同一 15 个连接目标恢复可达；
重加后的正式矩阵为 **15 次允许、30 次拒绝**，另 6 次 FQDN 解析成功。
策略生效后的 UE 上下行各 5/5，共 20 个唯一 Cookie、40 条正确代理连接，错误路由为 0；接收载荷 12,779,520 字节。

原有 18 个 Pod（15 个 5GC、3 个 CNF）从部署前到收尾身份不变，最终重启数为 0；
80 次 UPF 内存采样峰值 13.0390625 MiB，属于离散采样峰值。
临时 Pod 已删除；部署前、创建测试 Pod 后、UE 业务结束后和清理后均完成严格 5GC 验收。
Windows 与 VM1 共 **50 项单测通过**。独立审计复核原始连接的 ABA 对照、业务路径、身份和 **14 项输入哈希**，见
[`audit.json`](../results/20261015-cnf-service-chain-networkpolicy/audit.json)；
4 类损坏证据副本均被拒绝，原始材料不变，见
[`audit-negative-checks.json`](../results/20261015-cnf-service-chain-networkpolicy/audit-negative-checks.json)。

```bash
python scripts/audit-cnf-networkpolicy.py results/20261015-cnf-service-chain-networkpolicy/run-01 --out <新的审计JSON路径>
```

**R2.S2 已完成验收。** 当前保留 M4 放置与两条策略；M3 整形结果作为独立实验保留，当前未重新开启 TBF。
结论限于当前 VM1/VM3 普通 Pod、固定标签及端口；没有测试宿主机/hostNetwork 来源、经 UPF 直连后端或恶意改标签，
不将标签策略视为身份认证或全来源绕行防护。后续 R2.S3 已固化统一入口并完成恢复与重复测量，见下节。

## R2.S3 固定 Profile 与可重复部署测量

R2.S3 从隔离验收提交 `444698e` 继续，分支为 `codex/cnf-reproducible-profile`。
统一入口 [`cnf-profile.py`](../scripts/cnf-profile.py) 读取
[`service-chain-profile.json`](../infra/cnfs/service-chain-profile.json)，配置版本为 `cnf-service-chain-v1`。
它锁定 14 个既有配置/脚本输入的 LF SHA256，复用已验收实现，不覆盖 M1—M4 或 NetworkPolicy 的历史入口。
新测量元数据明确标为 R2.S3；继承的业务参数仍来自已锁定的 S2 资产。

### 固定状态与操作边界

| 状态 | 后端放置 | qdisc | NetworkPolicy |
|---|---|---|---|
| `neutral` | core/edge 都在 VM1 | 两侧 `noqueue` | 无本实验策略 |
| `qos` | core/edge 都在 VM1 | 同配置 TBF，仅速率为 5/15 Mbps | 无本实验策略 |
| `placed` | core 在 VM1、edge 在 VM3 | 两侧 `noqueue` | 无本实验策略 |
| `protected`（默认） | core 在 VM1、edge 在 VM3 | 两侧 `noqueue` | 保留两条已验收策略 |

范围只包含既有 `cnf-lab` 的 3 个 Deployment、3 个 Service、2 个实验 ConfigMap，以及按状态存在的两条策略。
自动生成的 `kube-root-ca.crt` 由 Kubernetes 管理，既不接管也不删除。
入口要求已有固定 5GC、Namespace、角色标签和节点镜像缓存；不初始化空白 VM、CNI、5GC 或订阅数据。
不删除 Namespace、Service、PVC 或 5GC 资源。受控重建只删除三个已校验的 CNF Deployment，随后从固定清单部署。

开始修改前，必须将现场唯一识别为上述四种状态之一；所检查的工作负载、Service、ConfigMap、NetworkPolicy 种类中，
未知对象、额外策略、节点/镜像/资源/权限漂移会被拒绝。系统 ServiceAccount、Secret、PVC 等其他种类不接管、不删除。
每次操作保留新的独立输出目录，并运行严格 5GC 验收。实际 Pod 节点、容器 Digest、Endpoints/EndpointSlice、qdisc
都要与状态一致，不能仅以 Deployment 配置或 Pod Ready 判断成功。
apply、重建及恢复先做服务端 dry-run；目标为保护状态时先应用隔离策略。
同状态重复 apply 要求所有原有 Pod UID、容器身份和重启数不变。

### 统一命令

在 VM1 独立源码目录设置 `KUBECONFIG` 后执行，`--out` 必须指向新目录：

| 命令 | 行为 |
|---|---|
| `render --target <状态>` | 离线渲染，校验固定输入哈希 |
| `check` | 严格 5GC 验收、识别并检查当前状态，保存状态快照 |
| `snapshot` | 与 `check` 相同的只读检查，显式生成可用于恢复的快照 |
| `apply --target <状态>` | 备份已识别起点，dry-run、应用、等待 rollout、校验；失败尝试回到起点 |
| `restore --snapshot <快照JSON>` | 只接受匹配当前 Profile 的已知状态快照，再从固定清单生成目标；不直接执行任意保存的 YAML |
| `rebuild --target <状态>` | 备份后只重建三个 CNF Deployment，保留 Service/VIP/NodePort |
| `recovery-check --target <不同状态>` | 实际切换后触发专用的预设验证失败，验证自动回滚；此命令是恢复演练，不是业务失败样本 |
| `measure` | 自动识别当前状态，按对应已锁定参数采集业务/整形/隔离证据，结束后严格复验 |

示例：

```bash
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/cnf-profile.py check --out <新目录>/check
python3 scripts/cnf-profile.py apply --target protected --out <新目录>/apply
python3 scripts/cnf-profile.py apply --target qos --out <新目录>/qos-apply
python3 scripts/cnf-profile.py measure --out <新目录>/qos-measure
python3 scripts/cnf-profile.py restore --snapshot <新目录>/qos-apply/snapshot.json --out <新目录>/restore
python3 scripts/cnf-profile.py measure --out <新目录>/protected-measure
```

快照记录 Profile 版本、固定状态、清单规范化哈希和安全的 Pod 身份；恢复时重新校验这些约束。
自动回滚只承诺本入口捕获的失败：在可识别起点保存快照后应用新状态，失败即尝试按固定起点恢复并保存 `recovery.json`。
若回滚本身失败，操作仍报告失败；不能将恢复尝试当作业务通过。
进程被强制终止、外部并发修改或无法识别的残缺现场不在本轮自动恢复承诺内，入口会拒绝直接覆盖。
部署/状态切换可能中断 CNF 服务；不声称在线无损迁移。

### 测量与独立审计

`qos` 继承 M3 的两侧各 5 轮、每轮 15 秒 TCP 下行和 5/15 Mbps 阈值；
`neutral/placed` 继承 M4 的两类上下行各 5 次、每次 5 秒/1 Mbps；
`protected` 保持策略不变，创建临时测试 Pod，执行 3 轮隔离矩阵及同样的 20 次 UE 业务，结束后清理测试 Pod。
保护状态测量不重复撤销策略，S2 的 ABA 对照保留为历史验收依据。
所有状态继续沿用 Pod/容器身份、重启与 512MiB UPF 内存护栏，失败批次不补测或拼接。

独立审计入口为 [`audit-cnf-profile.py`](../scripts/audit-cnf-profile.py)，从原始数据复核重复 apply、受控重建、
恢复轨迹、相同测量配置、动态 Pod/IP 证据、吞吐中位数与输入哈希，不依赖仅汇总的“通过”标记。

### 2026-10-16 实机验收结果

日期按 Asia/Shanghai 归档，原始日志保留 UTC 时间戳。证据为
[`results/20261016-cnf-service-chain-profile/`](../results/20261016-cnf-service-chain-profile/)，共 13 个独立操作目录。
所有操作均从当次已校验状态执行，不使用失败轮次拼接成功结果。

| 验收项 | 实际结果与证据 |
|---|---|
| 状态与输入固定 | 四状态唯一识别和实机切换通过；14 个继承输入固定，每次元数据另记录 Profile/入口哈希 |
| 幂等部署 | [`apply-idempotent-01`](../results/20261016-cnf-service-chain-profile/apply-idempotent-01/summary.json)、`02`、`03` 三次均不改变任何 Pod/容器身份或重启数 |
| 失败回滚 | [`recovery-01`](../results/20261016-cnf-service-chain-profile/recovery-01/recovery.json) 实际从 `protected` 切到 `neutral`，专用预设失败后回到 `protected`，随后完整业务测量通过 |
| 受控重建 | [`rebuild-01`](../results/20261016-cnf-service-chain-profile/rebuild-01/summary.json) 的 3 个 CNF Pod UID 均实际变化；Service UID、ClusterIP、NodePort 保留 |
| 整形与快照恢复 | `qos-apply-01` → [`qos-measure-01`](../results/20261016-cnf-service-chain-profile/qos-measure-01/summary.json) → [`restore-01`](../results/20261016-cnf-service-chain-profile/restore-01/summary.json)；恢复使用转换前快照重新生成固定保护状态 |
| 跨节点策略开关 | `placed-apply-01` → `placed-restore-01` 通过；仅撤销/恢复策略，Pod 身份不变 |
| 独立重复测量 | [`protected-measure-01`](../results/20261016-cnf-service-chain-profile/protected-measure-01/summary.json) 与 [`02`](../results/20261016-cnf-service-chain-profile/protected-measure-02/summary.json) 配置相同，中间经历 CNF 重建和状态切换；两批分别完整验收 |

两批保护状态各完成 20/20 会话，每批 core/edge 上下行各 5 次、40 条正确代理连接，
隔离矩阵各为 15 次允许、30 次拒绝，DNS 正确；测量期间不撤销策略，临时测试 Pod 均已清理。
整形状态另外完成两侧各 5×15 秒：中位吞吐 **4.826614/14.373986 Mbps**、比值 **2.978068**，
qdisc 与代理路径证据完整，满足原 5/15 Mbps 阈值。
三组测量合计 50 个唯一 Cookie、100 条正确代理连接、205,668,352 字节接收载荷。
这些合计仅描述证据数量，不将不同负载/配置拼成同一性能批次。

原有 15 个 5GC Pod 在 106 份身份检查记录中保持不变；CNF 只在明确的状态切换/重建步骤更新，
各测量批次内身份稳定。26 份服务检查记录中的三个 Service UID、VIP 和端口一致，最终容器重启数为 0。
280 次 UPF 内存采样峰值为 16.75390625 MiB，仅为离散采样峰值；各操作严格 5GC 验收均通过。
Windows 与 VM1 共 **58 项单测通过**，独立审计 **208 项输入哈希**匹配，结果见
[`audit.json`](../results/20261016-cnf-service-chain-profile/audit.json)。
快照哈希改写、Service 被替换、绕过成功、错误整形速率及 5GC Pod 替换这 5 类损坏副本均被审计器拒绝，原始证据不变，见
[`audit-negative-checks.json`](../results/20261016-cnf-service-chain-profile/audit-negative-checks.json)。

```bash
python scripts/audit-cnf-profile.py results/20261016-cnf-service-chain-profile --out <新的审计JSON路径>
```

**R2.S3 在既有集群范围内验收完成。** 当前回到默认 `protected`，core 在 VM1、edge 在 VM3、两侧无 TBF、两条策略生效。
这不是空白 VM/Namespace 重建、强制终止后的任意现场恢复或长期稳定性验收。下一阶段 R2.S4 确定性编排尚未开始，Agent 不提前进入编码。

### 2026-10-16 主目录 main 回归测试

在 `E:\GPT-project\postgraduate-analyse` 的 `main` 上运行测试，并从该 Git 树导出源码到 VM1 复验；证据归入
[`results/20261016-cnf-service-chain-regression/`](../results/20261016-cnf-service-chain-regression/)，来源提交和入口修复记录见其中 `metadata.json`。
Windows 和 VM1 均为 80 项测试中 79 项通过、1 项 Helm 集成因环境开关跳过；随后在 VM1 单独补跑该集成项。
专项验证发现入口执行位及 Windows 导出的换行问题，修正后真实 Helm 的重复渲染、post-renderer 一致性测试 1/1 通过；两次失败日志保留，详见 [PS-009](../problem%20solve.md#ps-009windows-导出源码后的-helm-post-renderer-启动失败)。

三台 VM SSH 可达、三个节点 Ready；严格 `check` 确认为 `protected`。新的一批业务完成两类上下行各 5/5、共 20 个独立会话及 40 条正确代理连接，接收载荷 12,779,520 字节；
三轮隔离矩阵共 15 次允许、30 次拒绝、6 次 DNS 成功。原有 15 个 5GC Pod 和 3 个 CNF Pod 身份不变，Service 与策略身份不变，最终重启数为 0，测试 Pod 已清理。
79 次 UPF 内存采样峰值 11.52734375 MiB。原始证据复核与 32 项输入哈希通过，见 [`audit.json`](../results/20261016-cnf-service-chain-regression/audit.json)；
旧 S3 验收的 208 项输入哈希与原始证据也重新审计通过，见同目录 `historical-s3-audit.json`。
本次是已完成基线的回归，不新增 QoS、长期稳定性或 R2.S4 确定性编排验收结论。

## 后续任务、验收与 Agent 接入

下表维护任务设计与依赖，实际阶段状态见总路线；A 的本次实测结果见下方实现与验收章节。A—F 是本模块任务引用，不替代 R2.S4/R2.S5 阶段编号；类别、负载和指标数值须在各批实验前固定。

### 任务依赖与进入条件

| 任务 | 目的与工作 | 进入条件 | 交付与通过条件 |
|---|---|---|---|
| A：双 UE 低负载基线 | 新增独立 SUPI/订阅的 UE2；分别验收后，与 UE1 同时访问 core/edge 并测上下行 | 先只读核对既有版本和单 UE 现场；准备支持并发的测试端点与采集器 | 两个独立会话和 IP、实际流量时间重叠、用户到代理/后端的证据对应；整批无错误路径、异常断流或非预期网元重启 |
| B：5G QoS 能力探针 | 先核查版本、开关、订阅、PDR/QER/QFI，再以受控策略变更测试数据面 | 只读核查可与 A 的准备并行；变更前保存可恢复配置，先验证会话建立时下发，再研究在线修改 | 输出支持/部分支持/不支持的能力清单，分别列限速、保底和调度；策略被接收不等于数据面有效，未执行项不能记为支持 |
| C：共享瓶颈竞争 | 使用已知正确需求标签，对比无差异、固定分配与可用的 5G 策略；测用户保底、上限、优先级和副作用 | A 通过；测试瓶颈、有效容量、身份映射和执行方式已明确。涉及 PCF/SMF 的组还须 B 证明相应能力可用 | 全部竞争流经过同一瓶颈且真实拥塞；接收端吞吐、窗口违约率、RTT 分位数、UDP 丢包及计数闭合；报告其他用户代价与超额需求处理 |
| D：需求模型与识别 | 将业务描述、应用上下文和可观测特征转为需求类型、参数和置信度 | 字段/标签设计可从 A 起同步；接入真实策略前须明确 B/C 的能力边界 | 独立业务标签、未知/歧义/冲突样本、识别指标和误判后果；类别及数值来源可追踪，端口切换后不依赖预设 core/edge 标签 |
| E：确定性编排工具 | 以结构化需求和能力目录生成方案，校验容量/权限/冲突，再执行、测量和受控恢复 | 拟使用的 A—C 能力已验收，D 的字段与策略映射已固定 | 合法需求可重复执行并形成真实效果证据；不可满足需求拒绝或明确降级，漂移/失败不误报成功；先脱离 LLM 独立验收 |
| F：Agent 与对照 | 接入业务语言、状态查询、规划工具和执行反馈，解释方案与异常 | E 的工具契约、失败路径和评价方法已验收 | 与相同执行工具下的人工结构化输入、固定规则方法对照，记录理解、合法方案、目标满足、故障处理和调用成本 |

下一项是 B，先做只读核查，D 可同步设计标签和需求字段。C 先使用正确标签隔离执行误差；接入识别后再比较识别误差导致的目标违约。长期稳定性在每种新配置通过功能验收后逐步延长测试，不在未通过对应功能验收时直接增加压力或宣称长期可靠。

### 首轮实验建议与能力边界

- A 首批固定为两个 UE；每个 UE 以 1 Mbps、10 秒测试 core/edge 上下行，每种组合 5 轮，共 40 次会话，按相同目标/方向成对并发。这是低负载基线参数，不是 QoS 指标；分别验收和并发数据不得混成一批。使用下方专用服务进程与多 UE 采集器，避免服务端忙或串行执行伪装成网络问题/并发通过。
- 现有 5GC 与 CNF Profile 锁定资源和身份集合。多 UE 或新增端点应使用明确声明的实验配置版本，不能直接扩副本复用同一 SUPI，不能关闭旧校验器来迁就新增对象，也不改写旧配置哈希或验收证据。
- 每批保存 SUPI/会话与动态 UE IP、五元组/连接 Cookie、代理/后端和策略的关联。NAT 和代理会改变可见源地址，不能只按后端看到的源 IP 区分用户；QFI 在 PDU Session 范围内使用，不能作为全局用户唯一键。
- B 的证据分为策略源、SMF/PFCP 下发的 PDR/QER、实际 Flow/QFI 与数据面效果。5QI/QFI、最大速率约束、最低带宽保障和调度优先级分别验证；不得把修改 5QI、看到 QER 或达到单流上限当成整条 QoS 闭环通过。独立 `tc`、PCF/SMF 驱动策略和 QFI 对接调度的实验必须标清执行来源。
- C 先测有效容量，再固定共享瓶颈、负载、预热、统计窗口、容差和完整轮次。所有保底之和须考虑协议开销与容量余量；超额时验证拒绝或降级，不能同时承诺所有用户无损。时延首轮用 RTT 分位数，单向时延需另证时钟同步；UDP 抖动不替代时延，TCP 收发成功不替代 UDP 丢包指标。
- 当前四种固定状态并未验证所有能力的任意组合；例如不能将 `qos` 与 `protected` 的独立验收拼成“跨节点、带策略的整形组合已通过”。新组合要有独立配置、执行及恢复证据。
- 现有 Envoy 业务入口为 TCP proxy。UDP 丢包或实时业务测试须先准备并验收对应的 UDP 代理或受控 N6 端点，核对策略允许范围，标清实际是否经过 Envoy；不能直接把 TCP 测试改为 `iperf3 -u` 就假定原链路可用。
- 实验结论限于已测负载和虚拟路径；无线调度、URLLC、完整端到端 SLA 不是现有 UERANSIM 环境已验证的能力。相关绝对阈值和是否扩展研究边界，见总路线待确认事项。

### A 双 UE 低负载基线的实现与验收

固定配置为 [`multi-ue-experiment.json`](../infra/cnfs/multi-ue-experiment.json)，执行入口为
[`cnf-multi-ue.py`](../scripts/cnf-multi-ue.py)，独立审计入口为 [`audit-cnf-multi-ue.py`](../scripts/audit-cnf-multi-ue.py)。
新增 [`UE2 测试订阅`](../infra/free5gc-test-subscriber-ue2.json) 的 SUPI 为 `imsi-208930000000002`，认证材料与 UE1 不同，仅用于隔离实验。

为避免单个 iperf3 服务进程拒绝第二个客户端，使用独立命名空间 `cnf-multi-ue` 中的专用 Envoy 和四个按用户分开的后端进程。
core 副本在 VM1，edge 副本在 VM3，与原链路使用相同锁定镜像和资源；新副本是并发测试夹具，不替换原来的 `cnf-lab`。
`free5gc` 中只增加 `cnf-a-ue2` Deployment/ConfigMap，不改变原 Helm UE 选择器，不重新部署控制面或 UPF。

| 测试用户与目标 | NodePort / Listener 端口 | 专用后端 Service |
|---|---|---|
| UE1 → core | 31301 / 15301 | `cnf-a-core-ue1:5201` |
| UE2 → core | 31302 / 15302 | `cnf-a-core-ue2:5201` |
| UE1 → edge | 31303 / 15303 | `cnf-a-edge-ue1:5201` |
| UE2 → edge | 31304 / 15304 | `cnf-a-edge-ue2:5201` |

这四个入口明确区分测试客户端与目标，不构成基于用户身份的访问控制或自主业务识别；也不证明原单服务进程能承接多个独立 iperf3 测试。
实验仍严格调用原 5GC 和 `protected` Profile 验证，再额外验证 UE2 与新命名空间的完整资源、放置、镜像、Endpoints、无整形及策略。
部署前有 18 个原有 Pod，新增 UE2、一个代理和四个后端后共监测 24 个 Pod；批次中 UID、容器标识或重启数改变即失败。

在 VM1 的本次源码目录中设置 `KUBECONFIG=/home/lhm/.kube/config`，每一步使用新的证据目录：

```bash
python3 scripts/cnf-multi-ue.py render --out <新目录>/render
python3 scripts/cnf-multi-ue.py deploy --out <新目录>/deploy
python3 scripts/cnf-multi-ue.py individual --out <新目录>/individual
python3 scripts/cnf-multi-ue.py smoke --out <新目录>/smoke
python3 scripts/cnf-multi-ue.py concurrent --out <新目录>/concurrent
python3 scripts/cnf-multi-ue.py check --out <新目录>/final-check
```

`individual` 是每 UE、每目标/方向各一次，共 8 次单独会话；`smoke` 为 4 组/8 次并发探测；正式 `concurrent` 为 20 组/40 次会话，三批独立判定，不拼接。
每组两个客户端均绑定各自 `uesimtun0` 地址；以同一 VM3 时钟下 iperf 的非空流量区间计算重叠，并扣除秒级时间戳的 1 秒不确定性，保守下界须至少 8 秒。
源地址与目标端口、独立 Cookie、两条代理连接、正确后端且其他三个后端不命中、UPF 双向计数均须对应；UPF 计数仍为聚合证据，不声称逐包追踪。
每组持续检查身份及 UPF 512MiB 内存护栏。正式失败后停止，不覆盖目录或补轮；历史失败、恢复和正式结果分别保留。

**2026-10-20 实机结果。** 证据为 [`results/20261020-cnf-multi-ue/`](../results/20261020-cnf-multi-ue/)。测试前 UE1 虽持有会话/IP，但旧 RRC 上下文相关异常导致隧道不通；保存 `preflight-01` 后仅重建 UE1，紧接着的 TUN 未就绪失败保留为 `preflight-02`，最终 `preflight-03` 严格验收通过。详见 [PS-010](../problem%20solve.md#ps-010ue-保留会话与-ip但旧-rrc-上下文导致业务不通)。下列稳定性比较均以恢复后的部署前快照为起点。

| 批次 | 结果与边界 |
|---|---|
| `deploy-01` | UE2 独立鉴权/注册/PDU Session/TUN 成功；UE1 为 `10.60.0.3`，UE2 为 `10.60.0.4`；原有 18 个 Pod 身份不变，新增 6 个 Pod |
| `individual-01` | 两个 UE 的 core/edge 上下行共 8/8 次单独会话，16 条正确代理连接 |
| `smoke-01` | 4/4 组、8/8 次并发探测，独立保存，未混入正式统计 |
| `concurrent-01` | 20/20 组、40/40 次正式会话、40 个唯一 Cookie、80 条正确代理连接；最小有效流量保守重叠下界 **9.000034 秒** |
| `final-check-01` | 两个 UE 仍已注册且会话活跃，绑定隧道 Ping 通过；24 个 Pod Running/Ready，容器重启数为 0，无遗留 iperf3 客户端 |

单独、预检和正式三批合计 56 个唯一 Cookie、112 条代理连接、70,647,808 字节接收载荷；合计仅表达证据数量，不合并为同一性能批次。
原有 18 个 Pod 在 64 份身份检查中未变，24 个受监测 Pod 的测量身份保持一致；8 份夹具资源记录中的 Service/策略/配置身份与规范一致。
415 次 UPF 内存采样峰值为 **11.90234375 MiB**，仅为离散采样峰值。100 项输入哈希匹配，原始业务、用户绑定、后端命中、实际重叠与收尾状态均经独立离线审计，见 [`audit.json`](../results/20261020-cnf-multi-ue/audit.json)。
源哈希改写、错误 UE 绑定、Cookie 出现在错误后端、伪造串行时间窗、SMF 被替换、策略绕过这 6 类损坏副本全部被拒绝，原始材料哈希不变，见 [`audit-negative-checks.json`](../results/20261020-cnf-multi-ue/audit-negative-checks.json)。
Windows 和 VM1 各发现 95 项测试，94 项通过、1 项既有 Helm 集成按环境开关跳过；Envoy 新配置使用实际镜像验证通过。测试结果文件与动作元数据均保存在本次证据目录。

当前保留 UE2 与专用夹具供后续核查使用；原 `cnf-lab` 继续为 `protected`，未启用整形或新的 5G QoS 策略。本结果证明该专用 TCP 夹具下的低负载双 UE 并发，不证明拥塞保底、优先级、自主需求识别、长期稳定性或无线 SLA；R2.S4 整体与 Agent 均未完成。

### 业务需求识别的输入与验收

用户已明确目标是业务需求类型。首批候选为实时交互、持续媒体、后台批量传输，具体分类与标签规则仍待确认。类型之外需记录用户/会话、上下行方向、最低/最高带宽、时延与丢包目标、优先级、有效期、置信度，以及各字段来自用户、审核过的模板还是观测推断；未知值应显式保留。

业务期限和服务等级可以是合理输入；期望 core/edge、目标 5QI 或正确类别标签不能充当需求识别的答案输入。仅凭相似包序列不能可靠判定紧急程度；“视频”等应用名称也不能自动确定数值 SLA。使用模板默认值须说明依据，低置信度要拒判或补充信息。

先用独立标注数据评估规则基线，再评估引入模型的收益；标注来自业务任务及审核过的需求，不能从现有端口映射反向生成“真实类别”。按业务/场景划分评价样本，包含端口交换、未知业务、相同流量不同期限、缺失参数和资源冲突。分别报告分类/参数提取、未知处理，以及误判造成的资源浪费与需求违约；不能只报告与 LLM 自身判断一致。

### Agent 接入与最终交付

Agent 位于需求理解与工具协调层：理解业务描述，查询现场，调用规划器，解释方案，协调验证及后续处理。规划器负责资源计算，校验器有否决权，执行器负责实际配置，测量器返回真实结果；LLM 不逐包调度，不任意生成 YAML/命令，不凭空填写 SLA，也不以返回一段“成功”文字代替验收。

下面是拟议工具契约，尚未实现为统一接口；现有 `cnf-profile.py` 只覆盖已验证固定状态的部分执行/恢复能力，不能将函数名视为已有功能。

| 拟议工具 | 输入与输出职责 |
|---|---|
| `inspect_state` | 返回版本化现场、用户/会话映射、能力、策略、容量估计与证据时间，未知项显式返回 |
| `plan_policy` | 输入结构化需求和状态，返回受控方案及约束依据，或不可满足原因；不修改集群 |
| `validate_plan` | 检查字段、白名单、容量、权限、冲突和状态漂移；未通过不能执行 |
| `apply_plan` | 只接受已校验方案及对应状态，保存快照、执行有限动作并返回实际状态与日志 |
| `verify_plan` | 将接收端指标和路径/策略证据与明确目标比较，返回满足、未满足或证据不足 |
| `restore` | 恢复允许的已知配置并复验；不承诺任意中断现场自动恢复 |

请求、方案、快照、执行与测量应通过标识关联，并记录配置版本、输入哈希、动作及错误。状态变化时重新校验；恢复失败必须保留失败状态，不伪装成成功。首版先完成固定工作流，不把模型自由选择任意系统操作作为目标。

最终可有一个 Python 启动入口，复用现有脚本并按职责扩展；交付还包括需求/方案契约、规划与校验、真实执行适配器、配置、对照实验和证据。一次运行拟保存 `requirements.json`、`plan.json`、执行轨迹和验证报告；这些是拟议产物名，不表示文件已实现，也不要求每次生成新 Python 代码。无需为形式预建空目录或强制增加 Web 界面。

主要对照共用同一执行/测量工具：人工结构化需求（正确标签参考）、固定规则理解需求、Agent 理解需求。分别评价识别与参数提取、计划合法率、真实目标满足率、失败处理、时延/调用成本。借鉴 WirelessAgent 的工作流、知识与消融设计，但不能将其数值网络模型的收益直接作为本项目证据；实网接入与文件拆分本身也不足以证明新方法的贡献。

## 实施路线与产出

阶段顺序和状态统一维护在 [R2 阶段表](03-research-roadmap.md#r2-当前阶段顺序)，本模块只记录具体产出与技术证据：

| 适用阶段 | 本模块维护的内容 | 主要产出 |
|---|---|---|
| R2.S1 | 功能调研、已有能力与候选实现核对 | 功能目录、接口与依赖矩阵 |
| R2.S2 | 手工链路、分类、引导及 QoS 效果验证 | 受控配置、端到端证据 |
| R2.S3 | 版本固定、部署/恢复脚本及重复测量 | 可重复数据与结果汇总 |
| R2.S4 | 受控方案生成、执行与校验 | 规则、校验器和测试 |
| R2.S5 | 意图理解、工具、记忆、规划与反馈 | Agent 工作流与对照实验 |

后续实现时，配置和部署资产放入 `infra/cnfs/`，脚本放入 `scripts/`，原始实验材料放入 `results/<日期>-cnf-service-chain-<实验名>/`。需要自行实现的控制器或服务开始开发时，再创建 `services/`；不要预先建立空目录。故障细节仍写入 [problem solve.md](../problem%20solve.md)。

实验元数据或已有汇总记录注明路线/阶段（例如 `R2.S2`）、配置版本和证据路径。后续纠偏不移动或覆盖已有结果；在新路线中说明哪些结论可继承，以及适用条件。

## 已确认决策与待验证事项

- [已确认] R2.S2 使用预部署实例和手工受控配置；运行时创建实例留到 R2.S4 评估。
- [已确认] 首版采用独立 Envoy、Linux `tc`、Kubernetes Service/NodePort 和 k3s NetworkPolicy，不更换 CNI、不安装服务网格。
- [已确认] 七项能力中，五项构成业务服务链，NAT/转发和 NetworkPolicy 是平台保障能力。
- [已验证，范围有限] Envoy TCP proxy 与当前 iperf3 3.9 的 M1 受控上行、M2 双入口和 M3 单流下行兼容性及路径日志；并发和代理资源开销对照仍待验证。
- [已验证，M3] Pod 网络命名空间内 TBF 的配置、撤销/重建、统计读取和本次 5/15 Mbps 各 5×15 秒验收；跨批次长期稳定性仍待验证。
- [已验证，M4] 去整形同节点对照、edge 的 VM1→VM3 放置及跨节点上下行路径；不证明时延收益或无损迁移。
- [已验证] VM1/VM3 的 kube-router 规则链与 `cnf-lab` 普通 Pod 隔离正反例、策略撤销/重加和 UE 业务回归；不外推到未测来源。
- [已验证，计数关联] 本轮 NodePort 路径的 UE 绑定选路、UPF NAT/接口计数、Envoy 连接及后端来源证据闭合；未进行逐流抓包或全流量绕行防护验收。
- [待验证] 现有 free5GC PCF/NEF 在 v4.2.2、Single-UPF Profile 中能否形成可测用户面效果；此项不阻塞 R2.S2。
- [待确认] 论文最终更偏服务链选择/配置算法、意图理解，还是平台自动化；低时延 SLA 绝对阈值也仍需导师确认。
