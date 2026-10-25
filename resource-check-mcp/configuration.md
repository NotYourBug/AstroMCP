# Host MCP 配置

启动时加载并冻结配置，修改后重启。优先级为默认值 < `MCP_CONFIG` 文件 < 环境变量；CLI 的 `--transport/--host/--port` 最后覆盖。未知键、无效布尔值、非有限数值、越界预算及缺失或损坏的显式文件都会报错。布尔环境变量建议使用 `true` / `false`。

`MCP_CONFIG` 指向 UTF-8 JSON，参考 [host-config.json](../examples/host-config.json)，替换其中的 `TEST_ONLY` 路径。显式磁盘工具参数 `threshold` 覆盖配置默认值。

## 基础配置

| JSON 键 | 环境变量 | 默认 / 约束 |
| --- | --- | --- |
| `transport` | `MCP_TRANSPORT` | `stdio`；支持 `http/sse` |
| `bind_host` | `MCP_BIND_HOST` | `127.0.0.1`；网络传输只允许 loopback |
| `bind_port` | `MCP_BIND_PORT` | 8081；1–65535 |
| `tool_timeout_seconds` | `MCP_TOOL_TIMEOUT_SECONDS` | 30，>0 |
| `queue_timeout_seconds` | `MCP_QUEUE_TIMEOUT_SECONDS` | 2，≥0 且小于总预算 |

## SSH 与采集

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

本机采集有 4 个有界 worker。CPU 采样窗为 1 秒；ping 和 TCP 探测最多各 5 秒，并受工具剩余预算约束。取消等待会关闭 SSH/探测资源；同步 worker 真正结束之前保留并发额度。Python 线程无法强行终止，底层库或系统调用不返回时可能延迟进程退出，不能将这些预算理解为可强杀线程的保证。

## 复现锁定安装

在仓库根目录执行 `uv sync --locked --all-packages --all-extras --dev` 可复现开发与验收环境。兼容 pip 的 requirements 从同一份根锁导出，更新依赖后重新生成：

```sh
uv export --frozen --package astromcp-host --no-dev --no-emit-workspace --no-hashes --output-file resource-check-mcp/requirements.txt
```

导出命令在仓库根目录运行。在本 MCP 目录执行 `pip install -r requirements.txt` 安装锁定依赖，再执行 `pip install --no-deps .` 安装包本身。
