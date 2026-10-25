# 主机资源检查 MCP

`astromcp-host` 为 MCP 客户端提供本机和 SSH 远程主机的资源采集，以及从服务端发起的网络连通性检查。需要 Python 3.10+，面向可信本地单操作者，所有工具均为只读。

## 功能

| 工具 | 用途 |
| --- | --- |
| `check_host_status` | 通过 ping 检查目标是否有 ICMP 回应 |
| `check_disk_usage` | 查看磁盘容量、使用率和阈值告警 |
| `check_mem` | 查看内存总量、已用量、可用量和使用率 |
| `check_cpu` | 查看 CPU 总忙碌率 |
| `check_tcp` | 统计目标主机的 TCP 连接数 |
| `check_service_port` | 检查目标 TCP 端口能否建立连接 |

CPU、内存、磁盘和 TCP 连接数默认检查本机，也可通过 SSH 检查远程主机。ping 和端口探测始终从 MCP 服务端所在网络发起。

## 安装与启动

从仓库根目录单独安装：

```sh
python -m pip install ./resource-check-mcp
astromcp-host
```

需要使用仓库锁定的依赖版本时，在仓库根目录执行：

```sh
uv sync --locked --all-packages --all-extras --dev
uv run --frozen --all-packages astromcp-host
```

默认 stdio，启动后等待 MCP 客户端输入。也可使用 `python -m resource_check_mcp`；旧 `resource-check-mcp.py` 是已安装包的兼容入口。stdout 专供协议，日志写 stderr。

## 接入 MCP 客户端

安装后可使用以下配置；若客户端找不到命令，将 `command` 换成实际虚拟环境中可执行文件的绝对路径。

```json
{
  "mcpServers": {
    "astromcp-host": {
      "command": "astromcp-host",
      "args": []
    }
  }
}
```

本机内存调用参数为 `{"host": "127.0.0.1"}`。磁盘检查需选择实际路径，例如 Linux 的 `/` 或 Windows 的 `C:\`。完整请求示例见 [tool-calls.json](../examples/tool-calls.json)。

## SSH 远程检查

远程资源工具使用 `host/port/username` 定位主机，并支持 `key_path`、ssh-agent、默认密钥或密码认证。先通过可信渠道核验服务器指纹，再加入系统 known_hosts 或 `MCP_SSH_KNOWN_HOSTS` 指定的文件；陌生或变化的密钥会拒绝连接。

例如，远程内存检查的参数形状为：

```json
{
  "host": "TEST_ONLY_HOST",
  "port": 22,
  "username": "TEST_ONLY_USER",
  "key_path": "TEST_ONLY_PRIVATE_KEY_PATH"
}
```

以上为占位值，使用前替换。凭据不写入服务日志；MCP 客户端自身可能记录工具参数，应使用可信客户端。

## HTTP / SSE

需要网络传输时显式指定：

```sh
astromcp-host --transport http --host 127.0.0.1 --port 8081
astromcp-host --transport sse --host 127.0.0.1 --port 8081
```

HTTP 路径为 `/mcp`，SSE 路径为 `/sse`。只允许 loopback 监听，适用于本机客户端。

## 配置与结果

配置使用 `MCP_CONFIG` 指向的 JSON 或环境变量，启动时校验，修改后重启。磁盘默认告警阈值为 85%，显式工具参数 `threshold` 可覆盖默认值。

结果包含 `ok/complete/warnings/request_id/timestamp`，数值结果保留 `total_gb/used_gb/free_gb/percent/tcp_count` 等字段。TCP 连接拒绝返回 `open=false`；DNS、超时、不可达等错误返回 `open=null` 和具体错误码。ping 无回应不能单独证明主机宕机。

- [配置与预算](configuration.md)：known_hosts、超时、并发、输出限额和锁定依赖安装。
- [兼容与结果说明](compatibility.md)：入口、错误分类及采集口径。
- [配置示例](../examples/host-config.json)、[客户端配置示例](../examples/mcp-clients.json)。
- [验证记录](../docs/validation/2026-10-25-validation.md)：远程 Linux/macOS/Windows 有解析测试，真实 SSH 主机尚未联调。
