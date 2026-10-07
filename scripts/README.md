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

单独创建订阅者：

```bash
WEBUI_URL=http://127.0.0.1:30500 \
  ./scripts/create-test-subscriber.sh infra/free5gc-test-subscriber-ue1.json
```

WebUI用户名和密码可以通过`WEBUI_USERNAME`、`WEBUI_PASSWORD`环境变量提供。脚本不会输出访问令牌或鉴权密钥。
