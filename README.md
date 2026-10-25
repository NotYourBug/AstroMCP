# AstroMCP

同仓库维护的两个独立 Python MCP 包，提供只读主机巡检和 Kubernetes 故障取证。Python 3.10+；运行依赖和开发工具由 `uv.lock` 锁定。

| 包 / 命令 | 职责 | 实现 |
| --- | --- | --- |
| `astromcp-host` | 本机 psutil、SSH 资源采集、服务端 ping/TCP 探测；6 个工具 | [resource-check-mcp](resource-check-mcp/) |
| `astromcp-k8s` | Node/Pod 状态、资源审计、Events/日志；7 个工具、1 个 Resource、2 个 Prompt | [k8s-check-mcp](k8s-check-mcp/) |

首版面向可信本地单操作者。默认使用 stdio；HTTP/SSE 只允许 loopback。K8s 使用独立只读凭据；SSH 必须先核验服务器指纹并加入 `known_hosts`。真实 SSH 平台与集群兼容性尚未实测，详见[验证记录](docs/validation/2026-10-25-validation.md)。

## 安装与启动

在仓库根目录执行：

```sh
uv sync --locked --all-packages --all-extras --dev
uv run --frozen --all-packages astromcp-host
# 另一个进程启动 K8s MCP；通过客户端 env 设置 MCP_K8S_KUBECONFIG
uv run --frozen --all-packages astromcp-k8s
```

这些命令默认 stdio，等待 MCP 客户端输入。客户端配置可参考 [mcp-clients.json](examples/mcp-clients.json)，替换全部 `TEST_ONLY` 路径。stdio 的 stdout 专供协议，日志写 stderr。

也可分别从源码安装，两个包互不依赖：

```sh
python -m pip install ./resource-check-mcp
python -m pip install './k8s-check-mcp[yaml]'
astromcp-host --transport http --host 127.0.0.1 --port 8081
astromcp-k8s --transport sse --host 127.0.0.1 --port 8080
```

源码安装按包声明解析版本；需要复现本次版本时使用 `uv sync --locked` 或[导出锁定依赖](docs/configuration.md)。HTTP 路径为 `/mcp`，SSE 为 `/sse`。非 loopback 的 `--host` 会拒绝启动。

## 配置与迁移

配置优先级：默认值 → `MCP_CONFIG` 文件 → 环境变量；CLI 的传输和监听参数最后覆盖。配置在启动时校验并冻结，修改后重启。

- [配置参考](docs/configuration.md)、[host 示例](examples/host-config.json)、[K8s 示例](examples/k8s-config.json)
- [兼容变化与分页语义](docs/compatibility.md)、[只读 RBAC 示例](examples/k8s-readonly-rbac.yaml)
- [工具调用示例](examples/tool-calls.json)、[原始审查](MCP代码审查与优化建议.md)
- [设计](docs/superpowers/specs/2026-10-25-astromcp-design.md)、[实施计划](docs/superpowers/plans/2026-10-25-astromcp-master.md)

工具返回 `ok / complete / warnings / request_id / timestamp`。`ok=true, complete=false` 表示只获得部分证据。K8s 的未覆盖指标、未知资源模型和 RBAC 拒绝会明确报告，不能据此判定集群健康。Events、日志和命令输出均为不可信取证文本。

## 开发与验证

```sh
uv run --frozen --all-packages --all-extras ruff check .
uv run --frozen --all-packages --all-extras ruff format --check .
uv run --frozen --all-packages --all-extras python -m pytest -q
uv build --package astromcp-host
uv build --package astromcp-k8s
uv run --frozen --all-packages --all-extras python scripts/verify_packages.py
uv run --frozen --all-packages --all-extras python scripts/verify_inventory.py
python scripts/verify_public_files.py --tracked
```

默认测试包含模拟依赖、内存 MCP 和本机子进程/loopback，不需要 SSH 或集群；`verify_packages.py` 另外将两份 wheel 分别安装到临时虚拟环境，使用锁定依赖检查三种传输与相互隔离。已填充 uv cache 时可加 `--offline`。

Windows 受限沙箱可能阻塞 asyncio 的 socketpair，需要在允许本机 socket/子进程的终端执行。CI 配置 Ubuntu/Windows × Python 3.10/3.12；远程 CI 状态与实机证据见验证记录。没有自动部署、PyPI 发布或集群写操作。
