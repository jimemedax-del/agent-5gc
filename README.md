# 基于 Agent 的 5G 核心网 NF 编排：项目总览与当前状态

> 最后更新：2026-10-08
> 文档状态：三节点固定部署 Profile 已完成一次受控重部署与严格业务验收；高负载限制保持归档

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

当前已完成 **Docker Compose、单节点 k3s/Helm 和三节点 NF 放置业务基线**。三节点为 VM1 控制面、VM2 UPF、VM3 UERANSIM，20 轮注册和建会话均成功。UPF 空指针补丁已部署；高负载限制保持归档，不继续追查。固定部署 Profile 已完成一次受控重部署、UE 注册、PDU Session 与隧道连通性验收，下一步可整理 NF 目录与受控规划输入，而非马上写 Agent。

## 当前结论

- [已验证] UE 已完成注册、5G AKA 鉴权、PDU Session 建立，并可经 UPF 访问公网。
- [已验证] 单节点 k3s/Helm 部署同样完成端到端业务验证，详见下文基线记录。
- [已验证] 第二台独立 VM 从零重建了该 k3s/Helm 基线；第一台保留作对照。
- [已验证] 第三台 VM 已完成 Tailscale/SSH 接入；三台 VM 位于同一 VMware `192.168.244.0/24` 网络并可直接互通。
- [已确认] 首版用户面采用 Single UPF；不把 ULCL、高可用或在线无损迁移作为 MVP 目标。
- [已确认] Agent 仅提出受控方案；规则/优化器负责计算，Go 校验器拥有否决权。
- [已验证] 三节点 Kubernetes 与 NF 放置；20/20 注册和 PDU Session 成功。受控 RTT 300/300 收到，均值 1.354 ms。
- [已发现，待定位] TCP 压测触发 UPF 的 1Gi 内存限制；服务已恢复，尚无完整 TCP 吞吐结果。
- [已验证] 后续 TCP/UDP 各 5/10/20 Mbps、每档 30 秒均完成，UDP 零丢包，无 OOM/重启，停流量后内存回落；不代表高负载或长期稳定性。
- [已发现，待定位] 50 Mbps TCP 的 10/30 秒及 100 Mbps 的 10 秒完成；100 Mbps/30 秒时 UPF 退出，未查到本次 OOM 记录，不能直接归因于 OOM。业务已恢复，详见受控数据面测试文档。
- [已验证] 带实时日志的 100 Mbps/30 秒复现，在约 12 秒时捕获 UPF `RemoteSess` 空指针崩溃、首次退出码 1；对应源码缺少空槽判空。服务已恢复；报告超时及 SEID=0 响应的触发原因仍待定位，未修改 NF 镜像或配置。
- [已验证] 上游 PR #97 已最小回移植至当前 UPF；旧代码回归测试复现 Panic，新代码通过。固定 Digest 镜像已部署，注册、会话及 TUN Ping 通过；100 Mbps 复测中 UPF 无 Panic/重启，但 SMF 重启使测试中止，不能宣称高负载通过。此问题作为限制归档，详见数据面测试模块。
- [待验证] 长时间负载稳定性、Multus、双 UPF 与受控 core/edge 时延差异。
- [待确认] 导师所说的“NF 挑选”究竟指完整 Profile、NF 实例、NF 类型，还是主要指 NF 放置位置。
- [已验证] `free5gc-3node-no-multus-v1` 锁定 Chart、配置、17 个镜像 Digest与15个工作负载；22 项单测及 1 项真实 Helm 集成测试通过。首次完整 `apply` 已通过：核心网 Revision 14、UERANSIM Revision 6，UE 注册、PDU Session 和 TUN Ping 均成功。

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
| `docs/` | 业务心智模型、实验基线、研究路线和三节点计划 |
| `infra/` | 受控Values、测试订阅、镜像源配置和Chart补丁 |
| `scripts/` | 固定 Profile 渲染/检查/重部署/验收入口；初始化脚本待实机验证 |
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
| [10-controlled-data-plane-baseline.md](docs/10-controlled-data-plane-baseline.md) | 受控 RTT、负载诊断、UPF OOM 与空指针崩溃证据 |
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
