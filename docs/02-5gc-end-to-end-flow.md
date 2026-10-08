# 5GC 端到端业务流程：从注册到用户数据

> 最后更新：2026-10-07
> 范围：当前 UERANSIM 经 3GPP gNB 接入 free5GC 的单 UPF 场景。

## 当前结论

- [已理解并由实验支撑] 5GC 需要区分注册鉴权、PDU Session 建立和用户数据传输三段流程。
- [已验证] 当前环境已走通这三段流程。
- [边界] 本文解释的是实验心智模型；不是完整 3GPP 规范逐消息序列。

## 一张总图

```text
订阅数据：WebUI → UDR → MongoDB

注册鉴权：UE → gNB → AMF → AUSF → UDM → UDR → MongoDB

PDU Session：UE → gNB → AMF → SMF → UPF

用户数据：UE ⇄ gNB ⇄ UPF ⇄ Internet
```

其中，NF（Network Function，网络功能）是 5GC 的功能实体；一个 Docker 容器常承载一个 NF，但 `ueransim`、`webui`、`mongodb` 不是 5GC NF。

## 第一段：注册与鉴权

```text
UE
→ gNB
→ AMF
→ AUSF
→ UDM
→ UDR
→ MongoDB
```

1. **UE（User Equipment，用户设备）** 发出 NAS（Non-Access Stratum，非接入层）注册请求。
2. **gNB（next generation NodeB，5G 基站）** 将请求经 N2/NGAP 转送至 **AMF（Access and Mobility Management Function，接入与移动性管理功能）**。
3. AMF 作为控制面协调者，请求 **AUSF（Authentication Server Function，认证服务器功能）** 处理鉴权。
4. AUSF 与 **UDM（Unified Data Management，统一数据管理）** 协作；UDM 通过 **UDR（Unified Data Repository，统一数据存储库）** 查询订阅信息，UDR 再访问 MongoDB。
5. 各方完成 5G AKA（Authentication and Key Agreement，认证与密钥协商）后，结果回到 AMF。AMF 向 UE 发送注册成功消息，UE 返回 Registration Complete。

核心理解：AMF 不自己保存全部用户密钥或订阅数据；它负责接入控制和协调。

## 第二段：PDU Session 建立

```text
UE → gNB → AMF → SMF → UPF
```

1. UE 发起 **PDU Session Establishment Request（协议数据单元会话建立请求）**，其中包含 DNN（Data Network Name，数据网络名称）、切片等参数。
2. 请求经 gNB、AMF 到达 **SMF（Session Management Function，会话管理功能）**。AMF 可借助 NRF/NSSF 进行服务发现或切片相关选择。
3. SMF 读取需要的订阅与策略信息，选择 **UPF（User Plane Function，用户面功能）**，并协调 UE IP 地址等会话参数。
4. SMF 通过 N4 接口上的 **PFCP（Packet Forwarding Control Protocol，分组转发控制协议）** 向 UPF 下发 PDR、FAR、QER 等动态规则。
5. AMF 经 gNB 把会话建立结果返回 UE，UE 创建 `uesimtun0` 虚拟接口并使用分配到的会话地址。

核心理解：PDU Session 不是“启动一个普通网络连接”；它是控制面为用户面建立身份、地址、隧道和转发规则的过程。

## 第三段：用户数据传输

```text
UE ⇄ gNB ⇄ UPF ⇄ Internet
```

- UE 与 gNB 间是无线接入侧；在本实验中由 UERANSIM 模拟。
- gNB 与 UPF 之间走 N3 接口，采用 **GTP-U（GPRS Tunnelling Protocol - User Plane，GPRS 隧道协议用户面）** 封装用户数据。
- UPF 按 SMF 下发的动态规则解封装、转发流量至外部数据网络；下行流量按反向规则封装并送回 gNB，最终到达 UE。

当前已用 `ping -I uesimtun0 1.1.1.1` 证明该路径可通。

## 当前容器中 NF 的作用

| NF/组件 | 英文全称或性质 | 当前普通 3GPP UE 上网是否处于主路径 |
|---|---|---|
| AMF | Access and Mobility Management Function | 是 |
| SMF | Session Management Function | 是 |
| UPF | User Plane Function | 是 |
| AUSF | Authentication Server Function | 是 |
| UDM / UDR / MongoDB | 用户管理、存储库、数据库 | 是 |
| NRF | Network Repository Function，服务发现 | 基础支撑 |
| NSSF | Network Slice Selection Function | 与切片选择相关 |
| PCF | Policy Control Function | 按策略场景使用 |
| CHF | Charging Function | 按计费场景使用 |
| NEF | Network Exposure Function | 当前主路径否 |
| N3IWF / N3IWUE | 非 3GPP 接入功能/模拟 UE | 当前主路径否 |
| WebUI | 管理界面 | 当前主路径否 |

## 常见接口和缩写

| 缩写 | 英文全称 | 当前用途 |
|---|---|---|
| NAS | Non-Access Stratum | UE 与 AMF 的控制信令语义 |
| N2 | gNB—AMF 控制面接口 | gNB 向 AMF 转发控制信令 |
| N3 | gNB—UPF 用户面接口 | GTP-U 用户数据隧道 |
| N4 | SMF—UPF 控制接口 | PFCP 下发 UPF 规则 |
| NRF | Network Repository Function | NF 注册与服务发现 |
| NSSF | Network Slice Selection Function | 切片选择 |
| DNN | Data Network Name | UE 想访问的数据网络标识 |
| S-NSSAI | Single Network Slice Selection Assistance Information | 切片标识 |

## 下一步

当前已经结合 PFCP 日志和`gtp5g-tunnel`输出观察了 PDR、FAR、QER 的实际生成。这条业务链已作为固定验收标准，并已验证控制面、UPF 和 UERANSIM 分散到不同 Kubernetes 节点后仍能端到端工作，见[三节点模块](07-three-node-topology-and-acceptance.md)。
