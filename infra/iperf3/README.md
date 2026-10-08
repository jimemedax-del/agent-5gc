# 受控测试端点配置

VM1 运行 `free5gc-iperf3.service`，固定监听 `192.168.244.128:5201`。服务使用 systemd 动态非特权用户，设置开机自启与失败重启。UDP 数据测试的接收 socket 由后续测试请求创建，配置阶段仅应观察到 TCP 5201 监听。

在 VM1 安装（只安装、启动，不发起性能测试）：

```bash
sudo bash scripts/configure-iperf3-server.sh infra/iperf3/free5gc-iperf3.service
systemctl status free5gc-iperf3.service
```

安装脚本保留 UFW 启用/禁用状态，仅添加 TCP/UDP 5201 的 worker 地址与 Pod 子网规则。当前 VM1 的 UFW 为 inactive。

## UE 客户端镜像

`Dockerfile` 在当前 UERANSIM 镜像的固定 digest 上增加 `iperf3`，保留 UE 配置与启动流程。Debian 11 包使用签名归档源，构建时复制宿主机公开 CA 根证书包；不复制私钥。归档元数据过期检查关闭，但 TLS 与 APT 包签名检查保留。

在 VM1 的独立构建目录内执行：

```bash
mkdir -p /home/lhm/.work/iperf3-client-build
cp infra/iperf3/Dockerfile /home/lhm/.work/iperf3-client-build/Dockerfile
cp /etc/ssl/certs/ca-certificates.crt /home/lhm/.work/iperf3-client-build/ca-certificates.crt
sudo docker build -t local/ueransim-iperf3:free5gc-v4.0.1-20261008 /home/lhm/.work/iperf3-client-build
sudo docker image save -o /tmp/ueransim-iperf3.tar local/ueransim-iperf3:free5gc-v4.0.1-20261008
```

将镜像归档传到 VM3 后，在 VM3 导入：

```bash
sudo k3s ctr images import /tmp/ueransim-iperf3.tar
```

之后在 VM1 升级 UERANSIM，第三个 Values 覆盖文件必须保留。该步骤会重建 UE Pod：

```bash
export KUBECONFIG=/home/lhm/.kube/config
/home/lhm/.local/bin/helm upgrade ueransim \
  /home/lhm/.work/free5gc-helm-v4.2.2/charts/ueransim \
  -n free5gc --reset-values \
  -f infra/ueransim-single-node-values.yaml \
  -f infra/ueransim-multinode-placement-values.yaml \
  -f infra/ueransim-iperf3-values.yaml \
  --atomic --wait --timeout 5m
kubectl exec -n free5gc deploy/ueransim-ue -- iperf3 --version
```

Values 使用 `pullPolicy: Never`。VM3 重装或镜像被清理后，需要先重新导入本地镜像；不向 Docker Hub 发布。当前实际构建记录与配置状态见 [配置记录](../../docs/09-controlled-data-endpoint-setup.md)。

性能测试留待下一阶段：应绑定当前 UE 的 `uesimtun0` 地址，确认路由经过 UPF，再采集 RTT、吞吐、抖动和丢包。服务与控制面共用 VM1，测量时需同时记录资源负载。
