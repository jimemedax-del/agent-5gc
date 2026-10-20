# 面向业务需求的 CNF 服务链编排

> 最后更新：2026-10-20
> 当前恢复配置：`cnf-service-chain-v1/protected` 加 `cnf-multi-ue-v1` 双 UE 夹具、原 Session-AMBR、UPF QoS 开关为 0；A 与 B 的观测及能力边界见下文。
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
- [已验证，A/C] 两个独立用户的低负载同时接入与业务，以及 C1/C2 双 UE 夹具在独立下行/上行共享瓶颈中的带宽分配与优先级交换；[部分验证，B] Session-AMBR 安装和端点限速，UPF 开启组吞吐退化、未通过。时延/丢包目标、同时双向竞争、更多用户、长期稳定性和专用 QoS Flow 仍待验证。
- [已实现，D1/D2 限定范围] 业务需求结构化合同、规则 v2 及六条合成描述的 Agent 自校验回归；真实业务泛化和自主流量识别未验收。当前 `core/edge` 是预设转发目标，日志 `business_class` 是配置标签，均不能作为系统已自主理解业务需求的证据。
- 后续任务的依赖、进入条件和验收标准见 [后续任务、验收与 Agent 接入](#后续任务验收与-agent-接入)，最终交付形态见 [Agent 接入与最终交付](#agent-接入与最终交付)。

## 与既有平台的关系

| 范围 | 当前结论 | 本模块中的作用 |
|---|---|---|
| UE、gNB、AMF、SMF、UPF | 已完成注册、PDU Session 与用户面连通验证 | 提供真实 UE 接入和基础用户面 |
| PCF | 已部署，QoS 策略是否产生可测用户面效果尚未验证 | 候选 QoS 策略控制能力 |
| NEF | 已部署，源码含 Traffic Influence 接口 | 候选应用/业务需求入口，需验证端到端效果 |
| Kubernetes 三节点与固定 Profile | 已验证可重复部署 | 承载既有 5GC 与后续新增 CNF |
| 显式端口分类、代理转发、可观测流量引导 | 限定范围能力与固定 Profile 的部署/恢复、重复测量已验收；D1 另有离线需求合同/规则基线，真实业务识别未验收 | 验证需求识别，再接确定性编排 |

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
| E：确定性编排工具 | 以结构化需求和能力目录生成方案，校验容量/权限/冲突，再执行、测量和受控恢复 | 拟使用的 A—C 能力已验收，结构化字段和作用域固定；可先人工声明实验需求，不要求识别器先成熟 | 合法需求可重复执行并形成真实效果证据；不可满足需求拒绝或明确降级，漂移/失败不误报成功；先脱离 LLM 独立验收 |
| F：Agent 与对照 | 接入业务语言、状态查询、规划工具和执行反馈，解释方案与异常 | E 的工具契约、失败路径和评价方法已验收 | 与相同执行工具下的人工结构化输入、固定规则方法对照，记录理解、合法方案、目标满足、故障处理和调用成本 |

B 首轮能力结论与 C1/C2 的独立方向带宽结果见下方，后续进入 D 的标签和需求字段设计。C 先使用正确标签和已验收执行方式隔离执行误差；上行 RTT 反例不支持时延保障，原生 UPF 组须另补执行点证据并处理退化。接入识别后再比较识别误差导致的目标违约。长期稳定性在新配置通过功能验收后逐步延长，不直接增加压力来替代功能验收。

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

### B 5G QoS 能力探针

输入为 [`qos-probe.json`](../infra/cnfs/qos-probe.json)，执行与恢复入口为
[`cnf-qos-probe.py`](../scripts/cnf-qos-probe.py)，独立复核为
[`audit-cnf-qos-probe.py`](../scripts/audit-cnf-qos-probe.py)。证据归入
[`results/20261020-cnf-qos-probe/`](../results/20261020-cnf-qos-probe/)，前三次失败独立保留，问题详见
[PS-011](../problem%20solve.md#ps-011qos-探针的-tcp-结束语义与-ue-自动补建会话竞态)。

**执行范围。** 保留 A 的 24 个 Pod、原 `protected` 和专用四路 TCP 夹具，只对 UE2 的
`subscriptionData.provisionedData.smData` 记录中 `dnnConfigurations.internet.sessionAmbr` 的上/下行两个字段做比较后更新。
这是一条实验室订阅配置通道，未实现可交给 Agent 的生产级策略 API；不是通过直接改内核 QER 伪装控制面下发。
不读取或回写 Authentication/SQN，其他订阅字段逐项比较；遇到非预期旧值则拒绝覆盖。
更新后先等待 5 秒观察旧规则，再释放 UE2 的 PDU 会话，等待 UERANSIM 自动补建，走正常 UDR/PCF/SMF/PFCP 建会话流程。
订阅数据库的直接更新没有触发在线 PCF 通知，故该 5 秒观察不能用于否定标准在线策略接口。

原 Session-AMBR 双向 1000 Mbps，探针为上行 2 Mbps、下行 4 Mbps；发送目标 8 Mbps，每次 15 秒。
顺序为原策略测量、探针策略下的 UPF 开关关/开/再关（各方向各 3 轮）、开启时 UE1 对照，以及恢复原策略后的双 UE 上下行。
正式批次共 26 次会话，用户端口/后端沿用 A 的 core 路径，尚未扩展 edge 或 UDP；这些数值是诊断参数，不是业务 SLA。
每次保存源地址与 Cookie、接收字节/窗口、代理与正确/错误后端日志、PDR/QER/FAR、QoS 开关、UPF 接口计数、Pod 身份和内存护栏。
恢复先关闭 QoS 开关，再还原原订阅并重建 UE2 会话；最终需严格检查单会话、双 UE TUN Ping 和真实业务。

**版本与机制核查。** 现场仍为锁定的 free5GC v4.2.2 镜像、带判空补丁的 go-upf v1.2.10、
gtp5g v0.9.5、内核 `6.8.0-138-generic` 和 UERANSIM v3.2.7。初始 gtp5g `QoS Enable: 0`，
每 UE 两条 PDR 关联一条默认 QoS QER 和一条速率 QER；QFI 均为 1，但 SEID 不同。
按 UE 动态 IP → PDR → SEID/关联 QER 验证速率字段，不能用全局 QFI=1 区分用户。
上游 gtp5g 的 MBR 路径创建令牌桶并在开关开启时检查包；本次读取的已安装模块版本、模块哈希和干净 v0.9.5 源码信息见
[`kernel-source-inspection.json`](../results/20261020-cnf-qos-probe/kernel-source-inspection.json)。这类 policer 超额丢包，不构成最低带宽调度器。

另外核查了 [UERANSIM v3.2.7 gNB 数据转发](https://github.com/aligungr/UERANSIM/blob/v3.2.7/src/gnb/gtp/task.cpp)
和 [RateLimiter](https://github.com/aligungr/UERANSIM/blob/v3.2.7/src/gnb/gtp/utils.cpp)：gNB 在建会话时将 Session-AMBR 写入自己的上下行令牌桶，
转发时先检查额度。因此 UPF 开关关闭并不等于整条路径不受 Session-AMBR 限制；仅看端点限速不能把效果独占归因于 UPF。
该上游版本上行还取 QoS Flow 列表第一个 QFI，未提供本项目所需的按业务流自主选择多个 QFI 的证明。
源码核查是对已报告版本的解释依据，不是对镜像中二进制重建后的证明，也不等于无线调度验收。

**TCP 测量边界。** B 的新采集器不修改 A 的既有验收条件。
限速下发送端可能仍有代理/内核缓冲，分别保留两端统计，不要求字节完全相等，也不据差额推导丢包率。
对于定时结束时数据连接的 `UR/DC`，仅当接收窗口至少 14.25 秒、代理数据计数覆盖接收载荷且独立控制连接正常时接受该次观测；
提前失败、连接失败、错误用户/后端仍拒绝。结果必须报告代理标志，不能描述为无损或干净 TCP 收尾。
普通异常的恢复由 `finally` 尝试完成，外部强杀/断电后的恢复须先检查现场再显式执行，不能宣称任意中断自动恢复。

| 能力 | 本轮结论与限制 |
|---|---|
| 建会话时下发 Session-AMBR | 订阅变更经 PCF/SMF 正常建会话后，UE2 的 AMBR 与关联 QER 变为 UL 2000 / DL 4000 Kbps；是会话总速率，不是按业务 QoS Flow |
| UPF 开关为 0 时的会话上限 | 端点接收约 2/4 Mbps，存在真实速率控制；结合源码解释为含 gNB AMBR 执行的路径，不能登记为 UPF 的独立效果 |
| UPF 开关为 1 的当前 TCP 路径 | 发生明显吞吐退化，上行低于原定 0.2 Mbps 连续传输诊断线；当前组合不接受为可用的 QoS 执行能力。原因未定位，不泛化为整个 gtp5g 或所有协议均不支持 |
| 默认 5QI/QFI | 订阅默认 5QI=9、安装 QFI=1；仅证明默认值存在，未验证改变 5QI 的差异处理或实际包级 QFI 分类 |
| 专用 QoS Flow / MFBR | 未实测；当前 UERANSIM 上行首 QFI 路径存在限制，不能承诺按业务分类并分别执行 |
| 在线策略修改 | 直接更新订阅后 5 秒旧 QER 仍存在，重建会话后才改变；未测试 PCF 策略通知，不能将该观察写成标准在线接口“不支持” |
| 用户保底、优先级调度、时延/丢包保障 | 均未通过此探针证明；MBR 上限/令牌桶不代替 GBR 调度，UE1 顺序对照也不证明竞争隔离 |

首轮能力目录只允许继承已验证的会话上限和策略安装事实；不会因为接收速率低于上限就接受 UPF 开启组。
后续 C 可用已验收的独立 `tc` 构造共享瓶颈和调度对照，明确执行来源；若要把原生 UPF 策略纳入 C/E，
须先隔离 gNB 混杂因素并补充执行点探针，解决或规避当前吞吐退化，再验收相应能力。

**2026-10-20 正式结果。** `probe-04` 完成 26 次独立定时会话、26 个唯一 Cookie 和 52 条正确路径代理连接。
下表为接收端 Mbps；关/开/再关每方向各 3 轮，列中位数。原策略和 UE1 对照各方向一次；恢复列为两个 UE 各一次的中位数。

| 观测组 | 上行 Mbps | 下行 Mbps |
|---|---:|---:|
| UE2 原策略、开关关闭 | 7.983022 | 8.021560 |
| UE2 临时 2/4 Mbps、开关关闭 | 2.021470 | 4.063965 |
| UE2 临时 2/4 Mbps、开关开启 | **0.054318** | **0.444524** |
| UE1 原策略、开关开启（顺序对照） | 7.983342 | 8.021557 |
| UE2 临时 2/4 Mbps、再次关闭 | 2.022130 | 4.065085 |
| 还原原策略、关闭开关后的双 UE 复验 | 7.983095 | 8.021557 |

开启时上行中位数仅为关闭时的 2.687%，下行为 10.938%；上行三轮均低于探针原定的 0.2 Mbps 连续传输诊断线。
**该组网络能力未通过验收。** 再次关闭和恢复原订阅后的吞吐均回升，不能把这次退化归为不可逆故障，也未定位为特定内核缺陷。
全批接收 192,266,240 字节；301 次 UPF 内存采样峰值 15.125 MiB。52 条代理记录中 43 条 `-`、9 条数据结束 `UR`，这些结束标志未被改写或藏入“全部连接正常”。
24 个 Pod 身份与容器标识保持不变、重启数为 0；UE2 仅重建 PDU Session/IP，最终 UE1 为 `.3`、UE2 为 `.13`，均有单个活跃会话、TUN Ping 和恢复后业务证据。
原订阅全部已读字段完全还原，QoS 开关回到 0，原 `protected` 与两条夹具策略保留，无遗留客户端。

正式建会话时 PCF 创建/删除策略的日志有保留；SMF trace 的历史行轮转很快，未完整保存该次安装期日志。
实际速率数值以 UE 会话和内核 PDR/QER 读回为证；恢复期通过实时选择采集保存了 SMF 的 PFCP 删除/建立成功响应。
本轮未抓取 PFCP 报文体或 GTP-U 包级 QFI，不把这些日志描述为逐字段 PFCP 抓包闭合。

Windows 与 VM1 的独立原始数据审计一致，见 [`audit.json`](../results/20261020-cnf-qos-probe/audit.json) 和
[`audit-vm1.json`](../results/20261020-cnf-qos-probe/audit-vm1.json)；24 项执行输入哈希匹配。
源码哈希、错误 UE、跨会话 QER、错误开关、改写速率、未恢复策略、重复会话、SMF 替换、错误后端 Cookie、缺轮、缺源码哈希和提前重置，
共 12 类损坏副本全部拒绝，正式原始材料哈希不变，见 [`audit-negative-checks.json`](../results/20261020-cnf-qos-probe/audit-negative-checks.json)。
当前 Windows 和 VM1 各发现 113 项测试、112 项通过、1 项既有 Helm 集成按环境开关跳过。

审计区分“证据有效”和“网络目标通过”：最初分析器把小于 0.1 Mbps 的完整正速率观测直接当作无效数据；
修订后保留原 10%-of-cap 诊断线及其失败结果，输出 `networkAcceptance` 未通过，不丢弃退化轮次。
执行时的 4 项 B 专用输入按原哈希归档在 `probe-04/executed-inputs/`，新分析器另外记录自身哈希；
未修改原执行元数据或重算旧输入哈希。原分析器失败输出与修订依据见
[`analysis-revision.json`](../results/20261020-cnf-qos-probe/analysis-revision.json)。因此 `audit.outcome=passed` 只表示证据复核通过，绝不表示 UPF 开启组 QoS 合格。

### C 共享瓶颈竞争：容器化调度与受控需求

配置为 [`contention-experiment.json`](../infra/cnfs/contention-experiment.json)，入口为
[`cnf-contention.py`](../scripts/cnf-contention.py)，独立审计为
[`audit-cnf-contention.py`](../scripts/audit-cnf-contention.py)。首轮 C1 明确限定为下行 UDP 竞争；
原 A 的 TCP/Envoy 链路与 B 的原订阅保持，原生 UPF QoS 开关为 0。
这里先使用正确的受控需求标签，不引入自主识别、PCF 策略通知或 QFI 到 TC 的适配。

**容器化边界与实际路径。** 新增 `cnf-contention` 命名空间中的单个 `cnf-c-endpoint` Pod：
两个非 root、无 capabilities 的 iperf3 服务容器分别监听 5201/5202；一个仅有 `NET_ADMIN` 的辅助容器配置同 Pod `eth0` 上的 HTB/FQ-CoDel。
三个容器共享网络命名空间和一个网口，保证两个用户竞争同一个根队列，不把两个独立 TBF 当成共享瓶颈。
镜像复用已锁定的 UE/iperf3 Digest；禁止 hostNetwork、hostPID、hostPath、特权容器和自动挂载服务账户。
辅助容器的有效 capability 必须为 `0x1000`，调度实际由宿主机 Linux 内核执行。

```text
同一测试 Pod 的两个服务容器
→ 同一个 eth0 / HTB 10 Mbps 根队列
→ Kubernetes Service/NodePort → UPF → gNB → 两个 UE 接收端
```

两个 TCP+UDP NodePort 为 31401/31402，分别对应 UE1/UE2 的受控端点；TCP 用于 iperf 协议控制，UDP 用于下行业务。
本实验使用直接 N6 测试端点，不经过 Envoy；原 TCP proxy 不因此获得 UDP 转发能力。
由于 NAT 会合并服务器可见的源 IP，按 UE CLI/SUPI、动态 TUN IP、源地址绑定、独立 Cookie 和服务进程日志建立关联。
这些端口是夹具标签，不是用户身份 ACL，也不代表自主识别。

**固定负载、策略与准入。** 两个 UE 各接收目标 8 Mbps、1200 字节数据报、30 秒，
分析接收端第 5—25 秒内的完整连续区间，排除启动和结束边界；实际有效区间与秒级时间戳不确定性一并保留。
拥塞组根容量为 10 Mbps，所有业务必须同时经过该根队列；用实际非空流量区间证明至少 28 秒保守重叠。
每批先测未整形容量，结束后恢复并复测，不能只看带策略的结果。

| 对照模式 | UE1 / UE2 的 HTB 配置 | 目的 |
|---|---|---|
| `shared` | 共享一个 10 Mbps 类与 FQ-CoDel | 无用户差异的拥塞基线 |
| `equal` | 每用户 `rate=4`、`ceil=8`、相同优先级 | 固定等额分配对照 |
| `guarantee-ue1/ue2` | 高需求用户 `rate=6, ceil=8, prio=0`；另一用户 `rate=2, ceil=4, prio=1`，交换归属 | 检查各用户最低分配、上限及一秒窗口违约 |
| `priority-ue1/ue2` | 两用户均 `rate=2, ceil=10`，只交换 `prio=0/1` | 隔离优先级对剩余带宽分配的作用 |

上述速率单位均为 Mbps；优先级是本地 HTB 参数，不直接等同于 5QI/ARP 或无线调度优先级。
所有模式固定 burst/cburst 32768 字节、class quantum 1514、FQ-CoDel limit 256、target 约 5ms、interval 约 100ms。
每用户最低速率之和加 0.5 Mbps 预留必须不超过根容量；6+5 Mbps 的超额请求应在修改 TC 前拒绝。
允许各用户上限之和大于容量，表示按规则借用剩余资源，不能承诺同时达到全部上限。

验收线在实测前固定到配置：未整形每用户至少 7 Mbps；拥塞组总接收 8.5—10.2 Mbps；等额组每用户至少 4 Mbps。
保障组高/低用户最低接收为 5.5/1.7 Mbps，上限为 8.4/4.2 Mbps，低于最低线的一秒窗口比例不超过 10%。
这些线考虑了报文开销和测量容差，是实验诊断合同，不是为真实业务凭空设定 SLA。
优先级组要求高用户至少 6 Mbps、低用户至少 1.7 Mbps，吞吐比至少 2；交换优先级后应随配置交换效果。

**RTT、丢包与证明边界。** 两个 UE 绑定 TUN，向共享 Pod IP 发每秒 5 次 ICMP 探针。
探针 ToS 40/80 仅作夹具标记；先在无业务时分别发 5 次，要求对应用户类计数增长、另一用户类不增长，再进行负载测量。
保留到 Pod IP 的实际 TUN 路由及接收端同一 VM3 时钟下的探针时间戳，报告稳态 RTT 的 p50/p95/p99。
这是映射到相同调度类的 ICMP RTT，不等于 UDP 应用单向时延或无线 PDB。
UDP 丢包来自接收端计数，并分别保留稳态与完整会话统计；不使用 TCP 收发差额或 iperf 抖动代替丢包/时延。
当前没有经业务确认的时延/丢包 SLA，因此这些指标作为观测与代价比较，不登记为绝对保障通过。

正式设计每模式各 3 轮，加前后未整形对照，共 20 组/40 次业务；小批试验另存，不合并正式统计。
持续监测原 24 个 Pod 与新增 Pod 的身份、重启及 UPF 512 MiB 内存护栏，保存 TC 原始输出、分类器、各类/根队列计数、UPF 计数和用户关联。
每次执行归档输入及 LF 规范化哈希。普通异常尝试撤销新 Pod 内的自有队列；未知根队列拒绝覆盖，恢复失败保留现场，不能宣称任意强杀/断电自动恢复。
收尾需回到 `noqueue`，再次严格检查 A/原 `protected`、原订阅 AMBR 和关闭的 UPF QoS，并复测业务。

**2026-10-20 正式结果与代价。** `measure-01` 已完整采集 20 组/40 个唯一 Cookie；
VM1 与 Windows 独立审计完全一致，证据完整性与预先固定的带宽诊断合同均通过，24 项输入哈希匹配且没有执行源码差异。
`pilot-05` 的 8 组/16 次会话单独审计通过，不并入下表；兼容性失败、恢复与小批版本保留，故障见 [PS-012](../problem%20solve.md#ps-012旧版-tc-输出与反向-udp-汇总字段导致-c-采集失败)。

下表吞吐为每模式三轮稳态中位数，丢包为三轮接收区间的丢失/总数据报合并比例；RTT 为各轮稳态 p95 的范围。
未整形行只有前后两个对照，不能视为三轮。

| 模式 | UE1 / UE2 接收 Mbps | UE1 / UE2 UDP 丢包 % | UE1 RTT p95 ms | UE2 RTT p95 ms |
|---|---|---|---|---|
| 前后未整形 | 8.001 / 8.001 | 0 / 0 | 5.76—5.96 | 5.10—5.75 |
| 共享 | 4.828 / 4.827 | 42.91 / 42.97 | 2.57—3.56 | 2.86—3.80 |
| 等额 | 4.827 / 4.827 | 42.98 / 43.00 | 3.33—4.21 | 3.35—4.14 |
| 保障 UE1 | 7.726 / 1.929 | 3.44 / 78.39 | 2.83—3.22 | 6.11—6.73 |
| 保障 UE2 | 1.929 / 7.726 | 78.38 / 3.43 | 6.08—6.33 | 2.93—3.16 |
| 优先 UE1 | 7.725 / 1.929 | 3.44 / 78.39 | 3.72—4.25 | 6.21—6.85 |
| 优先 UE2 | 1.929 / 7.726 | 78.39 / 3.44 | 5.91—6.27 | 3.68—3.76 |

保障组中两个用户各自低于诊断最低线的一秒窗口均为 0；所有拥塞组总载荷约 9.65 Mbps，根队列真实发生 overlimits。
TC 根速率的计费口径还覆盖报文头、探针和控制流，接收有效载荷不要求恰好 10 Mbps；本轮未逐项分解这些开销。
仅交换优先级的组保持 rate/ceil 不变，效果随用户交换；两类不对称策略均体现高需求用户收益和低需求用户吞吐、丢包及探针 RTT 的代价，不能称为两用户同时无损。
上限参数与当前负载下的未越界均有核查，但本轮没有让每个用户分别饱和到所有 ceil，不能据此声称各上限已独立测准。
所有组的保守有效流量重叠下界至少 **28.000091 秒**。677 个 UPF 内存采样的峰值为 **16,080,896 字节（约 15.34 MiB）**，低于 512 MiB 护栏。
原有 24 个 Pod 及新增 Pod 的身份均不变、重启为 0；无新增 UE 会话或策略变更，结束恢复 `noqueue`、原双向 1000 Mbps AMBR 与关闭的原生 QoS，严格业务检查通过。
新增测试 Pod/Service/策略保留供后续复核，当前不整形。

8 Mbps 是请求的发送目标；拥塞下服务端逐秒发送节奏存在波动，完整会话实际报告约为共享/等额每用户 7.83—7.84 Mbps、高用户 8.00 Mbps、低用户 7.41 Mbps。
因此稳态序列号计数损失不能用 `1－接收吞吐/8` 替代，也不能把本实验当成精确恒定开环到达过程。
接收吞吐由客户端逐区间字节重算并与队列计数核对；旧版反向 UDP 的结束字节字段不用于接收吞吐。
RTT 的 p50/p99 与逐组丢包保留在原始审计/汇总中。6000 个探针均有回复，但 ICMP 与 UDP 即使同 HTB 类也可能受到 FQ-CoDel 不同流队列的处理，不能据此推导 UDP 无丢包或应用时延保障。

**可复核产出与剩余范围。** 入口为 [`metadata.json`](../results/20261020-cnf-contention/metadata.json)、
[`正式原始组`](../results/20261020-cnf-contention/measure-01/groups.json)、
[`Windows 审计`](../results/20261020-cnf-contention/audit.json)、[`VM1 审计`](../results/20261020-cnf-contention/audit-formal-vm1.json)。
Windows/VM1 均发现 129 项单测，128 通过、1 项条件 Helm 集成测试跳过，其中新增 16 项 C 单测；
[`16 类损坏拒绝`](../results/20261020-cnf-contention/audit-negative-checks.json) 覆盖源码、身份、并发、分类器、容量、原生开关、RTT 绕行、SMF 替换、轮次及恢复，原始正式数据哈希保持不变。
首轮 C1 只形成这一双 UE 直接 N6 下行夹具的带宽与调度能力：上行竞争、真实混合业务、长期稳定性仍待验证；
业务时延/丢包保障须先在 D 明确目标和依据，再设计对应实验。PCF/SMF、QoS Flow/QFI、5QI 驱动调度尚未补齐，C1 不替代 B 的原生能力结论。
本脚本的固定策略准入检查也不等于 E 通用规划器，需求识别与 Agent 尚未实现。阶段状态仍以总路线为准。

#### C2 上行竞争：入口 IFB 与服务端接收证据

配置为 [`contention-uplink-experiment.json`](../infra/cnfs/contention-uplink-experiment.json)，执行与审计分别为
[`cnf-contention-uplink.py`](../scripts/cnf-contention-uplink.py)、[`audit-cnf-contention-uplink.py`](../scripts/audit-cnf-contention-uplink.py)。
保留 C1 的配置、源码和证据，新增独立 `cnf-contention-uplink/cnf-c2-endpoint` 三容器 Pod，使用同一固定镜像及权限边界。
NodePort 31403/31404 对应两个服务端口 5201/5202，服务端以 JSON 输出精确接收区间；所有原 25 个 Pod 纳入保护，共监测 26 个 Pod。

```text
两个 UE 发送端 → gNB → UPF → Service/NodePort
→ 同一测试 Pod 的 eth0 入口 → 单个 cnfc2ifb0 / HTB 10 Mbps
→ 两个 iperf3 接收服务
```

上行在端点入口排队，不能沿用 C1 的服务器出口队列。仅在新 Pod 内创建带所有权别名的 IFB 接口和 IPv4 入口重定向，
按目标服务端口区分受控用户，ICMP 请求按 ToS 映射同一用户类；不修改 UPF、宿主机网口或无线调度。
该瓶颈位于 UPF 之后，验证到达测试应用的上行分配，不能据此声称减少了 UE 发射或 UPF 前段负载。
IFB 内核能力已在 C1 Pod 中短暂创建/删除接口验证，探针已撤销；正式流量使用独立新 Pod。

固定负载、六种模式、三轮、容量余量与带宽诊断线继承 C1：双用户各目标 8 Mbps UDP、1200 字节、30 秒，分析第 5—25 秒内完整区间。
新增要求客户端实际稳态发送为 7.5—8.5 Mbps，不能仅凭发送目标宣称负载成立。
客户端与服务端 Cookie、动态 TUN IP、服务端口和 UPF 来源需对应；吞吐及丢包只从服务端接收 JSON 的逐区间计数重算。
发送区间在 VM3 时钟、接收区间在同一 VM1 时钟分别证明至少 28 秒保守重叠，不假定跨 VM 时钟可用于单向时延。
探针 RTT 仍按 UE 本地时间匹配稳态；同 HTB 类的 ICMP 与 UDP 可能获得不同流排队待遇，RTT/丢包仍仅作观测。

未整形对照保留 IFB 和重定向，但使用内核默认无速率限制 FQ-CoDel；这样只改变是否存在 HTB 带宽控制。
测量结束删除自有入口重定向及 IFB，要求端点恢复单个 `eth0/noqueue`，复验 C1/A/原 protected、原 AMBR 与关闭的原生 UPF QoS。
未知接口别名、外来入口规则或队列漂移拒绝覆盖。正式批次与小批试验分别归档，不将采集成功等同网络目标通过。

**RTT 反例与观测版本。** `cnf-contention-uplink-v1` 的 `pilot-03` 完整 8 组/16 会话审计通过，
但首个正式 `measure-01` 在记录 10 个完整组后，于 `02-guarantee-ue2` 因低用户稳态 RTT 仅 19 个回复而中止。
该组接收吞吐约 1.932/7.726 Mbps；低用户实际发送约 8 Mbps、UDP 丢包约 75.87%。
探针全程 150 发/40 收、丢失 73.33%；收到回复的全程 RTT 均值 208.053 ms、最大 344.428 ms。
这些是真实不利观测，不能按“采集兼容性”抹去，也不能用其他成功轮次外推低时延保障。
具体根因尚未证明，原始 FQ-CoDel 流/丢弃计数保留，见 [PS-013](../problem%20solve.md#ps-013c2-低分配用户的-rtt-恶化与观测完整性)。

后续 `cnf-contention-uplink-v2` 只调整观测处理：每会话仍要求实际发 150 个探针，仍以至少 30 个稳态回复才报告分位数；
不足时保留丢包、标记 `insufficient`、分位数为 `null`，继续完整采集后续带宽组；完全无回复也不伪造 RTT。
缺少/损坏探针日志仍拒绝。带宽诊断阈值、调度策略、发送负载和渲染的 Kubernetes 资源均不变，原 26 个 Pod 未因版本变更重建。
资源/接口所有权标记继续为 v1，实验统计版本明确记为 v2；保留首批失败与版本差异证据，不将其并入后续批次或改写为成功。
该变更避免只留下 RTT 良好时的带宽结果，不代表时延保障通过。

**2026-10-20 完整上行批次结果。** v2 的 `measure-02` 完成 20 组/40 个唯一 Cookie，
与首批不完整 `measure-01` 分开统计。VM1/Windows 独立审计一致，27 项执行输入哈希匹配、当前源码无差异；
预设带宽、窗口违约、优先级交换及超额准入拒绝均通过。下表仍按每模式三轮稳态吞吐中位数、合并数据报丢包、各轮 RTT p95 范围报告，未整形只有前后两次。

| 上行模式 | UE1 / UE2 接收 Mbps | UE1 / UE2 UDP 丢包 % | UE1 RTT p95 ms | UE2 RTT p95 ms |
|---|---|---|---|---|
| 前后未整形 | 8.000 / 8.001 | 0 / 0 | 5.43—5.56 | 5.12—5.44 |
| 共享 | 4.827 / 4.827 | 39.87 / 39.18 | 5.20—6.26 | 4.81—6.70 |
| 等额 | 4.827 / 4.827 | 39.59 / 39.56 | 6.93—7.77 | 7.46—7.56 |
| 保障 UE1 | 7.726 / 1.929 | 3.44 / 75.87 | 5.55—6.79 | 8.10—9.66 |
| 保障 UE2 | 1.929 / 7.726 | 75.87 / 3.43 | 8.54—8.71 | 5.80—6.56 |
| 优先 UE1 | 7.726 / 1.929 | 3.43 / 75.87 | 5.95—6.70 | 8.01—9.70 |
| 优先 UE2 | 1.929 / 7.726 | 75.87 / 3.43 | 8.59—8.67 | 5.62—5.97 |

40 个会话的客户端实际稳态发送均约 8.0003 Mbps；发送与接收端各自在本机时钟证明并发，最小保守重叠下界为 **29.000041 秒**。
保障组低于诊断最低线的一秒窗口数均为 0；620 个 UPF 内存采样峰值 **19,243,008 字节（约 18.35 MiB）**，低于 512 MiB 护栏。
完整批次的 6000 个探针均有回复、40 个会话的稳态样本均达到 30；这只描述本批次，不能覆盖首批 73.33% 探针丢失及约 208 ms 的反例。
不对称分配下低用户 UDP 仍丢失约 75.87%，不能宣称两用户同时无损，也没有业务时延/丢包 SLA 通过结论。

原 25 个与新增 Pod 共 26 个身份不变、重启为 0；入口重定向及 IFB 已删除，端点恢复 `eth0/noqueue`。
原 C1/A/protected、单会话、双向 1000 Mbps AMBR 和原生 UPF QoS 关闭状态严格复验通过。新增上行 Pod/Service/策略保留、不再整形。
资源仍使用 v1 所有权标记，完整观测批次为 v2；没有借此重建网元或更改原实验镜像。

证据入口为 [`C2 metadata`](../results/20261020-cnf-contention-uplink/metadata.json)、
[`完整原始组`](../results/20261020-cnf-contention-uplink/measure-02/groups.json)、
[`正式审计`](../results/20261020-cnf-contention-uplink/audit.json)、
[`首批异常与版本差异`](../results/20261020-cnf-contention-uplink/incomplete-batch-diagnostic.json)、
[`软件环境`](../results/20261020-cnf-contention-uplink/environment.json)。
Windows/VM1 各 145 项单测中 144 通过、1 项条件 Helm 集成测试跳过，新增 16 项 C2 单测；
[`20 类损坏拒绝`](../results/20261020-cnf-contention-uplink/audit-negative-checks.json) 包含方向、服务端接收缺失、实际负载不足、接收端串行、入口绕行、错误端口分类、伪造 RTT、配置/身份及恢复等，原正式数据哈希不变。
原 v1 小批用归档审计器在 Windows 复核，与 VM1 结果一致；原 C1 历史原始数据亦复审通过。
后续进入 D 明确需求及可满足边界；当前能力仅为这些独立方向受控流的带宽/调度，不是同时双向竞争、身份 ACL、自主识别、原生 5G QoS 或稳定低时延保障。

### 业务需求识别的输入与验收

用户已明确目标是业务需求类型，并于 2026-10-20 确认先按以下三类候选推进；样例标签、默认数值和 SLA 模板仍未获独立审核。

| 候选类别 | 判断依据 | 不自动推导的内容 |
|---|---|---|
| 实时交互 `interactive` | 人与人或人与系统依赖持续闭环反馈，例如视频会议、远程控制 | 时延数值、带宽、优先级、5QI |
| 持续媒体 `streaming` | 沿时间连续消费媒体，例如视频播放、直播观看 | 交互需求、数值 SLA、优先级 |
| 后台传输 `bulk` | 以完成文件或数据迁移为目标，例如备份、归档 | 有期限不自动等于高优先级或实时交互 |

业务期限和服务等级可以是合理输入；期望 core/edge、目标 5QI 或正确类别标签不能充当需求识别的答案输入。仅凭相似包序列不能可靠判定紧急程度；“视频”等应用名称也不能自动确定数值 SLA。使用模板默认值须说明依据，低置信度要拒判或补充信息。

#### D1 实现与接口边界

[`requirements-contract.json`](../infra/cnfs/requirements-contract.json) 固定 `cnf-requirements-v1`，由
[`cnf-requirements.py`](../scripts/cnf-requirements.py) 校验和解释。这是自定义版本化 JSON 合同，不声称支持完整 JSON Schema。
输入为请求标识、业务描述、用户/会话、应用上下文事实及显式目标；可附端口、协议和观测值，但这些提示不参与类别及需求数值推导。
本轮仅在本地离线运行，未连接集群、下发策略或新增容器；将来的 Agent 可消费相同结构化输出。

| 输出内容 | D1 规则 |
|---|---|
| 类别、候选与置信度 | 受控短语和显式上下文事实；未知拒判、多类或矛盾需澄清；置信度为规则强度等级，未经概率校准 |
| 用户与会话 | 保留标识，现场会话绑定明确标记为未验证 |
| 方向与带宽 | `direction`、`min_bandwidth_mbps`、`max_bandwidth_mbps`；区分 Mbps 与 MB/s，不将实测速率充当最低需求 |
| 时延与丢包 | 分别记录 RTT p95、单向时延 p95 及丢包率上限；不明确指标/分位数的表达需澄清 |
| 优先级与时间 | `priority`、相对有效期、相对完成期限、测量窗口；期限不自动变为优先级或有效期，E 仍需确定计时与任务量 |
| 字段来源与冲突 | 原文片段/位置或结构化 JSON 路径；未知保留 `null`，文本与结构化值矛盾保留冲突，拒绝最低值大于最高值等不一致 |

没有批准的 SLA 默认模板或自动 5QI 映射。否定作用域、复杂语义和未支持表达采用保守处理，不能把该规则接口视为通用自然语言理解器。
`parsed` 表示合同中的必需输入已解析；`candidateForEPlanning` 仅表示可交给 E 继续检查。
输出始终为 `executionAuthorized:false`、`planGenerated:false`，不代表已生成可执行方案或承诺可满足。

能力检查只引用 C1/C2/B 的历史实验：仅在双 UE 独立方向夹具验证过带宽分配；硬时延/丢包、同时双向保证、完成期限等目标会保留并阻止候选通过。
10 Mbps 是参考夹具根容量，不是现场剩余容量。两个最低各 6 Mbps 的请求可以分别成为候选，但共同超过根容量，必须由 E 联合准入；D 不做实时容量或身份校验，也不能将任意数值策略登记为已验证能力。

#### D1 开发验证与保留缺口

合成集含 36 条样例、34 个场景族：开发组 30 条、挑战组 6 条，同一场景族不跨组。
样例、草案期望值和规则均由 Agent 编写，**未经独立人工标注**；期望值来自场景规格，并非运行识别器后生成，评估器也不把期望答案传入识别器。
这只能检查开发一致性，不能给出真实业务准确率或证明模型收益。

| 检查 | 最终结果与范围 |
|---|---|
| 草案类别一致性 | 30/36；开发组 30/30，挑战组 0/6，六条均拒判或歧义 |
| 明确目标字段 | 189/189 与草案一致，多数直接来自结构化输入；其中仅 21/21 为原文目标提取，不能将全部 189 项当作自然语言提取成绩 |
| 未知/歧义草案 | 6/6 未输出确定类别；相对草案未出现补造字段 |
| 端口、协议与观测变化 | 108/108 项不变性比较通过；不能外推到所有表达或真实流量 |
| 约束风险代理指标 | 相对草案无错误类别候选及最低带宽少报/多报；只是输入约束比较，未测资源浪费或网络 SLA 违约 |
| 自动化测试 | Windows 共发现 173 项：172 通过、1 项条件 Helm 集成测试跳过；新增 D 测试 28 项 |
| 归档复现 | 最终 `evaluation-03` 的 9 份输入哈希与工作区一致，归档代码重放的报告、预测和不变性结果全部一致 |

六条挑战缺口保留如下，未为通过评估而补齐关键词：

| 场景 ID | 表达示例 | 草案类别 | 规则结果 |
|---|---|---|---|
| `unseen-interview` | 多方远程面试，需要及时问答 | 实时交互 | 未知 |
| `unseen-viewing` | 每晚看演讲回放，边播边看 | 持续媒体 | 未知 |
| `unseen-copy` | 夜间将照片目录复制到存储设备 | 后台传输 | 未知 |
| `meeting-recording` | 视频会议的录像作为视频播放 | 持续媒体 | 歧义 |
| `negation-without-comma` | 不是视频会议而是文件归档 | 后台传输 | 未知 |
| `unseen-game` | 多人在线对战，及时操作角色 | 实时交互 | 未知 |

证据入口：[`汇总元数据`](../results/20261020-cnf-requirements/metadata.json)、
[`最终报告`](../results/20261020-cnf-requirements/evaluation-03/report.json)、
[`逐样例输出与差异`](../results/20261020-cnf-requirements/evaluation-03/predictions.jsonl)、
[`复现核对`](../results/20261020-cnf-requirements/verification-03.json)、
[`测试输出`](../results/20261020-cnf-requirements/tests-windows-final.txt)。早期两次评估及旧核对文件按历史保留，不作为最终结果索引。
[`演示请求`](../results/20261020-cnf-requirements/demo-request.json) 与
[`结构化输出`](../results/20261020-cnf-requirements/demo-requirements.json) 展示显式下行 4/8 Mbps 的持续媒体请求及其来源，不产生网络动作。

#### D2 人工标注、数据冻结与独立验证

[`人工审核清单`](../results/20261020-cnf-requirements/label-review.json) 隐去规则预测和草案标签，提供业务描述、上下文、原始结构化输入及三类定义，36 条均为未审核；类别和目标字段分别留有审核位置，空目标审核表表示尚未审核。
审核类别与目标应以业务任务为依据，不能从端口或当前后端反推答案；现有清单只做合成样例审核。独立真实业务数据用于增强后续研究结论，当前工程可先用明确标记的合成场景推进。
之后固定标签、按业务/场景划分数据，评估规则基线，再比较模型收益；覆盖未知、否定、混合业务、缺失/冲突、相同流量不同期限及资源冲突。
分别报告分类、参数提取、拒判及误判后果。真实资源浪费和需求违约须待 E/执行闭环用实测评价，不能用本轮代理指标代替。
阶段状态统一见 [总路线](03-research-roadmap.md#r2-当前阶段顺序)；D1 开发完成不构成 D 整体、E 或 Agent 验收。

2026-10-20 新增 [`review-cnf-requirements.py`](../scripts/review-cnf-requirements.py)，实现 `prepare`、`render`、`audit`、`evaluate`。
D2 初版审核流程保留在 `review-01`，当时识别规则和 D1 九份输入哈希未变。后续用户授权 Agent 校验六条新增候选并继续，规则升级为 v2，修复前后分别归档；两次开发都未接触集群。

| 环节 | 已实现的行为及边界 |
|---|---|
| 原始需求采集 | 区分 `synthetic` 与 `real_requirement`，记录原始输入、作者/来源/时间、场景族和开发/留出划分；真实需求须声明人类来源，但工具不能核实真实性 |
| 审核前冻结 | 保存识别器、需求合同、审核工具和原始集合；哈希绑定样本内容、成员、场景划分和类别定义，之后只允许编辑审核字段 |
| 类别审核 | 初始不含草案答案或预测，支持三类、未知与混合/歧义，需审核者、依据、时间及未看预测的人工声明；留出样本作者与审核者须不同 |
| 数值审核 | 独立于类别，必须逐字段确认明确/未知/冲突/无效；明确值等需引用原文或结构化输入路径，检验原文确实存在及结构化值一致；语义正确性仍需人审 |
| 泄漏检查 | 拒绝同场景族/同归一化描述跨集合、D1 家族/描述进入新留出集，以及合成/Agent 编写场景冒充真实留出数据；无法自动识别任意改写或语义重合 |
| 评估 | 所有类别审核完成才评分；目标未审核时不计入目标指标。按来源/划分分开报告，区分纯描述与带应用上下文、文本目标与结构化目标 |
| 拒判与缺口 | 同时报已知类含拒判的正确率、识别覆盖率、已识别部分的正确率、未知/歧义安全拒判、精确决策差异及全部错误样本；不以删除失败样本提高分数 |
| 复现与执行边界 | 使用冻结的识别器，评估结果保存审核输入及代码，可归档重放；始终无方案/执行授权，不自动宣布 D 通过，也不产生实测 SLA 指标 |

人工身份、数据来源及盲标过程均为声明，哈希只能检验冻结后的完整性，不能认证声明或证明真实独立性。
场景族应按业务任务定义，不能靠改名绕过检查。开发组与留出组的门槛、样本规模和研究验收指标仍需按研究问题明确；先收集 3—6 个真实需求只能作为试采集。

[`本地类别审核表`](../results/20261020-cnf-requirements-d2/review-01/review.html) 可在本地浏览器打开，无外部依赖、不上传数据。
选择类别并填写理由和审核者后，导出 `review.json`，由审核者保存到同一审核包目录，再运行完整性检查。
表单只处理类别；带宽、时延、期限等目标保持原状态。数值审核在对应 JSON 的 `targetReview` 中填写，不能因完成类别审核自动算作数值已审核。
旧 D1 清单保留作历史入口，后续使用 v2 审核包，避免把旧清单的空字段当作确认的未知值。

本轮检查：新增 35 项 D2 测试，全套 208 项中 207 通过、1 项条件 Helm 测试跳过；覆盖冻结篡改、集合泄漏、伪造原文、非人工声明、未审标签拒绝评分、来源分层、归档命令重放及表单嵌入转义。
表单另经 JavaScript 语法与 6 项临时 DOM 模拟检查，未完成浏览器视觉预览；测试中的人工声明均为临时工程夹具，不作为人审证据保存。

当前 [`准备度检查`](../results/20261020-cnf-requirements-d2/readiness-01.json) 为：36 条合成开发样例完整性通过，类别审核 0、目标审核 0、真实需求 0，`evaluationReady:false`；未生成正式识别分数。
[`复现核对`](../results/20261020-cnf-requirements-d2/verification.json) 确认归档检查可重放，未审核集合尝试评分被拒绝且不创建评估目录。
其余证据见 [`汇总元数据`](../results/20261020-cnf-requirements-d2/metadata.json)、[`测试结果`](../results/20261020-cnf-requirements-d2/tests-windows.txt)、[`表单检查`](../results/20261020-cnf-requirements-d2/form-check.json)。

上述 `review-01` 是初版人工审核准备度记录，不表示后续合成开发必须等待真实平台或另一位人工审核者。
真实需求采集入口 [`collection-intake.json`](../results/20261020-cnf-requirements-d2/collection-intake.json) 仍为空，不计作真实数据；当前使用下述 Agent 合成自校验路径。
格式及命令见 [脚本说明](../scripts/README.md#d2-人工标注与冻结评估)。真实流量执行、资源浪费与 SLA 违约需待 E 的实网闭环验证。

##### 六条候选场景的自校验与规则 v2

用户允许 Agent 自行精确校验并继续。工具新增显式 `agent_synthetic` 模式，只接受合成开发集；
标注必须声明 `reviewerKind:agent`、`independentOfPredictions:false`，不能使用人工审核表冒充人工，不能进入真实留出集。
默认 `human` 模式及旧归档保持原有校验要求，独立验证与当前开发检查分别记录。

[`原始六条描述`](../results/20261020-cnf-requirements-d2/synthetic-pilot/collection.json) 按对话中实际展示的措辞保存，
[`语义参考`](../results/20261020-cnf-requirements-d2/synthetic-pilot/reference-labels.json) 在运行预测前另行冻结；未知目标显式保留，未补造 SLA、优先级或身份。
逐字段来源、时间换算及缺失项见 [`数据校验`](../results/20261020-cnf-requirements-d2/synthetic-pilot/data-audit.json)。六条内部没有互相矛盾的数值，但都不构成完整可执行需求：

| 场景 | 自校验类别 | 明确目标与边界 |
|---|---|---|
| 远程讨论实验操作 | 实时交互 | 持续人际反馈；未明确网络方向或数值目标 |
| 观看课程回放 | 持续媒体 | 下行 4–8 Mbps；服务时长 1800 秒，非完成期限；优先级/测量窗口等未指定 |
| 上传课程录像 | 后台传输 | 明确上传方向；无完成期限和速率目标，非边传边看 |
| 只有应用名称 | 未知 | “视频平台”“流畅”不足以确定任务或数值 |
| 互动与上传并行 | 混合/歧义 | 需拆分；子任务上传方向不能直接作为整个请求的方向 |
| 限时备份数据 | 后台传输 | 上行上限 10 Mbps、完成期限 1200 秒；不自动转为高优先级或有效期，缺数据量不能判断期限可行 |

初测发现四条已知业务全部拒判，并漏提取播放最低速率、播放服务时长、备份期限，另将混合请求赋予了整个上传方向。
`cnf-requirements-rules-v2` 保持需求字段合同 v1，增加有限的任务动作/目的组合规则、正反对比作用域、会议录像对象与实时会议的区分；
支持“至少需要”“接下来……”“……内传完”等表达，混合业务的未定作用域文本目标保留来源但不提升为全局约束。
这仍是有边界的规则方法，不是通用语义模型。

| 检查 | 修复前 → 修复后 |
|---|---|
| 六条类别/未知/歧义精确决策 | 1/6 → 6/6；四条已知类为 0/4 → 4/4 |
| 八个明确原文目标 | 5/8 → 8/8 |
| 全部字段值与状态，含未知 | 56/60 → 60/60；相对参考的补造字段 1 → 0 |
| 原 D1 合成集新代码回归 | 33/36 类别一致；`unseen-viewing`、`unseen-copy`、`unseen-game` 三条仍拒判 |
| 端口/协议/观测不变性 | 新六条 18/18，原 36 条 108/108 |
| 全套自动化测试 | 218 项：217 通过，1 项条件 Helm 集成跳过；本轮新增 10 项 |

六条已用于规则修复，上述结果只能称为暴露开发样例的回归一致性；标签由 Agent 自校验，人工标签仍为 0，不能当作独立识别准确率。
D1 旧版 30/36 和六条挑战失败仍按原始归档保存；新代码的 33/36 不覆盖历史记录。
修复前后报告、源码、参考与样本均归档可重放：[`前测`](../results/20261020-cnf-requirements-d2/synthetic-pilot/evaluation-before/report.json)、
[`后测`](../results/20261020-cnf-requirements-d2/synthetic-pilot/evaluation-after/report.json)、
[`旧集回归`](../results/20261020-cnf-requirements-d2/synthetic-pilot/d1-regression-v2/report.json)、
[`核对结果`](../results/20261020-cnf-requirements-d2/synthetic-pilot/verification.json)、
[`测试输出`](../results/20261020-cnf-requirements-d2/synthetic-pilot/tests-windows.txt)、
[`当前汇总`](../results/20261020-cnf-requirements-d2/synthetic-pilot/metadata.json)。

当前不需要用户为这六条合成样例补造参数，也不需要另搭真实平台。
后续 E 应使用另外声明的显式实验配置验证规划、拒绝和联合准入，保留这些不完整请求作为澄清/拆分用例；不能将缺失字段静默补齐后冒充原始需求。
独立泛化和真实应用效果留作后续增强证据，D 整体研究验收及 E/Agent 状态仍以总路线为准。

### E1 离线规划器与 Agent 工具接口

首版代码 [`cnf-planner.py`](../scripts/cnf-planner.py) 采用 Python 标准库，工具合同为
[`cnf-planner-tools-v1`](../infra/cnfs/planner-contract.json)。以显式结构化输入验证规划器，识别器与后续 Agent 通过相同字段合同接入；本轮不访问集群、不调用大模型。

| 接口 | 首版实际行为 |
|---|---|
| `inspect_state(source, at=None)` | 检查声明式离线状态，返回有哈希和期限的快照；没有实时采集，`liveStateObserved/liveBindingsVerified` 均为 `false` |
| `plan_policy(batch, state, at=None)` | 联合检查两用户请求，枚举既有队列模式，生成受约束方案或拒绝/澄清原因 |
| `validate_plan(plan, batch, state, at=None)` | 校验哈希、版本、用户/会话、容量、优先级、上限、参考门槛、测量/恢复描述和期限，返回 `valid_offline` 或拒绝 |
| `import_requirements(normalized, batch_id, protocol, assessment_scope)` | D 输出转规划输入；调用者显式选择 UDP 与受控均值评估口径，保留空字段，不信任 D 的候选标志作为准入结论 |
| `apply_plan / verify_plan / restore` | 已保留 Python/CLI 入口，统一返回 `unsupported`；未执行配置、采集测量或恢复 |

公共输出含 `toolVersion`、`tool`、`status`、`issues`；首版始终 `executionAuthorized:false`、`networkChanged:false`。
`planned` 表示有离线计划，`valid_offline` 表示通过当前离线检查；进程正常退出和这两个状态均不是网络成功或执行授权。
无需 Agent SDK/MCP 才能调用；后续可薄封装这些函数或 CLI。哈希用于完整性与漂移检查，不是签名、身份认证或实时容量锁。

#### 输入、作用域与固定策略目录

`cnf-planner-state-input-v1` 显式提供方向、声明时间/TTL、容量、现有模式、原生 QoS 标志、额外竞争及两个用户/会话到 `ue1/ue2` 的绑定。
`inspect_state` 只允许 `declared_fixture`，拒绝额外竞争、未知既有队列及原生 UPF QoS-on；这不证明声明与现场一致。
状态 TTL 最长 300 秒，计划最长可启动窗口 30 秒，且启动后完整实验必须仍处于状态期限内；过期不能自动刷新时间假装重新观察。

`cnf-planner-requests-v1` 为一批两个请求，每条含请求标识、用户、会话，以及方向、最低/最高带宽、相对优先级、有效期、测量窗口。
批次明确 `protocol:UDP`、`assessmentScope:controlled_receiver_steady_mean`；首版只覆盖 C1/C2 的独立上行或下行、30 秒实验与第 5—25 秒接收端平均速率。
硬时延、丢包、完成期限、同时双向、长期目标及未提供字段不通过，不按业务类别补造 SLA。

队列目录读取 C1/C2 已通过的配置与结果，校验配置及固定策略源码与历史采集哈希一致，并绑定整个目录版本。只有以下五个模式可选：

| 模式 | 已有队列参数，单位 Mbps | 受控接收均值参考下界 |
|---|---|---|
| `equal` | 两人 `rate=4, ceil=8, prio=0` | 每人 4 |
| `guarantee-ue1/ue2` | 高侧 `6/8/0`，低侧 `2/4/1` | 高侧 5.5、低侧 1.7 |
| `priority-ue1/ue2` | 两人 `rate=2, ceil=10`，高侧优先级 0、低侧 1 | 两人仅使用保守参考 1.7，不把高侧借用容量的观测值当作保底 |

联合检查要求最低需求之和、队列预留之和分别加 0.5 Mbps 余量后不超过 10 Mbps，队列上限不高于请求上限，参考下界不低于请求最低值，优先级顺序一致。
相同优先级要求对称调度；优先级只表示此批两用户相对先后，不自动推导跨系统服务等级。
在全部可行模式中依次最小化总队列预留、总上限，再按模式名稳定选择；这是有限目录枚举，不是任意队列优化算法。
这些历史下界是限定负载中的实验门槛，不构成无条件应用/无线 SLA。

例如，两人各最低 4、上限 8、同优先级时选择 `equal`；两人各最低 6 时联合拒绝。
两人各最低 4.5 虽未超过根容量，也会因当前没有满足要求的固定模式而拒绝；这表示目录能力不足，不能推断实际网络不存在其他可行配置。
规划不会静默降低保底、提高上限、替换用户或丢弃其中一人的需求。

#### 计划绑定、演示与验证

`cnf-policy-plan-v1` 保存需求批次、状态、目录哈希，配置动作、分配、验证目标和恢复目标；动作只声明既有命名空间/端点/队列的模式，不接收任意命令或 YAML。
校验器重新检查约束和规范化描述，即使修改者重算哈希，错误队列、身份、命名空间或测量目标仍拒绝。
校验和规划共享字段/目录及约束检查函数，不应把这称为独立数学证明。实际执行仍需新鲜现场、并发预留/锁与执行前再次校验。

[`演示需求`](../results/20261020-cnf-planner/demo-requests.json) 和 [`状态声明`](../results/20261020-cnf-planner/demo-state-source.json) 是额外明确编写的工程夹具，
没有补写六条原始 D 需求；[`最终计划`](../results/20261020-cnf-planner/demo-final/plan.json)、
[`离线校验`](../results/20261020-cnf-planner/demo-final/validation.json) 与
[`执行能力返回`](../results/20261020-cnf-planner/demo-final/execution-unsupported.json) 展示完整调用边界。
演示采用明确的历史模拟时钟，不能据此声称当前会话存在或当前容量可用。

[`28 条工程场景`](../infra/cnfs/planner-fixtures.json) 的参考状态在调用外指定：上下行的等额、保障/优先级角色交换共 10 条生成并通过离线校验，另 18 条按预期拒绝或要求澄清。
[`10 类对抗检查`](../results/20261020-cnf-planner/negative-checks-final.json) 含重算哈希后的队列、用户、动作、恢复、测量目标和超额需求，全部拒绝。
[`D→E 检查`](../results/20261020-cnf-planner/incomplete-d-bridge-final.json) 将六条原始不完整输出明确分为三个测试对，全部因缺失信息拒绝，未从编号猜测用户或补齐参数。
新增 36 项 E 测试；全套 254 项中 253 通过、1 项条件 Helm 集成测试跳过。

最终证据：[`报告`](../results/20261020-cnf-planner/evaluation-02/report.json)、
[`逐场景计划与原因`](../results/20261020-cnf-planner/evaluation-02/cases.json)、
[`最终复现核对`](../results/20261020-cnf-planner/verification-final.json)、
[`测试输出`](../results/20261020-cnf-planner/tests-windows-final.txt)、
[`汇总`](../results/20261020-cnf-planner/metadata.json)。归档 14 份源码/配置/参考输入，最终报告、场景及目录可重放一致；首轮材料仍按历史保留。

下一步连接受控现场查询与绑定、执行、接收端测量和恢复。先做单一既有模式及失败/漂移路径的实测，保持当前拒绝边界，再逐步扩展目录。
E1 离线检查不等于 E 完整闭环、Agent 或新的网络 QoS 验收；路线状态统一在总路线维护。

### Agent 接入与最终交付

Agent 位于需求理解与工具协调层：理解业务描述，查询现场，调用规划器，解释方案，协调验证及后续处理。规划器负责资源计算，校验器有否决权，执行器负责实际配置，测量器返回真实结果；LLM 不逐包调度，不任意生成 YAML/命令，不凭空填写 SLA，也不以返回一段“成功”文字代替验收。

下表给出完整目标职责。E1 已实现统一合同及前三项的离线版本，后三项仅返回不支持；既有 `cnf-profile.py` 的固定执行/恢复能力尚未接入统一工具，具体实现范围见上节。

| 工具目标 | 输入与输出职责 |
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
- [部分验证，B] v4.2.2/Single-UPF 的 Session-AMBR 可安装到 QER 并出现端点上限；gNB 混杂使 UPF 独立效果未隔离，UPF 开启组吞吐退化。专用 QoS Flow、在线 PCF 策略与 NEF 引导效果仍待验证；不改变 R2.S2 的历史验收。
- [待确认] 论文最终更偏服务链选择/配置算法、意图理解，还是平台自动化；低时延 SLA 绝对阈值也仍需导师确认。
