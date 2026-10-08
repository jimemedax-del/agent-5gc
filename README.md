# 基于 Agent 的 5G 核心网 NF 编排：项目总览与当前状态

> 最后更新：2026-10-08
> 文档状态：三节点功能通过；受控数据面测试暴露 UPF OOM，吞吐批次暂停

## 项目目标

本项目拟构建一条受约束的闭环：

```text
自然语言网络需求
→ 结构化意图
→ 规则/优化规划
→ 安全校验
→ Task 与 Helm 部署
→ Kubernetes 上的 free5GC
→ UERANSIM 端到端验证与性能反馈
```

研究重点不是让大模型自由生成 YAML 或直接控制集群，而是让它理解意图、选择受控部署方案，并由确定性代码负责安全和可执行性。

当前已完成 **Docker Compose、单节点 k3s/Helm 和三节点 NF 放置业务基线**。三节点为 VM1 控制面、VM2 UPF、VM3 UERANSIM，20 轮注册和建会话均成功。受控数据面 RTT 已完成；TCP 压测触发 UPF 容器 OOM，已恢复服务，下一步先排查负载稳定性，再完成吞吐和 core/edge 对照。

## 当前结论

- [已验证] UE 已完成注册、5G AKA 鉴权、PDU Session 建立，并可经 UPF 访问公网。
- [已验证] 单节点 k3s/Helm 部署同样完成端到端业务验证，详见下文基线记录。
- [已验证] 第二台独立 VM 从零重建了该 k3s/Helm 基线；第一台保留作对照。
- [已验证] 第三台 VM 已完成 Tailscale/SSH 接入；三台 VM 位于同一 VMware `192.168.244.0/24` 网络并可直接互通。
- [已确认] 首版用户面采用 Single UPF；不把 ULCL、高可用或在线无损迁移作为 MVP 目标。
- [已确认] Agent 仅提出受控方案；规则/优化器负责计算，Go 校验器拥有否决权。
- [已验证] 三节点 Kubernetes 与 NF 放置；20/20 注册和 PDU Session 成功。受控 RTT 300/300 收到，均值 1.354 ms。
- [已发现，待定位] TCP 压测触发 UPF 的 1Gi 内存限制；服务已恢复，尚无完整 TCP 吞吐结果。
- [待验证] 长时间负载稳定性、Multus、双 UPF 与受控 core/edge 时延差异。
- [待确认] 导师所说的“NF 挑选”究竟指完整 Profile、NF 实例、NF 类型，还是主要指 NF 放置位置。

## 已固化的Chart基线

项目不复制整份上游仓库，而采用“固定提交＋最小补丁＋受控Values”的方式重建实验Chart：

```text
free5gc-helm v4.2.2
commit 0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c
+ infra/patches/free5gc-helm-v4.2.2-single-upf.patch
+ infra/free5gc-single-upf-values.yaml
+ infra/ueransim-single-node-values.yaml
```

补丁已在干净上游提交上通过正向检查，并在现有修改副本上通过反向检查。`scripts/`中的自动化工具根据人工部署记录编写，目前标记为`[待验证]`，不能替代已经记录的人工验收证据。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `docs/` | 业务心智模型、实验基线、研究路线和三节点计划 |
| `infra/` | 受控Values、测试订阅、镜像源配置和Chart补丁 |
| `scripts/` | 节点初始化、部署、订阅创建和验收工具（待实机验证） |
| `problem solve.md` | 按问题编号维护的故障、根因、修复和证据 |

临时Chart副本、SSH密钥和镜像归档不会进入Git。可审核的原始测量数据保存在 `results/`，提交前检查敏感信息；`.log` 文件默认忽略。提交规范与后续协作流程见[CONTRIBUTING.md](CONTRIBUTING.md)。

## 文档导航

| 文档 | 用途 |
|---|---|
| [01-5gc-single-upf-baseline.md](docs/01-5gc-single-upf-baseline.md) | 单机 Docker Compose 实验环境、版本、验证结果与常用命令 |
| [05-k3s-helm-single-upf-baseline.md](docs/05-k3s-helm-single-upf-baseline.md) | 单节点 k3s/Helm 的实际版本、修补、业务验收与复查命令 |
| [06-clean-vm-rebuild.md](docs/06-clean-vm-rebuild.md) | 第二台 VM 从零重建的步骤、问题与验证证据 |
| [07-three-node-topology-and-acceptance.md](docs/07-three-node-topology-and-acceptance.md) | 三节点角色、网络现状、迁移步骤和阶段验收标准 |
| [08-multinode-functional-baseline.md](docs/08-multinode-functional-baseline.md) | 20 轮注册、PDU Session 功能测量 |
| [09-controlled-data-endpoint-setup.md](docs/09-controlled-data-endpoint-setup.md) | iperf3 服务端和 UE 客户端配置 |
| [10-controlled-data-plane-baseline.md](docs/10-controlled-data-plane-baseline.md) | 受控 RTT、未完成吞吐测试及 UPF OOM 证据 |
| [02-5gc-end-to-end-flow.md](docs/02-5gc-end-to-end-flow.md) | 注册、鉴权、PDU Session 和用户数据流的心智模型 |
| [03-research-roadmap.md](docs/03-research-roadmap.md) | 研究问题、实施路线、论文方向与待确认事项 |
| [04-project-notes-method.md](docs/04-project-notes-method.md) | 后续如何维护实验记录、决策记录和文献笔记 |
| [problem solve.md](<problem solve.md>) | 持续记录真实故障、根因、完整解决流程与验证证据 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 分支、验证、提交、版本固定和敏感信息规则 |

## 维护约定

- `[已验证]`：有命令输出、日志或可重复实验作为证据。
- `[已确认]`：已作出的项目决策，但不一定已实施。
- `[待验证]`：合理假设或计划，尚未由实验确认。
- `[待确认]`：需要导师、实际环境或外部资料明确的问题。

每次环境、版本、关键配置或实验结论改变时，更新相应文档；不要把聊天记录原样堆入文档。
