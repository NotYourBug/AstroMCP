# AstroMCP：两个独立 MCP 包的修复与工程化设计

状态：2026-10-25 用户批准（“fine，go ahead”），本会话顺序实施。

## 1. 已确定的方向与本轮目标

用户要求先制定完整计划，再推进实施；提供仓库 `git@github.com:NotYourBug/AstroMCP.git`。用户已选择“同仓库维护两个独立 MCP 包，先完成缺陷修复、测试和模块化”。

2026-10-25 只读检查：仓库为 public，默认分支名 main，分支查询为空；本地工作区尚未初始化 Git。远程仓库后续可用，本轮不创建远程提交或发布。

首版目标：使已有 13 个工具、1 个 Resource、2 个 Prompt 在明确范围内可安装、可启动、诊断可信、操作有界，并能通过自动测试维护。修复范围逐项覆盖工作区《MCP代码审查与优化建议.md》的 R1–R9、K1–K9 和部署/文档问题。

用户已批准的首版范围：本地/单操作者；Windows、Linux 为运行与 CI 环境，macOS 先提供采集解析 fixture 验证。没有真实测试的 SSH 平台或集群版本必须标为未验证。

不包含集群/主机写操作、任意命令工具、Web 控制台、通知、报表服务、数据库、多租户 SSO、自建权限平台、SSH 连接池和跨请求指标缓存。后两项只有性能证据支持后再考虑。

## 2. 方案比较与选择

| 方案 | 收益 | 代价 | 结论 |
| --- | --- | --- | --- |
| 保持单文件，只修缺陷 | 改动面小，仍便于复制 | 难隔离测试，平台和协议职责继续混合 | 作为过渡修复阶段，非最终组织方式 |
| 同仓库、两个独立包与入口 | 统一工程规范，各自安装和权限隔离，迁移成本可控 | 要建立包与测试边界 | 用户已选择 |
| 合并为一个 MCP 服务 | 客户端配置少一个入口 | 混合 SSH 与 K8s 权限、依赖和生命周期 | 本轮不采用 |

## 3. 结构、命名与依赖

保留现有目录名。根目录是 uv workspace，两个 member 单独构建和安装，不发布公共工具库。Python 最低版本保持 3.10；本机默认用现有 3.11；CI 首先测试 3.10 和 3.12。运行依赖保持原框架体系，FastMCP 限制在已验证的 3.x 范围，实际版本在环境任务中解析、验证、写入 uv.lock，不在计划中虚构版本。

```text
MCP/
  pyproject.toml                    # workspace、dev dependencies、pytest/Ruff
  uv.lock
  .gitignore
  README.md
  docs/{superpowers,configuration,compatibility,validation}/
  scripts/{verify_inventory.py,verify_public_files.py}
  tests/contracts/
  k8s-check-mcp/
    pyproject.toml                  # distribution: astromcp-k8s
    mcp_server.py                    # 最终保留为兼容薄入口
    src/k8s_check_mcp/
      __init__.py
      __main__.py
      server.py                     # 只做 MCP 映射与生命周期装配
      config.py
      models.py
      client.py                     # SDK、超时、分页、指标、关闭
      limiter.py                    # 有等待预算的现有限流器
      quantities.py
      resources.py
      diagnostics.py
      presentation.py               # Node/Pod/Event 的输出视图
      inspector.py                  # 工具编排，保持 server 薄层
      prompts.py
    tests/{unit,integration,fixtures}/
  resource-check-mcp/
    pyproject.toml                  # distribution: astromcp-host
    resource-check-mcp.py            # 最终保留为兼容薄入口
    src/resource_check_mcp/
      __init__.py
      __main__.py
      server.py
      config.py
      models.py
      ssh.py
      probes.py                     # ping 与 TCP 探测
      collectors/{__init__,local,linux,macos,windows}.py
    tests/{unit,integration,fixtures}/
```

命令入口：`astromcp-k8s`、`astromcp-host`；模块入口：`python -m k8s_check_mcp`、`python -m resource_check_mcp`。独立 wheel 不得依赖 workspace 根目录或另一个 MCP 包。采用 setuptools 的 src 布局，根项目 `tool.uv.package=false`，dev 依赖含 pytest、pytest-asyncio、Ruff。不新增统一基类、插件框架或万能 utils.py。

计划文件中的相对路径均以本工作区根目录为基准。新增接口位于对应包中；同名概念不代表跨包依赖。

## 4. 兼容承诺与明确变化

1. 保留现有全部工具名、参数名、默认值及主要成功字段；K8s Resource URI 与 Prompt 名不变。可增加可选参数和元数据，不把已有字段全部挪进 data。
2. host 的 `_ok/_err` 语义保留：ok、host、timestamp，以及原数字字段。K8s 增加相同基本元数据，但保留 cluster/items 等顶层结构。error 继续是字符串，增加 error_code。
3. 两包共同约定新增字段：`complete: bool`、`warnings: list[str]`、`request_id: str`。分页列表还返回 `returned: int`、`continue_token: str | None`。count 保留为本次返回条目数；节点全量扫描另给 scanned_count，只有完整扫描才有可靠总量。
4. `issue: str | None` 保留为最高优先级摘要，同时新增 `findings`。缺少指标不伪装为 0；网络探测失败时 open 可为 null；资源模型不完整时 overcommit 可为 null。这些语义改动在兼容文档逐项说明。
5. 首版不删除 host/username/password/key_path 参数，不擅自将全部调用改成 target_id。它们只适用于本地可信操作者；文档禁止将该凭据接口直接作为多人共享服务。目标配置与调用者授权留给独立的远程服务设计。
6. 新 CLI 默认 stdio。旧 K8s 脚本仍默认 SSE、8080，但默认监听改为 127.0.0.1；新 host 默认 stdio。显式 HTTP/SSE 均默认 loopback，端口 K8s 8080、host 8081。非 loopback 地址启动时拒绝，并说明尚未配置受保护远程部署；不因“只读”而放行。
7. 旧环境变量继续支持。安全收紧明确列为行为变化：未知/变化 SSH 主机密钥拒绝、非法配置启动失败、远程磁盘控制字符拒绝、查询结果分页及截断可见。
8. 完成包化后，原脚本作为已安装/editable 环境的兼容入口，不再承诺复制一个 py 文件即可运行；缺包时给安装指令。两个 wheel 则必须能离开工作区独立安装。

## 5. 配置、预算与错误模型

配置通过各包 `load_settings(environ: Mapping[str, str], config_path: Path | None = None) -> Settings` 读取并在启动验证。优先级固定为环境变量 > 配置文件 > 默认值；不自动重载。K8s 保留 MCP_CONFIG，支持 JSON/YAML，YAML 作为显式可选依赖 `yaml` extra。显式配置文件不存在、格式错误、类型/范围不符均报错，不静默采用默认值。

建议默认值（本设计的工程决策，待整体确认）：

| 设置 | 默认 | 约束 |
| --- | --- | --- |
| K8s API connect/read | 3 / 10 秒 | 均为有限正数 |
| 单工具操作预算 | 30 秒 | 有限正数，排队与重试计入 |
| 排队等待预算 | 2 秒 | 非负且小于操作预算 |
| K8s RPS / burst / in-flight | 5 / 10 / 3 | RPS 有限正数，另设显式关闭速率限制配置；其他为正整数 |
| 自动重试 | 0 次 | 首版不以重试掩盖错误；后续根据证据增加 |
| SSH connect/banner/auth | 各最多 8 秒，且受剩余预算约束 | 不将多个阶段 timeout 简单相加当总预算 |
| SSH 单命令 | 15 秒 | OS 探测和采集共用工具的 30 秒预算 |
| SSH 全局/单目标并发 | 4 / 2 | 目标以 host、port、username 区分；不记录密码 |
| SSH stdout+stderr | 总计 256 KiB | 超出停止采集，报 OUTPUT_LIMIT 并关闭 channel |
| ping / TCP | 5 / 5 秒 | DNS 与连接也受整体预算控制 |
| CPU 采样窗口 | 1 秒 | 阻塞采样在线程中运行 |
| API 内部分页/扫描最大对象 | 200 / 10000 | 达限标 complete=false，不静默当全量 |
| MCP 列表默认/最大页大小 | 100 / 200 | 不接受负数或无限值 |
| Events / 日志 | 最新 20 条 / API limit_bytes=16384，展示最多 4000 字符 | 报告截断状态 |
| Pending、Ready=False 持续阈值 | 300 / 60 秒 | 区分启动中与持续异常；缺时间则 unknown/warning |
| CPU/内存、重启、超配阈值 | 沿用现有 0.80/0.85、3、1.0/1.5 | 利用率 0–1；重启正整数；超配有限正数 |
| 磁盘 threshold | 85 | 整数 0–100 |

配置命名：新增共通项用 MCP_TOOL_TIMEOUT_SECONDS、MCP_QUEUE_TIMEOUT_SECONDS、MCP_TRANSPORT、MCP_BIND_HOST、MCP_BIND_PORT；K8s 专属用 MCP_K8S_CONNECT_TIMEOUT_SECONDS、MCP_K8S_READ_TIMEOUT_SECONDS、MCP_K8S_PAGE_SIZE、MCP_K8S_MAX_OBJECTS；SSH 用 MCP_SSH_CONNECT_TIMEOUT_SECONDS、MCP_SSH_COMMAND_TIMEOUT_SECONDS、MCP_SSH_MAX_IN_FLIGHT、MCP_SSH_MAX_PER_TARGET、MCP_SSH_OUTPUT_LIMIT_BYTES、MCP_SSH_KNOWN_HOSTS。未单独命名的设置作为配置文件字段，CLI 只覆盖 transport/host/port。

错误码至少包括 INVALID_INPUT、CONFIG_ERROR、BUSY、TIMEOUT、AUTH_FAILED、HOST_KEY_UNKNOWN、HOST_KEY_CHANGED、PERMISSION_DENIED、NOT_FOUND、UNREACHABLE、DNS_ERROR、COMMAND_FAILED、PARSE_ERROR、OUTPUT_LIMIT、UNSUPPORTED_RESOURCE_MODEL。对外返回可行动说明，对内日志有 request_id、工具、目标、耗时、错误码；不记录密码、私钥内容、原始日志或完整原始命令。

30 秒是总预算和可取消 I/O 的截止目标，不把线程取消误称为线程终止。底层同步调用仍运行时，保持它占用的并发名额直到退出，禁止释放后无限创建后台线程；报告受限状态。DNS/OS 层阻塞的强制终止边界需在验证报告说明。

## 6. 主机 MCP 设计

`SSHExecutor.execute(connection: SSHConnection, command: str, *, deadline: float) -> CommandResult` 为异步接口；blocking Paramiko 在有界 worker 中运行。deadline 使用 monotonic 时间。stdout/stderr 持续交替消费，状态就绪后读完已缓冲数据再取退出码。超时、超输出、取消均关闭 channel/client；未知或变化 host key 在执行命令前失败。加载系统 known_hosts 和可选指定文件，默认 RejectPolicy；不提供静默 AutoAdd。

不新增任意命令 MCP tool。collector 内生成固定命令，仅磁盘路径为外部参数；Unix 路径必须为绝对路径，拒绝 NUL/CR/LF，使用 shlex.quote，以避免参数拆分和选项注入。Windows 明确接受盘符形式 C:、C:\\ 或以盘符开头的绝对路径，检查对应卷；不接受无法准确解释的 UNC 或把错误路径悄悄变成 C:。

collector 接口：`collect_cpu/memory/disk/tcp(...)->dict`，依赖通过构造传入 executor；解析接口为纯函数 `parse_cpu_output(text: str) -> float`、`parse_memory_output(text: str) -> dict`、`parse_disk_output(text: str) -> dict`、`parse_tcp_output(text: str) -> int`，各 OS 模块独立实现。

CPU 统一为总忙碌率 100-idle 或等价值，附 sample_window_seconds；macOS 包含 user+sys。内存使用率以总量与可用量为口径，无法跨平台等价的字段添加口径说明。采集命令缺失、非零退出、JSON null/数组形状不符、计数解析失败不能返回健康的 0。本地 psutil 调用移出事件循环，线程数有界。

ping 是从 MCP 运行主机发起的探测；明确 probe_origin=server。超时取消 kill 并 wait 回收子进程；输出使用平台编码或可替换解码。TCP 使用 getaddrinfo/IPv4+IPv6 尝试，至少一个地址成功才 open=true；拒绝连接表达 closed，DNS/超时表达无法确认，附 error_code。

## 7. K8s MCP 设计

`K8sGateway.call(fn: Callable[..., Any], *args: Any, deadline: float, **kwargs: Any) -> Any` 统一设置剩余预算内的 `_request_timeout`，经过限流和有等待预算的 semaphore。SDK 初始化、关闭由服务 lifespan 管理；无需连接集群即可列出工具，首次业务访问才初始化客户端。单工具内必须复用 gateway 和一次指标快照。

`K8sGateway.iter_pages(fn, *, deadline: float, **kwargs) -> Iterator[PageResult]` 负责 limit/_continue 与对象上限；PageResult 含 items、continue_token、complete、warnings。达到预算/对象上限或遇到已知 API 错误时，产出一个 complete=false 的终止页，保留已获得数据；未知编程错误照常抛出。`fetch_metrics(deadline: float) -> MetricsSnapshot` 返回 usage_by_node、observed_at、available、error_code、complete、warnings。扫描函数只在一次调用中获取一次全量指标。无指标时仍检查 Node Conditions，但明确哪些负载检查未执行。

公开分页与内部聚合的契约必须区分，采用以下固定映射，不创建服务端快照游标：

| 接口 | 遍历方式 | 返回与上限 |
| --- | --- | --- |
| list_nodes / list_node_pods | 直接使用 K8s 的本页数据与原生 continuation；新增 limit=100、continue_token=None | count=本页返回数；继续令牌保留，next page 不丢项；Pod 页过滤终态后可能为空，令牌仍返回 |
| find_problem_nodes | 内部扫描节点全量，预算/10000 对象上限截止；只取一次 metrics | 新增 max_results=100（上限 200）；结果按严重程度、名称稳定排序截断；不支持结果游标，返回 scanned_count、matched_count（扫描未完成时为 null）、output_truncated、complete。继续定位可用 label_selector；不冒充可续页的全量结果 |
| get_node_resource_usage / get_node_detail | 内部分页遍历该节点活跃 Pod | 聚合在上限处停止并标记 complete=false；breakdown 前 50、pods_sample 前 20；不提供聚合游标，不完整时不确定的 overcommit 为 null |
| cluster_summary | 内部扫描节点，复用一次 metrics | 无返回列表；node_count/问题数仅在完整扫描时作为完整计数，否则附 scanned_count 与下界说明；metrics 不可用也标完整度不足 |
| health_check | list_node(limit=1)，只验证连通与权限 | status 表示连通性；保留 node_count 字段但值为 null，另附说明，不为健康检查遍历整个集群 |
| diagnose_pod_issues | Pod 精确 GET；Events 按 UID 内部分页 | 最新 20 条事件、受限日志；权限/预算失败作为 incomplete/warning，保留已获得的 Pod 证据 |

完整度包含采集完整度和输出完整度；两者任何一项不足均 complete=false，warnings 说明是哪项。分页列表的 complete 指本页处理成功，并通过 continue_token 明确后续页存在；这与聚合完整度的范围不同，文档明确其 scope。

`evaluate_pod(pod: Any, *, thresholds: Thresholds, now: datetime) -> list[Finding]` 同时处理 app/init statuses、当前和上次 terminated、Ready condition、Pending、重启阈值。Finding 含 code、severity（error/warning/info）、container、evidence；issue 由固定优先级取摘要。已结束的正常 init 不误报；刚启动且未就绪报告 info，持续 60 秒以上为 warning；CrashLoop/OOM 等硬故障立即报。累计重启包括 init，字段口径写清。

`effective_resources(pod: Any) -> PodResources` 使用 Decimal 精度；普通 init 按峰值，原生 sidecar 累加持续存活资源并计入后续 init 的并发峰值，最后取应用运行总量与启动峰值最大值，再加 overhead。CPU 转 millicores 向上取整，memory 转 bytes 向上取整，拒绝非法/负数/非有限 quantity。

算法约定：遍历 init 时维护 running_sidecar_sum；sidecar 加入该和；普通 init 的阶段资源等于该和加该 init 资源；init_peak 取各阶段最大值；运行期为 app_sum+running_sidecar_sum；有效 request=max(init_peak,运行期)+overhead。limit 单独跟踪是否无上限，不能把 absent 当 0 的全局上限。

首版支持传统容器、普通 init、原生 sidecar、Pod overhead 的声明资源核算。检测到 Pod 级 spec.resources、InPlace Resize 的已分配/声明资源不一致，返回 complete=false 和 UNSUPPORTED_RESOURCE_MODEL，不输出确定的“无超配”。SDK 丢弃未知字段的风险需要所选版本 fixture 测试，不能仅检查反序列化后的 getattr 就声称覆盖新字段。集群实际调度与动态状态不作未经实测的全版本一致承诺。

requests 超配建议按 requests 贡献排序，结合容量和 actual usage 提示继续核对；不建议仅按 BestEffort 自动迁移/驱逐。没有 limit 的负载显示 unbounded 信息，不冒充零上限。

Events 用 involvedObject.uid 过滤并取最新 20 条；日志请求带 limit_bytes，API 权限不足形成 warning，仍返回已有 Pod 证据。先枚举与问题对应的 init/app 容器。日志默认保持原行为，增加 MCP_K8S_INCLUDE_LOGS=false 可关闭；仅对常见 token/password/私钥标记做确定性脱敏，不宣称通用秘密检测。

## 8. 测试与交付

先在原文件边界补真实行为回归（通过 importlib/真实 FastMCP 加载，不把 AST 去装饰器的探针当最终单测），再在已有测试保护下提取模块。错误修复有先红后绿的用例；纯搬迁只需回归，不为每次文件移动添加实现镜像测试。

测试层：quantity/解析/规则单测；fake SDK、fake Paramiko channel 与可控时钟的 I/O 边界测试；FastMCP 内存客户端的注册与 tools/call 契约；真实子进程 stdio 握手/关闭；loopback HTTP/SSE 的注册、调用和关闭。单元测试不依赖外部集群或 SSH。

真实联调使用专用测试主机和只读 K8s RBAC，另设 integration marker，默认跳过。没有资源时保留“未验证”，不要求用户提供生产凭据，也不在 CI 连生产环境。首版发布前完整测试与构建必须通过，真实平台覆盖与限制须公开。

根 uv.lock 固定依赖；CI Windows/Linux × Python 3.10/3.12，分别运行安装、Ruff、测试、两个 wheel 独立构建/安装冒烟。扫描提交清单，排除 .venv、.codegraph、__pycache__、本地 .claude 配置和凭据；根 AGENTS 取代过时说明中的结构信息。许可证归属未知时不自行指定许可证、不发布 PyPI。

以原始源码快照为第一个本地基线提交，再以可验收任务为单位提交，最后准备分支/PR。远程已有内容以执行时重新读取为准，不覆盖、不 force push。提供仓库不等于要求本轮立即推送；首次公开推送前展示提交清单和检查结果。

## 9. 验收与执行顺序

执行顺序：工程基线 M1–M2 → 主机 H1–H5 → K8s K1–K6 → 总体验收 M3–M5。每项具备测试命令、预期结果和提交边界；失败先修复，不带已知回归进入下一阶段。按包的阶段可独立验收，最终共同交付。

验收必须包括：完整注册清单；安装后原入口与新入口可用；换行路径不能形成额外命令；未知密钥不执行命令；SSH 超时与输出上限释放资源；异步 CPU 不阻塞；5 节点扫描只取一次全量指标；无 metrics 有 incomplete 提示；init/Ready 被正确诊断；500m+200m+100m=800m；超配不建议盲目迁移 BestEffort；Pod UID 事件和日志字节上限；非法配置失败；非 loopback 启动拒绝；wheel 独立安装；干净公开文件清单。

回滚以任务提交为单位；兼容旧入口仍调用包内实现，不保留两份业务代码。迁移期间原文件保留；完成模块拆分后才缩为薄入口。每次任务提交前保留已完成测试记录。

## 10. 待整体审阅的提案性决策

- 首版以本地/单操作者、loopback 网络模式验收，团队远程鉴权另作设计。
- 旧主机凭据参数保留；默认拒绝未知 SSH 密钥，需部署者配置 known_hosts。
- 使用 uv workspace + setuptools；Python 最低 3.10；不把两个包耦合为一个发行包。
- 采用第 5 节的具体预算、分页和状态持续阈值；不实现盲目重试。
- 以本会话主代理顺序实施为推荐执行方式，结束时独立审查；若用户选择逐任务子代理执行，则按 Superpowers 的对应流程实施。

## 11. 设计审阅记录

独立 builder check 已完成。唯一需要补齐的关键问题是分页与全量扫描边界；已在第 7 节增加工具映射、count/continuation/截断语义。该问题属于可由工程方案解决的接口选择，已明确为本设计提案，未假称用户逐项选择。用户最终仍需审阅整份设计与计划。

参考依据：[既有审查](../../../MCP代码审查与优化建议.md)、[uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)、[FastMCP 运行与传输](https://gofastmcp.com/deployment/running-server)、[Kubernetes sidecar](https://kubernetes.io/docs/concepts/workloads/pods/sidecar-containers/)、[Paramiko channel](https://docs.paramiko.org/en/stable/api/channel.html)。
