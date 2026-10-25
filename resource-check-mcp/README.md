# AstroMCP Host

独立发行包 `astromcp-host`，Python 3.10+。在仓库根目录 `uv sync --locked --all-packages --all-extras --dev` 后运行 `uv run --frozen --all-packages astromcp-host`；也可在本目录 `pip install .` 后运行 `astromcp-host`。默认 stdio，原 `resource-check-mcp.py` 是已安装包的兼容入口。

提供 `check_host_status`、`check_disk_usage`、`check_mem`、`check_cpu`、`check_tcp`、`check_service_port` 六个只读工具。本机使用有界 psutil worker，远程资源经验证 known_hosts 的 SSH 采集；ping/TCP 从 MCP 服务端发起。磁盘路径按单参数引用，命令输出、等待时间和并发有界。

`src/resource_check_mcp` 中，`config/models` 定义配置与结果，`ssh` 管理连接和 worker，`collectors` 处理本机及各平台解析，`probes` 管理子进程/socket，`server` 装配工具和实例生命周期。没有第二份单文件业务实现。

配置及预算见[配置参考](../docs/configuration.md)，已变更的 host key、结果与监听语义见[迁移说明](../docs/compatibility.md)。测试、独立 wheel 和实机边界见[验证记录](../docs/validation/2026-10-25-validation.md)。远程 Linux/macOS/Windows 解析测试通过，SSH 实机尚未验证。
