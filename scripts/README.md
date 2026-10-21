# 自动化脚本

> 状态：**完整空白VM自动化待实机验证**；三节点 Profile 的干净复建、重复渲染、真实 Helm post-renderer、完整重部署及严格业务验收已通过。不得把“已有集群重部署通过”表述为“空白VM自动化已验证”。

这些脚本用于自动复现已经人工验证过的单节点基线，并为三节点集群提供统一入口。当前项目的正式可复现核心是固定上游提交、受控补丁和Values；脚本是辅助工具。

## 当前三节点固定 Profile 入口

`free5gc-profile.py` 需要 Python 3.10+、PyYAML、Git、Helm 3 和 kubectl。在 Linux VM1 仓库根目录运行，先设置 `KUBECONFIG` 并 `chmod +x scripts/free5gc-profile.py`。

| 命令 | 行为 |
|---|---|
| `render` | 验证输入，克隆固定 Chart、应用补丁、lint、生成 Digest 锁定清单 |
| `check` | 上述渲染加三角色节点唯一性、架构、Ready、调度条件与存储检查 |
| `compare-current` | 上述检查加与当前两份 Helm manifest 的全量语义对比（只规范化镜像） |
| `verify --allow-tag-baseline` | 检查现有 Tag 基线实际 Digest、放置、业务日志和 TUN Ping |
| `apply --confirm-disruption` | 现有两 Release 受控重部署；停止旧会话，稳定化 NRF 注册，部署并验收 |
| `verify` | 严格验收，要求 live spec 使用 Profile Digest |
| `image-commands --role user` | 仅打印指定角色的 containerd Digest 别名命令 |
| `post-render` | Helm SDK/CLI 的固定渲染入口，只从 stdin 处理受控清单 |

运行示例：`python3 scripts/free5gc-profile.py render`。参数与边界统一维护在
[三节点模板模块](../docs/07-three-node-topology-and-acceptance.md#受控可重复部署模板)，不在这里复制安装流程。
该工具不初始化集群、导入镜像、重写订阅或清空数据库，也不全面检测可用容量/网络/内核前提；`check` 不是完整环境准入器。
2026-10-08 已完成一次 `apply`：核心网 Revision 14、UERANSIM Revision 6，严格验收通过。该次发现并修正了两项前提：本地 UE 镜像需要 Digest 别名；核心 NF 在 NRF/MongoDB 就绪后需按依赖顺序重新注册。结果见[三节点模板模块](../docs/07-three-node-topology-and-acceptance.md#重部署语义与验证结果)。

单元测试：`python -m unittest discover -s tests -p test_free5gc_profile.py -v`。
Linux 真实 Helm 集成测试再设置 `F5GC_INTEGRATION_CHART_SOURCE=<Chart Git仓库>` 与
`F5GC_INTEGRATION_OUT=<独立输出目录>`；只克隆/渲染，不操作集群。

## CNF M0/M1 固定实验入口

`cnf-m1.py` 只处理 `infra/cnfs/m1-proxy.yaml` 的单代理/单后端实验。`render` 不访问集群；
`m0` 严格复查既有 5GC；`deploy` 创建独立 `cnf-lab` 资源；`probe` 运行 5 秒、5 Mbps TCP
兼容性探测并关联 UE 选路、UPF 计数、Envoy 和后端证据。集群命令要求 `--out <新的结果目录>`。
M0、M1 部署及端到端探测已在 VM1 验证（`cnf-m1-v3`）；日志等待上限为 15 秒。镜像导入、执行顺序、清理与局限见
[CNF 模块](../docs/cnf-service-chain-orchestration.md#m0m1-执行入口与验证记录)。

证据判定测试：`python -m unittest discover -s tests -p test_cnf_m1.py -v`。

## CNF M2 分类与引导入口

`cnf-m2.py` 以固定 M1 资产和 `infra/cnfs/m2-experiment.json` 渲染双 Listener / 双后端实验；
两后端同放 VM1、同镜像和资源，按 TCP NodePort `31201/31202` 区分 core/edge 逻辑业务。
命令为 `render`、`deploy`、`measure`，均要求 `--out <新的结果目录>`；
`measure --smoke` 只做各一次探测，`measure` 才执行各 20 次的正式验收。
当前配置 `cnf-m2-v4` 使用 1 秒、1 Mbps 参数、16KiB 块长的定时会话，要求收发一致且非空；
逐轮同时验证代理两连接、目标 Cookie、另一后端不误收及 UPF 计数，失败即停。
原固定字节数模式的失败记录保留，不能混入正式数据。运行 M2 时使用 M2 入口进行业务测量。
2026-10-15 的正式 v4 测量已通过：两类各 20/20、错误路由 0、收尾严格 5GC 验收通过。
实现、结果与恢复 M1 的方法见 [CNF 模块](../docs/cnf-service-chain-orchestration.md#m2-分类与引导实验)。

路由证据测试：`python -m unittest discover -s tests -p test_cnf_m2.py -v`。

## CNF M3 下行整形入口

`cnf-m3.py` 读取 `infra/cnfs/m3-experiment.json`，沿用 M2 路由，只修改同节点两后端，
各增加同镜像、同资源的 `tc` 辅助容器，在 Pod 内 `eth0` 配置 5/15 Mbps TBF。
命令为 `render`、`preflight`、`deploy`、`measure`，均要求 `--out <新的结果目录>`。
`preflight` 在原 M2 状态做各一次 5 秒下行探测；`deploy` 包括无流量的 qdisc 撤销/重建检查；
`measure --smoke` 只做各一轮，`measure` 执行各 5 轮、每轮 15 秒下行，统一使用 20 Mbps 发送上界。
正式数据按 UE 接收端统计判定，检查下行代理字节、后端 Cookie、UPF 计数及 TBF 活动。
512MiB UPF 内存阈值、重启/身份变化、超时或采集失效均停止批次，失败目录不补测覆盖。

2026-10-15 的 `cnf-m3-v1` 正式验收通过：中位数 4.823/14.376 Mbps，比值 2.981。
`audit-cnf-m3.py <固定结果目录> --out <新的JSON路径>` 对已归档的 `measure-01` 与同目录部署/预检材料独立离线复核；不访问集群。
M1—M3 证据判定测试：`python -m unittest discover -s tests -p 'test_cnf_m*.py' -v`。
配置、权限、恢复边界和结果见 [M3 模块章节](../docs/cnf-service-chain-orchestration.md#m3-同节点下行整形实验)。

## CNF M4 后端放置入口

`cnf-m4.py` 读取 `infra/cnfs/m4-experiment.json`，命令为 `render`、`normalize`、
`measure-neutral`、`place`、`measure`，均要求 `--out <新的结果目录>`。
`normalize` 只接受 M3 起点，去除两侧整形辅助容器，恢复同节点无整形后端；
`measure-neutral` 验证该对照的两类上下行各一次；`place` 再只修改 edge 的节点选择到 VM3。
`measure --smoke` 验证迁移后的 4 次会话，`measure` 执行两类上下行各 5 次、每次 5 秒/1 Mbps。
验证实际 Pod/Endpoint/EndpointSlice 的 UID、节点和 Ready，逐轮关联 UPF、代理方向和后端 Cookie，
护栏与失败即停策略沿用 M3。重复测量直接使用 `measure`，不要重跑要求 M3 起点的 `normalize`。

2026-10-15 `cnf-m4-v1` 正式验收通过：core 在 VM1、edge 在 VM3，20/20 会话、40 条正确代理连接，错误路由 0。
`audit-cnf-m4.py <结果目录> --out <新的JSON路径>` 离线核对 `measure-01` 与同目录的对照/部署证据；
可用 `--batch` 指定其他正式批次名。M1—M4 共 41 项单测通过。
执行顺序、恢复边界和限制见 [M4 模块章节](../docs/cnf-service-chain-orchestration.md#m4-后端放置与跨节点路径实验)。

## CNF NetworkPolicy 正反例入口

`cnf-networkpolicy.py` 读取两份 `infra/cnfs/networkpolicy*` 固定资产，命令为 `render`、`run`，
均要求 `--out <新的结果目录>`；`run` 另需 `--engine-dir` 指向含 VM1/VM3 规则链采集文件的目录。
只接受未配置本实验策略的 M4 起点，创建 VM1/VM3 非 Envoy 测试 Pod，执行无策略/加策略/撤销/重加对照，
覆盖两后端 Service/Pod IP、Envoy 出站及 DNS，再做两类上下行各 5 次、5 秒/1 Mbps 的 UE 回归。
工具错误不能算连接拒绝。原有配置/Pod 身份变化、业务失败或内存护栏触发即停，失败目录不覆盖。
成功后保留两条策略、删除临时 Pod，严格复查 5GC；完整对照复测前须先撤销本实验两条策略。

2026-10-15 `cnf-netpol-v1` 已通过：最终矩阵 15 次允许、30 次拒绝，20 次 UE 会话、40 条正确代理连接。
本地和 VM1 共 50 项单测通过：`python -m unittest discover -s tests -p 'test_cnf*.py' -v`。
`audit-cnf-networkpolicy.py <单次run目录> --out <新的JSON路径>` 独立离线复核对照和业务证据，不访问集群。
采集、复测与保护边界见 [NetworkPolicy 模块章节](../docs/cnf-service-chain-orchestration.md#networkpolicy-隔离正反例与阶段收尾)。

## R2.S3 统一 CNF Profile 入口

`cnf-profile.py` 读取 `infra/cnfs/service-chain-profile.json`，锁定 14 个继承输入，管理
`neutral/qos/placed/protected` 四种固定状态，默认 `protected`。所有命令要求新的 `--out` 目录：
`render --target` 离线渲染；`check/snapshot` 识别当前状态并保存快照；`apply --target` 备份、dry-run、部署和校验；
`restore --snapshot` 只恢复匹配当前 Profile 的已知状态；`rebuild --target` 只重建三个 CNF Deployment；
`recovery-check --target <不同状态>` 专门演练预设失败后的回滚；`measure` 自动选择当前状态的已锁定测量参数。
部署失败尝试回到已识别起点并保存恢复记录；未知现场拒绝覆盖。既有 5GC、Namespace、Service、PVC 不重建。

保护状态测量保留策略，创建/清理临时测试 Pod，执行隔离矩阵和 20 次 UE 业务；整形状态执行各 5×15 秒下行。
2026-10-16 实机验收通过：3 次幂等 apply、3 个 CNF 受控重建、快照恢复、失败回滚，
两批保护状态各 20 次业务和 10 次整形复测通过；58 项单测在 Windows/VM1 通过。
`audit-cnf-profile.py <结果目录> --out <新的JSON路径>` 独立离线复核固定数据集。
执行示例、恢复边界与证据见 [R2.S3 模块章节](../docs/cnf-service-chain-orchestration.md#r2s3-固定-profile-与可重复部署测量)。

## 初始化节点

Server示例：

```bash
sudo env NODE_IP=192.168.244.128 NODE_IFACE=ens33 INSTALL_GTP5G=1 \
  ./scripts/bootstrap-k3s-node.sh server
```

Agent示例：

```bash
sudo env NODE_IP=192.168.244.129 NODE_IFACE=ens33 \
  K3S_URL=https://192.168.244.128:6443 K3S_TOKEN='<运行时令牌>' \
  INSTALL_GTP5G=1 ./scripts/bootstrap-k3s-node.sh agent
```

脚本不会卸载现有K3s。VM2从独立Server迁移为Agent前，必须先做VMware快照，并按三节点实施记录单独执行迁移。

## 部署与验收Single UPF

在K3s Server上设置`KUBECONFIG`后执行：

```bash
export KUBECONFIG=/home/<user>/.kube/config
./scripts/deploy-single-upf.sh
```

脚本会固定上游提交、应用受控补丁、执行`helm lint`、部署核心网、创建测试订阅者、部署UERANSIM，并完成端到端Ping验收。

单独重复验收：

```bash
./scripts/verify-single-upf.sh
```

## 三节点功能基线测量

在 VM1 上执行。脚本会重启 UE 20 次，逐轮采集注册耗时、PDU Session 建立耗时、
`uesimtun0` 是否出现，以及经该接口发送 5 个 ICMP 包的收发结果。它不注入网络
时延，因此只用于多节点环境的功能稳定性基线，不能据此宣称 edge-UPF 时延收益。

```bash
export KUBECONFIG=/home/<user>/.kube/config
COUNT=20 PING_COUNT=5 ./scripts/measure-multinode-baseline.sh
```

结果以 TSV 和逐轮 UERANSIM 日志保存到 `results/`。该目录中的原始测量结果应在
确认无敏感信息后再决定是否提交；脚本本身可以进入版本控制。

单独创建订阅者：

```bash
WEBUI_URL=http://127.0.0.1:30500 \
  ./scripts/create-test-subscriber.sh infra/free5gc-test-subscriber-ue1.json
```

WebUI用户名和密码可以通过`WEBUI_USERNAME`、`WEBUI_PASSWORD`环境变量提供。脚本不会输出访问令牌或鉴权密钥。

## 受控数据面测试端点安装

`configure-iperf3-server.sh` 已于 2026-10-08 在 VM1 执行安装，服务监听
`192.168.244.128:5201` 并设置开机自启。它只安装并配置服务，不生成性能测试流量。
UE 客户端的镜像构建与导入流程见 [iperf3 配置说明](../infra/iperf3/README.md)。

## 受控数据面基线采集

在 VM1 运行 `measure-controlled-data-plane.py`。先确认 gNB 的 NG Setup、UE 的
PDU Session 与 UPF 转发均正常；gNB 和 UE 同时滚动升级后可能残留上下文，不能仅
凭 Pod Running 或 TUN 接口存在就启动性能测试。

```bash
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/measure-controlled-data-plane.py \
  --out-dir /home/lhm/.work/data-plane-results/<唯一运行目录> \
  --rounds 10 --duration 30
```

脚本在同一个 UE Pod、同一条会话中顺序测试：10轮 RTT（每轮30包、间隔0.2秒），
10轮单流 TCP 上行、10轮单流 TCP 下行、10轮 UDP 上行（20 Mbps、1200字节 payload）。
iperf3 每轮计量30秒，另有3秒预热，不计入统计；轮间等待2秒。TCP/UDP 均绑定动态
读取的 UE 隧道 IP，RTT 绑定 `uesimtun0`。进程超时、错误与原始输出均保留。

保存内容包括命令、逐轮 JSON/文本、CSV、资源采样、Pod/镜像/资源快照与汇总。
节点负载来自 metrics-server，每10秒读取一次；其自身有采样窗口，汇总按节点指标
时间戳去重，不能将这些数据解释为每个瞬间的 CPU 峰值。

可用 `--modes rtt` 或 `--modes udp-up` 分项运行。首个失败即停止，超时后尝试清理
UE 中的 iperf3 进程；失败数据不被替换。参数改变或恢复后的诊断须使用新的结果目录。
2026-10-08 实际测量因 UPF OOM 未完成吞吐批次，详见[测试记录](../docs/10-controlled-data-plane-baseline.md)。

同一脚本的 `--stability` 模式用于限速稳定性诊断，不测最大吞吐：空载 60 秒，
TCP/UDP 上行依次为 5、10、20 Mbps，每档 30 秒、无预热，每档结束后观察 30 秒。
约每秒采集 UPF cgroup v2 内存、匿名内存、CPU 和 OOM 计数；实际间隔还包含
`kubectl exec` 耗时。达到 512 MiB 或容器限额的 50%（取较小值）、出现 OOM 或
采样失效时停止负载。保护线不保证绝对杜绝突发 OOM，不代替长期稳定性验证。

```bash
python3 scripts/measure-controlled-data-plane.py --stability \
  --out-dir /home/lhm/.work/data-plane-results/<新的唯一诊断目录> \
  --duration 30 --idle-seconds 60 --cooldown-seconds 30 \
  --rates-mbps 5 10 20 --memory-stop-mib 512
```

结果保存为 `upf-cgroup.jsonl`、`rounds.json`、各档 iperf3 JSON、metadata 和 summary。
保持同一 UPF Pod 和 UE 会话；不修改资源限制、URR、模块版本或网络拓扑。

高档位诊断可用 `--protocols tcp` 只测 TCP，再以 `--rates-mbps 50` 或 `100`
指定单档；每档先 `--duration 10`，通过后再以新目录测 `--duration 30`。
保留相同保护线，失败则停止后续档位。TCP 结果同时记录发送/接收速率、窗口时长、
字节数与重传，避免把不一致的统计窗口误判为丢包；metadata 保存采集脚本 SHA256。

稳定性模式同时实时保存 `upf-live.txt`、`pod-watch.txt`、`pod-status-timeline.jsonl`
和按原 Pod UID 筛选的 `pod-events.txt`；首次观察到容器退出、等待或重启时保存
`first-exit-observation.json`。诊断流意外结束、采集异常或单文件超过 64 MiB 即停止负载，
结束时关闭后台采集；Pod watch 就绪后才进入负载流程。该采集在 2026-10-08 复现中已捕获
首次退出码及 Panic 堆栈，不依赖可能已被清理的 `kubectl logs --previous`。
完整 `upf-live.txt` 默认不进 Git，保留在实验目录和归档中；小型堆栈摘录可供审阅。
`--capture-smf` 可在稳定性模式同步保存 `smf-live.log`；该流同样受文件大小及意外 EOF
保护，SMF 容器结束会停止负载。完整运行日志默认忽略，不作为可提交的堆栈摘录。

## 双 UE 低负载基线入口

`cnf-multi-ue.py` 使用 `infra/cnfs/multi-ue-experiment.json` 和隔离测试订阅
`infra/free5gc-test-subscriber-ue2.json`，提供 `render`、`deploy`、`check`、`individual`、`smoke`、`concurrent`，均要求 `--out <新目录>`。
在 VM1 设置 `KUBECONFIG=/home/lhm/.kube/config` 后运行；`deploy` 只接受单 UE 的已验收 `protected` 起点，拒绝覆盖已有同名实验资源。
新建 `cnf-multi-ue` 中的专用 Envoy、四个独立服务端以及两条保护策略，并在 `free5gc` 中增加独立身份的 UE2；不修改旧 CNF 资源或关闭旧 Profile 验证。
分别验收 `individual` 后运行 `smoke`，再以独立目录运行 `concurrent`：正式批次 20 组、40 次 TCP 会话，每 UE 1 Mbps、10 秒；每组保守有效流量重叠至少 8 秒。
额外采集两侧 SUPI/会话/IP、源地址绑定、四路代理/后端 Cookie、UPF 计数、Pod 身份和内存护栏。它不证明 QoS 保底或自主识别。
离线审计入口为 `audit-cnf-multi-ue.py <证据目录> --out <新的JSON路径>`；具体配置和结果统一维护在
[双 UE 模块章节](../docs/cnf-service-chain-orchestration.md#a-双-ue-低负载基线的实现与验收)。

## 5G Session-AMBR 能力探针入口

`cnf-qos-probe.py` 复用已部署的 A 夹具，固定输入为 `infra/cnfs/qos-probe.json`。
在 VM1 设置 `KUBECONFIG=/home/lhm/.kube/config` 后，依次使用独立新目录：

```bash
python3 scripts/cnf-qos-probe.py inspect --out <新的只读目录>
python3 scripts/cnf-qos-probe.py probe --out <新的测量目录>
python3 scripts/audit-cnf-qos-probe.py <测量目录> --out <新的审计JSON>
```

`inspect` 不修改策略。`probe` 仅比较后更新 UE2 订阅中的两个 Session-AMBR 字段，
保留认证/SQN；经 UERANSIM 自动补建 PDU 会话验证 PCF/SMF 下发和 PDR/QER。
原值为双向 1000 Mbps，探针为上行 2、下行 4 Mbps，发送目标 8 Mbps、15 秒；
关/开/再关各方向 3 轮，并测原策略、UE1 对照和恢复后的业务，共 26 次 TCP 会话。
每次采集接收端窗口、代理/后端 Cookie、规则、开关、Pod 身份与 512 MiB UPF 内存护栏。

普通异常通过 `finally` 恢复原订阅、关闭全局 gtp5g QoS 并重建 UE2 会话。
外部强杀/断电不在该保证内；先只读检查，再使用
`python3 scripts/cnf-qos-probe.py restore --out <新的恢复目录>`，最后复验业务。
入口只接受固定原值或探针值，遇到其他策略漂移拒绝覆盖；不是任意现场恢复工具。

探针区分观测完成与能力支持。UERANSIM 自身也执行 Session-AMBR；开关关闭时已有上限，
不能据端点吞吐单独证明 UPF 限速有效。TCP 定时结束可能有收发统计差异及数据连接 `UR/DC`：
仅在完整接收窗口、足够代理载荷和正常控制连接成立时保留为测量，并报告标志；
提前失败/连接失败仍拒绝，不将它记为无损、干净收尾或带宽保底。
详细能力结论及失败记录见 [B 模块章节](../docs/cnf-service-chain-orchestration.md#b-5g-qos-能力探针)。

## 容器化共享瓶颈竞争入口

`cnf-contention.py` 使用 `infra/cnfs/contention-experiment.json`，新增独立 `cnf-contention` 命名空间和一个三容器 Pod。
两个 iperf3 服务进程共用 `eth0`，仅 queue 辅助容器有 `NET_ADMIN`，不挂载主机网络/目录。
`render/deploy/check/pilot/measure/restore` 均要求 `--out <新目录>`；在 VM1 设置既有 KUBECONFIG 后使用。

```bash
python3 scripts/cnf-contention.py render --out <新目录>/render
python3 scripts/cnf-contention.py deploy --out <新目录>/deploy
python3 scripts/cnf-contention.py pilot --out <新目录>/pilot
python3 scripts/audit-cnf-contention.py <小批目录> --out <新的小批审计JSON>
python3 scripts/cnf-contention.py measure --out <新的正式目录>
python3 scripts/audit-cnf-contention.py <正式目录> --out <新的正式审计JSON>
```

`deploy` 拒绝覆盖已有命名空间/端口，`check` 严格复用 A/原 protected 验证并核对单个共享端点、权限、镜像与原生 QoS 关闭状态。
正式下行 UDP 每用户 8 Mbps、30 秒，根瓶颈 10 Mbps；共享、等额、保障角色交换、仅优先级交换共六模式各 3 轮，另有前后未整形对照。
保留接收区间、丢包、类映射 RTT、真实重叠、队列/UPF 计数、原 24 个加新 Pod 的身份和 UPF 内存护栏。
RTT/丢包在本轮是观测指标，不承诺业务 SLA；端点直接经 N6 到 UE，不宣称经过 Envoy 或实现 PCF/QFI 控制。

普通异常尝试撤销新 Pod 内的自有队列。手工恢复使用
`python3 scripts/cnf-contention.py restore --out <新的恢复目录>`；未知队列/配置漂移拒绝覆盖，先保留诊断。
采集器兼容镜像中的 iproute2 5.9 原生输出；iperf3 3.9 反向 UDP 以客户端接收区间重算，不能用结束汇总字节误当接收量。
每次执行归档源码输入及哈希，审计分别返回证据完整性和网络目标结果。详见
[C 模块](../docs/cnf-service-chain-orchestration.md#c-共享瓶颈竞争容器化调度与受控需求) 与 [PS-012](../problem%20solve.md#ps-012旧版-tc-输出与反向-udp-汇总字段导致-c-采集失败)。

上行补充使用 `cnf-contention-uplink.py` 和独立配置 `infra/cnfs/contention-uplink-experiment.json`。
它新增独立上行测试 Pod，通过 Pod 内入口重定向到同一 IFB 排队，保留 C1 下行端点；服务端输出 JSON 接收区间。
依次在 VM1 使用相同的 `deploy/check/pilot/measure/restore --out <新目录>` 命令形式，离线审计使用
`python3 scripts/audit-cnf-contention-uplink.py <测量目录> --out <新审计JSON>`。
上行测量不加 `-R`；客户端发送数据只用于核对负载，实际吞吐/丢包读取服务端接收数据。
正常收尾删除新 Pod 内的 IFB 与入口规则，保留测试资源；手工恢复同样拒绝未知接口所有权或队列漂移。
详见 [C2 上行模块](../docs/cnf-service-chain-orchestration.md#c2-上行竞争入口-ifb-与服务端接收证据)。

## D1 离线业务需求解析与开发评估

`cnf-requirements.py` 使用 Python 3.10+ 标准库，读取 `infra/cnfs/requirements-contract.json`。
它解析实时交互、持续媒体、后台传输三类候选及显式目标，记录来源、未知、冲突和需澄清项；不访问集群。
可直接使用归档演示输入，输出文件须不存在且父目录已存在：

```powershell
python scripts/cnf-requirements.py results/20261020-cnf-requirements/demo-request.json --out <新的JSON路径>
python scripts/evaluate-cnf-requirements.py --out <新的结果目录>
```

请求字段为 `request_id`、`description`、`subject`、`context`、`requested`，可附 `flow`、`observations`；
端口/协议与实测指标不参与类别或目标推导，类别标签、后端选择与执行命令不能作为输入字段。
不支持任意自然语言；`parsed` 或 `candidateForEPlanning` 不表示可满足或获准执行，所有输出均无方案、无执行授权。

评估器默认读取 `tests/fixtures/requirements-cases.json`，可用 `--cases` 指定仓库内符合开发数据约束的样例文件。
结果归档报告、逐样例差异、不变性检查和 9 份代码/数据/能力依据输入及哈希，不覆盖旧目录。
当前 36 条合成样例由 Agent 编写，标签待人工审核；30 条草案类别一致、6 条复杂表达未识别，不能称为独立识别准确率。
新增 D 测试可用 `python -m unittest discover -s tests -p 'test*requirements*.py' -v` 运行。
能力边界、完整验证及 D2 审核入口见 [业务需求识别](../docs/cnf-service-chain-orchestration.md#业务需求识别的输入与验收)。

## D2 人工标注与冻结评估

`review-cnf-requirements.py` 使用 Python 3.10+ 标准库，在本地生成审核包并评价冻结识别器，不连接集群。
D1 的开发集评估器保持原样；D2 使用不同的审核格式，不能将修改旧集的 `humanReviewStatus` 当作完成审核。

```powershell
python scripts/review-cnf-requirements.py prepare --d1 --out <新的审核包目录>
python scripts/review-cnf-requirements.py prepare --collection <原始需求集合.json> --out <新的审核包目录>
python scripts/review-cnf-requirements.py prepare --collection <合成需求集合.json> --annotation-mode agent_synthetic --out <新的合成审核包目录>
python scripts/review-cnf-requirements.py audit --review <审核包目录>/review.json --out <新的检查.json>
python scripts/review-cnf-requirements.py evaluate --review <审核包目录>/review.json --out <新的评估目录>
```

`prepare` 两种来源二选一。审核包包括 `review.html`、`review.json`、原始集合、开发参考、版本及源码哈希；
`review.html` 离线打开，仅辅助类别标注并导出 JSON，不向外上传或自动保存。将导出的文件保存为该包的 `review.json`，保留其他文件。
如需从已有审核 JSON 重新生成表单，使用 `render --review <...>/review.json --out <新的HTML路径>`。
若工具后续发生变化，应使用该包中 `frozen-inputs/scripts/review-cnf-requirements.py` 执行 `audit/evaluate/render`，保留原识别器及审核口径。

默认 `--annotation-mode human` 保留人工声明检查和类别表单。
用户授权的 Agent 自校验使用 `agent_synthetic`，仅限 `synthetic/development`，不生成人工表单；
`reviewerKind` 必须为 `agent`、`independentOfPredictions` 必须为 `false`，报告明确为合成开发检查，人工计数不增加。
两种模式都需要逐字段依据及完整性检查；`evaluate` 在函数入口也重新检验冻结样本，避免绕过命令入口修改原始事实。
Agent 检查可推进工程，不替代独立泛化或真实应用证据。六条受控试验及前后对照见
[`synthetic-pilot/metadata.json`](../results/20261020-cnf-requirements-d2/synthetic-pilot/metadata.json)。

真实原始集合格式为 `collectionVersion:cnf-requirements-collection-v1`、`collectionId` 和非空 `cases`。
每条记录包含以下字段，尚未采集时保留空集合，不填入伪造案例：

| 字段 | 内容 |
|---|---|
| `id`、`scenarioFamily` | 唯一请求标识、业务场景族；同族不得跨开发和留出集合 |
| `split` | `development` 或 `holdout`；D1 已出现家族/描述不能作为新留出数据 |
| `origin` | `kind` 为 `synthetic` 或 `real_requirement`；以及 `authorKind`（`human/agent`）、`authorId`、原始证据 `sourceRef`、带时区的 `collectedAt` |
| `input` | D1 原始请求格式，`request_id` 与 `id` 相同；不得含草案类别、预测或正确答案 |

审核 `review` 需逐条填写 `decision`（`known/unknown/ambiguous`）、相应 `category`、理由、审核者标识、`reviewerKind:human`、时间和 `independentOfPredictions:true` 声明。
留出样本须为声明的真实人类需求，作者与审核者不同；工具不认证身份及声明，不检验所有语义重叠，仍需人工检查。
只有全部类别审核完成才评分；修改原始事实、成员或划分后必须重新冻结，不能悄悄删掉拒判样例。

`targetReview` 初始为 `status:unreviewed, fields:{}`，不参与目标评分。
完成数值审核时改为 `status:complete`，`fields` 必须覆盖合同全部十个字段，每项包含 `value`、`resolution`、`basis`、`evidence`：

- `resolution` 为 `explicit` 时值须合法；`unknown/conflict/invalid` 时值为 `null`，不能把未提供需求设成零。
- `basis` 写人工依据；明确/冲突/无效字段还需 `evidence`，例如原文 `{"path":"/description","quote":"最低 4 Mbps"}` 或原结构化值 `{"path":"/requested/min_bandwidth_mbps","value":4}`。该例仅说明格式，不是为样本指定答案。
- 原文片段存在和结构化值一致会被校验；是否正确理解任务仍由审核者负责，不从业务类别默认生成 SLA。

评估按合成/真实、开发/留出分层；分类区分带/不带应用上下文，参数区分文本/结构化来源；含拒判的正确率与识别覆盖率同时报告。
输出完整分歧及冻结输入，未审核目标不算正确未知，也不因评估脚本成功而宣称 D 验收或网络保证成立。
测试入口：`python -m unittest discover -s tests -p test_review_cnf_requirements.py -v`。
当前审核表、准备度和待采集信息见 [D2 模块](../docs/cnf-service-chain-orchestration.md#d2-人工标注数据冻结与独立验证)。

## E1 离线规划器与工具接口

`cnf-planner.py` 使用 Python 标准库，读取 `infra/cnfs/planner-contract.json` 和历史 C1/C2 固定配置/证据。
所有入口均不访问集群。先读 `capabilities` 判断支持情况，不能以方法名推断已实现执行能力。

```powershell
python scripts/cnf-planner.py capabilities --out <新的能力JSON>
python scripts/cnf-planner.py inspect --source results/20261020-cnf-planner/demo-state-source.json --at 2026-10-20T12:00:00+08:00 --out <新的状态JSON>
python scripts/cnf-planner.py plan --requests results/20261020-cnf-planner/demo-requests.json --state <状态JSON> --at 2026-10-20T12:00:00+08:00 --out <新的计划JSON>
python scripts/cnf-planner.py validate --requests results/20261020-cnf-planner/demo-requests.json --state <状态JSON> --plan <计划JSON> --at 2026-10-20T12:00:00+08:00 --out <新的校验JSON>
python scripts/evaluate-cnf-planner.py --out <新的工程评估目录>
```

输出文件须不存在且父目录已存在，评估目录也须为新目录。`--at` 只指定离线模拟时钟，演示时间是历史夹具时间，不能作为现场新鲜度或运行记录；省略时使用当前 UTC 时间，旧声明会过期拒绝。
`inspect` 输出的声明快照和 `plan` 的完整工具结果可直接传给后续 CLI，不必手工提取内部对象。
`planned/valid_offline` 不代表执行成功，进程退出码 0 也不代表准入成功；应读 JSON `status/issues`。全入口 `executionAuthorized/networkChanged` 均为 `false`。

输入合同和完整限制见 [E1 模块](../docs/cnf-service-chain-orchestration.md#e1-离线规划器与-agent-工具接口)。
首版两用户、同一独立方向、UDP、30 秒实验/20 秒接收稳态均值；联合校验最低需求与 0.5 Mbps 余量，再选择既有五种队列模式。
状态/需求/目录哈希和期限绑定旧计划；没有兼容固定配置时拒绝，不生成任意队列参数。
`apply/verify/restore` 命令及同名 Python 工具保留入口，明确返回 `unsupported`，尚未配置或测量任何现场。

D 输出导入为 JSON 列表，每项为 `cnf-requirements-v1` 结果：

```powershell
python scripts/cnf-planner.py import-requirements --input <D输出列表JSON> --batch-id <批次标识> --protocol UDP --assessment-scope controlled_receiver_steady_mean --out <新的导入JSON>
python scripts/cnf-planner.py plan --requests <导入JSON> --state <状态JSON> --at <离线时刻> --out <新的计划JSON>
```

导入要求调用方明确实验口径，不补用户/会话/参数，不信任分类候选标志；真正规划时仍需恰好两个匹配状态的完整请求。
Python 接口为 `inspect_state`、`plan_policy`、`validate_plan`、`import_requirements`，参数与版本边界见模块；后续 Agent 可复用相同接口，无需让模型生成系统命令。
测试：`python -m unittest discover -s tests -p '*planner*.py' -v`。
工程评估归档 14 份输入及哈希；可用归档 `inputs/scripts/evaluate-cnf-planner.py` 的 `evaluate` 函数重算报告和场景，用原固定模拟时钟复验。

## E2 受控现场执行适配器

`cnf-planner-live.py` 在 Linux VM1 运行，需 Python 3.10+、PyYAML、kubectl、既有 C1/C2 夹具及历史元数据。
从项目根目录使用，输出必须是新目录；源代码、配置和输入哈希随批次归档。

```sh
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/cnf-planner-live.py inspect --out <新的只读预检目录>
python3 scripts/cnf-planner-live.py run --out <新的执行批次目录>
python3 scripts/audit-cnf-planner-live.py <执行批次目录> --out <审计JSON>
```

`inspect` 只读但会发送严格基线所需探测；`run` 持有文件锁完成查询、规划、复查、执行、测量与恢复。
仅允许既有双 UE 下行 `none→equal→none`，显式实验目标各 4–8 Mbps，UDP 30 秒。没有模拟时钟或独立留置队列的 CLI。
E1 的旧执行入口仍返回 `unsupported`，E2 不将它改成隐式访问集群。
Python 工具有 `inspect_state`、`plan_policy`、`validate_plan`、`apply_plan`、`verify_plan`、`restore`，修改现场只用于当前持锁工作流。
写入前再次校验现场和计划期限，并固定原 Pod UID；正常结束或普通异常尝试撤销本轮已知队列，未知配置拒绝删除。
本锁不排斥其他管理工具；不承诺强杀/断电恢复、全局无竞争流量、业务 SLA 或原生 5G QoS。
失败时检查 `summary.json`、`execution-error.json`、`restoration.json` 和原始证据，不能只凭命令完成或 Pod Ready 判断成功。
审计从归档源码重算，并使用记录的历史时间验证旧计划；它不产生现场执行授权。

现场异常验收使用独立的 `exercise-cnf-planner-live.py`，不向 Agent 开放故障注入入口：

```sh
python3 scripts/exercise-cnf-planner-live.py --out <新的异常验收目录>
python3 scripts/audit-cnf-planner-live-faults.py <异常验收目录> --out <审计JSON>
```

它在现有专用 C1 夹具中依次测试真实等待计划过期、队列变化拒绝、重复 root 命令导致的真实部分写入失败、带唯一标记的接收进程中断，并运行一次正常业务对照。
注入队列由测试夹具单独持锁、记录并清理；执行器不能把拒绝前的他方写入当成本轮策略删除。
各异常批次的运行结果必须保留 `failed`；只有错误类型、清理和严格收尾均符合预期，测试验收才返回 `passed_expected_failure`。
普通失败恢复后仍会保存严格复查结果；进程清理逐项记录，单项清理错误不能阻止其他进程的清理尝试。
本验收不覆盖 Pod 替换、网络分区、强制杀进程或断电。

正式证据与完整限制见 [E2 模块](../docs/cnf-service-chain-orchestration.md#e2-受控现场与单模式执行闭环)。
测试：`python -m unittest discover -s tests -p '*planner*.py' -v`；这些本地测试不访问集群，不能当作实机故障注入。

## E3 完整请求与白名单策略选择

`cnf-planner-policy.py` 接收 `cnf-planner-requests-v1` 的完整两用户下行请求，仅开放 `equal`、`guarantee-ue1`、`guarantee-ue2`。
它按容量、已测参考下界、上限及相对优先级检查后，在可行白名单中依次按总预留速率、总上限、名称选择；不生成任意速率，也不自动绑定未知用户或旧会话。
E2 固定请求入口继续保留。E3 的 inspect 状态与 E2 一样实际读取会话，但使用独立的 `cnf-planner-policy-v1` 工具合同和 `policyPlan` 标识。

```sh
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/cnf-planner-policy.py inspect --out <新的现场目录>
python3 scripts/cnf-planner-policy.py plan --state <现场目录/state.json> --requests <完整请求JSON> --out <新计划JSON>
python3 scripts/cnf-planner-policy.py run --state <现场目录/state.json> --plan <计划JSON> --requests <同一请求JSON> --out <新的执行目录>
python3 scripts/audit-cnf-planner-policy.py <整套评估目录> --out <审计JSON>
```

调用者必须明确提供实际 SUPI、inspect 返回的 `session_id` 和全部必需目标；不能将历史样例的会话编号直接用于当前执行。
也可仅以 `run --requests <完整请求JSON> --out <新目录>` 在同一持锁运行内重新观察、规划和执行；仍不修改请求中的身份或目标。
`validate` 需 `--state/--plan/--requests/--out`，会重新查询现场，但不写队列。计划最多 30 秒内开始，旧计划过期须重新观察/规划，不能改时间戳；实时 CLI 不接受模拟时钟。
Python 接口为 `inspect_state`、`plan_policy`、`validate_plan`、`apply_plan`、`verify_plan`、`restore`、`run`。
执行器在同一锁内复查、配置、测量和恢复；同一后端实例不得重复应用已执行的请求，下一次需新运行和新状态。
`planned` 不表示已执行；`needs_clarification/rejected` 表示没有写入，退出码 0 也不能代替 JSON 状态判断。
这些目标只指既有 UDP 30 秒实验、5–25 秒候选范围内完整接收区间的稳态均值，不是连续租约或业务 SLA；时延/丢包、原生 5G QoS、上行和独立优先级模式仍未开放。

`evaluate-cnf-planner-policy.py --out <新的评估目录>` 是明确的实机测试入口，会生成预先声明的工程请求，执行四组合法请求、四组拒绝请求及一个 E2 正常对照。
它对当前实际 SUPI/会话进行显式实验绑定，不属于自然语言需求识别，也不把该绑定能力作为替用户补写信息的生产接口。

## 固定配置联合功能检查

`cnf-joint-check.py` 在 VM1 执行，复用固定 Envoy 路由、保护策略和镜像，将 core 保持在 VM1、edge 保持在 VM3。
只为两个后端增加容器内队列辅助容器；先无整形各 3 次、再 5/15 Mbps 各 5 次、最后撤销整形各 3 次。
共 22 次顺序单流 TCP 下行，每次 15 秒、发送端 pacing 上界 20 Mbps，接收端原始字节/时长用于验收。

```sh
export KUBECONFIG=/home/lhm/.kube/config
python3 scripts/cnf-joint-check.py render --out <新的本地配置检查目录>
python3 scripts/cnf-joint-check.py run --out <新的现场批次目录>
python3 scripts/audit-cnf-joint-check.py <现场批次目录> --out <新的审计JSON>
```

起点必须是已知 `protected` 配置，C1/C2 无队列残留、UPF 原生 QoS 关闭、两 UE 会话及严格基线正常。
与 E2/E3 共用执行锁；A—C 期间两个后端不重建，只改变 Pod 内 TBF。准备和恢复时两个后端的受控重建属于预期变化。
正式期保存全部受监测 Pod 身份、端点/节点、策略、代理日志、后端 Cookie、UPF 计数、队列及资源采样。
无整形两类中位数均须至少 18 Mbps；整形中位数为 core 4–5.75、edge 12–17.25 Mbps，且比值至少 2。
普通异常尝试恢复两个已知后端配置并复验；未知后端配置拒绝覆盖。恢复失败保留失败，不保证断电或强杀恢复。
活动联合配置不是旧 Profile 的四种固定状态，不可在测试中另行运行旧 Profile 的部署/测量入口。
该检查不提供业务自主识别、并发保障、原生 5G QoS、时延/丢包 SLA、持续在线策略或 Agent 能力。
证据与验收结论见 [联合功能检查](../docs/cnf-service-chain-orchestration.md#固定配置联合功能检查)。

## UPF 判空修复构建与部署前检查

`build-patched-upf.sh <绝对构建目录>` 只构建，不修改部署。目录需包含固定源码归档
`source.tar.gz`、官方 Go 1.25.5 Linux amd64 归档和
`infra/patches/go-upf-v1.2.10-remotesess-nil.patch`；脚本校验两份归档 SHA256。
从干净源码开始，不在已应用补丁的目录重复运行。归档、依赖缓存、二进制和镜像留在 `.work/`。

`check-upf-only-manifest-change.py`（需 PyYAML）比较 `helm get manifest` 与
`helm template --is-upgrade --skip-tests` 的 YAML 语义，仅允许唯一 UPF Deployment 的
镜像和拉取策略改变，资源集合或其他字段变化均拒绝。部署前保存完整当前 Values，使用
`--reset-values -f <保存的Values> -f infra/free5gc-upf-nilfix-values.yaml` 保持其余参数。
不把普通 Helm test Pod 渲染结果误当作新增运行资源。
