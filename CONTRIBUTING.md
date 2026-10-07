# 项目提交与验证约定

## 基本流程

每项改动使用独立分支，保持一次提交只解决一类问题：

```bash
git switch main
git pull --ff-only origin main
git switch -c <type>/<short-topic>

# 修改并完成对应验证
git status --short
git diff --check
git add <明确的文件>
git commit -m "<type>: <summary>"
git push -u origin <branch-name>
```

建议的提交类型：`docs`、`infra`、`feat`、`fix`、`test`、`chore`。

## 合入前检查

- 文档结论使用`[已验证]`、`[已确认]`、`[待验证]`或`[待确认]`；
- Chart变更必须记录上游仓库、版本和完整提交哈希，并保存最小补丁；
- 补丁至少通过干净基线的`git apply --check`；
- Shell脚本通过语法检查后，还必须在目标Ubuntu环境运行，才能标记为`[已验证]`；
- 基础设施改动记录版本、命令、关键日志和端到端验收结果；
- NF放置实验只改变声明的实验变量，并保留同一套业务验收方法。

## 不得提交

- SSH私钥、K3s令牌、Tailscale Auth Key、访问令牌和真实密码；
- 临时Chart工作副本、容器镜像归档、运行日志和大型抓包；
- 未脱敏的真实用户数据。

`infra/free5gc-test-subscriber-ue1.json`只包含隔离实验环境使用的测试身份和密钥，不得用于真实网络。WebUI默认密码同样只允许在隔离实验环境使用。

## 版本与实验记录

不要直接修改上游Chart后只保存整份目录。推荐流程：

```bash
git clone https://github.com/free5gc/free5gc-helm.git
git -C free5gc-helm checkout --detach 0d0b4b392bbb1b099acb9a1b37c39e0647ff6d4c
git -C free5gc-helm apply /path/to/infra/patches/free5gc-helm-v4.2.2-single-upf.patch
```

实验失败也应记录，但必须区分观察、推测、根因和最终验证。影响架构的决策写入对应文档，具体故障写入`problem solve.md`。
