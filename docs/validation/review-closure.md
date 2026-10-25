# AstroMCP 审查关闭记录

2026-10-25。用户批准同仓库两个独立 MCP 包并选择本会话顺序实施。原始基线 `50f4b40`；独立审查范围 `50f4b40..0fb6c08`，后续修复提交 `22dea2f`。本地全套 **129 passed**；Ruff check、format 和公开路径检查通过。真实 SSH/集群尚未验证，远程 CI 结果见 [PR #1 检查](https://github.com/NotYourBug/AstroMCP/pull/1/checks)。

“通过”仅指下列本地证据，真实主机、集群与远程 CI 的范围见[验证矩阵](2026-10-25-validation.md)。原始审查保留在[历史报告](../../MCP代码审查与优化建议.md)，旧源码行号仅用于定位当时版本。

## 原始发现逐项追踪

Host 文件位于 `resource-check-mcp/src/resource_check_mcp/`，K8s 文件位于 `k8s-check-mcp/src/k8s_check_mcp/`；表中省略此前缀，工程文件按根路径。测试位于对应包 `tests/unit`，另行注明的除外。提交列记录主要修复，后续提取/格式化提交保留同一行为。

| ID | 任务与最终文件 | 验证证据 | 状态、限制 | 主要提交 |
| --- | --- | --- | --- | --- |
| R1 | H1/H5/M3；`__main__.py`、兼容薄入口 | `test_stdio_entrypoint`、`test_legacy_script_defaults`、`test_real_transport`、`verify_packages.py` | 通过；真实 SSH 未测 | `ef07e03`、`e002594`、`3bc04ff` |
| R2 | H1；`collectors/linux.py`、`collectors/windows.py`、`config.py` | `test_unsafe_path_rejected`、`test_path_is_one_argument`、`test_windows_drive_validation` | 通过；Unix 引用/拒绝换行，远程 shell 未实测 | `ef07e03` |
| R3 | H1/H2；`ssh.py` | `test_load_known_hosts_and_reject_unknown`，RejectPolicy 与显式 known_hosts | 通过；真实服务器指纹变更场景未联调 | `ef07e03`、`4bc53a9` |
| R4 | H2；`ssh.py` | `test_drains_both_streams_before_exit`、`test_output_and_deadline_bounds`、`test_cancel_retains_permit_until_worker_exits`、`test_twenty_requests_respect_limits` | 通过；底层线程不能强杀，真实 SSH 长稳未测 | `4bc53a9` |
| R5 | H3；`collectors/local.py` | `test_cpu_sampling_does_not_block_loop`、内存协议实际调用 | 通过；全平台 psutil 权限与性能未实测 | `845b6d3` |
| R6 | H3；`collectors/macos.py` | `test_mac_cpu_includes_system_time`、`test_mac_uses_last_sample` | 通过；macOS SSH 命令版本未实测 | `845b6d3` |
| R7 | H3；`collectors/__init__.py`、平台解析器 | `test_command_failure_not_successful_zero`、`test_bad_windows_cpu_not_zero` | 通过；上游命令失败不伪造 0，远程环境未实测 | `845b6d3` |
| R8 | H4；`probes.py` | `test_ipv6_probe`、`test_tcp_failure_types` | 通过；IPv6/DNS/拒绝/超时模拟，真实多地址网络未测 | `981e76c` |
| R9 | H1/H4；`probes.py`、`config.py` | `test_ping_timeout_reaps_child`、`test_host_options_rejected`、`test_threshold_validation` | 通过；实际 ICMP 防火墙差异未测 | `ef07e03`、`981e76c` |
| K1 | K2；`client.py`、`inspector.py` | `test_each_scan_fetches_metrics_once`（1/10/100 节点） | 通过；实际 metrics-server 负载未测 | `407712c` |
| K2 | K1/M5；`client.py`、`limiter.py`、`inspector.py` | `test_gateway_supplies_timeout_and_closes`、`test_no_request_after_deadline`、`test_limiter_queue_is_bounded`、`test_overdue_api_response_is_not_healthy` | 通过；同步 SDK 不能强杀，真实 API 慢流未测 | `5b02543`、`22dea2f` |
| K3 | K3；`diagnostics.py`、`inspector.py` | `test_init_crash_and_restart_detected`、`test_state_duration`、`test_normal_init_and_recovered_oom_not_current_failure`、`test_missing_status_is_safe` | 通过；真实 Pod 生命周期未测 | `66d58fb` |
| K4 | K4/M5；`resources.py`、`quantities.py`、`client.py` | `test_effective_resources`、`test_large_memory_precision`、`test_cpu_rounds_up`、`test_pod_level_and_resize_are_explicitly_unsupported`、`test_raw_response_survives_sdk_field_loss`、`test_installed_sdk_preserves_raw_pods` | 通过；Pod-level/resize 不支持时 unknown，未做真实集群版本矩阵 | `b25a309`、`22dea2f` |
| K5 | K4；`resources.py`、`inspector.py` | `test_no_best_effort_eviction_advice`、`test_audit_preserves_known_requests_when_metrics_unavailable` | 通过；仅建议，不实施迁移，调度效果未实测 | `b25a309` |
| K6 | K2；`models.py`、`inspector.py` | `test_missing_metrics_is_incomplete` | 通过；证据不足明确 incomplete，真实缺权限集群未测 | `407712c` |
| K7 | K1/M4；`config.py`、`__main__.py` | `test_invalid_settings_rejected`、`test_env_overrides_config`、`test_explicit_missing_config_rejected`、`test_k8s_file_transport_is_used` | 通过；非法值启动失败，用户部署配置未实测 | `5b02543`、`0fb6c08` |
| K8 | K5；`client.py`、`presentation.py` | `test_events_use_uid_and_latest_twenty`、`test_log_budget_and_redaction`、`test_permission_failure_returns_partial_evidence`、`test_common_secrets_and_partial_private_key` | 通过；常见脱敏非任意敏感值保证，真实 Events/日志权限未测 | `09a729a` |
| K9 | K2；`client.py`、`inspector.py` | `test_public_list_passes_continuation`、`test_repeat_token_stops_without_duplicate_page`、`test_scan_result_limit_visible`、`test_health_is_bounded` | 通过；页/扫描语义区分，实际大集群未测 | `407712c` |
| DEPLOY | H1/K1/M3；两包 `__main__.py`，`examples/k8s-readonly-rbac.yaml` | `test_network_bind_rejected`、`test_invalid_cli_port_rejected`、`test_real_transport` | 通过；loopback 首版，团队认证授权未实现 | `3bc04ff`、`0fb6c08` |
| DEPS | M1/M2/M3/M4；根/成员 pyproject、uv.lock、CI | `test_independent_console_scripts`、`verify_packages.py`、`verify_public_files.py` | 通过；本机 Python 3.11，CI 3.10/3.12 尚未运行，未做漏洞扫描 | `c81bdab`、`3bc04ff`、`0fb6c08` |
| DOCS | H5/K6/M4；src 模块、README/AGENTS/CLAUDE、配置/迁移参考 | `test_host_package_factory_and_instances`、`test_k8s_factory_isolates_clients`、`test_example_configuration`、`test_client_commands_and_migration_documented` | 通过；无 legacy 业务副本，真实平台兼容性未验证 | `e002594`、`36ec717`、`0fb6c08` |

## 组织性与后续方向

每个 MCP 仍是一个进程、一个独立发行包。协议装配、客户端生命周期、平台采集/诊断算法和展示各有归属，没有拆成多个网络服务，也没有引入第三个公共业务包。单文件适用于小型原型；这两个服务已经跨平台、跨权限来源并有取消/分页/资源算法，模块化能让故障和测试边界更清楚。

下一阶段优先完成隔离环境的实机验收与远程 CI，然后按测量结果做负载/长稳测试。Pod-level resources 和 resize 的完整计算应随明确服务端版本支持矩阵推进。只有实际连接/指标采集成本成为瓶颈时，再考虑 SSH 连接复用或跨请求缓存。面向团队的远程服务需要单独设计调用者认证、授权、审计和凭据隔离。

## 执行中作出的裁决

按发生顺序，均来自计划进度记录：

1. 复用已批准的初始工作区，在 `feat/astromcp-foundation` 实施：计划要求原地初始化，另建 checkout 会隐藏当前共享目录的变化；代价是没有额外 checkout 隔离，原始 main 基线保留。
2. 使用 Windows 原生任务/测试记录脚本：Bash 在沙箱中被 NtCreateDirectoryObject 拒绝；代价是记录工具实现不同，任务 BASE、测试日志和提交证据保留。
3. 基线保留原始空白以保持源码 SHA256：代价是历史提交含空白问题，最终分支按 diff 检查整理。
4. 异步测试在允许本机 socket 的普通进程环境执行：沙箱 socketpair 在事件循环建立阶段阻塞；代价是使用正常本机进程权限。没有外部 SSH/K8s 连接；H2 的失败测试曾对不可变 H1 源码重放，确认 RED。
5. K8s 增加 `inspector.py` 编排模块：避免 server 重新膨胀及诊断/展示循环引用；代价是增加一个内部模块，公开接口保留。
6. Kubernetes 依赖收窄为 `>=37,<38`：原始 `>=29` 无法证明兼容新旧反序列化接口，本次已用真实 37.0.1 测试；代价是旧 SDK 使用者需要升级，Python 最低版本仍为 3.10。
7. 将 reviewer 的“启动建议遮盖崩溃”小项及旧 BestEffort SOP 建议按重要问题修复：它们会给操作者相反的排障引导；若分级过严，代价是额外的定向修复与回归成本。
8. 对“强制终止阻塞 DNS/SDK/OS 线程”维持已声明边界：超时后不得伪报成功，worker 未结束不得释放额度，但不强杀线程；代价是底层不返回时仍可能延迟进程退出。
9. 真实 SSH/macOS/集群及远程 CI 维持未验证：没有测试基础设施或远程运行证据，报告明确区分；代价是平台兼容风险仍待实机确认。
10. 不扩展为任意业务秘密检测：修复范围内的 JSON Bearer 已覆盖，常见格式之外仍不能保证；代价是非标准业务敏感文本可能保留，应收窄日志权限或关闭日志。
11. 保留单个问题容器的有界日志摘录：逐容器枚举 current/previous 日志超出本轮约定；代价是其他容器的根因可能仍需后续人工取证。
12. 暂不新增 ping/TCP 的全局并发池：批准设计的并发上限覆盖 SSH/SDK/本机 worker，探测仅有单次时间界限；代价是大量并发调用的探测数量没有独立全局上限。
13. M5 文档和 EOF 整理在独立审查期间完成，不认定为运行时缺口：最终有追踪清单及 diff 检查；若整理有误，代价是交付文档可能不完整。

## 独立审查与最终交付

使用 Superpowers 的一次 fresh-context reviewer（GPT-6 Astra）审查整个分支。reviewer 只读检查源码、测试、设计、三个计划及裁决，并运行内存诊断，没有 SSH/网络/集群调用。原结论是不可合并：一个 Critical、七个 Important、两个 Minor；建议中另指出旧 SOP。执行者按实际影响将其中一个 Minor 和 SOP 建议升为 Important，共修复 10 项。修复按 RED→GREEN 进行；没有再次派 reviewer，把回归测试作为本轮修复验收。

| 审查项 | 最终处理 | 失败回归及结果（修复均为 `22dea2f`） |
| --- | --- | --- |
| B1 Critical：SDK 反序列化接口错误 | 使用 SDK 37 公开三参数接口，原始 Pod 字段保留、连接回收；约束版本范围 | `test_installed_sdk_preserves_raw_pods` GET/list RED→GREEN；真实 SDK 的 401/403/404 分类也通过 |
| B2 Important：SSH 零排队与过期分类 | 空闲时立即尝试一次；总 deadline 优先返回 TIMEOUT | `test_zero_queue_budget_allows_idle_executor`、`test_elapsed_tool_deadline_is_timeout_not_busy` RED→GREEN |
| B3 Important：过期 API 结果仍健康 | 阻塞调用返回、解码及发布结果前检查 deadline，保留部分证据时明确 incomplete | `test_overdue_api_response_is_not_healthy` RED→GREEN |
| B4 Important：探测忽略总预算 | 将 min(5, 剩余预算) 传入 ping/TCP，过期不再启动 | `test_probes_receive_remaining_tool_budget` RED→GREEN |
| B5 Important：未知 Ready 视为健康 | ReadinessUnknown finding、warnings 与 incomplete 覆盖 | `test_missing_readiness_is_not_healthy`、`test_unknown_readiness_propagates_coverage` RED→GREEN |
| B6 Important：resize 状态漏检 | 独立比较 allocated/status requests/limits，检测活动 resize conditions | `test_resize_checks_status_resources_even_if_allocated_matches`、`test_active_resize_condition_is_unknown` RED→GREEN |
| B7 Important：JSON Authorization 漏脱敏 | 支持引号包裹的 header key/Bearer value | `test_json_authorization_redacted_in_log_and_event` RED→GREEN，覆盖实际日志/事件输出路径 |
| B8 Important：无容量却声称完成负载检查 | 单独校验两个分母；保留已知条件，完整问题计数为 null | `test_missing_node_capacity_keeps_scan_incomplete` RED→GREEN |
| B10 升为 Important：启动提示遮盖硬故障 | error/warning 建议优先，仅纯启动状态使用 startup 提示 | `test_starting_advice_does_not_hide_init_crash` RED→GREEN |
| B11 按 Important 处理：旧 BestEffort SOP | Prompt 使用 requests 贡献、缺失证据及 QoS 边界 | `test_prompt_uses_request_contributors` RED→GREEN |

新增 22 项回归，其中 19 项先失败后通过，另外 3 项确认真实 SDK 的认证/权限/不存在分类原本正确。定向测试 25 passed；全套 129 passed。Ruff check / format 均通过，双 wheel/sdist 已构建，最终独立安装六种传输全部通过；4 个归档路径检查通过，详见验证记录。

延期小项 B9（唯一）：详情最多 20 个 Pod 样本、资源 breakdown 最多 50 行，缺少单独的明细截断信号。汇总仍计算全部已扫描 Pod，故保持 Minor，未纳入本轮修复；已在兼容说明公开该限制。

21 项追踪清单、公开文件路径、常见敏感内容候选检查与分支 diff 检查均已执行。内容候选只命中脱敏实现和测试中的示例文本，没有发现真实凭据；这不是完整的秘密/漏洞扫描。

用户审阅交付结果后已批准首次推送。远程 `main` 保留 `50f4b40` 原始审查基线，完整修复在 `feat/astromcp-foundation` 和 [Draft PR #1](https://github.com/NotYourBug/AstroMCP/pull/1)，尚未合并。首次远程 CI 发现的 Windows 浮点测试误报和复验入口已补充到验证记录；此前裁决第 9 项的 CI 状态以该后续记录为准，实机边界保持不变。没有添加许可证、自动部署或 PyPI 发布。
