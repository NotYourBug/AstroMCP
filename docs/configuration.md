# 配置参考

两个包分别加载自己的配置模型，互不共享凭据或客户端。启动时加载并冻结；变更后重启。优先级为默认值 < `MCP_CONFIG` 文件 < 环境变量，CLI 的 `--transport/--host/--port` 最后覆盖。显式磁盘工具参数 `threshold` 覆盖 host 配置默认值。

`MCP_CONFIG` 指向 UTF-8 JSON；K8s 还支持 YAML，需安装 `astromcp-k8s[yaml]`。未知键、无效布尔值、非有限数值、越界预算、缺失/损坏的显式文件都会报错，不回退到宽松配置。布尔环境变量建议使用 `true` / `false`。样例中的 `TEST_ONLY` 路径必须替换。

## 共用配置键

| JSON 键 | 环境变量 | 默认 / 约束 |
| --- | --- | --- |
| `transport` | `MCP_TRANSPORT` | `stdio`；支持 `http/sse` |
| `bind_host` | `MCP_BIND_HOST` | `127.0.0.1`；网络传输只允许 loopback |
| `bind_port` | `MCP_BIND_PORT` | host 8081 / K8s 8080；1–65535 |
| `tool_timeout_seconds` | `MCP_TOOL_TIMEOUT_SECONDS` | 30，>0 |
| `queue_timeout_seconds` | `MCP_QUEUE_TIMEOUT_SECONDS` | 2，≥0 且小于总预算 |

## Host

| JSON 键 | 环境变量 | 默认 / 约束 |
| --- | --- | --- |
| `ssh_connect_timeout_seconds` | `MCP_SSH_CONNECT_TIMEOUT_SECONDS` | 8，>0，connect/banner/auth 各阶段受剩余总预算约束 |
| `ssh_command_timeout_seconds` | `MCP_SSH_COMMAND_TIMEOUT_SECONDS` | 15，>0 |
| `ssh_max_in_flight` | `MCP_SSH_MAX_IN_FLIGHT` | 4，≥1 |
| `ssh_max_per_target` | `MCP_SSH_MAX_PER_TARGET` | 2，≥1，按 host/port/username |
| `ssh_output_limit_bytes` | `MCP_SSH_OUTPUT_LIMIT_BYTES` | 262144，≥1，stdout+stderr 合计 |
| `ssh_known_hosts` | `MCP_SSH_KNOWN_HOSTS` | null；除系统 host keys 外额外加载的文件 |
| `disk_threshold` | `MCP_DISK_THRESHOLD` | 85，0–100；作为工具实例的默认值发布，显式参数优先 |

首次 SSH 连接前，通过可信渠道核验指纹，再录入 known_hosts；不会自动接受陌生/变化的密钥。密钥路径支持 Paramiko 文件认证，也可使用 ssh-agent/默认密钥；远程操作须提供用户名。兼容密码参数，但密码不进入服务日志或公开配置示例。工具参数和结果可能被 MCP 客户端记录，应由可信本地操作者使用。

本机采集有 4 个有界 worker。CPU 采样窗为 1 秒；ping 和 TCP 探测各 5 秒。取消等待会关闭 SSH/探测资源；同步 worker 真正结束之前保留并发额度。Python 线程无法强行终止，底层库或系统调用不返回时可能延迟进程退出，不能将这些预算理解为可强杀线程的保证。

## Kubernetes

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

SDK 显式关闭自动重试，每次 API 调用使用 connect/read timeout 和工具剩余预算。分页、限流排队、Events 和日志共用请求 deadline；无法中断已经执行的同步 SDK 调用。Metrics 每次扫描只获取一个快照，不跨请求缓存。RBAC 示例见 [k8s-readonly-rbac.yaml](../examples/k8s-readonly-rbac.yaml)，只包含 get/list；它是集群范围取证示例，部署者应按实际范围收窄。应用从不自动应用该文件。

日志在服务端限制为 16384 bytes、展示为 4000 chars；Events 按 Pod UID 筛选并选择最新 20 条。脱敏覆盖常见密码、token、私钥格式，无法保证识别任意业务敏感内容。关闭日志或收窄凭据权限可减少可读取内容。

## 复现锁定安装

根目录 `uv sync --locked --all-packages --all-extras --dev` 是开发和验收环境的来源。包目录内的 requirements 是从根锁导出的兼容入口，更新依赖后重新生成：

```sh
uv export --frozen --package astromcp-k8s --all-extras --no-dev --no-emit-workspace --no-hashes --output-file k8s-check-mcp/requirements.txt
uv export --frozen --package astromcp-host --no-dev --no-emit-workspace --no-hashes --output-file resource-check-mcp/requirements.txt
```

在对应目录 `pip install -r requirements.txt` 只安装依赖，随后 `pip install --no-deps .` 安装包本身。这些导出文件和 wheel 是跨 Python 标记的版本清单；独立 wheel 验收使用相同方式，不重新解析宽泛的运行依赖下界。
