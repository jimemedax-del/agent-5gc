# k3s + Helm 单节点 5GC 业务基线

> 记录日期：2026-10-06。与 [Compose 基线](01-5gc-single-upf-baseline.md) 分开；以下均为本次实际验证结果。

## 结论与范围

在远程 Ubuntu 22.04 VMware VM 上，单节点 k3s 部署的 free5GC 已完成：NF Ready → SMF/UPF PFCP 关联 → gNB/AMF NG Setup → UE 5G AKA 鉴权及注册 → 单个 PDU Session → UE 经 UPF 访问公网。2026-10-06 又完成第一台 **VM 重启恢复**及第二台 **空白 VM 重建**测试。此结果**不**证明多节点放置、Multus 或低时延收益。

## 版本与环境

| 项目 | 本次实际值 |
|---|---|
| VM | 8 vCPU、7.7 GiB 内存、49 GiB 根分区，Ubuntu 22.04 |
| Kubernetes | k3s `v1.30.14+k3s1`，单节点 Ready |
| Helm | `v3.17.2` |
| free5GC | Chart 仓库 `free5gc-helm v4.2.2`，提交 `0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c`；NF 镜像 `v4.2.2` |
| UERANSIM | Chart 镜像 `free5gc/ueransim:v4.0.1`；容器运行日志报告 **UERANSIM v3.2.7**。不是原计划的 v3.3.0，后续须冻结并说明 |
| 用户面 | Single UPF；`gtp5g` 内核模块已加载；无 Multus |
| Release | `free5gc-helm`、`ueransim`；namespace `free5gc` |
| 运行资源 | 结束时根分区约 26 GiB 可用，内存约 4.8 GiB available |

原 Compose 容器已停止但未删除。迁移前备份在 VM 的 `/home/lhm/free5gc-lab/backups/k8s-migration-20261006-115253`，压缩包已校验；不要将旧 MongoDB 整库覆盖到 k3s 的新实例。

### 第二台 VM：从零重建测试机（端到端通过）

2026-10-06 已通过 Tailscale + SSH 密钥登录 `lhm2@100.111.54.35`，实际主机名为 `lhm2-virtual-machine`。这是**另一台** VMware VM，不是上表已跑通业务的第一台 VM；Tailscale IP 只记录当前连接地址，不作为部署配置中的固定地址。

| 项目 | 第二台 VM 当前检查结果 |
|---|---|
| 系统与内核 | Ubuntu 22.04.5 LTS，`6.8.0-138-generic` |
| 资源 | 8 vCPU、7.7 GiB 内存、2 GiB swap；根分区 49 GiB，约 34 GiB 可用 |
| 远程访问 | Tailscale 已连通；OpenSSH `active`，现有密钥可非交互登录 |
| 起始状态 | Docker、k3s、kubectl、Helm 均未安装；`gtp5g` 未加载 |
| 重建结果 | k3s `v1.30.14+k3s1`、Helm `v3.17.2`、`gtp5g v0.9.5`；Single UPF free5GC 与 UERANSIM 均部署成功 |
| 业务验收 | gNB/AMF NG Setup、UE 鉴权与注册、`PSI[1]` 会话、TUN `10.60.0.1`，绑定 TUN ping `1.1.1.1` 为 3/3 成功 |
| 结束资源 | 根分区约 30 GiB 可用；内存约 5.1 GiB available |

完整步骤、证据和镜像导入注意事项见 [第二台 VM 从零重建记录](06-clean-vm-rebuild.md)。第一台 VM 保留为可用对照环境，运行中的服务未被重启或更改。

## 配置与实际修正

- 本地受控 Values：[free5gc-single-upf-values.yaml](../infra/free5gc-single-upf-values.yaml)、[ueransim-single-node-values.yaml](../infra/ueransim-single-node-values.yaml)。
- 本地临时Chart工作副本：`tmp/free5gc-helm-v4.2.2/charts/`；VM Chart：`/home/lhm/free5gc-lab/helm-v4.2.2/charts/`。正式基线采用[版本化补丁](../infra/patches/free5gc-helm-v4.2.2-single-upf.patch)重建，不提交临时上游副本。
- 上游 Chart 只设 `global.userPlaneArchitecture=single` 时，UPF 变为单实例，但 SMF 子 Chart 的非 Multus 配置仍指向 ULCL 三 UPF。已用 Values 覆盖 SMF 拓扑为 `gNB1 → UPF`，并修改 SMF wrapper 只解析单 UPF Service；保留 SMF 镜像 `v4.2.2`。
- k3s kubelet 已放行 UPF 所需的 `net.ipv4.ip_forward` sysctl；`gtp5g` 已加载。该配置需在空白 VM 复现清单中进一步固化。
- UERANSIM gNB 原 `wait-amf` initContainer 拉取 `towards5gs/sctp_test:latest` 超时。Chart 增加 `gnb.waitForAmf` 开关，本次因 AMF 已 Ready 而关闭；之后用 gNB 日志直接验证 SCTP/NG Setup，不能把“跳过检查”当成检查通过。
- 测试订阅来自 [UE1 样例](../infra/free5gc-test-subscriber-ue1.json)，仅用于实验；它与 UERANSIM Chart 默认 UE 的 IMSI、K、OPC、S-NSSAI 和 DNN 对应。通过 WebUI API 创建，HTTP 201。实际系统应限制 WebUI 访问并修改默认管理密码。

## 本次验收证据

1. `helm list -n free5gc`：两个 Release 均 `deployed`；所有现存 5GC、MongoDB、gNB、UE Pod 均 `1/1 Running`。
2. SMF 日志：`Received PFCP Association Setup Accepted Response from UPF[10.42.0.19]`。
3. gNB 日志：SCTP connection established；NG Setup procedure is successful。
4. UE 日志：`Initial Registration is successful`，`PDU Session establishment is successful PSI[1]`，TUN `uesimtun0` 地址 `10.60.0.1`。
5. `kubectl exec` 从 `uesimtun0` ping `1.1.1.1`：3 发 3 收、0% 丢包，平均约 207 ms。该公网 RTT **不能**作为 5GC 内部时延或 edge UPF 收益。

动态 Pod IP、TUN IP、Helm revision 和 RTT 均只是本次快照，不能写死在复现配置中。

### VM 重启恢复测试（2026-10-06）

- 重启前：k3s 为 `enabled`、`gtp5g` 已加载；UE 绑定 `uesimtun0` ping 公网 3/3 成功。
- 执行 VM 重启后：k3s 自动 `active`、节点 Ready、`gtp5g` 自动加载；现存的 5GC、MongoDB、gNB、UE Pod 均回到 `1/1 Running`。
- gNB 重新建立 SCTP 并收到 NG Setup Response；UE 重新完成鉴权、注册、`PSI[1]` PDU Session，创建 `uesimtun0`。
- 重启后 UE 绑定该接口 ping `1.1.1.1` 仍为 3/3 成功。订阅数据在 MongoDB 持久卷中保留，未重新创建用户。
- 启动最初约一分钟，UE 曾显示小区不可用；gNB/AMF 就绪后自动重试成功。`Pod Running` 不等于业务就绪，须继续看注册、会话和 TUN 发流量。

## 常用复查命令（在 VM 上）

```bash
export KUBECONFIG=/home/lhm/.kube/config
export PATH=/home/lhm/.local/bin:$PATH
kubectl get nodes
helm list -n free5gc
kubectl -n free5gc get pods -o wide
kubectl -n free5gc logs deploy/free5gc-helm-free5gc-smf-smf --tail=80
kubectl -n free5gc logs deploy/ueransim-gnb --tail=50
kubectl -n free5gc logs deploy/ueransim-ue --tail=60
kubectl -n free5gc exec deploy/ueransim-ue -- ip -br addr show uesimtun0
kubectl -n free5gc exec deploy/ueransim-ue -- ping -I uesimtun0 -c 3 -W 4 1.1.1.1
```

## 下一步

单节点空白 VM 重建和第一台重启恢复均已验证，Chart 修补也已整理为版本化补丁并完成正向/反向检查。三节点 Flannel 单网络基线及固定 Profile 重部署已完成，见[三节点模块](07-three-node-topology-and-acceptance.md)；Multus 与真实 core/edge 拓扑仍待验证。`scripts/`中的空白 VM 初始化脚本尚未在空白 VM 完整运行，不能据此宣称自动化部署已经验证。
