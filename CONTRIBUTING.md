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

## 团队协作流程

`main`是**稳定分支名**，不是本地目录。它只保留已审阅、可复现的成果；任何人都不直接在`main`开发或向其直接推送。

协作单位固定为：**一个 GitHub Issue（或明确任务）→ 一个短期分支 → 一个 Pull Request（PR）**。例如：

```text
docs/cnf-capability-survey       # 文档或能力调研
feat/pcf-capability-probe        # 新功能或最小验证
test/qos-probe                   # 测试与测量
infra/multus-lab-profile         # 环境与部署配置
```

新成员首次加入：

```bash
git clone https://github.com/jimemedax-del/agent-5gc.git
cd agent-5gc
git switch main
git pull --ff-only origin main
git switch -c <type>/<short-topic>
```

日常提交后，向自己的远程分支推送，并在GitHub创建PR：

```bash
git status --short
git diff --check
git add <明确的文件>
git commit -m "<type>: <summary>"
git push -u origin <type>/<short-topic>
```

PR描述必须写清：任务目标、改动范围、验证命令及结果、尚未验证的边界，以及关联的Issue。至少一名同学审阅后，由项目负责人使用**Squash merge**合入`main`；合并后删除远程分支。

合并前将自己的分支同步到最新`main`。只允许重写**自己**的分支历史，且使用保护性选项：

```bash
git fetch origin
git rebase origin/main
# 如有冲突：修复冲突后 git add <文件> && git rebase --continue
git push --force-with-lease
```

`--force-with-lease`不能用于`main`，也不要用无保护的`--force`。PR合并后，本地清理方式：

```bash
git switch main
git pull --ff-only origin main
git branch -d <type>/<short-topic>
```

### 分工与实验环境

- 开始任务前先认领Issue；多人不要同时修改同一文件的同一段、同一Chart Values或同一实验记录。
- VM、集群和实验数据属于共享状态：一次实验指定唯一执行人；执行前记录配置版本，结束后记录结果目录和结论。不要将临时运行状态当作可合并源码。
- PR审阅检查范围是否越界、是否保留可复现证据、是否泄露凭据，以及是否符合当前路线`R2`。

### GitHub仓库设置（由仓库管理员操作）

- 仓库保持`Private`，邀请同学为Collaborator（通常授予`Write`）；
- 对`main`启用分支保护：必须PR、至少1个批准、合并前必须同步最新`main`、禁止force push和删除；
- 使用GitHub Issue分派任务，用PR关联Issue；不在聊天记录中以“口头完成”替代提交和PR。

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
