# 第二台 VM 从零重建：k3s / Helm / Single UPF free5GC

> 2026-10-06 实测记录。目标是验证部署与业务流程在另一台干净 Ubuntu VM 上能否复现；不是多节点实验。

## 结果

第二台 `lhm2-virtual-machine` 从未安装 Docker/k3s/Helm/gtp5g 的状态，完成了同版本部署。所有 5GC、MongoDB、gNB、UE Pod `1/1 Running`；SMF 收到 UPF 的 PFCP Association Accepted；UE 完成 5G AKA、注册和 `PSI[1]` PDU Session。绑定 `uesimtun0` ping `1.1.1.1` 为 **3/3 成功、0% 丢包**。

这证明了**配置与业务基线可在另一台 VM 重建**。镜像获取依赖从第一台导出的已验证缓存；因此尚未证明校园网络能够稳定地独立拉取所有镜像。第二台的重启恢复测试也已通过，见下文。

## 起始环境与固定版本

| 项目 | 值 |
|---|---|
| VM | Ubuntu 22.04.5 LTS，`6.8.0-138-generic`；8 vCPU、7.7 GiB 内存、49 GiB 根分区 |
| SSH | Tailscale 当前地址 `100.111.54.35`，用户 `lhm2`；地址可能变化，不写入 Chart |
| `gtp5g` | Tag `v0.9.5`，提交 `973d001b25832c5a8e8d34f6381eb0c705fb523d` |
| k3s | `v1.30.14+k3s1`；二进制 SHA-256 `12ab717251944a7bd6296a78769ccf3bdae012adc1ae277441b0f37484eb295a`，与第一台一致 |
| Helm | `v3.17.2`，官方 tarball 校验通过 |
| free5GC Chart | `free5gc-helm v4.2.2`，基线提交 `0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c` 加本地受控修补 |
| UERANSIM | 镜像 `free5gc/ueransim:v4.0.1`，容器报告程序版本 `v3.2.7` |

## 重建步骤

1. 安装编译依赖：`git`、`build-essential`、与 `uname -r` 匹配的 `linux-headers`，以及 **`gcc-12`**。第一次只装 `build-essential` 得到 GCC 11，编译报 `/bin/sh: gcc-12: not found`；补装后成功。不能把它解释为缺少或需要升级 Linux 内核。
2. 从官方仓库固定 `gtp5g v0.9.5`，核对提交，再运行 `make -j 8`、`sudo make install`。安装后 `modinfo gtp5g` 显示 `v0.9.5` 和匹配的 `6.8.0-138-generic` vermagic，`lsmod` 确认加载；安装脚本生成 `/etc/modules-load.d/gtp5g.conf`，包含 `udp_tunnel` 与 `gtp5g`。
3. 在安装 k3s 前，把 [镜像源配置](../infra/k3s-registries.yaml) 放到 `/etc/rancher/k3s/registries.yaml`。官方安装器固定 `INSTALL_K3S_VERSION='v1.30.14+k3s1'`，服务参数为：

   ```text
   server --disable traefik --disable servicelb
     --kubelet-arg fail-swap-on=false
     --kubelet-arg allowed-unsafe-sysctls=net.ipv4.ip_forward
   ```

   将 `/etc/rancher/k3s/k3s.yaml` 以仅用户可读权限复制为 `/home/lhm2/.kube/config`。确认节点 Ready、CoreDNS、local-path-provisioner、metrics-server Ready，以及默认 `local-path` StorageClass。
4. Helm 使用官方 `helm-v3.17.2-linux-amd64.tar.gz` 并核对 `.sha256sum`。Chart 工作目录：`/home/lhm2/free5gc-lab/helm-v4.2.2/charts/`；从固定基线 tarball 解压后，覆盖以下本地已验证修补：

   - SMF `templates/smf-configmap.yaml`：Single UPF wrapper；
   - UERANSIM `templates/gnb/gnb-deployment.yaml` 与 `values.yaml`：`gnb.waitForAmf` 开关；
   - [Single UPF Values](../infra/free5gc-single-upf-values.yaml) 与 [UERANSIM Values](../infra/ueransim-single-node-values.yaml)。

   两个 Chart 的 `helm lint` 均通过；渲染 SMF 只有 `gNB1 → UPF`，不含旧 ULCL 的 Branching/Anchor UPF。
5. 镜像源实际很慢：三个 k3s 系统 Pod 曾卡在 `ContainerCreating`，local-path-provisioner 拉取耗时约 7 分钟，CoreDNS 和 metrics-server 超过 13 分钟。备用源直接拉取也超时。改为从第一台**导出镜像缓存**、传到第二台并比对 SHA-256，再用 `k3s ctr -n k8s.io images import` 导入；第二台重启 k3s 后系统 Pod 即从本地缓存启动。系统镜像包 SHA-256：`11a3171e79c21684365cbbd7a3102923521e2686068e80aac7286c2397079303`。
6. 业务镜像包含 free5GC v4.2.2 的 NF、MongoDB、curl、busybox、debian 和 UERANSIM。缓存包 SHA-256：`72971042094ffdbec83d75a6cb210f57602127e915c977b4cd33f2a50115cd6a`。**导入必须指定** `--platform linux/amd64`；不指定平台时出现某个多架构内容摘要不存在，指定后全部镜像解包成功。第二台保存两个经过校验的 tar；第一台的临时导出 tar 已删除，第一台运行中的服务未更改。
7. 创建 `free5gc` namespace，按受控 Values 安装两个 Release：

   ```bash
   export KUBECONFIG=/home/lhm2/.kube/config
   export PATH=/home/lhm2/.local/bin:$PATH
   helm install free5gc-helm /home/lhm2/free5gc-lab/helm-v4.2.2/charts/free5gc \
     -n free5gc -f /home/lhm2/free5gc-lab/helm-v4.2.2/free5gc-single-upf-values.yaml \
     --wait --timeout 10m
   # 在 WebUI 中按 UE1 样例创建测试订阅；本次 API 返回 HTTP 201。
   helm install ueransim /home/lhm2/free5gc-lab/helm-v4.2.2/charts/ueransim \
     -n free5gc -f /home/lhm2/free5gc-lab/helm-v4.2.2/ueransim-single-node-values.yaml \
     --wait --timeout 5m
   ```

   订阅字段见 [UE1 样例](../infra/free5gc-test-subscriber-ue1.json)。实验系统应限制 WebUI NodePort 的访问并修改默认管理密码；不在文档中保存登录令牌。

## 独立验收命令

```bash
export KUBECONFIG=/home/lhm2/.kube/config
kubectl -n free5gc get pods
kubectl -n free5gc logs deploy/free5gc-helm-free5gc-smf-smf --tail=50
kubectl -n free5gc logs deploy/ueransim-gnb --tail=50
kubectl -n free5gc logs deploy/ueransim-ue --tail=60
kubectl -n free5gc exec deploy/ueransim-ue -- ip -br addr show uesimtun0
kubectl -n free5gc exec deploy/ueransim-ue -- ping -I uesimtun0 -c 3 -W 4 1.1.1.1
```

本次 UE 获得 `10.60.0.1/16`，公网 ping 平均约 207 ms。Pod IP、UE IP 和公网 RTT 是动态观测值，不是固定配置或 edge UPF 论文指标。

## 重启恢复验证（2026-10-06）

重启前，15 个 Pod 均为 `1/1 Running`，UE 通过 `uesimtun0` ping `1.1.1.1` 为 3/3 成功。执行 VM 重启后，k3s 节点恢复 `Ready`，`gtp5g` 自动加载。启动初期，多个 NF Pod 短暂显示 `Unknown`，UE 尚无 `uesimtun0`；这是容器运行时和依赖服务恢复期间的瞬时状态，本次未手工重建 Pod 或 Release。约两分钟后，15 个 Pod 全部恢复 `1/1 Running`，UE 自动获得 `10.60.0.1/16`，再次从 `uesimtun0` ping `1.1.1.1` 为 **3/3 成功、0% 丢包**。

结论：该 VM 的 k3s、`gtp5g`、free5GC 和 UERANSIM 能在本次重启后自动恢复业务；不能把刚启动时的 Pod `Unknown` 当作最终结果。此测试只验证单节点重启恢复，不代表多节点故障迁移或高可用。

## 自动化工具状态

仓库中的`bootstrap-k3s-node.sh`、`deploy-single-upf.sh`、`create-test-subscriber.sh`和`verify-single-upf.sh`根据本章人工步骤整理，用于减少后续重复操作。它们目前为`[待验证]`：只有在另一台空白VM完整运行并得到同等端到端证据后，才能把脚本化重建标记为已验证。本章人工记录仍是当前可信基线。
