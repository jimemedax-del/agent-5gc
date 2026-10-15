# 面向业务需求的 CNF 服务链编排

> 最后更新：2026-10-15
> 状态：R2.S1 选型已完成；R2.S2 的 M0—M3 验收通过，当前运行 `cnf-m3-v1`；下一步撤销整形差异后做 M4 放置实验。
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
| 流量识别、代理转发、可观测流量引导 | 单代理链及两类端口分类引导已验收 | 后续增加 QoS、放置与隔离效果验证 |

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
| 流量识别 | Envoy 两个受控 TCP Listener，按目的端口区分两类 iperf3 业务；访问日志记录 Listener、目标 Cluster、字节数与连接结果 | 链内分类 | [已验证，M2] 两类各 20/20；仅 L4 端口分类，HTTP Path/Header 留到 UE HTTP 客户端镜像固定后 |
| QoS 控制 | Linux `tc` TBF；由仅授予 `NET_ADMIN` 的辅助容器在后端 Pod 共享网络命名空间内配置和读取 qdisc | 链内整形 | [已验证，M3] 5/15 Mbps 各 5×15 秒下行，中位吞吐比 2.981；限于顺序单流，不解释为 5G 无线 QoS |
| 代理转发 | 单副本独立 Envoy Deployment，静态 v3 API 配置；镜像锁定版本与 Digest | 链内 L4 代理 | [已验证，M1] 单 Listener、单后端的受控上行通过；不安装 Istio、不接管其他 Pod 流量 |
| 流量引导 | Envoy Listener/Cluster 映射到两个 ClusterIP 后端；外部只暴露 Envoy NodePort | 链内选择后端 | [已验证，M2] 手工静态映射、错误路由 0；R2.S4 再接 xDS/受控规划器 |
| 边缘服务接入 | 同一 iperf3 服务镜像分别放置到 VM1 与 VM3，使用现有节点标签固定位置 | 链终点与放置变量 | [待验证] 当前三台 VM 同宿主机，不宣称 VM3 具备真实 MEC 时延优势 |
| NAT/地址转发 | 复用 UPF N6、Flannel、kube-proxy 与固定 NodePort；保存路由和转发表证据 | 平台连通底座 | [已验证，M1 计数关联] 不迁移 Cilium/Calico，不新增第二 CNI |
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
- [后续进展] M2 分类引导与 M3 `tc` 已完成，见后续章节；M4 放置及 NetworkPolicy 正反例仍待验证。M1 单次短时探测不等于 R2.S2 全阶段完成。

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

当前 M3 状态下，M1 的 `probe`、M2 的 `measure` 不适合作为 M3 验收入口；严格 5GC `verify` 仍可独立执行。
重复测量直接运行 M3 `measure`；不要重复执行要求原 M2 状态的 `deploy`。

M4 前须先撤销两侧整形差异。可生成原 M2 的两个后端清单，再审阅并应用；此路径会重建两个后端，
本轮实测的恢复范围仅为 Pod 内 qdisc 删除/重建，**尚未执行下列整套 Deployment 回退**：

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

当前保留两侧 5/15 Mbps TBF 配置。M3 已完成，R2.S2 仍需 M4 放置与 NetworkPolicy 正反例；
整套后端 Deployment 回退及跨节点放置不计入本轮已验证范围。

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
- [待验证] k3s kube-router NetworkPolicy 控制器在当前集群是否启用并正确拦截 `cnf-lab` 流量。
- [已验证，计数关联] 本轮 NodePort 路径的 UE 绑定选路、UPF NAT/接口计数、Envoy 连接及后端来源证据闭合；未进行逐流抓包或全流量绕行防护验收。
- [待验证] 现有 free5GC PCF/NEF 在 v4.2.2、Single-UPF Profile 中能否形成可测用户面效果；此项不阻塞 R2.S2。
- [待确认] 论文最终更偏服务链选择/配置算法、意图理解，还是平台自动化；低时延 SLA 绝对阈值也仍需导师确认。
