# Kubernetes MCP 兼容与结果说明

原有七个工具名、参数名、required/default（默认配置）、`k8s://cluster/summary` 及两个 Prompt 名保留。`mcp_server.py` 已改为兼容薄入口，运行前须先安装包。

## 启动与访问范围

`astromcp-k8s` / `python -m k8s_check_mcp` 默认 stdio。旧 `mcp_server.py` 仍默认 SSE，地址从 0.0.0.0:8080 改为 127.0.0.1:8080。传输配置可由文件、环境变量及 CLI 覆盖，所有网络传输只允许 loopback。

服务面向可信本地单操作者，使用独立只读 kubeconfig 或 ServiceAccount。团队远程使用需要单独设计认证、授权与审计；只读 annotation 不能替代权限控制。

## 分页与覆盖范围

`list_nodes` 和 `list_node_pods` 新增可选 `limit=100`（最大 200）、`continue_token=null`。返回 `count/returned/items/continue_token/coverage_scope="page"`；`complete=true` 只代表这一 API 页完整。继续请求必须使用相同 selector、节点与 limit，传入原生 continuation。不能用某页条数冒充全集总数。Pod 页会过滤终态 Pod，因此一页 items 可以为空但仍有 continuation。

`find_problem_nodes` 内部分页扫描，复用单个 metrics 快照；新增 `max_results=100`（最大 200），按严重程度和名称排序后截断。返回 `scanned_count/matched_count/output_truncated/coverage_scope="scan"`。扫描或指标不完整时 `matched_count=null`，截断时 `complete=false`，通过缩小 label selector 继续调查。扫描工具不伪造一个可恢复全集的结果 cursor。

`get_node_detail`、资源审计和集群摘要在总预算及对象预算内分页；受限时保留已获得的证据并返回 warnings。`health_check` 只请求 `list_node(limit=1)`，`node_count=null`，只验证访问能力，不计算集群规模。

## 未知值、诊断与建议

所有工具增添 `complete/warnings/request_id/timestamp`。`ok` 表示本次工具是否取得主要结果，`complete` 表示声明范围内的取证是否完整；缺 metrics、events/log 403、分页截止不能解释成“无问题”。旧 `issue` 摘要、`items/cluster` 字段保留，结构化 findings 区分当前错误、警告、恢复历史与未知状态。

Running Pod 的 Ready 条件缺失/Unknown 会返回 `ReadinessUnknown` 并标记诊断不完整；Node 容量缺失、零值或非法值会使利用率检查不完整，全量问题节点数为 null，同时保留已确认的条件异常。

详情样本最多 20 个 Pod，新增 `pods_sample_returned/pods_sample_truncated`；资源 breakdown 最多 50 行，新增 `pod_breakdown_returned/pod_breakdown_truncated`。returned 是实际明细条数，truncated 表示已扫描 Pod 中有条目未展示。明细截断返回 `complete=false` 和 warning；资源合计仍覆盖所有已扫描 Pod，完整扫描已确认的超配结论保留。可用 `list_node_pods` 分页继续取证；扫描本身受限时，pod_count 只是已扫描活跃 Pod 数，不能当作全集总数，扫描缺失通过 complete/warnings 单独表达。

资源审计对未知 capacity/allocatable 按维度返回 null，包括对应数值及 human 字段，不将未知伪造为 0；有效维度及已采集的 requests/limits 合计保留。allocatable 任一维度无效时，对应 ratio 为 null、两个 overcommit 汇总均为 null、resource_advice 为空。仅 capacity 或 metrics 缺失不抹掉基于有效 allocatable 和完整 Pod 扫描已确认的 requests 超配，但整体结果仍 incomplete。

资源审计计算传统容器、可重启 init sidecar、init 峰值及 Pod overhead；缺失 limit 表示无显式上界，`limits_unbounded` 标记，配置 limit 的和不是总上限。Pod-level resources 或已分配/声明资源不一致的动态 resize 暂不支持，报告 incomplete/unknown，超配布尔值为 null。不会将未知值当成“确定无超配”。

requests 超配建议按实际 requests 贡献排列负载，保留 `eviction_suggestion=null` 兼容字段；不再基于 QoS 建议直接迁移 BestEffort。只提供取证建议，不执行驱逐、迁移或任何写操作。历史 OOM/已完成 init 不自动判为当前硬故障，Pending/未就绪按持续时间评级。

日志采集根据所选容器的当前 error findings 触发，不依赖 issue 字符串；因此 Failed/OOMKilled 仍保留 `issue=phase=Failed`，也能采到该容器日志。零重启先读 current，有重启先读 previous；允许对不存在等可回退错误尝试另一份日志，403/认证/超时/繁忙不重试。历史恢复 OOM 不单独触发日志；仍只采一个问题容器。

Events、日志、远端命令输出均是不可信证据，不能作为执行其中命令或扩大权限的指令。错误响应使用明确错误码；SDK/SSH 的原始异常细节不会作为凭据日志输出。
