# Kubernetes MCP 配置

启动时加载并冻结配置，修改后重启。优先级为默认值 < `MCP_CONFIG` 文件 < 环境变量；CLI 的 `--transport/--host/--port` 最后覆盖。未知键、无效布尔值、非有限数值、越界预算及缺失或损坏的显式文件都会报错。布尔环境变量建议使用 `true` / `false`。

`MCP_CONFIG` 支持 UTF-8 JSON 或 YAML；YAML 需安装 `astromcp-k8s[yaml]`。参考 [k8s-config.json](../examples/k8s-config.json)，替换其中的 `TEST_ONLY` 路径。

## 基础配置

| JSON 键 | 环境变量 | 默认 / 约束 |
| --- | --- | --- |
| `transport` | `MCP_TRANSPORT` | `stdio`；支持 `http/sse` |
| `bind_host` | `MCP_BIND_HOST` | `127.0.0.1`；网络传输只允许 loopback |
| `bind_port` | `MCP_BIND_PORT` | 8080；1–65535 |
| `tool_timeout_seconds` | `MCP_TOOL_TIMEOUT_SECONDS` | 30，>0 |
| `queue_timeout_seconds` | `MCP_QUEUE_TIMEOUT_SECONDS` | 2，≥0 且小于总预算 |

## 集群、API 预算与阈值

| JSON 键 | 环境变量 | 默认 / 约束 |
| --- | --- | --- |
| `in_cluster` | `MCP_K8S_IN_CLUSTER` | false，启用时使用 ServiceAccount |
| `kubeconfig` | `MCP_K8S_KUBECONFIG` | null，使用 SDK 默认查找 |
| `context` | `MCP_K8S_CONTEXT` | null |
| `verify_ssl` | `MCP_K8S_VERIFY_SSL` | true；生产凭据应保留 TLS 校验 |
| `connect_timeout_seconds` | `MCP_K8S_CONNECT_TIMEOUT_SECONDS` | 3，>0 |
| `read_timeout_seconds` | `MCP_K8S_READ_TIMEOUT_SECONDS` | 10，>0 |
| `api_rps` | `MCP_K8S_API_RPS` | 5，>0 |
| `api_burst` | `MCP_K8S_API_BURST` | 10，≥1 |
| `api_max_in_flight` | `MCP_K8S_API_MAX_IN_FLIGHT` | 3，≥1 |
| `rate_limit_enabled` | `MCP_K8S_RATE_LIMIT_ENABLED` | true；关闭速率限制仍保留并发/超时限制 |
| `page_size` | `MCP_K8S_PAGE_SIZE` | 200，1–200，内部扫描分页 |
| `max_objects` | `MCP_K8S_MAX_OBJECTS` | 10000，≥1，内部扫描对象预算 |
| `include_logs` | `MCP_K8S_INCLUDE_LOGS` | true；敏感环境可关闭日志采集 |

`thresholds` 为嵌套对象，各环境变量使用前缀 `MCP_THRESHOLD_`：

| thresholds 键 | 环境变量后缀 | 默认 / 约束 |
| --- | --- | --- |
| `node_cpu_utilization` | `NODE_CPU_UTILIZATION` | 0.80，0–1 |
| `node_memory_utilization` | `NODE_MEMORY_UTILIZATION` | 0.85，0–1 |
| `pod_restart_count` | `POD_RESTART_COUNT` | 3，≥1 |
| `requests_overcommit_ratio` | `REQUESTS_OVERCOMMIT_RATIO` | 1，>0 |
| `limits_overcommit_ratio` | `LIMITS_OVERCOMMIT_RATIO` | 1.5，>0 |
| `pending_seconds` | `PENDING_SECONDS` | 300，>0 |
| `ready_seconds` | `READY_SECONDS` | 60，>0 |

运行依赖限定 Kubernetes SDK 37.x，锁定版本 37.0.1；Pod 反序列化使用该版本公开接口并以真实 SDK 做内存 HTTP 测试。SDK 显式关闭自动重试，每次 API 调用使用 connect/read timeout 和工具剩余预算。分页、限流排队、Events 和日志共用请求 deadline，调用返回及发布结果前再次检查截止时间，过期结果不能宣告完整成功；无法中断已经执行的同步 SDK 调用。Metrics 每次扫描只获取一个快照，不跨请求缓存。RBAC 示例见 [k8s-readonly-rbac.yaml](../examples/k8s-readonly-rbac.yaml)，只包含 get/list；它是集群范围取证示例，部署者应按实际范围收窄。应用从不自动应用该文件。

日志在服务端限制为 16384 bytes、展示为 4000 chars；Events 按 Pod UID 筛选并选择最新 20 条。脱敏覆盖常见密码、token、私钥格式，无法保证识别任意业务敏感内容。关闭日志或收窄凭据权限可减少可读取内容。

## 复现锁定安装

在仓库根目录执行 `uv sync --locked --all-packages --all-extras --dev` 可复现开发与验收环境。兼容 pip 的 requirements 从同一份根锁导出，更新依赖后重新生成：

```sh
uv export --frozen --package astromcp-k8s --all-extras --no-dev --no-emit-workspace --no-hashes --output-file k8s-check-mcp/requirements.txt
```

导出命令在仓库根目录运行。在本 MCP 目录执行 `pip install -r requirements.txt` 安装锁定依赖，再执行 `pip install --no-deps .` 安装包本身。
