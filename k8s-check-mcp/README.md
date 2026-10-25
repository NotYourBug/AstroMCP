# AstroMCP Kubernetes

独立发行包 `astromcp-k8s`，Python 3.10+。根目录 `uv sync --locked --all-packages --all-extras --dev` 后运行 `uv run --frozen --all-packages astromcp-k8s`。独立安装可在本目录执行 `pip install '.[yaml]'`。通过 `MCP_K8S_KUBECONFIG/MCP_K8S_CONTEXT` 选择 kubeconfig，或 `MCP_K8S_IN_CLUSTER=true` 使用只读 ServiceAccount。

新 CLI 默认 stdio。旧 `mcp_server.py` 是已安装包的兼容薄入口，仍默认 SSE，地址改为 127.0.0.1:8080。HTTP/SSE 只允许 loopback。

| 类型 | 名称 |
| --- | --- |
| Tool | `list_nodes`、`get_node_detail`、`get_node_resource_usage`、`find_problem_nodes`、`list_node_pods`、`diagnose_pod_issues`、`health_check` |
| Resource | `k8s://cluster/summary` |
| Prompt | `node_triage_prompt`、`cluster_problem_scan_prompt` |

`src/k8s_check_mcp` 按配置/模型、SDK gateway/限流、quantity/资源算法、诊断、展示、prompts、inspector 编排与 MCP server 装配分工。扫描分页并共享一次 metrics 快照；缺证据返回 incomplete/unknown，Pod init/readiness 与资源模型均有单测。Events 按 UID、日志在 API 和展示两层限量并做常见脱敏。

本服务只读，不执行驱逐、迁移或配置变更。配置见[参考](../docs/configuration.md)，权限见[RBAC 示例](../examples/k8s-readonly-rbac.yaml)，分页与未知值见[兼容说明](../docs/compatibility.md)，实际验证边界见[记录](../docs/validation/2026-10-25-validation.md)。真实集群尚未联调。
