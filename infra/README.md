# 基础设施基线

本目录保存可进入版本控制的最小实验配置：

| 文件 | 用途 |
|---|---|
| `free5gc-single-upf-values.yaml` | 单节点、无Multus的Single UPF配置 |
| `ueransim-single-node-values.yaml` | 与测试订阅匹配的UERANSIM配置 |
| `free5gc-multinode-placement-values.yaml` | 三节点阶段的free5GC放置覆盖：控制面/数据库在VM1，UPF在VM2 |
| `ueransim-multinode-placement-values.yaml` | 三节点阶段的UERANSIM放置覆盖：gNB与UE在VM3 |
| `ueransim-iperf3-values.yaml` | UE 客户端镜像覆盖；使用前先向 VM3 的 containerd 导入镜像 |
| `free5gc-upf-nilfix-values.yaml` | 固定 Digest 的 UPF 空指针修复镜像覆盖，使用前在 UPF 节点导入镜像及 Digest 别名 |
| `upf/` | 在固定原 UPF 运行时镜像上仅替换修复后二进制的 Dockerfile |
| `iperf3/` | VM1 测试服务 systemd 配置、UE 客户端 Dockerfile 与复现步骤 |
| `free5gc-test-subscriber-ue1.json` | 隔离实验环境使用的UE1测试订阅 |
| `k3s-registries.yaml` | k3s镜像源配置 |
| `patches/` | 相对于固定上游提交的最小Chart修改 |

正式Chart基线为`free5gc-helm v4.2.2`提交`0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c`，应用`patches/free5gc-helm-v4.2.2-single-upf.patch`后再传入受控Values。`tmp/`只是本地验证副本，不进入Git。

MongoDB Values 显式禁用上游默认的 `install-tini` init container，改用镜像原生 entrypoint，避免实验 Pod 在启动时依赖 Debian 软件源。

CHF 保持启用，但 Values 关闭其默认的 CGF FTP CDR 导出。该导出目标不属于本实验环境，连接超时会同步阻塞会话创建并使 AMF 的 PDU Session 请求超时；关闭它不影响 CHF 的 SBI API、订阅策略或本实验的用户面验证。

测试订阅中的身份和密钥仅用于公开实验配置，禁止用于真实网络。任何K3s令牌、SSH密钥、Tailscale Auth Key或访问令牌都不得保存在本目录。

三节点部署时，必须先传入对应的单节点基线文件，再传入放置覆盖文件。例如：

```bash
helm upgrade --install free5gc-helm <fixed-chart-path> -n free5gc \
  --reset-values \
  -f infra/free5gc-single-upf-values.yaml \
  -f infra/free5gc-multinode-placement-values.yaml \
  --atomic --wait --timeout 10m
```

先使用同一命令加`--dry-run`检查渲染结果，再执行真实升级。`--reset-values`是必须项：Helm 的历史 Values 可能保留旧数组或旧配置。渲染结果应显示控制面位于VM1、UPF位于VM2且UERANSIM位于VM3。`local-path` MongoDB PV不跨节点迁移。

当前已部署独立 UPF 判空修复镜像；继续维护这一运行基线时，在上述两份 Values 之后增加
`-f infra/free5gc-upf-nilfix-values.yaml`，否则会回到原始 UPF。旧配置保留作历史基线与回滚。
镜像不发布到公网；`Never` 模式需先导入，并将 Tag 注册为对应 `@sha256` 别名。
构建、验证结果与回滚边界见[数据面测试模块](../docs/10-controlled-data-plane-baseline.md)。
