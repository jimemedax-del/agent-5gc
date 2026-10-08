# Problem Solve：free5GC 实验问题与解决记录

> 最后更新：2026-10-07
> 用途：持续记录项目中已实际遇到的问题、根因、完整解决流程、验证证据和复现方法。
> 安全约定：不记录虚拟机密码、SSH 私钥、WebUI 令牌或真实生产密钥。文中的 K、OPc、SUPI 均为封闭实验环境的测试数据。

## 案例索引

| 编号 | 日期 | 问题 | 结论 |
|---|---|---|---|
| PS-001 | 2026-10-05 | 在现有单 UE free5GC Compose 基线上新增第二个 UERANSIM UE | [已解决] 两个 UE 均完成注册、5G AKA 鉴权、PDU Session 和公网连通性验证 |
| PS-002 | 2026-10-05 | 双 PDU Session 中 `PSI[1]` 建立失败，而 `PSI[2]` 正常 | [已解决] 补齐 PCF 的 FlowRule 与 ChargingData 后，UE1、UE2 的两条会话均建立并经 UPF 访问公网 |
| PS-003 | 2026-10-06 | k3s/Helm 单 UPF 部署中 UPF、SMF 和 gNB 依次受阻 | [已解决] 放行 UPF sysctl、修正 SMF 拓扑、关闭非必需的 gNB 前置镜像检查；端到端业务通过 |
| PS-004 | 2026-10-06 | 第二台空白 VM 的 `gtp5g` 编译失败与 k3s 镜像长时间拉取 | [已解决] 补齐匹配内核的 GCC 12，校验后导入同版本镜像缓存；第二台业务端到端通过 |
| PS-005 | 2026-10-07 | VM 重启后 Tailscale 在线但 SSH 与 VS Code Remote-SSH 超时 | [已解决] 重启 `tailscaled` 后普通数据面和 SSH 恢复；VS Code 重新建立 Remote-SSH 会话 |

---

# PS-001：新增第二个 UERANSIM UE，并完成注册与鉴权

## 1. 目标与验收标准

在现有 free5GC Docker Compose 单机基线上新增第二名独立用户，不停止核心网 NF，并满足：

1. UE1 和 UE2 使用不同的 SUPI、K、OPc 和设备标识；
2. UE2 能通过现有 UERANSIM gNB 接入 AMF；
3. UE2 完成 5G AKA 鉴权和 NAS 安全模式；
4. UE2 收到 `Registration Accept`；
5. UE2 建立 PDU Session 并得到独立地址；
6. UE1 仍然可用；
7. 两个 UE 均能通过各自的 `uesimtun0` 经 UPF 访问公网。

## 2. 实验环境与路径

| 项目 | 本次使用值 |
|---|---|
| 远程工作目录 | `/home/lhm/free5gc-lab/free5gc-compose` |
| free5GC 编排版本 | free5gc-compose `v4.2.2` |
| NF 镜像版本 | `v4.2.1`（官方 Compose v4.2.2 的实际固定值） |
| UERANSIM | `v3.3.0` |
| gtp5g | `v0.9.5` |
| Docker 网络 | `free5gc-compose_privnet` |
| PLMN | MCC `208`、MNC `93`，即 `20893` |
| 成功使用的切片 | SST `1`、SD `112233` |
| DNN | `internet` |

相关文件：

```text
config/gnbcfg.yaml       # gNB 配置
config/uecfg.yaml        # UE1 配置
config/uecfg2.yaml       # 本次新增的 UE2 配置
```

## 3. 最终实现结构

```text
UE1 nr-ue 进程（位于 ueransim 容器）
                     \
                      → 同一个 UERANSIM gNB → AMF → AUSF → UDM → UDR/MongoDB
                     /                           ↓
UE2 独立容器（ue2）                               SMF → UPF → Internet
```

当前采用的是最简实验实现：

- `ueransim` 容器运行 gNB，同时后台运行 UE1；
- `ue2` 是独立 Docker 容器；
- 两个 UE 位于不同网络命名空间，因此都可以拥有名为 `uesimtun0` 的接口，不会冲突；
- UE2 不是当前 Compose 文件中的正式服务，属于手工创建的实验容器；长期方案应把每个 UE 写成 Compose 服务。

## 4. 两名用户的实验身份

| 字段 | UE1 | UE2 |
|---|---|---|
| SUPI | `imsi-208930000000001` | `imsi-208930000000002` |
| IMEI | `356938035643803` | `356938035643804` |
| IMEISV | `4370816125816151` | `4370816125816152` |
| K | `8baf473f2f8fd09487cccbd7097c6862` | `465b5ce8b199b49faa5f0a2ee238a6bc` |
| OP 类型 | `OPC` | `OPC` |
| OPc | `8e27b6af0e692e750f32667a3b14605d` | `e8ed289deba952e4283b54e88e6183ca` |
| AMF | `8000` | `8000` |

这些值仅用于本地实验。真实网络中，每个用户的长期鉴权密钥必须独立生成、受控保存，不能提交到公共仓库。

## 5. 完整实现流程

### 5.1 创建 UE2 配置

从已经验证过的 UE1 配置复制：

```bash
cd ~/free5gc-lab/free5gc-compose
cp config/uecfg.yaml config/uecfg2.yaml
```

在 `config/uecfg2.yaml` 中至少修改：

```yaml
supi: "imsi-208930000000002"
mcc: "208"
mnc: "93"
key: "465b5ce8b199b49faa5f0a2ee238a6bc"
op: "e8ed289deba952e4283b54e88e6183ca"
opType: "OPC"
amf: "8000"
imei: "356938035643804"
imeiSv: "4370816125816152"
```

保留与 UE1 相同的 PLMN、切片、DNN 和 gNB 搜索列表。当前列表包含：

```yaml
gnbSearchList:
  - 127.0.0.1
  - gnb.free5gc.org
```

UE2 在独立容器中无法使用 `127.0.0.1` 访问另一个容器中的 gNB，但可通过 Docker DNS 名称 `gnb.free5gc.org` 访问。

### 5.2 允许独立 UE 容器连接 gNB

原配置为：

```yaml
linkIp: 127.0.0.1
```

这只允许 gNB 容器内部的 UE 进程连接模拟无线链路。按 free5gc-compose 官方说明改为：

```yaml
linkIp: gnb.free5gc.org
ngapIp: gnb.free5gc.org
gtpIp: gnb.free5gc.org
```

三个地址分别服务于：

- `linkIp`：UERANSIM 自己的 UE-gNB 模拟无线链路；
- `ngapIp`：gNB 到 AMF 的 N2/NGAP 接口；
- `gtpIp`：gNB 到 UPF 的 N3/GTP-U 接口。

修改配置后必须重建 gNB 容器，因为运行中的 `nr-gnb` 不会自动重新加载配置：

```bash
cd ~/free5gc-lab/free5gc-compose
docker compose up -d --force-recreate ueransim
```

验证：

```bash
docker logs --tail 30 ueransim
```

应看到：

```text
SCTP connection established
NG Setup procedure is successful
```

### 5.3 在核心网创建完整的 UE2 订阅

核心网中的订阅身份必须与 `uecfg2.yaml` 完全一致。注册和鉴权涉及：

```text
UE2 → gNB → AMF → AUSF → UDM → UDR → MongoDB
```

本次通过 WebUI API 创建，接口为：

```text
POST /api/subscriber/imsi-208930000000002/20893
```

请求需要运行时取得 WebUI Token，不能把 Token 写入文档或仓库。完整请求对象使用与当前 WebUI 版本严格匹配的官方 Postman 样例：

```text
/home/lhm/free5gc-lab/webconsole-v1.4.4/
free5gc-Webconsole.postman_collection.json
```

不要只提交 SUPI、K 和 OPc。一个可正常完成注册和 PDU Session 的订阅至少应完整包含：

```text
AuthenticationSubscription
AccessAndMobilitySubscriptionData
SessionManagementSubscriptionData
SmfSelectionSubscriptionData
AmPolicyData
SmPolicyData
```

本次 UE2 的关键鉴权字段为：

```json
{
  "ueId": "imsi-208930000000002",
  "plmnID": "20893",
  "AuthenticationSubscription": {
    "authenticationManagementField": "8000",
    "authenticationMethod": "5G_AKA",
    "sequenceNumber": "000000000023",
    "permanentKey": {
      "encryptionAlgorithm": 0,
      "encryptionKey": 0,
      "permanentKeyValue": "465b5ce8b199b49faa5f0a2ee238a6bc"
    },
    "milenage": {
      "op": {
        "encryptionAlgorithm": 0,
        "encryptionKey": 0,
        "opValue": ""
      }
    },
    "opc": {
      "encryptionAlgorithm": 0,
      "encryptionKey": 0,
      "opcValue": "e8ed289deba952e4283b54e88e6183ca"
    }
  }
}
```

同时设置非空 GPSI，例如：

```json
"gpsis": ["msisdn-0900000002"]
```

创建成功返回：

```text
HTTP 201 Created
```

更新已存在的完整订阅成功返回：

```text
HTTP 204 No Content
```

回读时不能只检查 HTTP 状态码，必须检查：

- `AuthenticationSubscription.permanentKey.permanentKeyValue`；
- `AuthenticationSubscription.opc.opcValue`；
- `AccessAndMobilitySubscriptionData.nssai.defaultSingleNssais` 非空；
- `SmfSelectionSubscriptionData.subscribedSnssaiInfos` 非空；
- DNN、SST、SD 与 UE/SMF 配置一致。

### 5.4 恢复并验证 UE1

gNB 容器被重建后，原来运行在其中的 UE1 进程会消失，需要重新启动：

```bash
docker exec -d ueransim sh -c \
  'exec ./nr-ue -c ./config/uecfg.yaml > /tmp/ue1.log 2>&1'
```

检查 UE1：

```bash
docker exec ueransim grep -E \
  'Authentication Request|Security Mode|Registration accept|Initial Registration is successful|PDU Session establishment is successful|TUN interface' \
  /tmp/ue1.log
```

### 5.5 启动独立 UE2 容器

```bash
docker run -d \
  --name ue2 \
  --network free5gc-compose_privnet \
  --cap-add NET_ADMIN \
  --device /dev/net/tun \
  -v /home/lhm/free5gc-lab/free5gc-compose/config/uecfg2.yaml:/ueransim/config/uecfg2.yaml:ro \
  free5gc/ueransim:latest \
  ./nr-ue -c ./config/uecfg2.yaml
```

参数含义：

| 参数 | 作用 |
|---|---|
| `--network free5gc-compose_privnet` | 让 UE2 能通过 Docker DNS 找到 `gnb.free5gc.org` |
| `--cap-add NET_ADMIN` | 允许 UERANSIM 配置路由和 TUN 接口 |
| `--device /dev/net/tun` | 把宿主机 TUN 设备提供给容器 |
| `-v ...:ro` | 以只读方式挂载 UE2 配置 |
| `./nr-ue -c ...` | 启动 UE2 模拟进程 |

### 5.6 验证注册、鉴权和 PDU Session

```bash
docker logs ue2
```

关键成功标志及其含义：

```text
Authentication Request received
# AMF 已经通过 AUSF/UDM/UDR 获取鉴权向量并向 UE 发起 5G AKA

Security Mode Command received
# 鉴权通过，开始建立 NAS 安全上下文

Registration accept received
Initial Registration is successful
# 核心网注册完成

PDU Session establishment is successful
# SMF/UPF 会话建立成功

TUN interface[uesimtun0, 10.61.0.8] is up
# UE2 获得会话地址并创建用户面接口
```

查看接口：

```bash
docker exec ue2 ip -br addr show uesimtun0
```

验证用户面：

```bash
docker exec ue2 ping -I uesimtun0 -c 3 1.1.1.1
```

绑定 `uesimtun0` 非常重要。否则 Ping 可能直接使用容器的普通 `eth0`，无法证明流量经过 gNB、GTP-U 和 UPF。

## 6. 本次遇到的问题、根因与解决过程

### 问题 1：独立 UE2 无法使用 gNB 的 `127.0.0.1`

**现象**

原来的 UE1 与 gNB 位于同一个容器，`linkIp: 127.0.0.1` 可以工作。但 UE2 位于独立容器，容器内的 `127.0.0.1` 只代表 UE2 自己。

**根因**

容器拥有独立网络命名空间，不同容器不能通过回环地址互访。

**解决**

将 `config/gnbcfg.yaml` 中的 `linkIp` 改为 Docker DNS 名称 `gnb.free5gc.org`，然后重建 gNB 容器。

### 问题 2：查询不存在的订阅者返回 `200 + 空骨架`

**现象**

调用：

```text
GET /api/subscriber/imsi-208930000000002/20893
```

即使底层订阅不存在，WebUI 仍可能返回 HTTP `200`，并填充 URL 中的 `ueId`、`plmnID`，但鉴权字段为空。

**错误判断**

如果只检查 HTTP 200，会误以为用户已存在。

**正确判断**

必须进一步检查 K、OPc、NSSAI 和 SMF 选择信息是否实际存在。

### 问题 3：从不完整订阅对象复制 UE2，导致两名用户数据不全

**现象**

最初直接读取 UE1 聚合对象并复制为 UE2。该对象当时已经缺少部分底层订阅记录，因此 UE2 虽然创建成功，但不是完整订阅模板。

**根因**

WebUI 的聚合 GET 返回值不能保证每个底层 UDR 集合都完整存在。复制“当前对象”会把缺失状态一并复制。

**解决**

改用与 WebUI `v1.4.4` 完全匹配的官方 Postman 完整样例，分别补齐 UE1 和 UE2：

- 鉴权订阅；
- 接入与移动性订阅；
- 会话管理订阅；
- SMF 选择订阅；
- AM/SM 策略；
- GPSI、切片和 DNN。

### 问题 4：UE 显示 `Initial Registration failed [CONGESTION]`

**UE 侧现象**

```text
Initial Registration failed [CONGESTION]
```

**AMF 真实错误**

```text
Nausf_UEAU Authenticate Request Error: 500, Internal Server Error
Authentication procedure failed
```

**UDM/UDR 真实错误**

```text
Error on QueryAuthSubsData: 404, Not Found
QueryAuthSubsDataProcedure err: Data not found
```

**结论**

`CONGESTION` 在这里不是 CPU、内存或链路拥塞，而是 AMF 对内部鉴权失败映射出的通用 NAS 拒绝原因。排错不能停留在 UE 日志，必须沿：

```text
UE → AMF → AUSF → UDM → UDR
```

逐层查看日志。

**解决**

通过 WebUI API 补齐 `AuthenticationSubscription`，并确保 K、OPc 与 UERANSIM 配置一致。

### 问题 5：鉴权成功，但收不到 `Registration Accept`

**UE 侧现象**

```text
Authentication Request received
Security Mode Command received
NAS timer[3510] expired
```

**AMF 真实错误**

```text
SDM_Get Slice Selection Subscription Data Failed
Status:500 Cause:SYSTEM_FAILURE
AMF can not select an target AMF by NRF
```

**根因**

鉴权订阅已经恢复，但切片选择订阅仍缺失。AMF 在完成安全模式后无法获取完整 NSSAI 数据，因此不能完成初始注册。

**解决**

不能只修复 K 和 OPc；必须用完整模板恢复：

```text
AccessAndMobilitySubscriptionData.nssai
SmfSelectionSubscriptionData.subscribedSnssaiInfos
SessionManagementSubscriptionData
```

### 问题 6：WebUI 更新接口返回 500，并出现反射 Panic

**日志**

```text
panic: reflect: call of reflect.Value.Len on zero Value
WebUI.getMsisdn
PUT /api/subscriber/... → 500
```

**根因**

WebUI `v1.4.4` 的更新逻辑会读取 GPSI/MSISDN；当 `gpsis` 为空或字段缺失时，代码对无效反射值调用 `Len()`，触发 Panic。

**解决**

提交 PUT 前保证：

```json
"AccessAndMobilitySubscriptionData": {
  "gpsis": ["msisdn-0900000001"]
}
```

UE2 使用不同值，例如 `msisdn-0900000002`。

### 问题 7：修复数据后 UE 没有立刻恢复

**现象**

UE 连续注册失败后进入 NAS 定时器和退避流程，修复数据库后不一定马上重试。

**解决**

停止并重新启动对应 `nr-ue` 进程，从干净状态重新执行注册：

```bash
docker exec ueransim pkill -f './nr-ue' || true
docker exec -d ueransim sh -c \
  'exec ./nr-ue -c ./config/uecfg.yaml > /tmp/ue1.log 2>&1'
```

UE2 可使用：

```bash
docker restart ue2
```

## 7. 最终验证结果

| 用户 | SUPI | 本次分配地址 | 注册 | 鉴权 | PDU Session | 公网 Ping |
|---|---|---:|---|---|---|---|
| UE1 | `imsi-208930000000001` | `10.61.0.7` | 成功 | 成功 | 成功 | 3/3，0% 丢包 |
| UE2 | `imsi-208930000000002` | `10.61.0.8` | 成功 | 成功 | 成功 | 3/3，0% 丢包 |

UE1 关键日志：

```text
Authentication Request received
Received SQN [000000000048]
Security Mode Command received
Registration accept received
Initial Registration is successful
PDU Session establishment is successful PSI[2]
TUN interface[uesimtun0, 10.61.0.7] is up
```

UE2 关键日志：

```text
Authentication Request received
Received SQN [000000000023]
Security Mode Command received
Registration accept received
Initial Registration is successful
PDU Session establishment is successful PSI[2]
TUN interface[uesimtun0, 10.61.0.8] is up
```

数据面验证：

```text
UE1 → 1.1.1.1：3 transmitted, 3 received, 0% packet loss
UE2 → 1.1.1.1：3 transmitted, 3 received, 0% packet loss
```

IP 地址由 SMF/UPF 动态分配，`10.61.0.7` 和 `10.61.0.8` 只是本次结果，不能作为固定地址写入自动化逻辑。

## 8. 日常检查命令

```bash
# 容器状态
docker ps --filter name=ueransim --filter name=ue2

# UE1 进程与接口
docker exec ueransim pgrep -af './nr-ue'
docker exec ueransim ip -br addr show uesimtun0
docker exec ueransim tail -n 100 /tmp/ue1.log

# UE2 状态、接口与日志
docker logs --tail 100 ue2
docker exec ue2 ip -br addr show uesimtun0

# 分别验证用户面，必须绑定 TUN 接口
docker exec ueransim ping -I uesimtun0 -c 3 1.1.1.1
docker exec ue2 ping -I uesimtun0 -c 3 1.1.1.1

# 查看核心网相关日志
docker logs --tail 100 amf
docker logs --tail 100 ausf
docker logs --tail 100 udm
docker logs --tail 100 udr
docker logs --tail 100 smf
docker logs --tail 100 upf
```

## 9. 停止、重启与清理

停止或重启 UE2：

```bash
docker stop ue2
docker start ue2
```

当前 UE2 不是 Compose 正式服务。执行整套环境的 `docker compose down` 前，先移除 UE2，否则 Compose 网络可能因仍有活动端点而无法删除：

```bash
docker stop ue2
docker rm ue2

cd ~/free5gc-lab/free5gc-compose
docker compose down
```

不要随意执行：

```bash
docker compose down -v
```

`-v` 会删除 MongoDB 命名卷，已创建的订阅者也会丢失。

## 10. 本次沉淀出的通用排错顺序

新增 UE 失败时，按以下顺序排查：

```text
1. 配置层：SUPI、K、OPc、PLMN、SST、SD、DNN 是否一致
2. 接入层：UE 是否发现小区，RRC 是否建立
3. NGAP 层：gNB 是否已向 AMF 完成 NG Setup
4. 鉴权层：AMF → AUSF → UDM → UDR 是否成功
5. 订阅层：鉴权、NSSAI、SMF 选择、DNN 数据是否完整
6. 注册层：是否收到 Security Mode Command 和 Registration Accept
7. 会话层：是否收到 PDU Session Establishment Accept
8. 用户面：是否创建 TUN，绑定 TUN 的 Ping 是否成功
```

不要只看 UE 最后一条错误。`CONGESTION`、超时等终端现象可能只是核心网内部 404/500 的外层表现。

## 11. 后续改进

- [待实现] 将 UE1 和 UE2 都改为正式 Compose 服务，避免手工 `docker run`；
- [待实现] 编写幂等的订阅者创建工具，不能把 HTTP 200 直接解释为“用户存在”；
- [待实现] 工具应校验完整订阅结构，而不只是 SUPI/K/OPc；
- [待实现] 增加优雅注销和会话释放，避免 UPF 遗留旧 PDR/FAR/QER；
- [待验证] 扩展到更多并发 UE，记录注册成功率、会话建立时间和用户面吞吐量；
- [待验证] 将同样的用户目录和验证方法迁移到 Helm/Kubernetes 环境。

---

# PS-002：双 PDU Session 的 `PSI[1]` 因 PCF 策略数据不完整而失败

## 1. 目标与验收标准

让 UE1、UE2 均能同时建立两条 PDU Session，并证明两条会话都实际经过
gNB 与 UPF 访问外部网络：

| 会话 | 切片 | 预期结果 |
|---|---|---|
| `PSI[1]` | SST `1`、SD `010203` | 建立成功，创建独立 TUN 接口，可 Ping `1.1.1.1` |
| `PSI[2]` | SST `1`、SD `112233` | 建立成功，创建独立 TUN 接口，可 Ping `1.1.1.1` |

`PSI`（PDU Session Identity）只是 UE 对一条 PDU Session 的本地编号；它不是
额外的 NF，也不等同于切片编号。本实验中，两条 `sessions` 配置按顺序获得
`PSI[1]`、`PSI[2]`。

## 2. 环境与相关数据

| 项目 | 使用值 |
|---|---|
| 编排环境 | PS-001 的单机 free5GC Compose 基线 |
| PCF 镜像 / 源码版本 | `free5gc/pcf:v4.2.1` / `pcf v1.4.1` |
| 策略数据数据库 | `free5gc.policyData`（MongoDB） |
| UE | UE1：`imsi-208930000000001`；UE2：`imsi-208930000000002` |
| 失败会话 | `PSI[1]`，`1-010203`，DNN `internet` |
| 正常会话 | `PSI[2]`，`1-112233`，DNN `internet` |

本案例处理的是**注册已经成功之后**的 SM 策略与会话建立故障。不能因为
`Registration Accept` 已出现，便推断所有 PDU Session 都已可用。

## 3. 现象与错误路径

两个 UE 都完成了注册与鉴权；`PSI[2]` 正常获得 `uesimtun0`。但两台 UE 的
`PSI[1]` 均反复超时重传，收不到 `PDU Session Establishment Accept`。

排查路径如下：

```text
UE 的 PSI[1] 重传/超时
→ AMF/SMF 向 PCF 请求 SM Policy
→ PCF 的 /npcf-smpolicycontrol/v1/sm-policies 返回 HTTP 500
→ SMF 无法完成 PSI[1] 建立
```

因此问题不在 UE、gNB、SCTP 或 UPF 数据面，而在该切片/DNN 对应的 PCF 订阅策略。

## 4. 根因

同一会话的策略记录存在三个连续缺陷。前一个修复后，才会暴露下一个：

1. `flowRule` 缺少 `precedence`。PCF `smpolicy.go` 读取
   `flowRule["precedence"].(float64)`，缺失时发生：

   ```text
   interface conversion: interface {} is nil, not float64
   ```

2. 原 `filter` 为：

   ```text
   permit out 17 from 192.168.0.0/24 8000 to 60.60.0.0/24
   ```

   该格式不符合当前 PCF/WebUI 模板所使用的 CIDR 形式。只补 `precedence` 后，
   PCF 又出现：

   ```text
   index out of range [1] with length 1
   ```

3. 没有与该 FlowRule 精确匹配的 `chargingData`。PCF 按
   `ueId + snssai + dnn + filter` 查找；查不到仍读取
   `chargingMethod`，触发：

   ```text
   interface conversion: interface {} is nil, not string
   ```

第 3 点反映了 `pcf v1.4.1` 的空值保护不足；但更直接的配置问题是本实验订阅者的
策略数据确实不完整。

## 5. 最小修复流程

> 风险说明：本次不再通过 WebUI 删除后重建订阅者。该版本 WebUI 曾对空 `gpsis`
> 触发 Panic，且 PUT/PATCH 可能重写其他订阅数据。修复只更新两个 UE 的目标切片、
> DNN 的目标策略字段；操作前必须先导出对应 MongoDB 文档。

### 5.1 备份并确认目标记录

目标集合为：

```text
free5gc.policyData.ues.flowRule
free5gc.policyData.ues.chargingData
```

仅选择以下条件的记录：UE1 或 UE2、`snssai: "01010203"`（SST 1 / SD 010203 的
内部编码）、`dnn: "internet"`。不要对其他切片或其他 UE 批量覆盖。

### 5.2 补齐 FlowRule

对两名 UE 的 `1-010203 / internet` FlowRule 写入以下受当前版本支持的最小字段：

```json
{
  "filter": "1.1.1.1/32",
  "precedence": 128,
  "qosRef": 0
}
```

并确认同一切片/DNN 已有对应的 QoS Flow：

```json
{
  "snssai": "01010203",
  "dnn": "internet",
  "qosRef": 0,
  "5qi": 5,
  "mbrUL": "200 Mbps",
  "mbrDL": "100 Mbps"
}
```

### 5.3 新增精确匹配的 ChargingData

为 UE1、UE2 各增加一条 Flow-level 计费策略；其中 `filter` 必须与 FlowRule 完全相同：

```json
{
  "snssai": "01010203",
  "dnn": "internet",
  "qosRef": 0,
  "filter": "1.1.1.1/32",
  "chargingMethod": "Offline",
  "quota": "0",
  "unitCost": "1"
}
```

### 5.4 重新发起会话

策略数据修复后，已超时的 UE 不一定自动立即重试。重启对应 UE 进程或容器，
让其从干净状态重新注册并建立两条 Session；无需删除用户或重建核心网。

## 6. 验证结果

PCF 的关键接口由失败变为：

```text
POST /npcf-smpolicycontrol/v1/sm-policies → 201 Created
```

最终验证：

| UE | `PSI[2]` | `PSI[1]` | 用户面验证 |
|---|---|---|---|
| UE1 | `uesimtun0`，`10.61.0.1` | `uesimtun1`，`10.60.2.21` | 两接口 Ping `1.1.1.1` 均 `3/3` 成功 |
| UE2 | `uesimtun0`，`10.61.0.2` | `uesimtun1`，`10.60.2.20` | 两接口 Ping `1.1.1.1` 均 `3/3` 成功 |

每条会话都必须绑定自己的 TUN 接口验证，例如：

```bash
# UE1 的两个 PDU Session
docker exec ueransim ping -I uesimtun0 -c 3 1.1.1.1
docker exec ueransim ping -I uesimtun1 -c 3 1.1.1.1

# UE2 的两个 PDU Session
docker exec ue2 ping -I uesimtun0 -c 3 1.1.1.1
docker exec ue2 ping -I uesimtun1 -c 3 1.1.1.1
```

这证明的用户面路径是：

```text
UE 的指定 uesimtunX → gNB → UPF → 外网 1.1.1.1
```

## 7. 复用的排错原则与启动注意事项

1. 区分层次：注册成功只说明 UE 已接入控制面；每个 `PSI` 都必须独立看到
   `PDU Session Establishment Accept`、独立 TUN 接口和绑定接口的连通性。
2. 看完整链路：PDU Session 建立失败时，先查 SMF/PCF 的 HTTP 错误和 PCF 日志，
   不要只在 UE 侧反复重启。
3. 策略数据必须一致：FlowRule、QoS Flow 和 ChargingData 的
   `snssai`、`dnn`、`qosRef`、`filter` 应能够精确关联。
4. 谨慎修改数据：先备份、再做最小更新、回读验证；避免用 WebUI 的全量 PUT/PATCH
   意外覆盖其他订阅集合。
5. 整套 `docker compose restart` 会并行重启 AMF 与 gNB，gNB 可能因 AMF 尚未稳定而
   出现 SCTP association shutdown。应先等待 AMF 稳定，确认 gNB 的 NG Setup 成功，
   再启动 UE。

## 8. 参考实现位置

当前 WebUI 的官方订阅模板可用于核对字段结构：

```text
/home/lhm/free5gc-lab/webconsole-v1.4.4/frontend/src/lib/dtos/subscription.test.ts
```

其中 FlowRule、QoS Flow、ChargingData 示例分别位于约 171、187、209 行之后。模板
适合用于**字段核对**；写入现有用户前仍需按本案例的最小更新原则限定目标范围。

---

# PS-003：k3s/Helm 单 UPF 从 Pod Ready 到真实业务通过

## 1. 目标与现象

目标是把已有 Compose 业务基线迁移到单节点 k3s/Helm，而不是仅让 Helm 返回 `deployed`。部署期间依次遇到：

1. UPF Pod 因 `net.ipv4.ip_forward` sysctl 被 kubelet 拒绝；
2. UPF 改为 Single 后，SMF 仍寻找 ULCL 的 `IUPF1/PSAUPF1/PSAUPF2`；
3. UERANSIM gNB 的 `wait-amf` initContainer 拉取 `towards5gs/sctp_test:latest` 超时。

## 2. 根因与最小修正

- k3s 默认不允许该 unsafe sysctl；在 kubelet 配置中明确放行，验证 `gtp5g` 模块加载，再重建 UPF Pod。先前失败留下的临时 UPF Pod 已清理。
- Helm 顶层 `global.userPlaneArchitecture=single` 只改变 UPF 实例数，SMF 子 Chart 的默认非 Multus 配置仍是 ULCL。受控 Values 覆盖 SMF 拓扑为 `gNB1 → UPF`；SMF wrapper 只解析该 UPF 的 Headless Service，未降级 SMF 镜像版本。
- AMF 已 Ready，`sctp_test` 镜像拉取不是业务必要条件。在 gNB Chart 增加 `waitForAmf` 开关并于本基线关闭，改用真实 gNB 日志核验 SCTP 与 NG Setup。保留开关供后续环境使用。

修正文件、实际版本与复查命令集中在 [k3s/Helm 基线](docs/05-k3s-helm-single-upf-baseline.md)。

## 3. 验收与边界

- free5GC、MongoDB、gNB、UE 的现存 Pod 均 `1/1 Running`；SMF 收到 UPF PFCP Association Accepted。
- gNB 收到 NG Setup Response；UE 完成 5G AKA 鉴权、注册及 `PSI[1]` 的 PDU Session，建立 `uesimtun0`。
- `ping -I uesimtun0 -c 3 -W 4 1.1.1.1` 为 3/3 成功。
- VM 重启后 k3s、`gtp5g` 和全部服务自动恢复；UE 自动重新注册、建会话，TUN 出网再次 3/3 成功。启动初期短暂的小区不可用属于依赖尚未就绪，约一分钟后自动恢复。
- 旧 Compose 环境仅停止，备份已校验；新订阅单独创建，没有整库恢复。
- 尚未验证空白 VM 复现、多节点与 Multus。公网约 207 ms RTT 不可用于证明 edge UPF 优势。

---

# PS-004：第二台 VM 从零重建时的编译器与镜像获取问题

## 1. 现象与根因

- `gtp5g v0.9.5` 首次编译报 `/bin/sh: gcc-12: not found`。内核及头文件均为 `6.8.0-138-generic`，问题是 `build-essential` 默认安装 GCC 11，而当前内核构建需要 GCC 12；无需升级内核。
- k3s 节点虽 Ready，CoreDNS 等系统 Pod 长时间处于 `ContainerCreating`。镜像源请求未立即报错，但 local-path-provisioner 一张镜像耗时约 7 分钟，另外两张超过 13 分钟。节点 Ready 不等于 DNS/存储可用。
- 业务镜像包首次 `ctr images import` 报多架构内容摘要不存在；源 VM 仅缓存了本机平台的内容，需要在导入时限定 `linux/amd64`。

## 2. 修复与验证

1. 安装 `gcc-12` 后重新编译，`make` 成功；`modinfo gtp5g` 显示 `v0.9.5` 和匹配的 vermagic，模块已加载。
2. 从第一台导出同版本缓存镜像、传输并比对 SHA-256；系统镜像导入后重启第二台 k3s，三个系统 Pod 全部 Ready。业务镜像采用 `k3s ctr -n k8s.io images import --platform linux/amd64` 成功导入。
3. free5GC、MongoDB、UERANSIM 全部 Pod `1/1 Running`；SMF/UPF PFCP 成功，UE 鉴权、注册、PDU Session 成功；绑定 `uesimtun0` ping 公网为 3/3 成功。

完整版本、校验值和命令见 [第二台 VM 重建记录](docs/06-clean-vm-rebuild.md)。此验证依赖已缓存镜像，未证明校园网络可以稳定独立拉取全部镜像。

---

# PS-005：VM 重启后 Tailscale 在线但 SSH 超时

## 1. 现象与定位

VM1、VM2 重启后，`tailscale status`仍显示在线，TSMP Ping 也能收到响应，但普通 ICMP 和 SSH 22 端口均超时。VM 内部的`sshd`已监听`0.0.0.0:22`，UFW 未启用；临时在 INPUT 链最前面放行`tailscale0:22`也没有恢复连接。因此故障不在 SSH 服务或普通防火墙规则，而是 Tailscale 普通 IP 数据面没有正确恢复。其更深层触发原因是否与 k3s 启动顺序有关尚未验证。

## 2. 恢复与验证

分别在两台 VM 的本地控制台执行：

```bash
sudo systemctl restart tailscaled
```

重启服务后，Tailscale ICMP 恢复，SSH 能建立连接，使用 SSH 配置中的主机别名和密钥可正常登录。VS Code Remote-SSH 的旧失败会话仍可能保留重启前的超时结果；完全关闭并重新打开 VS Code，再选择`free5gc-vm-1`或`free5gc-vm-2`即可重新连接。

## 3. 复用结论

遇到“Tailscale 显示在线但 SSH 超时”时，应区分 Tailscale 自身探测与普通 IP 数据面：先确认`sshd`监听，再比较 TSMP、ICMP和SSH结果。若只有TSMP可达，可先重启`tailscaled`，不要反复修改SSH密钥或Chart。该命令是本次有效恢复手段；如果故障反复出现，再补充systemd启动顺序与日志分析。

---

# PS-006：受控 TCP 压测触发 UPF OOM，容器重启无法恢复

## 1. 现象与证据

2026-10-08，VM3 UE 经 VM2 UPF 向 VM1 iperf3 进行 TCP 单流压测。首轮未完成，
VM2 内核在 11:19:43 报告 `CONSTRAINT_MEMCG` 并杀死 `upf`，匿名驻留内存约 1Gi，
对应 UPF 容器 1Gi 限制。后续重启出现 `open Gtp5g: ... create: file exists`。

已确定直接失败机制是容器 OOM 及同 Pod 内残留 `upfgtp`。为何负载期间内存增长
尚未确定；SMF 的大量 URR quota 报告只是线索，不足以证明根因。

## 2. 恢复与边界

停止 UE，删除并重建 UPF Pod；等待 Ready 后重启 SMF，以重新解析已变化的 UPF
Pod IP，再启动 UE。恢复后注册、PDU Session、TUN Ping 5/5 成功，5 秒 20 Mbps
UDP 探测零丢包。资源限额、URR 和模块版本均未修改；根因未修复，不能宣称长期稳定。

完整数据、方法和后续诊断边界见[受控数据面基线](docs/10-controlled-data-plane-baseline.md)。

补充取证：当日下午另一次 100 Mbps/30 秒受保护复现捕获 `RemoteSess(node.go:710)`
空指针与首次退出码 1。对应版本的会话删除留下 `nil` 槽，查找时缺少判空；此次直接
失败机制为应用 Panic，不能将上午 OOM 倒推为相同根因。业务已恢复，尚未应用修复；
报告超时和 SEID=0 响应的触发原因仍待定位，证据与源码核对见上述模块文档。

有限排查已找到上游合并的 [go-upf PR #97](https://github.com/free5gc/go-upf/pull/97)：
go-upf v1.2.12/1.2.13 含判空修复及回归测试，当前 v1.2.10 不含。建议最小回移植，
尚未实施。故障时旧 SMF 日志已不在，无法确认 SEID=0 的具体触发分支；本轮不再复压。

---

## 新案例追加模板

```markdown
# PS-XXX：问题名称

## 1. 目标与验收标准
## 2. 环境与版本
## 3. 现象
## 4. 错误路径与证据
## 5. 根因
## 6. 完整解决流程
## 7. 验证结果
## 8. 常用命令
## 9. 风险、回滚和后续改进
```
