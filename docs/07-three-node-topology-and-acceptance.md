# 三节点 Kubernetes 拓扑与阶段验收

> 最后更新：2026-10-22。本文记录三节点集群、受控部署模板、可视化观测台及实际验收边界；CNF 放置与双 UE 实验通过链接引用，不改变历史单 UE Profile。

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
- [已验证，2026-10-20] 双 UE 专用实验在 VM3 新增独立身份 UE2，在 VM1/VM3 增加专用代理及按用户分开的 core/edge 后端；原固定 Profile 与 `cnf-lab/protected` 保留，当前共 24 个受监测 Pod。正式低负载并发通过；配置、结果及 UE1 前置恢复边界统一见 [A 实验](cnf-service-chain-orchestration.md#a-双-ue-低负载基线的实现与验收)，不外推为多用户 QoS 保障。

## 集群可视化观测台

适用 R2 的平台观测支撑；阶段状态以[总路线](03-research-roadmap.md)为准。代码在 [`services/cluster_dashboard/`](../services/cluster_dashboard/)，与尚未实现的 Agent 应用独立。文档沿用本模块，不新增逐次开发说明。

### 启动与使用

Windows 仓库根目录使用独立后台入口：

```powershell
python scripts/start-cluster-dashboard.py
```

启动器检查已有服务，防止重复启动；Windows 下脱离启动终端及其临时进程作业运行。主进程独立后台运行，SSH 等采集子进程使用 `CREATE_NO_WINDOW`，避免定时采集时弹出控制台。进程号和日志保存在 `.work/cluster-dashboard/runtime/process.json`、`stdout.log`、`stderr.log`。这不是带自动恢复或开机自启的 Windows 服务，系统重启或进程被停止后需再次运行该命令。原 `python scripts/cluster-dashboard.py` 仍是前台调试入口，使用时须保持终端打开。

打开 <http://127.0.0.1:8765>。默认每 15 秒经 `free5gc-vm-1` SSH 别名采集；本地使用 Python 3.10+ 与 OpenSSH，无需 Node、前端构建或额外 Python 依赖。远端需已有 `kubectl`、`python3`、可读的 `~/.kube/config`；Envoy 挂载配置解析复用 VM1 已有 PyYAML，缺失时明确报告，不自动安装。SSH 使用现有密钥，不把密码写入代码。

| 页面 | 内容与操作 |
|---|---|
| 运行总览 | 实际节点角色、IP、CPU/内存、Pod 放置数量、代理路径、计数曲线和近期事件；点击节点查看主机、内核、标签和承载资源 |
| 网络拓扑 | 基础/双 UE 业务路径、所有 Pod 按节点放置、Service→Pod 端点绑定、控制面职责参考；支持命名空间筛选、缩放、点击详情 |
| 流量实验 | 配置环境、UE、Core/Edge、上下行、目标发送速率与时长；预设/并发、启动/停止、分段进度、结果历史及 JSON 报告；按任务环境跳转拓扑 |
| 资源清单 | Pod、Service、Deployment/StatefulSet、NetworkPolicy、PVC；支持搜索、筛选与观测 JSON 导出 |
| 健康检查 | API、节点压力、Pod Ready、副本、端点、PVC、UE 隧道、网络/代理计数和 CPU/内存采集；保留异常与未知 |
| 系统导览 | 三节点分工、5GC 与 CNF 功能、连线证据、观测方式与能力边界 |

可选参数：`--ssh-host <现有别名>`、`--port 8765`、`--interval 15`（10–300 秒）。服务仅监听本机回环地址；检查 Host/Origin，发流写接口额外检查同站请求、JSON 长度和本进程令牌。健康/刷新按钮只采集；流量实验只接受下述白名单参数，不接受任意命令、端口、IP 或配置。重复刷新合并，使用采集轮次确认完成；单轮 SSH 总超时 55 秒，失败保留旧数据并显示断连/过期提示。

也可把仓库放在 VM1，运行 `python3 scripts/cluster-dashboard.py --source local`，再从本机建立 `ssh -L 8765:127.0.0.1:8765 free5gc-vm-1`。浏览器仍访问本地地址；无需对外暴露管理端口。归档回放使用 `--source replay --snapshot-file <collect.py 的原始 JSON>`，始终标记回放，保留原时间且不生成虚构速率。页面导出的是显示模型，不作为该原始回放输入。

连接超时需区分本地网站存活与远端采集成功：`/healthz` 正常不代表 SSH 或集群实时可达。2026-10-22 的排查确认管理链路间歇超时，刷新 Tailscale 套接字后仍有复发，具体触发原因待分流对照；详见[PS-005 的本轮排查](../problem%20solve.md#4-2026-10-22观测台-ssh-间歇超时暂时恢复但未根治)。

### 数据来源与可信边界

- 节点/资源来自 Kubernetes API，服务绑定来自 EndpointSlice 并核对已提供的 Pod UID。挂载配置只取当前代理 Pod 引用的 ConfigMap，排除历史闲置配置；尚未逐项核对 Envoy 进程的运行时 `config_dump`，不能将挂载声明等同已热加载配置。
- 代理业务速率来自 Envoy upstream cluster 字节差分，分别显示后端→代理、代理→后端；首轮、计数下降、Pod 替换或不合适的采样间隔显示空缺。对应速率大于零且数据新鲜时才出现方向动画；不能把接口总量或 cluster 统计外推为每个 UE 的逐包路径。
- Pod eth0 计数优先读取 kubelet `stats/summary`，缺失时降级读取 `/proc/net/dev`；CPU/内存来自 metrics-server。每个来源保留独立采样时间，指标/接口计数超过 120 秒在检查中标为未知。API 读取并非同一时刻的原子快照。
- 业务图前段新增**橙色端点活动关联**：独立读取每个 UE 的 `uesimtun*`、UPF 的 `upfgtp` 接口，以及每个代理的 downstream 计数；保留身份和采样窗口。UE→gNB 按 UE 挂载配置中的发现目标解析到当前 Ready 端点，gNB→UPF 仅在当前单 gNB/单 UPF 且全部 UE 绑定明确时关联；UPF→Envoy 要求 UPF 与该代理入口同向有活动。不会仅根据后端流量点亮前段，也不会因 UE1 活动点亮空闲 UE2。
- 橙色不是逐跳抓包或同一连接追踪，不提供推算的链路 Mbps；点击连线查看各端点独立计数和范围。N3 覆盖全部 UE/业务，N6 未按五元组匹配；并发流量下同向活动不证明同一流。UE/UPF 卡片显示的是各自接口总量，包含 TCP ACK，双向箭头不等于两个独立业务。首轮、接口/Pod 身份变化、计数重置、过期或窗口重叠不足一半均不点亮；约 15 秒采样另加采集耗时，起停显示会滞后。
- 控制面协议连线仍为架构参考。Pod Ready 不证明 NF 注册和业务成功；隧道存在不证明当前 PDU 会话可用；策略对象存在不证明拦截生效。流量实验仅增加受控 TCP 发流，不执行 Pod 重启、部署、整形或策略下发，也不宣称已实现全量流量抓包、自主识别、原生 5G QoS 或 Agent。
- 当前 SSH 用户仍使用已有集群权限；采集代码限定只读操作，实验代码额外允许自身客户端的启动/终止，并不声称已经部署专用最小权限 RBAC。采集不返回 Secret、容器环境变量或任意 ConfigMap 全文。导出含内部 IP、标签和资源状态，按项目实验材料保管。

### GUI 流量实验的参数与任务边界

- 首版只支持现有 TCP 代理。选择基础链或双用户环境、UE1/UE2、Core/Edge、上行/下行，每流目标发送速率 `0.1–4 Mbps`，任务合计不超过 `4 Mbps`，最多 4 流，持续 `10–300` 整数秒。基础链的 Core/Edge 为共享测试后端，各只允许一条流；双用户环境每个 UE 各有独立后端，同一后端也只能一条流。速率上限限定实验负载，不是带宽保障。
- 单流、双向混合、四流并发预设可以继续编辑；四流预设自动选择双用户环境。代理 NodePort、Ready 后端及 UE 当前 `uesimtun0` IP 自动解析；启动前核对当前挂载代理路由与业务标签，并按实际端点去重。尚未逐项核对 Envoy 运行时 `config_dump`，该边界与观测台一致。
- 任务运行于 VM1 的独立 Python worker，状态和分段原始结果在 `~/.work/cnf-dashboard-jobs/<任务 ID>/` 持久保存。关闭浏览器或重启本地网站后台不会主动终止任务，网站重新连接后按同一 ID 找回。网页显示最近 20 个终态任务及所有待确认任务；报告保留配置、解析后的真实地址、分段状态及收发结果。服务器端历史文件暂不自动删除。
- 每段不超过 45 秒，长任务自动重连；进度表示执行进度，吞吐只来自已完成 iperf3 分段，不能把中断分段记为零或成功。分段有空档，不作为连续连接稳定性验收；拓扑采样另有约 15 秒延迟。
- 全程持有既有 `~/.work/cnf-planner-live.lock`。停止操作设置任务取消标识，并在 UE 内按本任务标识、PID 起始时间及 Pod UID 核对客户端后终止；只有清理确认后才显示终态并释放实验锁。清理未确认时保留 `unknown`，worker 持锁重试，不开放新任务。
- 网络断连只表示状态未知，不等于任务结束。worker 被硬杀后，由重新连接的管理程序核验存活身份并尝试清理，不能保证硬杀瞬间与不经过此管理程序的外部脚本互斥；VM1 重启、原 UE Pod 身份变化等情况可能需要人工核对。面板不会为了测试自动重启任何 NF。归档回放模式禁用发流和停止真实任务。
- 代码：[`traffic.py`](../services/cluster_dashboard/traffic.py) 提供参数与任务接口，[`traffic_remote.py`](../services/cluster_dashboard/traffic_remote.py) 负责 VM1 worker；网页为 [`traffic.js`](../services/cluster_dashboard/web/traffic.js)、[`traffic.css`](../services/cluster_dashboard/web/traffic.css)。接口仅为本机 GUI/未来受控调用提供任务能力，不等同 Agent 已开发。

### 本轮验收与下一步

2026-10-21 实机采集到 3 个 Ready 节点、29 个 Ready Pod（26 个实验/核心网 Pod + 3 个系统 Pod）、30 个 Service、29 个工作负载、7 个 NetworkPolicy、1 个 PVC 和 6 条代理挂载配置路径。当前 202 次累计容器重启来自 3 个系统 Pod 的既有历史，不能解释为本轮新增重启。

- 专项自动检查覆盖计数复位/身份变化/采样过期、端点 UID、挂载关系、回放、失败保留、并发和刷新轮次、同源访问及任意输入拒绝；结果见[专项测试](../results/20261021-cluster-dashboard/unit-tests.txt)。
- 首次 1 Mbps、20 秒 TCP 上/下行短测均成功，接收约 1.001/1.003 Mbps；为核对方向动画另有短测，均持有既有实验锁，不修改队列、策略或部署。两批各自前后 Pod 身份与重启数一致；不是新的吞吐容量或 QoS 验收。
- 浏览器核对五个页面、四种拓扑、五类资源、详情、筛选、缩放和手动检查；390px 窄屏检查无页面整体横向溢出。原始观测、短测、页面截图和独立评审统一保存于[验收目录](../results/20261021-cluster-dashboard/)。
- 原版独立子智能体终评 **96.1/100**，五个维度均不低于 95；仅覆盖评审记录中的源码哈希，不包含此后新增的前段活动关联，见[评审记录](../results/20261021-cluster-dashboard/review.json)。后续 Agent 开发仍按[Agent 模块](agent-development.md)推进，不能把观测台算作 Agent 运行骨架。
- 前段活动关联补验：27 项自动检查通过；真实 UE1 绑定 `10.60.0.14`，经基础服务链到 Core 的 1 Mbps、30 秒 TCP 上/下行均成功，接收约 **0.999 / 1.005 Mbps**。两次均观测到 UE1 接入/N3/基础 N6 活动、UE2 空闲，停止后全部前段恢复空闲；29 个 Pod 身份和累计重启不变。新增第 11 项健康检查为独立接口有效双次采样。证据见[活动关联验收](../results/20261021-cluster-dashboard/endpoint-activity/)，不扩展为逐包路径或容量/QoS 验收。
- 2026-10-22 双 UE 混合流量检查：在 `cnf-multi-ue` 连续执行 A（UE1/Core 上行 + UE2/Edge 下行）、B（增加 UE1/Edge 下行 + UE2/Core 上行，共四流）、C（仅 UE2/Core 下行 + UE2/Edge 上行）三个阶段，各 45 秒、每流 1 Mbps TCP，最后空闲观察 35 秒。8 次传输全部成功，实际接收 **1.001–1.002 Mbps/流**；完整采样窗口内，活动端口及主方向符合预期，非活动端口计数为零，C 阶段 UE1 恢复空闲，收尾全部连线恢复空闲。29 个 Pod 身份、就绪状态与累计重启不变。全程持有既有实验锁，无部署/队列/策略修改；只证明此次最大四流、总负载约 4 Mbps 的路径和观测行为，不证明拥塞优先级、丢包/时延保障或原生 5G QoS。入口为 [`cluster-dashboard-traffic-test.py`](../scripts/cluster-dashboard-traffic-test.py)，原始结果、采样与画面见[混合流量证据](../results/20261022-cluster-dashboard-mixed-traffic/)；复测需指定新的输出目录：`python scripts/cluster-dashboard-traffic-test.py --output results/<新的实验目录>`。观察时选择“网络拓扑 → 双用户实验”；总览固定展示基础服务链，不能用总览后端连线判断另一环境的流量。
- GUI 面板验收：**45 项自动检查通过**；真实 GUI 单流 10 秒约 1.006 Mbps，四流 90 秒/两段的 8 条结果均成功（约 1.000–1.005 Mbps），网站后台重启后同一任务继续执行；专用 300 秒双流任务在约 35 秒提前停止，两个 UE 的本任务进程另行核验均退出。持外部实验锁时 GUI 拒绝启动，未新增任务；基础链 Edge 下行约 1.009 Mbps，GUI 下载报告及部署后缓存导出通过。测试前曾受控恢复 gNB/两个 UE，见 [PS-009](../problem%20solve.md#ps-009隧道存在但接入链路不通)；正式起点后 29 个 Pod 身份和重启数不变，不外推为全程未重建。
- 两个独立子 agent 分别给出**功能性 96.3/100、美观度 96.0/100**；审核覆盖当前源码、真实运行/停止/完成/断连及 390px 窄屏。评分为专家审核，不等同用户研究或 QoS 性能结论。源码哈希、验收清单与原始证据见[面板验收](../results/20261022-cluster-dashboard-traffic-panel/validation.json)、[审核记录](../results/20261022-cluster-dashboard-traffic-panel/review.json)。Tailscale SSH 在本轮有间歇超时，面板保留已确认结果、显示断开并禁发新流；未宣称该外部网络问题已消除。

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

2026-10-16 跨平台复测已将入口的 Git 权限固定为 `100755`，并通过 `.gitattributes` 固定 Python 文件为 LF，保证 Linux 上 Helm 可直接执行 post-renderer；从修正后的 Git 树导出源码，真实 Helm 集成测试通过。未保留执行权限的复制方式仍需上面的 `chmod`。失败与修复证据见 [PS-009](../problem%20solve.md#ps-009windows-导出源码后的-helm-post-renderer-启动失败)。

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

R2.S2 的 M4 已在独立 `cnf-lab` 中验证：VM1 运行 Envoy 与 core 后端，VM3 在 UERANSIM 之外增加 edge 后端。
原有 5GC 固定 Profile 的角色与放置不变，UPF 仍在 VM2；此扩展不是 edge-UPF 迁移。
配置与证据统一维护在 [M4 模块章节](cnf-service-chain-orchestration.md#m4-后端放置与跨节点路径实验)。

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
