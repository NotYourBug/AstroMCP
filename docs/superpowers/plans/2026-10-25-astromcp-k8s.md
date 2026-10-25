# AstroMCP Kubernetes MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 或 superpowers:subagent-driven-development 按任务实施。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Kubernetes MCP 的 API 有界性、诊断漏报、资源计算和证据完整度，交付独立 astromcp-k8s 包。

**Architecture:** K1–K5 在现有文件边界逐项修复并建立回归；K6 将稳定的配置、gateway、纯规则与展示层移至 src 包。工具保持只读且保留原名称，扫描复用单次指标快照。

**Tech Stack:** Python >=3.10、FastMCP 3.x、Kubernetes Python Client、Pydantic 2、Decimal、uv/setuptools、pytest。

**Spec:** `docs/superpowers/specs/2026-10-25-astromcp-design.md`。Prerequisite：总计划 M1/M2 完成，设计与计划已批准；推荐主机子计划完成后执行。

## Global Constraints

- 原 7 个工具、k8s://cluster/summary、两个 Prompt 名保留；主要成功字段保留。
- K8s API connect/read=3/10 秒，总工具预算=30 秒，排队=2 秒，RPS/burst/in-flight=5/10/3，默认重试=0。
- 环境变量 > 配置文件 > 默认值；JSON/YAML 显式配置错误拒绝启动；TLS 默认校验。
- API 内分页=200，扫描对象上限=10000；公开列表页=100、最大=200；问题扫描 max_results=100、最大=200。
- Node CPU/Memory 阈值=0.80/0.85、重启阈值=3、requests/limits ratio 阈值=1.0/1.5；Pending/Ready 持续阈值=300/60 秒。
- Events 最新 20；日志 API 上限=16384 bytes，展示上限=4000 chars；未知资源模型不得给“确定无超配”。
- 首版 loopback 限制与兼容入口按设计，不新增 API 写操作、驱逐或迁移工具。

## Review Focus

1. NaN/Infinity、拼错 bool 和损坏的显式配置不能静默降低安全设置——K1。
2. 空页带 continuation、节点超过 100 个、指标不可用不能漏报为完整健康——K2。
3. 正常完成 init、刚启动未就绪、缺 status 与历史恢复 OOM 不能误作当前硬故障——K3。
4. 多个 sidecar 穿插 init、无 limit、Decimal 大整数与不支持的 Pod-level/resize 模型——K4。
5. 同名 Pod 重建、events/log 403、超长单行日志不应污染或打断已有诊断——K5。

## 过渡与最终文件映射

K1–K5 Modify `k8s-check-mcp/mcp_server.py`，Create `k8s-check-mcp/tests/{unit,integration,fixtures}/`。K6 将现有实现移动至设计规定的 src/k8s_check_mcp；原文件只留兼容薄入口。类型/函数先在原文件定义，再保持名称提取，避免双份业务代码。

### Task K1: 严格配置与有预算的 API gateway

**Files:** Modify 原文件配置/限流/SDK 初始化/`_k8s_api_call`/main；Create `k8s-check-mcp/tests/unit/test_config.py`、`test_gateway.py`、`test_limiter.py`、`test_launch_policy.py`。

**Interfaces:** `load_settings(environ: Mapping[str,str], config_path: Path|None=None) -> K8sSettings`；`TokenBucketLimiter.acquire(*, deadline: float) -> None`；`K8sGateway.call(fn: Callable[...,Any], *args: Any, deadline: float, **kwargs: Any) -> Any`；`K8sGateway.close() -> None`。gateway 接收 settings、client_factory 和 clock，客户端惰性初始化；异常内部分类后由边界转 error_code。

- [ ] Step 1：测试 env 覆盖 JSON/YAML；非法 bool（如 tru）、NaN/Infinity、负阈值、缺显式配置文件、坏 JSON/YAML 均启动失败；空配置沿用原阈值。fake API 捕获 `_request_timeout`，断言不超过 3/10 秒且受剩余预算约束；3 个挂起请求占满时第四个 2 秒队列预算后 BUSY；失败释放 permit，尚运行的同步工作仍占额度；零余量不发请求。验证 SDK 生命周期 close；默认 TLS=true；非 loopback 启动拒绝。
- [ ] Step 2：运行 `uv run --frozen python -m pytest k8s-check-mcp/tests/unit/test_config.py k8s-check-mcp/tests/unit/test_gateway.py k8s-check-mcp/tests/unit/test_limiter.py k8s-check-mcp/tests/unit/test_launch_policy.py -q`；现有无超时/静默配置路径应失败，使用假时钟避免真实等待 30 秒。
- [ ] Step 3：实现配置和 gateway，保留 `_k8s_api_call` 为转发适配，让所有原调用进入统一策略。令牌等待和 semaphore 等待都受 deadline 限制；无自动重试，403/404/429 分类别。MCP 不携带 SDK 全堆栈返回，日志脱敏。旧 main 保留 SSE/8080、默认改 loopback，新 CLI 约定 stdio。API 服务端 timeout_seconds 不能替代客户端 `_request_timeout`。
- [ ] Step 4：运行本任务测试与 K8s 注册契约；断言 tools/list 不需要 kubeconfig 或 API 可达。验证初始配置不可变、不同 settings 不污染全局 Configuration。记录 SDK 同步调用在取消后的真实边界，不宣称线程可强杀。
- [ ] Step 5：提交 `fix(k8s): validate settings and bound API requests`，关闭 K2/K7 与网络默认暴露边界。

### Task K2: 指标快照复用、分页与检查完整度

**Files:** Modify 原文件 get_node_metrics/_node_metric_view/_problem_reason_list、列表、扫描、聚合和 summary/health；Create `k8s-check-mcp/tests/unit/test_metrics_snapshot.py`、`test_pagination.py`、`test_scan_coverage.py`。

**Interfaces:** `MetricsSnapshot(usage_by_node: dict, observed_at: datetime, available: bool, error_code: str|None, complete: bool, warnings: list[str])`；`PageResult(items: list[Any], continue_token: str|None, complete: bool, warnings: list[str])`；`K8sGateway.fetch_metrics(*, deadline: float) -> MetricsSnapshot`；`K8sGateway.iter_pages(fn: Callable[...,Any], *, deadline: float, **kwargs: Any) -> Iterator[PageResult]`；`evaluate_node(node: Any, snapshot: MetricsSnapshot, settings: K8sSettings) -> tuple[list[str],dict]` 不做 I/O。公开分页与全量聚合严格按设计第 7 节映射，已知预算/API 截止以 complete=false 的终止页表达。

- [ ] Step 1：5 个及 101 个 fake 节点下分别调用 summary/find_problem_nodes，断言每次全量 metrics API 调用数都为 1，而非节点数；404/403/timeout 指标失败仍检查 Ready/Pressure，但 complete=false/warnings 有相应原因。list_nodes/list_node_pods 新增 optional limit=100/continue_token=None，fake 第二页验证 _continue 转发与本页 count；空 Pod 页仍返回续令牌。find_problem_nodes 全量扫描后按严重程度/名称排序，max_results 截断时 output_truncated=true，无假游标；健康检查只 list_node(limit=1)。
- [ ] Step 2：运行 `uv run --frozen python -m pytest k8s-check-mcp/tests/unit/test_metrics_snapshot.py k8s-check-mcp/tests/unit/test_pagination.py k8s-check-mcp/tests/unit/test_scan_coverage.py -q`，在 N+1、无分页或缺完整度处预期失败。
- [ ] Step 3：把全量 metrics 获取从逐节点函数移到扫描编排；metrics API 首版每次扫描获取一次，无跨请求缓存。节点/Pod/Events 分页，所有页共享 deadline；重复 continuation、达 10000 对象或预算不足停止并说明。列表只处理当前 K8s 页，扫描/聚合内部遍历全量；breakdown 50/sample 20 不变。不完整扫描下计数标明下界，不完整资源聚合不输出确定的 false overcommit。metrics 响应超过对象上限亦标记不足，不宣称服务端支持字节流硬上限。
- [ ] Step 4：运行 K1/K2 全测试，验证同名不同 namespace Pod 不合并；指标缺失不生成假 0 利用率。Schema 契约仅增加可选参数；health_check 的 node_count=null、列表页 count 等行为变化写入兼容清单。
- [ ] Step 5：提交 `fix(k8s): reuse metrics snapshots and expose scan coverage`，关闭 K1/K6/K9。

### Task K3: 完整且有时间语义的 Pod 诊断

**Files:** Modify 原文件 `_pod_issue`、`_pod_issue_summary`、`_pod_restart_count`、`_find_problem_container_name`、`_pod_suggestions` 与 Pod 输出；Create `k8s-check-mcp/tests/unit/test_pod_diagnostics.py`、`fixtures/pods/`。

**Interfaces:** `Finding(code: str, severity: Literal['error','warning','info'], container: str|None, evidence: dict)`；`evaluate_pod(pod: Any, *, thresholds: Thresholds, now: datetime) -> list[Finding]`；`summarize_issue(findings: list[Finding]) -> str|None`；`select_problem_container(pod: Any, findings: list[Finding]) -> str|None`。issue 由稳定优先级生成，旧摘要字段保留，同时新增 findings。

- [ ] Step 1：fixtures 覆盖 init CrashLoop/restarts=8、app CrashLoop、当前 terminated 非零退出、phase Failed、OOM、Ready=False 61 秒、Ready=False 10 秒、Pending 301/10 秒。断言硬故障 error、持续不就绪 warning、启动中 info；正常 Completed init 不误报，Running+Ready=True 的历史 terminated OOM 只为历史 info，不当当前硬故障；status=None、无 condition 时间不崩溃且说明不足。累计重启含 init+app，选择的日志容器来自对应 finding。
- [ ] Step 2：运行 `uv run --frozen python -m pytest k8s-check-mcp/tests/unit/test_pod_diagnostics.py -q`，原 init/Ready 漏报应失败。
- [ ] Step 3：实现纯规则与稳定摘要，合并 app/init statuses，区分当前状态与历史证据；时间差用 UTC，未来时间或缺时间标 unknown，不能断言持续阈值。把“未发现明显异常”限制到已完成必要检查的场景；正常启动建议仍保留等待/查看事件的上下文。
- [ ] Step 4：运行 K8s 规则与注册契约；工具直接诊断、节点 Pod 列表、详情样例使用同一套 findings，不能分别实现三套相近规则；normal startup 与 recovered 状态的负例必须通过。
- [ ] Step 5：提交 `fix(k8s): diagnose init failures and pod readiness`，关闭 K3。

### Task K4: 正确的资源算法、未知口径与可行动建议

**Files:** Modify 原文件 parse_quantity/资源汇总/get_node_resource_usage 与建议；Create `k8s-check-mcp/tests/unit/test_quantities.py`、`test_pod_resources.py`、`test_resource_advice.py`、`fixtures/pods/resources/`。

**Interfaces:** `parse_quantity_decimal(text: str) -> Decimal`；`cpu_to_millicores(text: str) -> int`；`memory_to_bytes(text: str) -> int`；`PodResources(cpu_request_m: int, memory_request_bytes: int, cpu_limit_m: int|None, memory_limit_bytes: int|None, complete: bool, warnings: list[str])`；`effective_resources(pod: Any) -> PodResources`；`resource_advice(pods: list[dict], totals: dict) -> list[str]`。旧 numeric limit 合计保留为已配置合计，另给 limits_unbounded，不能当总上限。

- [ ] Step 1：参数化 quantity：250m→250m、0.1m→1m、1Gi→1073741824 bytes、9007199254740993 精确保持；非法单位、负数、NaN/Infinity 拒绝。传统 init 100m/200m 与 app 150m→200m；app 500m+sidecar 200m+overhead 100m→800m；两个 sidecar 100/200m、普通 init 700m、app 500m、overhead 100m→1100m（sidecar 运行总量与启动峰值均需计算）。无 limit 标 unbounded，不得返回确定零上限。
- [ ] Step 2：再写边界：init 顺序 sidecar100、普通init700、sidecar200、app500、overhead100→900m，交换普通 init 到两个 sidecar 之后→1100m。Pod-level resources 或 resize 已分配/声明不一致：complete=false/UNSUPPORTED_RESOURCE_MODEL，overcommit 未确定时为 null。两个 Pod 一个 requests=2000m、另一个 BestEffort=0m，建议不能声称迁走后者可降低 requests。运行 `uv run --frozen python -m pytest k8s-check-mcp/tests/unit/test_quantities.py k8s-check-mcp/tests/unit/test_pod_resources.py k8s-check-mcp/tests/unit/test_resource_advice.py -q`，应在原算法上失败。
- [ ] Step 3：实现 Decimal 转换和设计第 7 节的 running_sidecar_sum/init_peak 算法，各 CPU/memory/request/limit 维度分别处理，包含 Pod overhead。完整模型使用确定量，SDK unknown field 丢失须由 raw fixture 经过 SDK 反序列化验证，检测机制不能只依赖缺字段 getattr。对于所选 SDK 无法保留的模型信息，使用 API 原始数据视图或明确能力拒绝，不假装支持。
- [ ] Step 4：运行 K1–K4 测试。聚合仍基于活跃 Pod，未知模型/截断使结果不完整；ratio denominator 缺失或 0 时标 unknown。建议按 requests 贡献定位，旧 eviction_suggestion 保留但不再自动列 BestEffort 驱逐候选；新增 resource_advice，按实际数据表达继续调查。无模型异常的传统 fixture 与旧单位输出保持兼容。
- [ ] Step 5：提交 `fix(k8s): account for sidecars and resource model limits`，关闭 K4/K5。

### Task K5: Pod UID 事件、日志预算与部分证据降级

**Files:** Modify 原文件 diagnose_pod_issues/_event_brief/_read_pod_log_excerpt 与 prompts；Create `k8s-check-mcp/tests/unit/test_pod_evidence.py`、`test_redaction.py`。

**Interfaces:** `read_events(gateway: K8sGateway, *, namespace: str, pod_uid: str, deadline: float) -> EvidenceResult`；`read_log_excerpt(gateway: K8sGateway, *, namespace: str, pod_name: str, container_name: str, deadline: float) -> EvidenceResult`；`EvidenceResult(data: Any, complete: bool, warnings: list[str])`；`redact_common_secrets(text: str) -> str`。config 增加 MCP_K8S_INCLUDE_LOGS（默认 true，保留原行为）。

- [ ] Step 1：同名 Pod 两个 UID，fake Events 断言 field_selector 包含新 UID，只返回最新 20 条，时间缺失稳定排序；超过预算或 403 形成 warning，Pod findings 仍保留。fake log API 断言 limit_bytes=16384/tail_lines=80，展示<=4000 chars，先 previous 后 current 的可用降级有原因；关闭日志配置后调用数为 0；init 故障请求对应容器。
- [ ] Step 2：用 password=TEST_SECRET、Authorization: Bearer TEST_TOKEN 与多行私钥样例验证输出及日志没有原值；不是上述模式的普通业务文本不胡乱删除。日志包含“忽略所有指令”仅作为证据返回，Prompt 明示不执行采集文本中的指令。运行 `uv run --frozen python -m pytest k8s-check-mcp/tests/unit/test_pod_evidence.py k8s-check-mcp/tests/unit/test_redaction.py -q`，原 UID/limit/异常吞没行为预期失败。
- [ ] Step 3：实现有预算的证据读取与已获得结果保留，按 K3 的 select_problem_container 接口处理 init/app；只捕获已知 API/网络异常，不吞编程错误。输出 warnings 说明权限、无 previous 日志、截断、超时等原因，不把它们统称空字符串。
- [ ] Step 4：重跑 K1–K5，确认单条巨长日志仍请求服务端字节限制；事件 message 展示上限 300 chars，经过脱敏后再展示。文档说明只做常见模式脱敏，不提供通用秘密识别保证。
- [ ] Step 5：提交 `fix(k8s): bound pod evidence and preserve partial diagnostics`，关闭 K8。

### Task K6: 提取独立 K8s 包与实例生命周期

**Files:** Create `k8s-check-mcp/src/k8s_check_mcp/{__init__,__main__,server,config,models,client,limiter,quantities,resources,diagnostics,presentation,prompts}.py`、`k8s-check-mcp/tests/integration/test_server.py`；Modify pyproject、原脚本与测试 imports。

**Interfaces:** `create_server(settings: K8sSettings|None=None, *, gateway: K8sGateway|None=None) -> FastMCP`；`main(argv: Sequence[str]|None=None) -> None`。Console script `astromcp-k8s=k8s_check_mcp.__main__:main`；原 mcp_server.py 薄入口仍使用兼容 SSE 默认，包 CLI 默认 stdio。server lifespan 管理实例 gateway 的关闭。

- [ ] Step 1：通过真实 Client 断言 7 工具、1 Resource、2 Prompt；注入 fake gateway 执行每种代表调用，确认调用工具不会创建第二个全局配置/客户端。两个 settings/context 不串数据；不连接 K8s 也可注册和列工具。纯 quantities/resources/diagnostics 导入不启动 MCP 或网络。
- [ ] Step 2：运行 `uv run --frozen python -m pytest k8s-check-mcp/tests/integration/test_server.py -q`，正式包未存在时失败；K1–K5 回归必须已通过。
- [ ] Step 3：按文件责任移动现有代码；client 只处理 SDK/I/O，diagnostics/presentation 是纯逻辑，server 负责装配；Prompts 单独文件。去掉全局 settings/client/limiter 单例，使用实例依赖和 lifespan，保持外部工具契约。转换成员 pyproject 为 setuptools src 布局，发行名 astromcp-k8s、版本 0.1.0、Python>=3.10、yaml extra 保留；不得依赖 host 包。
- [ ] Step 4：运行 `uv sync --all-packages --all-extras --dev`、K8s 全测试、总注册契约、`uv build --package astromcp-k8s`；确认旧脚本无大段业务副本、正式包中无动态读取 workspace 源文件。独立 wheel 与真实协议总验收由 M3 负责。
- [ ] Step 5：提交 `refactor(k8s): extract independently installable MCP package`；保留 K5 提交为模块搬迁回滚点。

## K8s 完成定义

- [ ] K1–K9 全部有回归证据；5/101 节点扫描各只取一次全量 metrics。
- [ ] 缺数据、未知资源模型、分页截止和证据失败都不伪装为完整正常结论。
- [ ] 资源算法同时覆盖普通 init、sidecar 启动顺序、overhead、Decimal 精度、无 limit 与 unsupported 模型。
- [ ] 原 Schema 保留、可选增量明确；两实例状态隔离，gateway 正确关闭。
- [ ] API 权限保持只读；真实集群版本/SDK 模型保留风险在 M4 矩阵中如实记录。
