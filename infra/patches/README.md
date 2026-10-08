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

## UPF 空指针回移植（独立于 Chart 补丁）

`go-upf-v1.2.10-remotesess-nil.patch` 来自上游修复提交
`cbad64a4caa89ac99ef062e257e80e23cb9eba29`（[PR #97](https://github.com/free5gc/go-upf/pull/97)），
应用基线为 go-upf `04c1ab640350f5d354d09fac82cd7b4d66c78533`（v1.2.10）。
仅修改 `RemoteSess` 的空值处理，并加入上游删除会话槽回归测试，不升级依赖或其他 NF。

`scripts/build-patched-upf.sh` 在原实现上先运行新增测试并要求出现空指针，之后应用修复，
运行 `TestLocalNode` 和 `go vet`，校验依赖文件未变，再用 Go 1.25.5 构建二进制。
独立镜像使用 `infra/upf/Dockerfile`，只替换固定原镜像中的 `/free5gc/upf`；
部署使用 `infra/free5gc-upf-nilfix-values.yaml`，详细验证及回滚见数据面测试模块。
