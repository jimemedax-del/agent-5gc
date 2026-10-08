# 自动化脚本

> 状态：**待空白VM实机验证**。脚本根据两次人工部署记录整理，不得将“脚本已编写”表述为“自动化流程已验证”。

这些脚本用于自动复现已经人工验证过的单节点基线，并为三节点集群提供统一入口。当前项目的正式可复现核心是固定上游提交、受控补丁和Values；脚本是辅助工具。

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
