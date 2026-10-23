# 基于 Agent 的 5G 核心网 NF 编排：项目总览与当前状态

> 最后更新：2026-10-23
> 文档状态：Agent F1 离线骨架已实现；真实模型、实网接入及完整编排尚未完成，R2.S4 既有验收边界保持

**集群可视化入口：** 在仓库根目录运行 `python scripts/start-cluster-dashboard.py`，独立后台启动后打开 <http://127.0.0.1:8765>。本地页面通过现有 SSH 读取 VM1，展示真实节点/Pod、业务路径、服务绑定、资源与健康检查；“流量实验”面板提供受控 TCP 参数配置、并发发流、停止与分段结果。运行方式和观测边界见[可视化观测台](docs/07-three-node-topology-and-acceptance.md#集群可视化观测台)。它是独立观测支撑；Agent 当前仅有 F1 离线骨架，真实模型与实网工具待接入。

## 当前路线与阶段

**当前：Agent F1 离线骨架已实现。** 已有合同、状态机、预算、会话记忆、轨迹、CLI 与回放模拟器；启动和验证见 [F1 使用说明](docs/agent-development.md#6-f1-使用与验证)。后续路径为 F2 真实模型 → F3a 三模式工具接入 → F3b 需求驱动的后端绑定与带宽组合配置 → F4 反馈/对照。模拟和三模式仅为过渡，组合能力仍待实现和专项验收；阶段状态以总路线为准，底层接口随具体任务补齐。

**固定配置的小实验已补齐联合验收。** 同一跨节点路径、Envoy 预设端口分类/代理/引导及保护策略下，22 次 TCP 下行业务全部通过：无整形两类约 20.054 Mbps，5/15 Mbps 整形后约 4.823/14.377 Mbps，撤销后恢复约 20.052/20.053 Mbps。44 条代理连接均正确，正式期 26 个 Pod 稳定，准备/恢复只受控重建两侧后端，其他 24 个 Pod 全程不变。原保护配置与严格基线已恢复；自主识别、原生 5G QoS 和 Agent 仍未完成，见 [联合检查](docs/cnf-service-chain-orchestration.md#固定配置联合功能检查)。

**当前：R2.S4 的 E3 已能根据完整结构化请求选择并执行下行策略。** 开放等额、保障 UE1、保障 UE2 三个既有模式；四组合法请求的实际接收目标均通过，保障对象交换后速率由约 7.725/1.929 Mbps 变为 1.929/7.725 Mbps。四组不完整、旧会话、超额或不支持目标的请求按预期澄清/拒绝，没有下发配置；各组恢复及严格复查通过。工具、请求实例与证据见 [E3 模块](docs/cnf-service-chain-orchestration.md#e3-完整请求与受控策略选择)。

**2026-10-21 补充现场异常验收。** 真实计划过期、专用队列变化、部分写入失败、接收进程中断四项均按预期拒绝或恢复；失败运行保留失败状态，每项严格收尾通过。最后正常对照约 4.824/4.824 Mbps，正式起点后 26 个 Pod 不变；测试前曾恢复两个已有隧道故障的 UE，不能外推为跨日未重建。详见 [异常验收](docs/cnf-service-chain-orchestration.md#e2-的现场异常验收与恢复)。

E1 保留离线入口及其 `unsupported` 执行返回；28 个工程场景中，10 个计划合法、18 个拒绝或澄清。E2 固定等额入口保持，E3 使用独立合同接收完整请求；二者均没有补写六条 D 需求或完成自主识别。后续 Agent 可复用这些工具，限定原型可先只开放已验证模式；泛化执行和完整编排仍待验证。

A 已验收：2026-10-20 两个独立 UE 完成正式 20 组/40 次低负载 TCP 并发会话，80 条正确代理连接，最小有效流量保守重叠下界 9.000034 秒。原 `cnf-lab/protected` 保留，另有专用 `cnf-multi-ue` 夹具和 UE2；正式起点后的原有 18 个 Pod 身份不变，共 24 个 Pod 稳定、重启为 0。测试前 UE1 故障与恢复单独归档，不能外推为全程未重建。

**B 首轮结论：Session-AMBR 可下发，但当前 UPF QoS 开启组不可作为已验收能力。** 26 次正式观测中，临时 2/4 Mbps 策略在开关关闭时已有会话限速；开启后上/下行中位数降至 0.054/0.445 Mbps，再次关闭后恢复。已还原原订阅和开关，24 个 Pod 未重启。证据与边界见 [B 能力探针](docs/cnf-service-chain-orchestration.md#b-5g-qos-能力探针)。

**C1：容器化共享瓶颈下行竞争已验收。** 双 UE、10 Mbps 共享根队列完成正式 20 组/40 次 UDP 会话；共享/等额约每人 4.83 Mbps，保障及优先级组约 7.73/1.93 Mbps，交换用户后效果随配置交换。带宽及窗口诊断目标通过，RTT/丢包仅作观测；原 24 个加新增测试 Pod 均未重启，队列已撤销。实现、其他用户代价与证据见 [C 模块](docs/cnf-service-chain-orchestration.md#c-共享瓶颈竞争容器化调度与受控需求)。

**C2：上行带宽分配与优先级也已验收，并发现 RTT 反例。** 独立容器入口共享队列完成完整 20 组/40 会话，带宽效果与角色交换成立。首个不完整批次中，低分配用户探针丢失 73.33%，已收到回复的全程 RTT 均值约 208 ms；该反例单独保留，后续完整批次不覆盖它。26 个 Pod 未重启，入口规则和排队接口已撤销。详见 [C2 上行结果](docs/cnf-service-chain-orchestration.md#c2-上行竞争入口-ifb-与服务端接收证据)。

**D2 已完成六条候选合成场景的 Agent 自校验和受控回归。** 修复原规则的业务目的、服务时长、完成期限及混合子任务作用域遗漏后，六条类别/拒判决策和 8 个明确目标均与冻结参考一致；原 36 条开发样例复测为 33 条类别一致、3 条仍拒判。样例已用于调试，只是开发检查，不能称为独立识别准确率。工程推进无需另建真实平台或等待人工标签；后续研究泛化与应用效果仍需更强证据。校验、修复和可继续推进的范围见 [需求识别模块](docs/cnf-service-chain-orchestration.md#业务需求识别的输入与验收)。

C1/C2 使用显式端点标签和容器内 `tc`；独立方向带宽控制不等于时延/丢包保障、同时双向竞争、自主流量识别或 PCF/QFI 驱动控制。原生 UPF 策略仍须处理 B 的退化；完整 E 尚未验收。阶段状态以 [研究路线](docs/03-research-roadmap.md#r2-当前阶段顺序) 为准。

| 路线 | 推进顺序与定位 | 入口 |
|---|---|---|
| R1（历史，底座成果已继承） | Compose → 单节点/空白 VM 重建 → 三节点验收 → 固定 Profile；后续部署编排研究计划已调整 | [历史里程碑](docs/03-research-roadmap.md#r1-历史里程碑与成果入口) |
| R2（当前） | S1 能力盘点与选型 → S2 手工最小实验 → S3 可重复部署与测量 → S4 确定性编排 → S5 Agent | [当前阶段表](docs/03-research-roadmap.md#r2-当前阶段顺序) |

阶段状态和纠偏依据统一维护在 [研究路线](docs/03-research-roadmap.md)。模块文档按职责维护；新路线继承可用成果，文件名不随阶段重新编号。

## 项目目标

本项目拟构建一条受约束的闭环：

```text
业务需求
→ 结构化意图与受控规划
→ 5GC 开放能力和 CNF 服务链配置
→ Kubernetes 上的 free5GC 与新增 CNF
→ UE 端到端验证与性能反馈
```

研究重点不是让大模型自由生成 YAML 或直接控制集群，而是让它理解业务意图，在确定性约束下规划、配置并验证可编排网络功能服务链。

当前已完成 **Docker Compose、单节点 k3s/Helm 和三节点 NF 放置业务基线**。三节点为 VM1 控制面、VM2 UPF、VM3 UERANSIM，20 轮注册和建会话均成功。UPF 空指针补丁已部署；高负载限制保持归档，不继续追查。R2.S2 能力实验与 R2.S3 固定 CNF Profile 的部署、恢复及重复测量均已通过，详见 [CNF 服务链编排模块](docs/cnf-service-chain-orchestration.md)。

## 当前结论

- [已验证] UE 已完成注册、5G AKA 鉴权、PDU Session 建立，并可经 UPF 访问公网。
- [已验证] 单节点 k3s/Helm 部署同样完成端到端业务验证，详见下文基线记录。
- [已验证] 第二台独立 VM 从零重建了该 k3s/Helm 基线；第一台保留作对照。
- [已验证] 第三台 VM 已完成 Tailscale/SSH 接入；三台 VM 位于同一 VMware `192.168.244.0/24` 网络并可直接互通。
- [已确认] 首版用户面采用 Single UPF；不把 ULCL、高可用或在线无损迁移作为 MVP 目标。
- [已确认] Agent 仅提出受控方案；规则/优化器负责计算，Go 校验器拥有否决权。
- [已验证] 三节点 Kubernetes 与 NF 放置；20/20 注册和 PDU Session 成功。受控 RTT 300/300 收到，均值 1.354 ms。
- [已记录，归档限制] TCP 压测曾触发 UPF 的 1Gi 内存限制；服务已恢复，尚无完整 TCP 吞吐结果。
- [已验证] 后续 TCP/UDP 各 5/10/20 Mbps、每档 30 秒均完成，UDP 零丢包，无 OOM/重启，停流量后内存回落；不代表高负载或长期稳定性。
- [已记录，归档限制] 50 Mbps TCP 的 10/30 秒及 100 Mbps 的 10 秒完成；100 Mbps/30 秒时 UPF 退出，未查到本次 OOM 记录，不能直接归因于 OOM。业务已恢复，详见受控数据面测试文档。
- [已验证] 带实时日志的 100 Mbps/30 秒复现，在约 12 秒时捕获 UPF `RemoteSess` 空指针崩溃、首次退出码 1；对应源码缺少空槽判空。服务已恢复；报告超时及 SEID=0 响应的触发原因尚未证明，且不再继续追查。
- [已验证] 上游 PR #97 已最小回移植至当前 UPF；旧代码回归测试复现 Panic，新代码通过。固定 Digest 镜像已部署，注册、会话及 TUN Ping 通过；100 Mbps 复测中 UPF 无 Panic/重启，但 SMF 重启使测试中止，不能宣称高负载通过。此问题作为限制归档，详见数据面测试模块。
- [待验证] 长时间负载稳定性、Multus、双 UPF 与受控 core/edge 时延差异。
- [已确认] 研究方向扩展为：以现有 5GC 为接入和用户面底座，构建可编排 CNF 服务链；SMF/UPF 实例选择保留为后续扩展能力。
- [部分验证，B] Session-AMBR 订阅可经 PCF/SMF 建会话后安装到 QER，并出现端点限速；gNB 自身执行 AMBR，UPF 开启组发生吞吐退化、未通过验收。专用 QoS Flow、在线策略、NEF 引导和用户保底仍待验证。
- [已验证] `free5gc-3node-no-multus-v1` 锁定 Chart、配置、17 个镜像 Digest与15个工作负载；22 项单测及 1 项真实 Helm 集成测试通过。首次完整 `apply` 已通过：核心网 Revision 14、UERANSIM Revision 6，UE 注册、PDU Session 和 TUN Ping 均成功。
- [已确认] R2.S1 首版选型为独立 Envoy + Linux `tc` + k3s 原生 Service/NetworkPolicy；先做 L4 端口分类、代理、引导和 5/15 Mbps 受控差异，不迁移 CNI。
- [已验证] `cnf-lab` 中单个 Envoy 与 iperf3 3.9 后端完成 UE→UPF→代理→后端探测，控制/数据连接、后端来源与 UPF 计数证据齐全，收尾严格 5GC 验收通过；原有 15 个 Pod UID 未变、重启数仍为 0。
- [已验证] M2 两类端口业务各 20/20 命中预期后端，40 次独立定时会话、80 条代理 TCP 连接，错误路由为 0；原有 5GC 状态稳定，收尾严格验收通过。
- [已验证] M3 同节点 5/15 Mbps TBF 下行控制通过；10 轮全部完成，qdisc 计数和路径证据完整，原有 15 个 5GC Pod 身份不变、重启为 0，收尾严格验收通过。
- [已验证] M4 先撤销两侧整形，再仅将 edge 后端移到 VM3；两类业务上下行各 5 次，共 20 次正式会话、40 条正确代理连接；原有 5GC 与 Envoy 稳定，收尾严格验收通过。
- [已验证] NetworkPolicy 的无策略/加策略/撤销/重加对照通过；最终 15 次允许、30 次拒绝均符合预期，DNS 和 20 次 UE 业务回归通过。两条策略保留，临时测试 Pod 已删除；原有 18 个 Pod 身份不变、重启为 0。
- [已验证] R2.S3 的统一 Profile 覆盖四种固定状态，部署/重建/快照恢复/受控失败回滚及重复 apply 通过。两批独立保护状态业务和整形复测共 50 个唯一 Cookie、100 条正确代理连接；原有 15 个 5GC Pod 全程不变，Service/VIP/NodePort 保留，最终重启数为 0；58 项单测通过。
- [边界] M3/M4/NetworkPolicy 不代表无线 QoS、并发隔离、最大容量、真实 edge 时延优势或全来源绕行防护。

## 已固化的Chart基线

项目不复制整份上游仓库，而采用“固定提交＋最小补丁＋受控Values”的方式重建实验Chart：

```text
free5gc-helm v4.2.2
commit 0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c
+ infra/patches/free5gc-helm-v4.2.2-single-upf.patch
+ infra/free5gc-single-upf-values.yaml
+ infra/ueransim-single-node-values.yaml
```

2026-10-08 修正 CHF 补丁上下文后，干净提交复建及反向检查通过。三节点统一入口为
[free5gc-profile.py](scripts/free5gc-profile.py)，输入为[固定 Profile](infra/profiles/free5gc-3node-no-multus-v1.json)。
渲染、重复性、真实 post-renderer、现有环境核对及一次完整重部署已验证；空白 VM 初始化脚本仍待验收。
使用方法与边界见[三节点模块](docs/07-three-node-topology-and-acceptance.md#受控可重复部署模板)。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `docs/` | 业务心智模型、实验基线、研究路线、三节点计划和 CNF 服务链设计 |
| `infra/` | 受控Values、测试订阅、镜像源配置和Chart补丁 |
| `services/cnf_agent/` | F1 离线运行骨架、回放模型、工具模拟器、SQLite 记忆与 CLI；真实适配待接入 |
| `services/cluster_dashboard/` | 本地集群观测台：Python 采集/API 与原生 HTML/CSS/JavaScript 页面 |
| `scripts/` | 固定 Profile 渲染/检查/重部署/验收入口；初始化脚本待实机验证 |
| `problem solve.md` | 按问题编号维护的故障、根因、修复和证据 |

临时Chart副本、SSH密钥和镜像归档不会进入Git。可审核的原始测量数据保存在 `results/`，提交前检查敏感信息；`.log` 文件默认忽略。提交规范与后续协作流程见[CONTRIBUTING.md](CONTRIBUTING.md)。

## 文档导航

| 文档 | 用途 |
|---|---|
| [03-research-roadmap.md](docs/03-research-roadmap.md) | 路线版本、纠偏依据、历史里程碑和当前阶段顺序的主记录 |
| [cnf-service-chain-orchestration.md](docs/cnf-service-chain-orchestration.md) | 已验证 CNF 能力、实验与底层规划/执行接口 |
| [agent-development.md](docs/agent-development.md) | Agent 开发主文档：目标、选型、代码分工、工具/记忆边界、开发顺序与验收 |
| [04-project-notes-method.md](docs/04-project-notes-method.md) | 路线、阶段、模块和实验记录的维护方法 |
| [01-5gc-single-upf-baseline.md](docs/01-5gc-single-upf-baseline.md) | 单机 Docker Compose 实验环境、版本、验证结果与常用命令 |
| [02-5gc-end-to-end-flow.md](docs/02-5gc-end-to-end-flow.md) | 注册、鉴权、PDU Session 和用户数据流的心智模型 |
| [05-k3s-helm-single-upf-baseline.md](docs/05-k3s-helm-single-upf-baseline.md) | 单节点 k3s/Helm 的实际版本、修补、业务验收与复查命令 |
| [06-clean-vm-rebuild.md](docs/06-clean-vm-rebuild.md) | 第二台 VM 从零重建的步骤、问题与验证证据 |
| [07-three-node-topology-and-acceptance.md](docs/07-three-node-topology-and-acceptance.md) | 三节点角色、网络现状、迁移步骤和阶段验收标准 |
| [08-multinode-functional-baseline.md](docs/08-multinode-functional-baseline.md) | 20 轮注册、PDU Session 功能测量 |
| [09-controlled-data-endpoint-setup.md](docs/09-controlled-data-endpoint-setup.md) | iperf3 服务端和 UE 客户端配置 |
| [10-controlled-data-plane-baseline.md](docs/10-controlled-data-plane-baseline.md) | 受控 RTT、负载诊断、UPF OOM 与空指针崩溃证据 |
| [problem solve.md](<problem solve.md>) | 持续记录真实故障、根因、完整解决流程与验证证据 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 分支、验证、提交、版本固定和敏感信息规则 |

## 维护约定

- `[已验证]`：有命令输出、日志或可重复实验作为证据。
- `[已确认]`：已作出的项目决策，但不一定已实施。
- `[待验证]`：合理假设或计划，尚未由实验确认。
- `[待确认]`：需要导师、实际环境或外部资料明确的问题。

每次环境、版本、关键配置或实验结论改变时，更新相应文档；不要把聊天记录原样堆入文档。
