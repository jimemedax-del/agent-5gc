# 基础设施基线

本目录保存可进入版本控制的最小实验配置：

| 文件 | 用途 |
|---|---|
| `free5gc-single-upf-values.yaml` | 单节点、无Multus的Single UPF配置 |
| `ueransim-single-node-values.yaml` | 与测试订阅匹配的UERANSIM配置 |
| `free5gc-test-subscriber-ue1.json` | 隔离实验环境使用的UE1测试订阅 |
| `k3s-registries.yaml` | k3s镜像源配置 |
| `patches/` | 相对于固定上游提交的最小Chart修改 |

正式Chart基线为`free5gc-helm v4.2.2`提交`0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c`，应用`patches/free5gc-helm-v4.2.2-single-upf.patch`后再传入受控Values。`tmp/`只是本地验证副本，不进入Git。

测试订阅中的身份和密钥仅用于公开实验配置，禁止用于真实网络。任何K3s令牌、SSH密钥、Tailscale Auth Key或访问令牌都不得保存在本目录。
