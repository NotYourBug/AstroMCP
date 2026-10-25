**技术栈**： Python、Kubernetes Python SDK、FastMCP、MCP、Pydantic

**项目描述**：

面向 Kubernetes 集群巡检与故障诊断场景，自主设计并实现一套基于 MCP 协议的智能运维工具服务。项目通过 FastMCP 将节点状态检索、资源审计、异常节点识别、Pod 故障定位等能力封装为可供大模型直接调用的 Tools / Resources / Prompts，使 LLM 能够以自然语言方式完成 K8s 状态感知、问题分析和排障建议生成，提升运维自动化与诊断效率。

**核心贡献：**

- MCP Server 架构设计： 设计“LLM → FastMCP Server → K8s Client → Kubernetes API Server”的调用链路，构建适合 AI 使用的 K8s 运维工具层，实现巡检与诊断逻辑的标准化暴露。
- 工具化能力抽象： 输出节点列表、节点详情、节点资源审计、异常节点发现、节点 Pod 查询、Pod 问题诊断等多个核心工具，支持从集群级问题扫描逐步下钻到单节点、单 Pod 的排障过程。
- 资源解析与风险识别： 开发资源换算与审计逻辑，统一处理 CPU / Memory 资源单位，计算 Requests / Limits 与节点 allocatable 的占比，识别资源超配、节点高负载及潜在驱逐风险。
- 异常诊断增强： 在 Pod 非健康状态下联动 Events 与日志摘录，识别 CrashLoopBackOff 、镜像拉取失败、OOM、重启偏高等典型问题，提升 AI 对故障根因的分析能力。
- 面向 LLM 的 Prompt 设计： 预设结构化巡检与排障 Prompt，将人工排障经验抽象为可复用 SOP，降低 K8s 运维知识门槛，增强大模型在巡检分析场景中的可控性与稳定性。
- AI 工具调用治理（限流与并发保护）： 在 MCP服务侧实现令牌桶 + 并发上限控制，对 K8s API 调用做统一节流，防止 LLM 高频并发触发 list_pods/list_nodes 等重操作导致 API Server 压力飙升与服务内存突增。