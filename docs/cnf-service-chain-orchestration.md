# 面向业务需求的 CNF 服务链编排

> 最后更新：2026-10-15
> 状态：R2.S1 调研复核与首版选型已完成；R2.S2 部署资产和实机能力实验尚未开始，本文中的方案不代表已经实现。
> 适用路线/阶段：R2.S1—R2.S3（能力盘点、手工实验、复现与测量）；R2.S4—R2.S5 的编排与 Agent 细节后续继续维护在本模块。阶段状态见 [研究路线](03-research-roadmap.md#r2-当前阶段顺序)。

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

## 与既有平台的关系

| 范围 | 当前结论 | 本模块中的作用 |
|---|---|---|
| UE、gNB、AMF、SMF、UPF | 已完成注册、PDU Session 与用户面连通验证 | 提供真实 UE 接入和基础用户面 |
| PCF | 已部署，QoS 策略是否产生可测用户面效果尚未验证 | 候选 QoS 策略控制能力 |
| NEF | 已部署，源码含 Traffic Influence 接口 | 候选应用/业务需求入口，需验证端到端效果 |
| Kubernetes 三节点与固定 Profile | 已验证可重复部署 | 承载既有 5GC 与后续新增 CNF |
| 流量识别、代理转发、可观测流量引导 | 已完成首版选型，尚未部署 | 本模块需要补充的核心能力 |

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
- **free5GC 的“支持 QoS”不等于当前环境已提供可测用户面保障。** 官方功能表把 5QI、ARP、GBR、MBR 标为 `Control Plane only`；PCF→SMF→UPF 的实际转发效果仍须单独实验。[free5GC 功能表](https://free5gc.org/guide/features/)
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
| 流量识别 | Envoy 两个受控 TCP Listener，按目的端口区分两类 iperf3 业务；访问日志记录 Listener、目标 Cluster、字节数与连接结果 | 链内分类 | [待验证] 首版只证明 L4 分类；HTTP Path/Header 分类留到 UE HTTP 客户端镜像固定后 |
| QoS 控制 | Linux `tc` TBF/HTB；由仅授予 `NET_ADMIN` 的辅助容器在后端 Pod 共享网络命名空间内配置和读取 qdisc | 链内整形 | [待验证] 先做 5/15 Mbps 受控差异；不解释为 5G 无线 QoS |
| 代理转发 | 单副本独立 Envoy Deployment，静态 v3 配置；镜像在部署前锁定版本与 Digest | 链内 L4 代理 | [待验证] 不安装 Istio，不接管其他 Pod 流量 |
| 流量引导 | Envoy Listener/Cluster 映射到两个 ClusterIP 后端；外部只暴露 Envoy NodePort | 链内选择后端 | [待验证] R2.S2 手工配置；R2.S4 再接 xDS/受控规划器 |
| 边缘服务接入 | 同一 iperf3 服务镜像分别放置到 VM1 与 VM3，使用现有节点标签固定位置 | 链终点与放置变量 | [待验证] 当前三台 VM 同宿主机，不宣称 VM3 具备真实 MEC 时延优势 |
| NAT/地址转发 | 复用 UPF N6、Flannel、kube-proxy 与固定 NodePort；保存路由和转发表证据 | 平台连通底座 | [已有基线，新增路径待验证] 不迁移 Cilium/Calico，不新增第二 CNI |
| 网络策略控制 | `cnf-lab` 独立 Namespace + k3s kube-router 执行标准 NetworkPolicy，只允许 Envoy 到后端和必要 DNS | 平台隔离护栏 | [待验证] 必须有允许与拒绝两类实测，不能只看对象已创建 |

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
- [待验证] Envoy TCP proxy 对当前 iperf3 3.9 控制连接和数据连接的实际兼容性、日志字段及资源开销。
- [待验证] Pod 网络命名空间内 `tc` 的配置、恢复、统计读取和 5/15 Mbps 验收阈值是否稳定。
- [待验证] k3s kube-router NetworkPolicy 控制器在当前集群是否启用并正确拦截 `cnf-lab` 流量。
- [待验证] NodePort 经 UPF 进入 Envoy 后的源地址、NAT 层次和完整路径证据。
- [待验证] 现有 free5GC PCF/NEF 在 v4.2.2、Single-UPF Profile 中能否形成可测用户面效果；此项不阻塞 R2.S2。
- [待确认] 论文最终更偏服务链选择/配置算法、意图理解，还是平台自动化；低时延 SLA 绝对阈值也仍需导师确认。
