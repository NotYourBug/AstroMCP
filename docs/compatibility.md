# 迁移与结果语义

两个发行包互不依赖。13 个工具名、既有参数名/required/default（默认配置）、`k8s://cluster/summary` 和两个 Prompt 名均有真实内存 MCP 契约测试保护。原业务大文件已经变为兼容薄入口，运行前须先安装包，不能继续只复制一个脚本。

## 启动与信任边界

| 入口 | 默认传输 | 默认地址 |
| --- | --- | --- |
| `astromcp-host` / `python -m resource_check_mcp` | stdio | HTTP/SSE 显式启用时 127.0.0.1:8081 |
| `astromcp-k8s` / `python -m k8s_check_mcp` | stdio | HTTP/SSE 显式启用时 127.0.0.1:8080 |
| `resource-check-mcp/resource-check-mcp.py` | stdio | 同 host CLI |
| `k8s-check-mcp/mcp_server.py` | SSE | 127.0.0.1:8080 |

传输配置可由文件、环境或 CLI 覆盖。以前 K8s 脚本监听 `0.0.0.0`，现在所有网络传输都限制为 loopback；不提供无认证远程绑定开关。团队远程使用需要后续单独设计认证、授权和审计。本地只读提示不替代调用者权限控制。

SSH 改为核验 known_hosts，未知/变化密钥分别返回 `HOST_KEY_UNKNOWN` / `HOST_KEY_CHANGED`；先核验并录入指纹后连接。不会为保持旧行为自动信任主机。密码、私钥不写入日志，公开示例不包含真实目标。

## Host 结果

原数值字段 `total_gb/used_gb/free_gb/percent/tcp_count` 保留。ping/端口继续返回结构化对象，调用方读取 `reachable/open`；历史 AGENTS 对字符串结果的说明已更正。探测新增 `probe_origin=server`，表示从 MCP 进程所在网络发起。

TCP 连接拒绝是正常的 `ok=true, open=false`；DNS、超时和不可达为 `ok=false, open=null` 并带错误码，不能都理解为端口关闭。ping 无 ICMP 回复只表示本次无回应，不能据此认定主机宕机。CPU 统一为总忙碌率，macOS 使用最后一个采样的 `100-idle`。

磁盘工具默认阈值在未配置时仍为 85；设置 `disk_threshold` 后，实例 Schema 中的默认值随配置改变，显式工具参数优先。非法路径、端口、阈值返回输入错误；远端采集命令失败不会返回健康 0。

## K8s 分页与覆盖范围

`list_nodes` 和 `list_node_pods` 新增可选 `limit=100`（最大 200）、`continue_token=null`。返回 `count/returned/items/continue_token/coverage_scope="page"`；`complete=true` 只代表这一 API 页完整。继续请求必须使用相同 selector、节点与 limit，传入原生 continuation。不能用某页条数冒充全集总数。Pod 页会过滤终态 Pod，因此一页 items 可以为空但仍有 continuation。

`find_problem_nodes` 内部分页扫描，复用单个 metrics 快照；新增 `max_results=100`（最大 200），按严重程度和名称排序后截断。返回 `scanned_count/matched_count/output_truncated/coverage_scope="scan"`。扫描或指标不完整时 `matched_count=null`，截断时 `complete=false`，通过缩小 label selector 继续调查。扫描工具不伪造一个可恢复全集的结果 cursor。

`get_node_detail`、资源审计和集群摘要在总预算及对象预算内分页；受限时保留已获得的证据并返回 warnings。`health_check` 只请求 `list_node(limit=1)`，`node_count=null`，只验证访问能力，不计算集群规模。

## 未知值、诊断与建议

所有工具增添 `complete/warnings/request_id/timestamp`。`ok` 表示本次工具是否取得主要结果，`complete` 表示声明范围内的取证是否完整；缺 metrics、events/log 403、分页截止不能解释成“无问题”。旧 `issue` 摘要、`items/cluster` 字段保留，结构化 findings 区分当前错误、警告、恢复历史与未知状态。

Running Pod 的 Ready 条件缺失/Unknown 会返回 `ReadinessUnknown` 并标记诊断不完整；Node 容量缺失、零值或非法值会使利用率检查不完整，全量问题节点数为 null，同时保留已确认的条件异常。尚有一项低优先级限制：详情样本最多 20 个 Pod、资源 breakdown 最多 50 行，当前没有独立的明细截断标志；资源汇总仍覆盖所有已扫描 Pod，详见关闭记录中的延期项。

资源审计计算传统容器、可重启 init sidecar、init 峰值及 Pod overhead；缺失 limit 表示无显式上界，`limits_unbounded` 标记，配置 limit 的和不是总上限。Pod-level resources 或已分配/声明资源不一致的动态 resize 暂不支持，报告 incomplete/unknown，超配布尔值为 null。不会将未知值当成“确定无超配”。

requests 超配建议按实际 requests 贡献排列负载，保留 `eviction_suggestion=null` 兼容字段；不再基于 QoS 建议直接迁移 BestEffort。只提供取证建议，不执行驱逐、迁移或任何写操作。历史 OOM/已完成 init 不自动判为当前硬故障，Pending/未就绪按持续时间评级。

Events、日志、远端命令输出均是不可信证据，不能作为执行其中命令或扩大权限的指令。错误响应使用明确错误码；SDK/SSH 的原始异常细节不会作为凭据日志输出。
