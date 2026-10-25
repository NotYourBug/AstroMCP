# 开发与维护

AstroMCP 中的 MCP 分别维护；当前两个 Python 包使用同一个 uv workspace，共享开发工具和依赖锁定，各自安装和运行。根 README 只维护仓库介绍与目录索引，功能与操作文档放在对应 MCP 目录。

## 开发环境

Python 3.10+，在仓库根目录执行：

```sh
uv sync --locked --all-packages --all-extras --dev
```

运行依赖和开发工具由 `uv.lock` 锁定。通过包目录直接 `pip install` 会根据包声明解析版本；需要复现已验证版本时使用 locked sync，或参照各包的配置文档导出依赖。

## 检查与构建

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

默认测试包含模拟依赖、内存 MCP 和本机子进程/loopback，不需要 SSH 或集群。`verify_packages.py` 将两份 wheel 分别安装到临时虚拟环境，使用锁定依赖检查三种传输与相互隔离；已填充 uv cache 时可加 `--offline`。

Windows 受限沙箱可能阻塞 asyncio 的 socketpair，需要在允许本机 socket/子进程的终端执行。CI 覆盖 Ubuntu/Windows × Python 3.10/3.12，实际结果与实机验证范围见[验证记录](docs/validation/2026-10-25-validation.md)。

## 代码与文档归属

- `resource-check-mcp/src/resource_check_mcp/`：配置和结果模型、SSH 执行、平台采集、网络探测与 MCP 装配。
- `k8s-check-mcp/src/k8s_check_mcp/`：SDK gateway、限流、资源计算、诊断、展示、Prompt 和工具编排。
- 各 MCP 的 `README.md`、`configuration.md`、`compatibility.md`：功能、使用、配置和迁移说明的维护入口。
- `examples/`：配置、客户端和工具调用示例；保留 `TEST_ONLY` 占位值。
- `docs/validation/`：审查与验证记录，明确区分模拟、本机协议和真实基础设施证据。

历史[代码审查](MCP代码审查与优化建议.md)、[设计](docs/superpowers/specs/2026-10-25-astromcp-design.md)及[实施计划](docs/superpowers/plans/2026-10-25-astromcp-master.md)保留作为演进记录。当前运行实现以各目录的 src 包为准。
