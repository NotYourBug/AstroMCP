# 两个 MCP 的代码审查与优化建议

审查日期：2026-10-24。审查对象为当前目录中的完整实现，不是 Git 差异。

## 1. 总体判断

**两个项目都有可保留的业务实现，但目前都不宜直接作为已完成生产验证的长期服务。K8s MCP 的工程基础更完整；主机资源 MCP 仍有启动、安全和异步执行方面的关键缺口。**

| 维度 | k8s-check-mcp | resource-check-mcp |
| --- | --- | --- |
| 当前源码 | `mcp_server.py`，1274 行，51 个顶层函数 | `resource-check-mcp.py`，696 行，27 个顶层函数 |
| MCP 能力 | 7 tools、1 resource、2 prompts | 6 tools |
| 已有优点 | 工具范围清楚、只读 API、类型与数据类、客户端/配置复用、线程锁、令牌桶与并发保护、TLS 默认开启、有限结果采样、RBAC 示例 | 采集工具范围清楚、统一 `ok/timestamp`、SSH 连接最终释放、远程执行已移到线程、部分输入校验、Windows 使用 CIM |
| 主要缺口 | 诊断漏报、资源口径不完整、重复全量指标查询、API 无显式超时、缺少测试与部署边界配置 | 缺少直接运行入口、远程路径注入、未验证 SSH 主机身份、等待可能无界、本地阻塞、采集口径与失败表达不完整 |
| 工程配套 | README 和依赖清单已有；缺少测试、锁定依赖与检查配置 | 无 README、依赖清单、测试；AGENTS/CLAUDE 有多处过时内容 |

本轮未发现应直接定为 P0 的证据。下面 P1 表示进入稳定使用前应优先处理，P2 表示后续可靠性与维护改进。网络暴露相关风险单独标明前提，不能据此断定当前服务已对外暴露。

## 2. 优先处理的问题

### R1 · P1 · 主机资源 MCP 缺少直接启动入口

位置：[resource-check-mcp.py](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:696)。

文件到 696 行结束，没有 `main()`、`if __name__ == "__main__"` 或 `mcp.run()`。依赖齐备时，直接 `python resource-check-mcp.py` 只完成定义和注册，随后退出，不会开始提供服务。AGENTS 中的启动文件名还写成了不存在的 `resource-check.-mcp.py`。

可以选择补正式入口，也可以统一约定 `fastmcp run 文件:mcp` 启动；后者能够加载现有 MCP 对象，不能把“缺少 Python 直接入口”理解成“任何方式都不能启动”。入口、安装步骤、客户端配置必须一致。参见 [FastMCP 启动说明](https://gofastmcp.com/deployment/running-server)。

### R2 · P1 · 远程磁盘路径可以注入 shell 命令

位置：[路径校验](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:63)、[命令拼接](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:299)。

`_check_path()` 的字符黑名单允许换行，而 `_disk_unix()` 直接把路径拼成 `df -kP {path}`。隔离模拟确认：包含换行的 `/data\nwhoami` 通过校验，并生成两行 shell 命令。远程 Unix 路径由工具调用者控制且 SSH 认证成功时，可执行检查之外的命令，破坏预期的只读边界。普通带空格路径也会被拆成多个参数。

应使用适合目标 shell 的参数编码（Unix 可用 `shlex.quote`，并妥善处理以 `-` 开头的路径），明确拒绝不支持的控制字符。不要继续扩大字符黑名单来替代参数边界。补含空格、引号、换行及选项前缀的回归用例。

### R3 · P1 · SSH 没有验证目标主机身份

位置：[run_remote_command](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:115)。

每次新建 `SSHClient` 后直接使用 `AutoAddPolicy()`，也没有加载系统或项目 `known_hosts`。因此无法可靠发现未知或被替换的主机密钥。连接可能被劫持时，会影响凭据安全和采集结果可信度；“在内网”不能提供主机身份校验。

建议加载明确的 `known_hosts` 并默认拒绝未知密钥；初始信任配置由部署侧完成。如确实需要测试模式，必须显式开启并记录。

### R4 · P1 · SSH 先等待退出、后读取输出，可能一直挂起

位置：[SSH 输出读取](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:135)。

代码先调用 `recv_exit_status()`，再读取 stdout/stderr。输出超过 SSH 窗口时，远端可能等待读取腾出窗口，本地却等待远端退出。即使输出很少，远程命令一直不结束时，该等待也没有明确的整体截止时间。`exec_command(timeout=30)` 不等于整个命令保证在 30 秒结束。

应同时持续消费 stdout/stderr，限制输出字节数，设置整个操作的 deadline，超时关闭 channel/client，并限制并发连接。仅给 `asyncio.to_thread()` 外层加取消，不能保证底层线程和连接已经终止。该等待顺序的风险有 [Paramiko 官方说明](https://docs.paramiko.org/en/stable/api/channel.html#paramiko.channel.Channel.recv_exit_status)。

### K1 · P1 · 全局扫描对每个节点重复拉取全量 metrics

位置：[get_node_metrics](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:628)、[节点判定](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:735)、[扫描循环](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:1014)、[摘要循环](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:1198)。

调用链是：每个 node → `_problem_reason_list` → `_node_metric_view` → 拉取所有节点 metrics。模拟 5 个节点时，摘要和扫描各自请求了 5 次全量指标。N 个节点带来 N 次全量请求，指标数据处理量约为 O(N²)。Prompt 还会先读摘要再扫描，再次重复这项工作。限流只能降低请求速率，不能消除重复工作。

应每次扫描获取一次指标快照，再把按节点索引的结果传入判定函数。单节点详情可使用单节点指标接口。跨工具短 TTL 缓存可以后加，并返回采样时间；第一步只需做到单次扫描内复用。

### K2 · P1 · K8s API 缺少显式超时，可能耗尽全部并发名额

位置：[API 包装器](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:178)。

包装器和现有调用点均未设置 `_request_timeout`，限流器等待也没有截止时间。API 慢读或无响应时，请求可以长期持有 semaphore；默认三个调用名额都被占满后，其他工具和健康检查也会等待。

建议在统一 API 层设置连接/读取超时，再为整个工具和排队等待设置预算。429 与短暂服务错误可做有限次数、带退避的重试；403/404 不应盲目重试。SDK 对默认无 socket 超时的说明见 [Kubernetes Python Client](https://github.com/kubernetes-client/python/blob/master/examples/watch/timeout-settings.md)。

### K3 · P1 · Pod 诊断漏掉 init 故障和就绪失败

位置：[Pod 异常判定](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:680)、[问题容器选择](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:1072)。

只检查 `container_statuses`，不检查 `init_container_statuses`；也没有使用 Pod Ready condition 或容器 ready 状态。模拟确认：init CrashLoop 返回 `None`；Running 但 Ready=False 也返回 `None`，随后生成“当前未发现明显异常”的建议。重启累计和日志容器选择也有同样的 init 覆盖缺口。

建议返回多个结构化 findings，分别表达容器故障、就绪失败、Pending 和重启异常，并带容器名、证据与严重程度。Pending/未就绪需考虑持续时间，避免把正常启动过程一律判为故障。

### K4 · P1 · Pod 资源审计仅覆盖传统容器模型

位置：[有效资源计算](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:560)。

普通 init 容器取最大值的历史修复是正确方向，但不能覆盖 `restartPolicy: Always` 的原生 sidecar 与 Pod overhead。模拟：主容器 500m、持续运行的 init sidecar 200m、overhead 100m，应计 800m，当前返回 500m。

此外，未设置 limit 被当作 0 累加，结果缺少“部分容器未设上限”的信息，容易把“已配置 limits 合计”误读成“总资源上限”。Pod 级资源声明等新语义也需要依据明确支持的集群版本决定是否实现；未覆盖时应标明能力范围。

应先明确支持矩阵，再分别测试普通 init、持续 sidecar、启动次序、overhead、缺少 limit 等情况。参见 [Kubernetes sidecar 资源规则](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/#resource-sharing-within-containers)。

## 3. 后续应修的问题与边界

| 编号 / 级别 | 位置 | 当前问题与推荐调整 |
| --- | --- | --- |
| R5 / P2 | [545 行](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:545) | `async` 工具里直接执行 `psutil.cpu_percent(interval=1)`，会阻塞事件循环约一秒。模拟阻塞采样确认协程在返回前不让出执行权。改成线程执行；磁盘和连接枚举也评估移出事件循环，使用有界并发。 |
| R6 / P2 | [581 行](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:581) | macOS CPU 命令只提取 user%，遗漏 sys%，与本地/Linux 的总忙碌率不一致。统一为清晰口径，例如 `100 - idle`，记录采样窗口；远程平台尚未实机验证。 |
| R7 / P2 | [648 行](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:648) | `netstat \| grep \| wc` 只体现末端命令状态，前面命令不存在时仍可能成功输出 0。Unix CPU 管道也需要检查上游失败。采集失败不能返回“0 连接/0 负载”。优先取原始输出后在 Python 解析，检查采集命令状态及必需字段。 |
| R8 / P2 | [674 行](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:674) | 端口检查只创建 AF_INET socket，IPv6 目标会失败；DNS 错误、连接拒绝和超时都压成 `open=False`。支持地址解析结果，返回错误类型；结果中明确探测发生在 MCP 服务端，而非远程目标机。 |
| R9 / P2 | [213 行](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:213)、[250 行](C:/Users/23033/Desktop/MCP/resource-check-mcp/resource-check-mcp.py:250) | ping 没有整体 deadline 和取消清理；磁盘阈值无 0–100 约束。增加输入边界、超时后 kill/wait；解析结果校验形状和数值范围。 |
| K5 / P2 | [941 行](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:941) | requests 超配时优先建议迁移 BestEffort，因果关系不成立。普通 BestEffort Pod 不贡献 CPU/内存 requests，迁走不降低这些 requests 总量。应按 requests 贡献定位负载；真实内存压力下的驱逐风险另行评估，不能仅按 QoS 列候选。 |
| K6 / P2 | [647 行](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:647)、[1203 行](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:1203) | 指标失败被吞掉，摘要没有说明高负载检查未执行。模拟指标接口失败仍得到 `problem_node_count=0`。应返回完整度、未执行检查及原因，区分“未发现异常”和“缺少证据”；缺权限、服务不存在、连接失败分别表达。 |
| K7 / P2 | [220 行](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:220)、[260 行](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:260) | 配置没有有限数值/范围校验，错误值可能静默退默认值或在首次请求才失败。环境变量实际覆盖配置文件，与函数注释优先级相反。使用经过校验的启动配置，非法配置明确报错，约定缓存后改配置需重启。 |
| K8 / P2 | [1136 行](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:1136)、[1094 行](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:1094) | Events 按名字过滤、直接前 20 条，可能混入重建前同名 Pod 的旧事件，也不保证最新。按 UID 并按时间排序。日志只在完整读取后切 4000 字符，网络读取量仍可能很大；使用服务端 `limit_bytes`，返回截断标记。 |
| K9 / P2 | [511 行](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:511)、[821 行](C:/Users/23033/Desktop/MCP/k8s-check-mcp/mcp_server.py:821) | nodes/pods/events 基本全量拉取，无分页；部分工具也全量返回。增加 API 分页与对 LLM 的结果预算，返回 `total/returned/truncated` 或 continuation。健康检查避免每次完整遍历节点。 |

K5 的 QoS 前提见 [Kubernetes Pod QoS 说明](https://kubernetes.io/docs/concepts/workloads/pods/pod-qos/)。这里的建议错误来自代码行为与资源计算的组合分析，而非宣称迁移 BestEffort 在任何压力场景都无效。

### 需要在联网部署前明确的权限边界

- K8s 入口固定监听 `0.0.0.0:8080`，源码未配置调用者认证。若运行环境允许其他主机访问、且没有外部网关认证，调用者可借服务端 ServiceAccount/kubeconfig 权限读取集群和日志。当前未检查实际网络、网关及 RBAC，不能认定已发生暴露。监听地址和端口应可配置，本机使用可默认 loopback；团队服务应定义认证及调用者授权范围。只读 annotation 是客户端提示，不能替代授权。
- 主机工具允许调用者提供任意 host、password、key_path，并自动尝试 agent/默认密钥。对于受信任的个人本地使用，这是一种灵活接口；若升级为多人服务，会借用服务器的网络可达性和凭据能力。建议服务端维护目标和凭据配置，工具只接收 `target_id`，按身份限制目标，密码不进入普通工具参数与对话记录。
- 日志、Events、SSH 输出可能包含敏感内容和不可信文本。按权限控制读取，限制长度，对常见秘密脱敏，并在诊断 Prompt 中明确将采集文本作为证据，不把其中内容当成操作指令。

### 工程配套与文档

两个目录均未发现应用测试或 CI 配置；当前工作区与两个项目目录也都不是 Git 仓库。K8s 的 `requirements.txt` 仅有依赖下界；主机项目没有依赖清单。建议配置 Python 版本、项目级 Ruff 规则、经过验证的依赖锁定与最小自动检查，确保新环境可以复现安装和启动。

文档存在漂移：主机 AGENTS/CLAUDE 写了错误文件名、旧超时值、旧返回类型和旧 Windows 命令；K8s AGENTS 仍称 README 引用了旧包结构，但当前 README 已修正，README 的工具清单又漏了 `health_check`。应以实际 CLI、配置模型和工具注册为事实来源，减少重复维护大段清单。

K8s 历史审计中的客户端复用、线程锁、配置缓存、TLS 默认开启等改动已经落地，本轮不把它们重复列为未修问题。历史“已修复”记录不能替代回归测试，尤其资源模型仍有覆盖边界。

## 4. 全写在一个程序中是否可以

**一个 MCP 服务使用一个进程和一个启动入口完全可以。源码长期全部放进一个文件，对这两个项目已经不合适。**

单文件适合短期验证、能力少、单人维护、需要直接拷贝的工具。这不是行数超过某个数字就必然错误。目前两个文件都已混合协议注册、远程连接、配置、数据转换、诊断规则和输出组织；主机工具还混合三种操作系统。修改其中一种职责，必须理解更多不相关行为，测试时还要绕过 FastMCP 和外部依赖。

建议保留两个独立 MCP，因为 K8s API 与主机 SSH 的权限、依赖和部署场景不同。可以放在一个 monorepo 中分别打包，也可以分别建仓库；无需先合并成“大运维服务”，也无需拆成更多网络微服务。

### 最小职责拆分示例

```text
MCP/
├─ k8s-check-mcp/
│  ├─ pyproject.toml
│  ├─ src/k8s_check_mcp/
│  │  ├─ __main__.py       # 统一启动入口
│  │  ├─ server.py         # MCP 注册、参数映射、调用服务
│  │  ├─ config.py         # 经校验的配置
│  │  ├─ client.py         # SDK 生命周期、超时、限流、分页、指标读取
│  │  ├─ resources.py      # quantity 与 Pod 资源计算
│  │  ├─ diagnostics.py    # 节点/Pod 规则及诊断编排
│  │  └─ prompts.py        # SOP 文本
│  └─ tests/
└─ resource-check-mcp/
   ├─ pyproject.toml
   ├─ src/resource_check_mcp/
   │  ├─ __main__.py
   │  ├─ server.py
   │  ├─ config.py         # 目标、凭据引用、超时及并发
   │  ├─ ssh.py            # 身份验证、命令执行、输出预算和清理
   │  └─ collectors/
   │     ├─ local.py
   │     ├─ linux.py
   │     ├─ macos.py
   │     └─ windows.py
   └─ tests/
```

这是职责示例，不要求一次创建全部文件。Windows/Linux/macOS 的输出解析先作为纯函数留在对应 collector 中即可，不必每个小函数独立成文件。返回类型模型也可等稳定后再独立。

依赖方向保持简单：MCP 适配层 → 诊断/采集编排 → API/SSH；规则和解析尽量接受普通数据，只在边界接触 SDK。客户端/执行器通过参数或构造函数传入，便于模拟。避免把现有函数原样搬进一个巨大 `utils.py`，那不会改善职责。

无需先引入通用插件系统、抽象工厂或共享框架。两个项目先统一结果语义和开发约定；等存在稳定、重复的代码，再考虑提取共享库。保留原入口作为兼容薄封装也可维持部署便利。

## 5. 推荐修改顺序与验收点

| 阶段 | 工作 | 验收标准 |
| --- | --- | --- |
| 第一阶段：稳定启动与访问 | 主机入口和安装清单；修复路径注入；SSH 主机验证；API/SSH/ping 总体超时、输出预算、并发上限；明确网络认证边界 | 干净环境可启动并列出工具；恶意/特殊路径不会改变命令结构；慢连接、无输出命令、大输出均能在预算内失败并释放资源 |
| 第二阶段：诊断结果可信 | metrics 单次扫描复用；Pod init/Ready 检查；sidecar/overhead/limits 口径；修正 BestEffort 建议；采集失败与指标缺失明确表达 | N 个节点扫描只取一次全量指标；关键 Pod 与资源用例有回归测试；未知数据不会被解释成正常或 0；输出有证据、时间与完整度 |
| 第三阶段：渐进模块化 | 先补关键回归用例，再依次抽配置、客户端/SSH、纯计算规则，最后让 server.py 变薄 | 工具名和参数兼容；重构前后的正常输入结果符合约定；单测无需连接集群/SSH，也无需启动 MCP 服务 |
| 第四阶段：部署与性能完善 | 锁定依赖、自动检查、更新文档；MCP 真实客户端契约测试；连接复用/OS 短期缓存；分页、缓存时间戳、请求日志与耗时统计 | 干净安装通过；tools/list 和代表性调用通过；多请求下资源有界；缓存不会掩盖过期数据或跨越凭据/身份边界 |

传输层也应配置化：个人客户端可用 stdio，团队网络服务评估 Streamable HTTP；现有 SSE 客户端按兼容要求迁移。FastMCP 已将 SSE 标为兼容用途，参见 [官方传输说明](https://gofastmcp.com/deployment/running-server#sse-transport-legacy)。这项调整排在正确性和可靠性修复之后，不应以升级协议替代修复现有问题。

## 6. 本轮检查与结论边界

- 完整阅读两份 Python 源码，并核对依赖、README、AGENTS 和历史审计记录。
- 两份源码均通过 Python AST 语法解析。Ruff 在当前环境规则下报告 33 项，主要为 Optional 写法、宽泛异常捕获等；这些不等同于 33 个运行时 Bug。
- 通过抽取真实函数、去除 MCP 装饰器并注入模拟依赖，验证了：5 节点触发 5 次全量指标请求、init 与 Ready 漏报、800m 被算成 500m、换行路径进入命令、本地 CPU 协程阻塞、API 包装器无超时参数、指标失败摘要仍显示 0 个问题节点。
- 路径注入只验证命令构造，未连接或执行任何远程命令；资源示例未访问真实集群。SSH 等待风险根据代码和官方 SDK 文档核对，未实际制造挂起的远端命令。
- 当前选用的 Python 3.11 环境没有安装 fastmcp、kubernetes、paramiko、psutil、pytest、PyYAML。没有安装依赖，没有启动完整服务，没有进行 SSH/K8s 联调或负载测试，也没有进行依赖漏洞扫描。
- 未修改两份程序。本轮仅新增本审查报告。
