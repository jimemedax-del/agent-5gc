# free5GC Helm 补丁

本目录保存项目相对于上游 Chart 的最小修改。补丁基线固定为：

```text
仓库：https://github.com/free5gc/free5gc-helm.git
版本：v4.2.2
提交：0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c
```

`free5gc-helm-v4.2.2-single-upf.patch`包含：

1. 在 Single UPF 模式下，让 SMF wrapper 只解析唯一 UPF Service；
2. 为 UERANSIM gNB 的 `wait-amf` initContainer 增加开关；
3. 在 UERANSIM 默认 Values 中声明该开关，默认保持上游行为。

应用补丁：

```bash
git -C /path/to/free5gc-helm checkout --detach 0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c
git -C /path/to/free5gc-helm apply /path/to/infra/patches/free5gc-helm-v4.2.2-single-upf.patch
```

部署脚本会自动完成固定提交、补丁检查和应用，不需要手工修改 Chart。
