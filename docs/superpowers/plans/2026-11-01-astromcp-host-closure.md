# AstroMCP Host 收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Host 的过期成功与 TCP 清理覆盖结果，保留既有只读接口及取消后的 worker lease。

**Architecture:** 延续 server.py 的协议结果边界、collectors/local.py 的本机 worker、probes.py 的探测职责；不改变 SSH 身份或客户端生命周期。

**Tech Stack:** Python >=3.10、现有锁定 FastMCP/Paramiko/psutil/Pydantic、pytest、uv、Ruff。

**Spec:** [收尾设计提案](../specs/2026-11-01-astromcp-closure-design.md)。状态：待审阅、未实施。

## Global Constraints

- 继承设计全部约束；保持 6 工具签名、required/default 和主要数值字段。
- 不升级依赖，不新增工具或全局业务单例，不强杀线程，不提前释放取消 worker 的名额。
- 本轮日期 2026-11-01 仅为计划日期，实施记录使用实际执行日期。
- 以下命令在仓库根目录运行；本计划不访问真实 SSH 或外部探测目标。

## Review Focus

1. future 已完成但事件循环延迟，不能绕过最终 deadline：H1。
2. 队列先过期与总预算先过期必须区分，且不多启动 worker：H1。
3. 超时/取消后正在运行的 worker 仍占有 lease：H1。
4. 成功握手后的 close/wait_closed 错误保留 open=true：H2。
5. 清理阶段取消必须传播，不能被宽泛异常捕获吞掉：H2。

## 文件职责

| 文件 | 修改目的 |
| --- | --- |
| resource-check-mcp/src/resource_check_mcp/server.py | 工具发布结果前检查总 deadline |
| resource-check-mcp/src/resource_check_mcp/collectors/local.py | 提交前/返回后校验预算及错误分类 |
| resource-check-mcp/src/resource_check_mcp/probes.py | TCP 清理与探测结果分离，仍有界 |
| resource-check-mcp/tests/unit/test_deadline_regressions.py（新增） | 可控时钟、worker lease 回归 |
| resource-check-mcp/tests/unit/test_probes.py | TCP 清理错误、取消回归 |
| resource-check-mcp/compatibility.md、configuration.md | 结果完整度与预算边界 |

### Task H1：修复过期成功（H-C1）

**Interfaces:** 保持 `LocalCollector.call(self, fn, *args, **kwargs)` 与 `_operation(fn)`；已有 `HostOperationError(code, message)`、`_TOOL_DEADLINE`、`_err()` 继续使用。不新增公开参数。

- [ ] 在新增测试文件写下列失败用例，使用实例注入/模块局部时钟替身和 threading.Event；不要修改全局 time.monotonic 或依赖毫秒级真实机器调度：
  - `test_expired_deadline_does_not_submit_local_work`：设置过去的 `_TOOL_DEADLINE`，断言 TIMEOUT 且 injected worker 调用次数为 0。
  - `test_elapsed_deadline_beats_busy`：占满 4 个本机 worker，推进总 deadline，断言 TIMEOUT；另一用例只耗尽排队预算则 BUSY。
  - `test_completed_local_result_after_deadline_is_timeout`：worker 已完成，发布前推进可控时钟；工具结果 `ok is False`、`complete is False`、`error_code == 'TIMEOUT'`。
  - `test_local_cancel_and_timeout_keep_worker_lease`：worker 由 Event 阻塞；取消/超时 waiter 后额度仍满；释放 Event、worker 退出后才可提交新工作；finally 释放 Event 并 aclose。
- [ ] 运行 `uv run --frozen --all-packages --all-extras python -m pytest resource-check-mcp/tests/unit/test_deadline_regressions.py -q`。确认前三类命中本次缺陷而失败，不把测试环境/导入错误当 RED；lease 测试可从一开始就通过。
- [ ] 在本机提交前和返回后检查预算；总 deadline 优先于 queue deadline。server._operation 在返回成功结果前再次检查 deadline，阻止探测/SSH 等路径发布过期成功。保持错误结果、request_id、finally 清理和取消语义；不将过期数值发布为完整有效结果。
- [ ] 重跑定向测试及 `uv run --frozen --all-packages --all-extras python -m pytest resource-check-mcp/tests tests/contracts -q`，全部通过；更新 Host 权威文档说明最终复查与线程不能强杀的边界。
- [ ] 显式审阅本任务差异，若本轮执行已授权提交，提交 `fix(host): reject results after operation deadline`，只 stage 本任务文件。

### Task H2：保留 TCP 握手事实（H-C2）

**Interfaces:** 保持 `probe_tcp(host: str, port: int, *, timeout: float = 5.0) -> dict`；使用 `_TOOL_DEADLINE` 获取工具剩余预算，无 ContextVar 时使用本次探测的局部截止时间。成功清理结果保持原样；失败新增 warnings，不新增工具参数。

- [ ] 在 test_probes.py 注入 fake writer，新增：
  - `test_tcp_close_reset_preserves_open`：open_connection 成功、wait_closed 抛 ConnectionResetError；断言 `ok is True`、`open is True`、`complete is False`、warnings 含 `TCP_CLEANUP_FAILED`。
  - `test_tcp_close_timeout_preserves_open`：清理超时同上，warnings 含 `TCP_CLEANUP_TIMEOUT`；探测本身超时仍为 `ok=False, open=None, error_code=TIMEOUT`。
  - `test_tcp_close_error_is_bounded`：writer.close 抛 OSError 也转清理 warning；没有剩余预算时不再等待完整的 1 秒。
  - `test_tcp_cancel_during_cleanup_propagates`：等待关闭时取消，断言 CancelledError 传播且 writer.close 已调用；已有 DNS/拒绝/IPv6 分类仍成立。
- [ ] 运行 `uv run --frozen --all-packages --all-extras python -m pytest resource-check-mcp/tests/unit/test_probes.py -q`，确认清理异常回归先失败。
- [ ] 将成功探测结果保存后执行有界清理，只捕获相应 OSError/TimeoutError，不捕获 BaseException/CancelledError；等待上限取 1 秒、探测剩余预算、工具剩余预算中的最小值。结果返回前仍接受 H1 的总 deadline 复查。
- [ ] 重跑 Host 与 contracts 测试，全部通过；compatibility.md 明确 `open=true` 是已确认事实、清理不完整通过 complete/warnings 表达。
- [ ] 审阅后按提交授权执行 `fix(host): preserve TCP probe results during cleanup`。

## 子计划完成条件

H1/H2 失败回归均转绿；Host+contracts 无回退；没有提前释放 worker 名额、吞掉取消或新增外部连接。本子计划不自行宣告整个项目验收完成，转主计划 C2。
