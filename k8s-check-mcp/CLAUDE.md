# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

A self-contained MCP (Model Context Protocol) server that wraps Kubernetes node/Pod troubleshooting actions as MCP tools, resources, and prompts. LLMs consume these via FastMCP to diagnose clusters using a "tool取证 + SOP prompt" pattern. The server is **read-only** — it never mutates cluster state.

## Commands

```bash
# Install dependencies (Python 3.10+)
pip install -r requirements.txt

# Run locally with kubeconfig
export MCP_K8S_KUBECONFIG="$HOME/.kube/config"
export MCP_K8S_CONTEXT="your-context"
python mcp_server.py

# Run in-cluster (ServiceAccount)
export MCP_K8S_IN_CLUSTER="true"
python mcp_server.py
```

The server starts on `0.0.0.0:8080` via SSE transport (`main()` at line 1174).

## Architecture

Everything lives in a single file: `mcp_server.py` (~1180 lines). There are no internal modules or packages — the file is designed to be copied and run standalone. The README references a `k8s_inspector/` package layout, but the actual code is flat and self-contained.

**Call chain:** `LLM / MCP Client → FastMCP (SSE) → Kubernetes Python Client → K8s API Server`

### Key subsystems

1. **Dual-mode K8s client init** (`init_k8s_client`, line 247): Supports both local kubeconfig and in-cluster ServiceAccount. TLS verification is toggled via `MCP_K8S_VERIFY_SSL`.

2. **Three-layer rate limiter** (`_TokenBucketLimiter`, line 118): Token bucket + burst cap + in-flight semaphore. Every K8s API call goes through `_k8s_api_call()` (line 172). Defaults: 5 RPS, burst 10, max 3 concurrent. Tune via `MCP_K8S_API_RPS`, `MCP_K8S_API_BURST`, `MCP_K8S_API_MAX_IN_FLIGHT`.

3. **Resource quantity parsing** (`parse_quantity`, line 332): Handles both decimal (m, k, M, G) and binary (Ki, Mi, Gi) K8s units. Used by CPU/memory conversion helpers throughout.

4. **Thresholds configuration** (`Thresholds` dataclass + `_get_runtime_config`, line 206): Precedence is JSON/YAML config file (`MCP_CONFIG`) → environment variables (`MCP_THRESHOLD_*`) → hardcoded defaults. All thresholds are runtime-configurable.

5. **Pod issue detection** (`_pod_issue`, line 615): Checks container waiting/terminated reasons against `_BAD_POD_REASONS` (CrashLoopBackOff, ImagePullBackOff, OOMKilled, etc.) and restart counts against threshold.

### MCP surface

| Type | Name | Purpose |
|------|------|---------|
| Tool | `list_nodes` | Flat node summary with label selector |
| Tool | `get_node_detail` | Single node deep-dive with metrics + Pod sample |
| Tool | `get_node_resource_usage` | Requests/limits audit vs allocatable, overcommit detection |
| Tool | `find_problem_nodes` | Scan for NotReady, pressure, high-load nodes |
| Tool | `list_node_pods` | Active Pods on a node with issue flags |
| Tool | `diagnose_pod_issues` | Pod-level diagnosis with events + log excerpts |
| Tool | `health_check` | Verify K8s API connectivity |
| Resource | `k8s://cluster/summary` | Lightweight cluster overview |
| Prompt | `node_triage_prompt` | SOP for single-node troubleshooting |
| Prompt | `cluster_problem_scan_prompt` | SOP for cluster-wide health scan |

### Design boundaries

The server deliberately excludes: report generation, Prometheus metrics push, Alertmanager integration, IM notifications, and PV/PVC lifecycle management. It is scoped to LLM-callable diagnostics only.

## Configuration reference

All configuration is via environment variables. See the README for the full list including `MCP_THRESHOLD_NODE_CPU_UTILIZATION` (default 0.80), `MCP_THRESHOLD_NODE_MEMORY_UTILIZATION` (default 0.85), `MCP_THRESHOLD_POD_RESTART_COUNT` (default 3), and overcommit ratios.
