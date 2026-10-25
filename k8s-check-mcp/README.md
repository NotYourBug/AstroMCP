# K8s Check MCP Server

一个独立可移植的 MCP Server：把常见的 Kubernetes 节点/Pod 排障动作封装成 MCP Tools/Resources/Prompts，供 Claude/Trae 等 MCP Client 直接调用，用“工具取证 + SOP Prompt”方式降低排障门槛并提升建议准确性。

## 特性

- 节点状态检索：Ready/Conditions/Taints/Allocatable 等摘要
- 节点详情下钻：节点实时用量（依赖 metrics-server，缺失自动降级）、节点上的 Pod 样例
- 节点资源审计：汇总活跃 Pod 的 requests/limits，计算与 allocatable 的占比，并识别 requests 超配风险
- 异常节点扫描：NotReady/Pressure/高负载等
- Pod 异常诊断：CrashLoopBackOff/ImagePullBackOff/OOMKilled/重启过高等，并联动 Events 与日志摘录
- 速率限制与并发保护：令牌桶 + in-flight 上限，避免 LLM 高频调用压垮 apiserver

## 能力清单

- Tools：
  - `list_nodes`
  - `get_node_detail`
  - `get_node_resource_usage`
  - `find_problem_nodes`
  - `list_node_pods`
  - `diagnose_pod_issues`
- Resource：
  - `k8s://cluster/summary`
- Prompts：
  - `node_triage_prompt`
  - `cluster_problem_scan_prompt`

## 架构（调用链）

`LLM / MCP Client → MCP Server（FastMCP）→ Kubernetes Python Client → Kubernetes API Server`

MCP Server 只提供读为主的排障接口，建议配合最小权限 RBAC 与网络隔离使用。

## 目录结构（最小可运行）

- `mcp_server.py`
- `requirements.txt`

`mcp_server.py` 设计为”单文件自包含”：内置资源单位换算、阈值读取、Pod 异常识别等逻辑，可单独拷贝到新目录运行。

## 环境要求

- Python 3.10+
- 可访问 Kubernetes API：
  - 本地模式：使用 kubeconfig
  - 集群内模式：使用 ServiceAccount

## 安装

```bash
pip install -r requirements.txt
```

## 运行

### 1) 本地 kubeconfig 模式

Windows PowerShell：

```powershell
$env:MCP_K8S_KUBECONFIG="C:\Users\your-user\.kube\config"
$env:MCP_K8S_CONTEXT="your-context"
$env:MCP_K8S_VERIFY_SSL="true"
python mcp_server.py
```

Linux/macOS：

```bash
export MCP_K8S_KUBECONFIG="$HOME/.kube/config"
export MCP_K8S_CONTEXT="your-context"
export MCP_K8S_VERIFY_SSL="true"
python mcp_server.py
```

### 2) 集群内模式（InClusterConfig）

```bash
export MCP_K8S_IN_CLUSTER="true"
export MCP_K8S_VERIFY_SSL="true"
python mcp_server.py
```

## 配置

### 连接相关环境变量

- `MCP_K8S_IN_CLUSTER`
  - 是否使用 InClusterConfig
- `MCP_K8S_KUBECONFIG`
  - 本地 kubeconfig 路径
- `MCP_K8S_CONTEXT`
  - kubeconfig context 名称
- `MCP_K8S_VERIFY_SSL`
  - 是否校验证书，教学/测试环境可设为 `false`
- `MCP_CONFIG`
  - 可选，JSON/YAML 配置文件路径，用于覆盖阈值

### 阈值与诊断策略

- `MCP_THRESHOLD_NODE_CPU_UTILIZATION`
  - 可选，节点 CPU usage/capacity 的阈值（默认 0.80）
- `MCP_THRESHOLD_NODE_MEMORY_UTILIZATION`
  - 可选，节点内存 usage/capacity 的阈值（默认 0.85）
- `MCP_THRESHOLD_POD_RESTART_COUNT`
  - 可选，Pod 重启次数阈值（默认 3）
- `MCP_THRESHOLD_REQUESTS_OVERCOMMIT_RATIO`
  - 可选，requests/allocatable 超配阈值（默认 1.0）
- `MCP_THRESHOLD_LIMITS_OVERCOMMIT_RATIO`
  - 可选，limits/allocatable 提示阈值（默认 1.5）

### 限流与并发保护（防止 LLM 高频调用）

MCP Server 内部对 K8s API 调用做了令牌桶限流和 in-flight 并发上限控制。

- `MCP_K8S_API_RPS`
  - 可选，K8s API 调用速率限制（每秒令牌数，默认 5）
- `MCP_K8S_API_BURST`
  - 可选，K8s API 令牌桶突发容量（默认 10）
- `MCP_K8S_API_MAX_IN_FLIGHT`
  - 可选，同时进行中的 K8s API 调用上限（默认 3）

如果你在大集群里使用，建议从更保守的参数开始（例如 `RPS=2~3`、`MAX_IN_FLIGHT=1~2`）。

## Tool 说明

### `list_nodes`

列出节点摘要，适合让 LLM 先建立上下文。

### `get_node_detail`

查看单节点详细信息，适合对异常节点做二次下钻。

### `get_node_resource_usage`

做节点资源审计：统计节点上活跃 Pod 的 CPU/Memory requests/limits，并计算其与 allocatable 的占比。

- requests 超过阈值：输出 BestEffort Pod 候选建议（硬风险）
- limits 超过阈值：输出提示信息（软风险）

### `find_problem_nodes`

自动识别异常节点，聚焦 NotReady/Pressure/Unschedulable/高负载等信号。

### `list_node_pods`

列出指定节点上的活跃 Pod，并把问题 Pod 标出来。

### `diagnose_pod_issues`

对指定 Pod 做精简版诊断：

- `Pending`：补充 Events
- `CrashLoopBackOff` / `ImagePullBackOff` / `OOMKilled` 等：尝试补充日志摘录（默认截断）

## Resource 与 Prompt

### Resource: `k8s://cluster/summary`

给 LLM 一个轻量级集群摘要：集群名、节点总数、异常节点数、可用工具列表等。

### Prompt: `node_triage_prompt`

节点排障 SOP：节点详情 → 资源审计 → 节点 Pod → 单 Pod 诊断。

### Prompt: `cluster_problem_scan_prompt`

全局巡检 SOP：先看摘要 → 扫描问题节点 → 逐步下钻定位。

## 最小权限 RBAC（集群内模式建议）

建议为 MCP Server 单独创建 ServiceAccount，并授予只读为主权限（nodes/pods/events/pods/log + metrics 只读）。不同公司/集群策略不同，你可以按需裁剪：

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: k8s-check-mcp
  namespace: kube-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: k8s-check-mcp-readonly
rules:
  - apiGroups: [""]
    resources: ["nodes", "pods", "events"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
  - apiGroups: ["metrics.k8s.io"]
    resources: ["nodes"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: k8s-check-mcp-readonly
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: k8s-check-mcp-readonly
subjects:
  - kind: ServiceAccount
    name: k8s-check-mcp
    namespace: kube-system
```

## 安全建议

- 不要让 LLM 直接持有 kubeconfig/token，统一走 MCP Server 做能力收敛与审计边界。
- 如果你不希望暴露日志内容，建议在运行环境层面限制 `pods/log` 权限或在代码侧增加开关禁用日志读取。
- 生产环境建议启用证书校验（`MCP_K8S_VERIFY_SSL=true`），并配合 NetworkPolicy/内网访问控制。

## 设计取舍与边界

这份 MCP Server 专注于“LLM 可调用的排障工具层”，刻意不包含：

- 报告输出（JSON / HTML）
- Pushgateway / Prometheus 指标推送
- Alertmanager 告警治理
- IM 通知与协同（飞书/钉钉等）
- PV/PVC 归档与清理链路

你可以把它作为独立仓库发布，或作为现有运维体系的“AI 工具层”接入。

