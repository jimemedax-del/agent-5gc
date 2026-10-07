# 研究路线：基于 Agent 的 5G 核心网 NF 编排

> 最后更新：2026-10-07
> 状态：研究设计初稿；其中未落地部分均为计划，不代表已实现。

## 当前研究定位

目标是面向自然语言网络需求，构建一套安全、可解释、可验证的 5GC 部署编排闭环：

```text
网络需求
→ NetworkIntent
→ 规则/优化规划器
→ CoreNetworkPlan
→ 确定性校验器
→ Task / Helm
→ Kubernetes / free5GC
→ UERANSIM 与性能反馈
```

核心分工：

- **Agent**：理解自然语言、生成结构化意图、选择受控 Profile、解释与协调重规划。
- **规则/优化规划器**：根据资源、网络和业务约束计算放置方案。
- **Go 校验器**：检查版本、依赖、资源、网络能力和安全白名单；有最终否决权。
- **Task 与 Helm 适配器**：排队、审计、幂等、部署、回滚与生命周期管理。
- **free5GC/UERANSIM**：真实运行与端到端业务验证。

Agent 不直接参与 5G 信令，不直接执行 Helm/Kubernetes，不生成任意 YAML、镜像地址或 Helm Values。

## 当前阶段

当前进度：Compose与单节点k3s/Helm基线已完成；Chart已使用固定提交、正式补丁和受控Values固化。三节点Flannel单网络基线是当前实施阶段，Agent尚不进入编码阶段。

- [已验证] Docker Compose 与单节点 k3s/Helm 的 Single UPF 业务闭环；k3s 环境重启后可自动恢复。
- [已验证] 第二台独立 Ubuntu VM 从零重建了相同版本的 k3s/Helm/Single UPF 基线，并完成 UE 端到端出网。
- [已验证] 第三台空白 Ubuntu VM 已创建并完成 SSH 接入；三台 VM 的 VMware 局域网互通正常。
- [进行中] 固化 Chart 补丁并将现有环境迁移为一台 Server、两台 Agent 的三节点集群。
- [尚未开始] 多节点 Kubernetes、Go Task 平台集成、规划算法、Agent 工作流。

## 为什么不能停在单 UPF

单 UPF 场景中的“低时延业务就选 edge UPF”只是直觉规则，选择空间很小。若仅实现“自然语言 → Agent → Helm → free5GC”，工程价值存在，但学术创新不足，难以作为高质量小论文的主要贡献。

更合理的研究问题是：

> 在多业务意图、多个候选 UPF、有限节点资源与不同网络路径条件下，如何生成满足硬约束、兼顾时延与成本/负载的 5GC 部署方案，并安全地落地验证？

这要求后续至少具备双 UPF、多个节点和可测量的网络差异。

## 建议实施顺序

### 1. 基线可复现

- 固化 Compose、free5GC、UERANSIM、gtp5g 与配置版本。
- 从干净环境重现注册、PDU Session、用户面联通。
- 学会观察 AMF/SMF/UPF 日志和 PFCP/gtp5g 动态规则。

### 2. Kubernetes 与多节点网络

- [已验证] 固定Single UPF Profile已经部署至单节点k3s，并完成空白VM复现和重启恢复测试。
- [进行中] 在同宿主机三台VM上建立Server、core/upf与ran-test等逻辑角色。
- 后续引入 Multus、第二网卡和实际 N6 网络；通过真实拓扑或受控仿真构造 edge/core 路径差异。

### 3. NF 目录与确定性规划

- 固定完整核心网 Profile，不允许 Agent 删除 AMF、SMF、UPF、NRF、AUSF、UDM、UDR 等依赖。
- 建立 `standard-core-upf` 与 `low-latency-edge-upf` 等受控 Profile。
- 在 Agent 之前实现规则规划器和安全校验器。
- 将 `placementClass` 映射为平台批准的节点标签、Affinity 和 Helm Values，禁止用户或模型注入任意 YAML。

### 4. Task、Helm 与 Agent

- 在获得完整 Go 平台源码后，扩展网络服务 Task，解决长生命周期服务的状态、幂等、资源记账、超时、回滚和卸载。
- 使用 Go Helm SDK 操作固定 Chart。
- 数据结构稳定后，再以 Python 函数或 LangGraph 实现 Agent 工作流：解析 → 查目录/容量 → 规划 → 校验 → 最多两次重规划 → 创建 Task → 监控。

## 论文价值的最低门槛

论文的主要贡献应是“受约束的意图驱动 NF 放置/编排方法”，而不是“调用了一个大模型”。建议包含：

1. **问题建模**：硬约束（资源、依赖、网络能力）与偏好（低时延、边缘部署）的区分。
2. **方法设计**：资源与时延感知的规则/优化算法；Agent 仅提供语义理解与可解释协调。
3. **安全机制**：目录白名单、结构化输出、确定性校验和失败反馈。
4. **真实验证**：在 free5GC、UERANSIM、Kubernetes 上验证，而不止于纯仿真。
5. **对照实验**：人工固定部署、规则法、贪心/优化基线、Prompt-only LLM，以及完整方案。

可比较的指标包括：计划合法率、部署成功率、Task 到 NF Ready 的时间、UE 注册/PDU Session 成功率、RTT、吞吐量、资源利用率、规划时延和模型调用成本。

## WirelessAgent 文献带来的启发

Tong 等 *WirelessAgent: Large Language Model Agents for Intelligent Wireless Networks*（2025）说明了“LLM + 知识库 + 外部工具”的结构化网络智能体思路。

- [可借鉴] Perception—Memory—Planning—Action 职责划分、Global State、知识库、工具调用轨迹、Prompt-only 与规则方法对照。
- [不可照搬] 该文主要处理切片带宽分配和仿真场景，没有解决 Helm、Kubernetes、gtp5g、Multus、回滚与真实 5GC 部署约束。
- [项目影响] Agent 应使用固定工作流与结构化输出；专业计算和安全约束由规划器、校验器承担。

## 待确认事项

- [待确认] 导师的“NF 挑选”到底要求选择 NF 类型、NF 实例、完整 Profile，还是放置位置。
- [待确认] 论文更偏意图理解、NF 放置算法，还是平台自动化。
- [待确认] 是否需要动态扩缩容、故障恢复、在线重编排、多 UPF 或切片竞争。
- [待确认] 低时延 SLA 的阈值、可用网络拓扑和评价方法。
- [待确认] 多节点实验使用的主机资源、VMware 网络权限与可用网卡。
