# AstroMCP 主机资源 MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 或 superpowers:subagent-driven-development 按任务实施。Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 修复主机 MCP 的启动、SSH 安全、执行有界性和采集口径，交付独立 astromcp-host 包。

**Architecture:** H1–H4 在现有文件上修复真实行为并补回归；H5 在测试保护下提取配置、SSH、probe 和平台采集模块。MCP server 仅映射参数和输出，底层执行器可注入。

**Tech Stack:** Python >=3.10、FastMCP 3.x、Paramiko、psutil、Pydantic 2、pytest/pytest-asyncio、uv/setuptools。

**Spec:** `docs/superpowers/specs/2026-10-25-astromcp-design.md`。Prerequisite：总计划 M1/M2 完成，设计与计划已批准。

## Global Constraints

- 原 6 个工具名、参数和主要成功字段保留；不新增任意命令工具。
- 配置优先级：环境变量 > 配置文件 > 默认值；非法值启动失败。
- SSH 默认 RejectPolicy；不提供自动接受未知密钥的隐式降级。
- 工具总预算 30 秒、排队 2 秒、SSH 各连接阶段最多 8 秒、命令 15 秒、总输出 256 KiB、全局/单目标并发 4/2。
- ping/TCP 各 5 秒，CPU 窗口 1 秒，磁盘阈值 85 且限定 0–100。
- 首版仅本地/可信单操作者，HTTP/SSE 只允许 loopback；凭据不写入日志或公开示例。
- 目录根均为当前 workspace；原源码行号仅定位，修改后按函数名定位。

## Review Focus

1. Unicode、空格和引号的绝对 Unix 路径仍可检查，换行不能执行第二条命令——H1。
2. SSH stdout/stderr 同时满窗口、无退出码或无输出时仍会截止——H2。
3. asyncio 取消后底层 worker 未退出不能释放额度——H2。
4. Windows JSON 单对象/数组/null 与 macOS user+sys 不能悄悄变成健康 0——H3。
5. 多个 A/AAAA 地址、DNS 错误、超时、拒绝连接应有可区分结果——H4。

## 过渡与最终文件映射

H1–H4：Modify `resource-check-mcp/resource-check-mcp.py`，Create `resource-check-mcp/tests/unit/` 与 `fixtures/`；不先复制第二份业务实现。H5 将已修复内容移动到设计规定的 src/resource_check_mcp；原文件缩成薄入口。

### Task H1: 可运行入口、安全路径与 SSH 主机身份

**Files:** Modify `resource-check-mcp/resource-check-mcp.py:42–158,247–361,EOF`；Create `resource-check-mcp/tests/unit/test_launch.py`、`test_validation.py`、`test_host_keys.py`。

**Interfaces:** 新增 `load_settings(environ: Mapping[str,str], config_path: Path|None=None) -> HostSettings`；`build_unix_disk_command(path: str) -> str`；`build_windows_disk_command(path: str) -> str`；`create_ssh_client(settings: HostSettings) -> paramiko.SSHClient`；`main(argv: Sequence[str]|None=None) -> None`。迁移前定义在原文件，H5 保持接口移动到 config/collectors/ssh/入口。旧 run_remote_command 的 tuple[int,str,str] 暂保留。

- [x] Step 1：写失败用例：`/data\nwhoami`、CR/NUL 和相对 `-x` 路径拒绝；`/data files/业务`、`/data/O'Reilly` 经 `shlex.split(command)` 恰为 `['df','-kP',原路径]`；Windows C: 与 C:\\data 选 C 卷、UNC/无盘符拒绝。fake SSHClient 断言调用 load_system_host_keys、可选 load_host_keys，使用 RejectPolicy，未知/变化密钥时 exec_command 调用数为 0。`--transport stdio` 调用 run，HTTP 非 loopback 在 run 前拒绝，阈值 -1/101 拒绝。
- [x] Step 2：运行 `uv run --frozen python -m pytest resource-check-mcp/tests/unit/test_launch.py resource-check-mcp/tests/unit/test_validation.py resource-check-mcp/tests/unit/test_host_keys.py -q`，确认在当前入口、校验或策略处失败，而非测试导入方式失效。
- [x] Step 3：实现以上接口与 `if __name__ == '__main__': main()`。保留原工具签名；内部对 path 采用结构校验与 shell quoting，不能继续靠扩大黑名单。启动默认 stdio，显式 HTTP/SSE 默认 127.0.0.1:8081，非 loopback 拒绝；logging 保持 stderr。将 host key 异常分成 HOST_KEY_UNKNOWN/HOST_KEY_CHANGED，对外不包含私钥或密码。
- [x] Step 4：重跑上述测试和 `tests/contracts/test_registration.py`；新增成功 path 行为、缺 known_hosts 的失败说明断言。无真实 SSH 即可完成，不能通过测试时放宽主机策略。
- [x] Step 5：提交 `fix(host): secure SSH targets and add launch entrypoint`，关闭 R1/R2/R3；记录未知密钥需运维预配的迁移行为。

### Task H2: 双流读取、deadline、输出与并发清理

**Files:** Modify 原文件 `run_remote_command`、`detect_remote_os` 与各远程调用点；Create `resource-check-mcp/tests/unit/test_ssh_executor.py`、`test_ssh_cancellation.py`。

**Interfaces:** `SSHConnection(host: str, port: int, username: str, password: str|None, key_path: str|None)`（password 排除 repr，禁止整体日志序列化）；`CommandResult(exit_code: int|None, stdout: str, stderr: str, error_code: str|None, complete: bool)`；`SSHExecutor.execute(connection: SSHConnection, command: str, *, deadline: float) -> CommandResult`（async）；`SSHExecutor.aclose() -> None`。保留 run_remote_command 兼容适配，工具编排使用一个 deadline 覆盖 OS 探测与采集。执行器有全局和按目标额度，clock/client_factory 可注入。

- [x] Step 1：fake channel 构造 stdout/stderr 交替超窗口数据，断言退出状态获取之前已消费两种输出；构造只持续写 stderr、永不 exit、无输出、exit_code 非零、超过 262144 bytes。断言 TIMEOUT/OUTPUT_LIMIT、channel.close/client.close、permit 最终归还。取消后让 fake worker 延迟退出，断言新调用仍占不到旧额度，后台数量不超过配置。
- [x] Step 2：运行 `uv run --frozen python -m pytest resource-check-mcp/tests/unit/test_ssh_executor.py resource-check-mcp/tests/unit/test_ssh_cancellation.py -q`，应复现原顺序/无截止或并发不受限；用可控时钟而非 sleep 15 秒。
- [x] Step 3：实现有界 worker、持续双流 drain、整体 monotonic deadline、连接与命令阶段剩余预算、输出字节统计、退出后余量读取和 finally 清理。取消时通知并关闭底层连接，不把 asyncio Future 的取消当成线程已退出；按 worker 实际结束归还额度。OS 探测认证失败不再盲目重连尝试 Windows；只有命令不支持才允许第二种探测，共用时间预算。
- [x] Step 4：运行 H1/H2 全部测试；并发 20 个 fake 请求，断言 active_global<=4、active_target<=2、排队超 2 秒得到 BUSY。验证失败信息中不出现测试 password/key 内容。记录平台网络调用不响应 close 的剩余边界，不宣称可以强杀 Python 线程。
- [x] Step 5：提交 `fix(host): bound SSH execution and clean up cancelled work`，关闭 R4。

### Task H3: 本地异步采集与跨平台解析口径

**Files:** Modify 原文件本地 psutil 分支及 `_mem_*`、`_cpu_*`、`_tcp_remote`、`_disk_*`；Create `resource-check-mcp/tests/unit/test_local_async.py`、`test_collectors.py`；Create `fixtures/{linux,macos,windows}/` 的无敏感文本/JSON 样例。

**Interfaces:** 各平台纯函数 `parse_cpu_output(text: str) -> float`、`parse_memory_output(text: str) -> dict`、`parse_disk_output(text: str) -> dict`、`parse_tcp_output(text: str) -> int`；采集编排 `collect_cpu/memory/disk/tcp` 接收 H2 executor 与 deadline。本地 Collector 用有界线程执行 psutil，复用原 `_ok/_err` 并增加 complete/warnings/request_id/error_code。

- [x] Step 1：本地 fake CPU 等待 threading.Event，同时启动 heartbeat，断言 heartbeat 在释放采样前运行。macOS fixture `CPU usage: 12.3% user, 7.7% sys, 80.0% idle` 断言 percent=20.0。Linux free 的 available 与 used 口径一致；Windows CPU 单对象/多 CPU 数组、memory/disk 正常 JSON 正确。额外断言 null/空输出/错误字段类型/非法百分比均 PARSE_ERROR，缺采集命令不能成为 tcp_count=0 的成功结果。
- [x] Step 2：运行 `uv run --frozen python -m pytest resource-check-mcp/tests/unit/test_local_async.py resource-check-mcp/tests/unit/test_collectors.py -q`，预期本地阻塞/mac CPU/管道失败行为失败；测试自身有 15 秒上限，不阻塞整个 suite。
- [x] Step 3：移出阻塞 psutil 调用；Unix 采集尽量执行一个能反映真实退出状态的原命令，在 Python 中解析，避免 `netstat|grep|wc` 掩盖失败。Linux CPU 使用 vmstat 两次采样输出后解析；macOS CPU 明确 total busy，采样窗口有说明；Windows CIM JSON 形状先校验。字节/计数在内部保留原精度、展示字段再转换，free_gb 的 available 口径写清。
- [x] Step 4：运行 H1–H3 单测及 host 成功结果兼容契约，断言原 total_gb/used_gb/free_gb/percent/tcp_count 字段保留；真实不可用值为 null/error，不能静默填 0。fixtures 标记来源为人工构造或实机脱敏，不声称 macOS 已实机联调。
- [x] Step 5：提交 `fix(host): keep collectors responsive and normalize metrics`，关闭 R5/R6/R7。

### Task H4: 有界 ping、IPv4/IPv6 TCP 与失败语义

**Files:** Modify 原文件 `check_host_status`、`_parse_ping_summary`、`check_service_port`；Create `resource-check-mcp/tests/unit/test_probes.py`、`test_probe_cancellation.py`。

**Interfaces:** `probe_ping(host: str, *, timeout: float=5.0) -> dict`、`probe_tcp(host: str, port: int, *, timeout: float=5.0) -> dict`（async）；`probe_origin='server'`。正常连接拒绝：ok=true/open=false；DNS/超时：ok=false/open=null/error_code；host 结果同样不把命令不可用当 unreachable。

- [x] Step 1：模拟 ping 成功、缺命令、5 秒超时和取消，断言超时/取消均 kill 后 wait，pipe 被回收；包含中文 Windows 输出时摘要不因固定 UTF-8 丢失。TCP 模拟 IPv6-only、IPv4+IPv6 后者成功、全部 refused、DNS error、timeout，断言具体状态及 probe_origin。host 含 shell 字符和前导选项拒绝，port=0/65536 拒绝。
- [x] Step 2：运行 `uv run --frozen python -m pytest resource-check-mcp/tests/unit/test_probes.py resource-check-mcp/tests/unit/test_probe_cancellation.py -q`，原 AF_INET 与无 deadline 路径应失败。
- [x] Step 3：实现 subprocess 整体等待与清理，不调用 shell；域名/IP 用结构验证，支持 IPv6。DNS 和各地址尝试共享 5 秒预算，不为每个地址重复分配 5 秒。socket 在全部路径关闭；能够确认 refused 时报告 closed，无法确认时给错误类型。
- [x] Step 4：重跑 host 全测试；使用仅 loopback 的可控监听器验证 IPv4/IPv6（平台不支持 IPv6 时明确 skip 原因）；外网与生产主机不是测试前提。检查无法连接的结果不会误标成机器整体宕机。
- [x] Step 5：提交 `fix(host): bound network probes and report precise failures`，关闭 R8/R9。

### Task H5: 在行为回归保护下提取独立 host 包

**Files:** Create `resource-check-mcp/src/resource_check_mcp/{__init__,__main__,server,config,models,ssh,probes}.py`、`collectors/{__init__,local,linux,macos,windows}.py`、`resource-check-mcp/tests/integration/test_server.py`；Modify 成员 pyproject、原脚本与已有测试 imports；原脚本只留薄入口。

**Interfaces:** `create_server(settings: HostSettings|None=None, *, executor: SSHExecutor|None=None) -> FastMCP` 位于 server.py；`main(argv=None)` 位于 __main__.py；settings 从 config.load_settings 读取；models 包含 H2 数据类和结果结构。工具注册在 create_server 内，不在 import 时创建网络连接。Console script `astromcp-host=resource_check_mcp.__main__:main`。

- [x] Step 1：在真实 FastMCP Client 下断言 6 个工具 Schema 不变，用注入 fake executor 调用 disk/mem/cpu/tcp 和 probe 替身，断言主要字段与 H1–H4 相同；创建两个不同 server 的配置/并发状态互不串用。导入纯 parser 不需要启动 MCP 或 SSH。
- [x] Step 2：运行 `uv run --frozen python -m pytest resource-check-mcp/tests/integration/test_server.py -q`，正式包尚不存在时应失败；此前 H1–H4 必须保持通过。
- [x] Step 3：按职责移动已验证函数，依赖方向为 server→collectors/probes→ssh，models/config 不反向导入 server。将成员从 virtual project 转为 setuptools src package，发行名 astromcp-host、版本 0.1.0、Python>=3.10。旧脚本导入 main，无 legacy.py/重复业务副本。lifespan 关闭 executor；局部 worker 未完成时按 H2 有界清理规则报告。
- [x] Step 4：运行 `uv sync --all-packages --all-extras --dev` 更新锁的 workspace 元数据；运行 host 全测试、总注册契约和 `uv build --package astromcp-host`。根目录外的独立 wheel 安装由 M3 最终验证。
- [x] Step 5：提交 `refactor(host): extract independently installable MCP package`；保留重构前 H4 提交为快速回滚点。

## Host 完成定义

- [x] R1–R9 每项有通过的回归测试和关闭记录。
- [x] 所有主要错误返回有 error_code，未知数据不报健康 0；SSH/探测的预算和输出限制可配置。
- [x] 配置/状态按 server 实例持有，无无限 worker、无默认 AutoAdd、无原始凭据日志。
- [x] 包构建、6 工具注册和 fake 业务调用通过；最终真实传输与独立安装交给 M3。
- [x] 未进行的 macOS/SSH 实机检查在 M4 验证矩阵中保留未验证标记。
