# AstroMCP Kubernetes 收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 保留缺容量时的部分证据，补齐 Failed/OOMKilled 日志和明细截断信号。

**Architecture:** inspector.py 编排，diagnostics.py 纯诊断，client.py 管日志读取预算。沿用原 SDK gateway、分页和 metrics snapshot，不重构资源模型。

**Tech Stack:** Python >=3.10、现有锁定 FastMCP/Kubernetes 37.x/Pydantic、pytest、uv、Ruff。

**Spec:** [收尾设计提案](../specs/2026-11-01-astromcp-closure-design.md)。状态：用户已批准，子计划修复及包级验收完成；整体验收见主计划和本轮验证记录。

## Global Constraints

- 继承设计全部约束；保持 7 tools、1 resource、2 prompts、原参数 required/default。
- 所有测试使用 fake SDK 或真实 SDK 的内存对象，不访问默认 kubeconfig/集群；不扩大 RBAC。
- Pod 原始字段保留，未知资源继续 unknown；不补做 Pod-level/resize 模型，不升级 SDK。
- 字段扩展为可选的结果增量；有效输入的既有数值和 issue 摘要保持兼容。

## Review Focus

1. capacity/allocatable 的 None、空字典、缺维度、0、非法 quantity：K1。
2. 有效 allocatable + 缺 capacity/metrics 时，不抹掉已知超配：K1。
3. Failed/OOMKilled、零重启的 current 日志、恢复历史：K2。
4. 403/关闭日志/日志截断不能伪报完整，也不能阻断已有 Pod 证据：K2。
5. 20/21 与 50/51 边界、API 部分页、完整汇总与不完整明细：K3。

## 文件职责

| 文件 | 修改目的 |
| --- | --- |
| k8s-check-mcp/src/k8s_check_mcp/inspector.py | 容量容错、日志触发和明细元数据 |
| k8s-check-mcp/src/k8s_check_mcp/diagnostics.py | 如需，抽取纯函数复用容量/容器状态判定，禁止 MCP 导入 |
| k8s-check-mcp/src/k8s_check_mcp/client.py | 有界日志 current/previous 次序 |
| k8s-check-mcp/tests/unit/test_resource_advice.py | 无效容量与已知汇总 |
| k8s-check-mcp/tests/unit/test_pod_evidence.py | 诊断到日志的完整调用路径 |
| k8s-check-mcp/tests/unit/test_detail_coverage.py（新增） | 明细截断与扫描覆盖 |
| k8s-check-mcp/compatibility.md、configuration.md | 新字段、nullable 边界、日志语义 |

### Task K1：缺容量时保留审计（K-C1）

**Interfaces:** 保持 `K8sInspector.get_node_resource_usage(self, node_name) -> dict`。复用 quantities.py 的解析规则；如抽内部解析 helper，由实现者在 inspector/diagnostics 中决定最小放置位置，不能改变有效输入结果。

- [x] 在 test_resource_advice.py 使用现有 cluster fixture，加入 `test_audit_handles_unknown_capacity`，对 capacity、allocatable 分别参数化 None、{}、缺 cpu、缺 memory、0、非法字符串。保留一个 requests=5 CPU/1Gi 的 Pod 和 allocatable=4 CPU/8Gi 基准。
  - 未知维度数值及 human 为 None，有效维度不变，requests 合计仍 5000m/1073741824 bytes。
  - allocatable 任一维度未知：对应 ratio 为 None、`overcommit == {'requests': None, 'limits': None, ...}` 的两个布尔值为 None、resource_advice 为空、complete=False、warnings 非空；不能抛 AttributeError/ValueError。
  - 仅 capacity 无效但 allocatable 与 Pod 扫描有效：requests 超配仍 True，整体 complete=False。
  - metrics 404 时，原先已知 requests 超配继续 True；不支持 Pod 模型仍 unknown。
- [x] 运行 `uv run --frozen --all-packages --all-extras python -m pytest k8s-check-mcp/tests/unit/test_resource_advice.py -q`，确认本次空字段/非法字段回归失败。
- [x] 分维度归一化容量并捕获解析错误；缺值不用字符串 None 或伪造 0；标记 incomplete，保留已知 requests/limits 和其他证据。只使用有效 allocatable 判 overcommit。human 格式化接受未知分支，不向原格式化函数传 None。
- [x] 重跑该文件及 `uv run --frozen --all-packages --all-extras python -m pytest k8s-check-mcp/tests tests/contracts -q`；兼容说明记录异常输入改为部分结果，以及 nullable 数值/human。
- [x] 审阅后按提交授权执行 `fix(k8s): retain resource evidence with unknown capacity`。

### Task K2：修复日志触发与读取次序（K-C2）

**Interfaces:** `diagnose_pod_issues(self, pod_name, namespace) -> dict` 保持；`read_log_excerpt(gateway, *, namespace: str, pod_name: str, container_name: str, deadline: float, prefer_previous: bool = True) -> EvidenceResult` 是唯一内部接口增量。

- [x] 在 test_pod_evidence.py 补 end-to-end inspector 测试：
  - `test_failed_oom_collects_current_logs`：真实 SDK Pod 对象，phase=Failed、app terminated OOMKilled/137、restart_count=0；返回仍 `issue == 'phase=Failed'`，选 app，第一次日志调用 previous=False，日志摘要包含模拟证据。
  - `test_crashloop_prefers_previous_logs`：重启 3 次、当前 waiting CrashLoopBackOff，第一次 previous=True。
  - `test_recovered_oom_does_not_collect_logs`：Running Ready=True、历史 OOM、重启 1 次；issue 为 None、日志调用 0 次。
  - `test_failed_oom_log_denied_retains_findings`：403/关闭 include_logs 分别保持 Pod/findings，complete=False、warnings 非空；403 不反复尝试。
  - current 不存在时允许回退 previous；复用已有日志字节上限、截断、脱敏测试，不能因次序调整退化。
- [x] 运行 `uv run --frozen --all-packages --all-extras python -m pytest k8s-check-mcp/tests/unit/test_pod_evidence.py -q`，确认 Failed/OOM 日志触发回归先失败。
- [x] 从所选容器当前 error findings 判断日志条件，禁止使用 issue 子串或 info 历史触发。通过该容器 restart_count 决定 prefer_previous；client 按指定次序尝试，保留既有失败分类与预算。
- [x] 重跑 K8s+contracts，更新兼容说明；日志仍只采一个问题容器，不扩大取证范围。
- [x] 审阅后按提交授权执行 `fix(k8s): collect logs for failed OOM containers`。

### Task K3：让明细截断可见（K-C3、历史 K9/B9）

**Interfaces:** `get_node_detail` 增 `pods_sample_returned: int`、`pods_sample_truncated: bool`；`get_node_resource_usage` 增 `pod_breakdown_returned: int`、`pod_breakdown_truncated: bool`。不改工具签名、API continuation 或扫描对象上限。

- [x] 在新 test_detail_coverage.py 使用完整 fake Pod 列表和匹配 metrics，增加：
  - `test_pod_sample_boundary` 参数化 20/21：returned=20；truncated 分别 False/True；21 时 complete=False 且有 warning。
  - `test_resource_breakdown_boundary` 参数化 50/51：returned=50；truncated 分别 False/True；51 时 pod_count=51、requests 合计覆盖 51 个 Pod。
  - `test_truncation_preserves_known_overcommit`：完整扫描 51 个 Pod、合计 CPU requests 超过 allocatable，明细截断但 overcommit.requests 仍 True。
  - `test_partial_api_scan_does_not_claim_total`：分页中途 403/预算耗尽，保留已扫描样本、原 incomplete/warnings；不凭样本数量推断全集完整，returned 仍等于实际列表长度。
- [x] 运行 `uv run --frozen --all-packages --all-extras python -m pytest k8s-check-mcp/tests/unit/test_detail_coverage.py -q`，预期新增字段断言失败。
- [x] 添加明细元数据；在资源汇总和超配结论计算完后再标记明细截断，保持有效合计；warning 提示详情可通过 list_node_pods 分页，资源明细可按分页 Pod 继续取证，不能暗示存在未实现的资源 cursor。
- [x] 重跑 K8s+contracts。compatibility.md 移除“无独立明细截断标志”限制，解释 complete 与汇总已知值可并存；配置默认值无需改变。
- [x] 审阅后按提交授权执行 `fix(k8s): expose detail truncation metadata`。

## 子计划完成条件

K1/K2/K3 回归从失败转绿；未知不伪装 0、phase 摘要不遮盖当前日志、完整合计不被明细截断破坏；无 RBAC/工具契约扩大。转主计划整体审查与验收。
