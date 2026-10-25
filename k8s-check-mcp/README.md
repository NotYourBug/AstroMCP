# Kubernetes 巡检与排障 MCP

`astromcp-k8s` 为 MCP 客户端提供 Kubernetes 节点与 Pod 状态查询、资源审计和故障取证。需要 Python 3.10+，使用独立的只读 kubeconfig 或 ServiceAccount，不执行驱逐、迁移或配置变更。

## 功能

| 工具 | 用途 |
| --- | --- |
| `health_check` | 验证 API 访问能力，不统计集群总节点数 |
| `list_nodes` | 按页查询节点，可使用 label selector |
| `get_node_detail` | 查看节点条件、实时指标和 Pod 样本 |
| `get_node_resource_usage` | 审计 requests/limits 与 allocatable，列出资源贡献和风险 |
| `find_problem_nodes` | 扫描 NotReady、压力、高负载等问题节点 |
| `list_node_pods` | 按页查询节点上的活动 Pod 及问题摘要 |
| `diagnose_pod_issues` | 检查 Pod/init 容器状态，结合 Events 和问题容器日志摘录给出建议 |

还提供集群摘要资源 `k8s://cluster/summary`，以及节点排障提示词 `node_triage_prompt` 和集群巡检提示词 `cluster_problem_scan_prompt`。

## 安装与启动

从仓库根目录单独安装；`yaml` extra 用于读取 YAML 配置：

```sh
python -m pip install './k8s-check-mcp[yaml]'
astromcp-k8s
```

需要使用仓库锁定的依赖版本时，在仓库根目录执行：

```sh
uv sync --locked --all-packages --all-extras --dev
uv run --frozen --all-packages astromcp-k8s
```

新 CLI 默认 stdio，启动后等待 MCP 客户端输入，也可使用 `python -m k8s_check_mcp`。旧 `mcp_server.py` 是已安装包的兼容入口，仍默认 SSE，地址为 127.0.0.1:8080。

## 连接集群与 MCP 客户端

通过 `MCP_K8S_KUBECONFIG` 指定 kubeconfig，必要时用 `MCP_K8S_CONTEXT` 选择 context。未显式指定时使用 SDK 默认 kubeconfig 查找。以下客户端配置中的 `TEST_ONLY` 路径需替换；若找不到命令，将 `command` 换成虚拟环境中可执行文件的绝对路径。

```json
{
  "mcpServers": {
    "astromcp-k8s": {
      "command": "astromcp-k8s",
      "args": [],
      "env": {
        "MCP_K8S_KUBECONFIG": "TEST_ONLY_KUBECONFIG_PATH"
      }
    }
  }
}
```

在集群内运行可设置 `MCP_K8S_IN_CLUSTER=true` 使用 ServiceAccount。凭据需具备所需 Node、Pod、Events、日志和 metrics 读取权限；[只读 RBAC 示例](../examples/k8s-readonly-rbac.yaml)提供集群范围的 get/list 权限，使用时按实际范围调整。TLS 校验默认开启。

## 常用排障顺序

1. 调用 `health_check` 确认 API 可以访问。
2. 读取 `k8s://cluster/summary` 或调用 `find_problem_nodes` 找到调查范围。
3. 对目标节点调用 `get_node_detail`、`get_node_resource_usage` 和 `list_node_pods`。
4. 对问题 Pod 调用 `diagnose_pod_issues`，提供 `pod_name` 与 `namespace`。

完整请求示例见 [tool-calls.json](../examples/tool-calls.json)。公开列表默认每页 100 条，最大 200 条；有 `continue_token` 时带回相同参数继续请求。问题扫描使用 `max_results` 限制输出，通过缩小 label selector 进一步调查。

## HTTP / SSE

需要网络传输时显式指定：

```sh
astromcp-k8s --transport http --host 127.0.0.1 --port 8080
astromcp-k8s --transport sse --host 127.0.0.1 --port 8080
```

HTTP 路径为 `/mcp`，SSE 路径为 `/sse`。所有网络传输只允许 loopback；stdio 的 stdout 专供协议，日志写 stderr。

## 配置与诊断边界

配置支持 JSON/YAML 文件和环境变量，启动时校验，修改后重启。日志采集可通过 `MCP_K8S_INCLUDE_LOGS=false` 关闭。

结果中的 `complete=false`、warnings 和未知值表示证据不足。缺 metrics、缺权限或资源模型未覆盖时，不能据此判定集群健康。Events、日志及采集文本是不可信取证材料；常见脱敏不能保证识别所有业务敏感内容。

- [配置与预算](configuration.md)：集群、API 速率/并发、阈值、分页、日志与锁定依赖安装。
- [兼容与结果说明](compatibility.md)：continuation、覆盖范围、未知资源模型和入口迁移。
- [配置示例](../examples/k8s-config.json)、[客户端配置示例](../examples/mcp-clients.json)。
- [验证记录](../docs/validation/2026-10-25-validation.md)：模拟与协议测试已执行，真实集群尚未联调。
