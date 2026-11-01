# AstroMCP 收尾设计提案

日期：2026-11-01。基线：`c61d407`，工作区原先干净。状态：用户已批准并授权按本设计顺序实施；最新进展见本轮验证记录。

## 意图与成功标准

将两个独立 MCP 收敛为可维护、行为可信、安装可复现的个人本地工具。收尾意味着本轮确定缺陷有回归证据、公开契约不回退、剩余限制有明确归属，不意味着已经获得生产认证或实现所有历史性能建议。

本会话已对该基线执行：129 项既有测试通过，Ruff check/format、13 工具清单、113 条已跟踪公开路径检查通过；两包 wheel/sdist 构建、各自独立安装后的 stdio/HTTP/SSE 六种验收通过。额外模拟复现了下表四项缺陷。这些是基线证据，不能替代修复后的重跑。

## 方案比较与建议

| 方案 | 收益 | 代价及判断 |
| --- | --- | --- |
| 只修四个缺陷 | 最小改动 | 留下已知截断提示和状态文档漂移，难以完成交付闭环 |
| 修复缺陷、补齐结果语义、完成验收和文档 | 明确终点，沿用现有架构 | 推荐；需要两个包各自回归与一次整体交付验收 |
| 同时实现多人服务、缓存、连接复用、新资源模型 | 扩展用途 | 引入权限和版本矩阵的新项目，推迟本轮收尾 |

采用第二项。最初根据仓库“个人 MCP 存储与维护”定位提出，用户随后批准并授权实施。

## 约束与范围

- 保留两个独立发行包、依赖和实例生命周期，不创建共享业务包或网络微服务。
- 保持 Python >=3.10、当前锁定依赖；没有必要不升级依赖或版本号。
- 保留 13 个工具名称、既有参数 required/default、主要数值字段，以及 K8s 的 1 resource、2 prompts。
- 只读；HTTP/SSE 仅 loopback，stdio 继续支持。没有集群变更、任意命令或隐式扩大 RBAC。
- SDK/SSH/进程工作仍通过实例适配器注入。规则模块不导入 MCP 注册。
- 取消后，worker 未结束不得释放额度；不承诺 Python 可强杀线程。
- 默认测试只使用 mock、内存协议和 loopback，不读取真实默认集群或自动寻找凭据。
- 本轮修改源码、测试及各包权威文档；根 README 保持简短目录，历史报告保留原文。

## 本轮修复与行为决定

| ID | 当前问题 | 决定与验收重点 |
| --- | --- | --- |
| H-C1 | Host 过期后仍可能完整成功 | worker 提交前、结果返回后及工具发布前检查单调时钟；已过总 deadline 返回 TIMEOUT，不能误报 BUSY 或成功；保持 lease 到 worker 真正结束 |
| H-C2 | TCP 关闭异常覆盖已确定的端口状态 | 连接成功后 open=true 不因关闭异常改变；关闭 OSError/TimeoutError 转 warnings、complete=false，保留 ok=true；取消仍传播 |
| K-C1 | Node 容量字段 None 导致整次审计异常 | 对 None、空字典、缺维度、0、非法 quantity 分维度处理；未知数值返回 null，保留有效维度和已采集 Pod 合计；allocatable 任一维度无效则两个 overcommit 汇总为 null、advice 为空、complete=false |
| K-C2 | Failed/OOMKilled 被 phase 摘要遮盖，漏取日志 | 保持旧 issue 摘要；日志触发改看所选容器的当前 error findings；不因历史 OOM 采日志；没有重启先读 current，有重启先读 previous |
| K-C3 | 20/50 行明细静默截断 | 增加明细 returned/truncated 字段和 warnings；不改变扫描预算与已知资源汇总；只有实际省略条目才判明细截断 |

### Host 的时间与清理语义

总截止时间优先于排队预算；例如总预算已耗尽时应返回 TIMEOUT。若排队预算先耗尽且总预算尚在，则返回 BUSY。事件循环延迟后即使 future 已完成，也不得发布过期的完整成功结果。防止调用继续启动新的过期本机工作。

TCP 关闭等待仍有界，最多使用已有的 1 秒上限与工具剩余预算；预算不足时停止等待，不能再花完整探测 timeout。已完成的连接事实保留。清理失败 warnings 使用 `TCP_CLEANUP_FAILED`，清理超时使用 `TCP_CLEANUP_TIMEOUT`。不吞掉 CancelledError，不因取消而返回正常成功结果。

优先级：取消传播最高，其次总工具 deadline。上述保留 ok=true/open=true 的清理结果适用于工具发布时总预算仍有效的情况；若发布时总预算已过期，由 H-C1 返回 TIMEOUT，而不是用清理成功或已握手事实绕过时间约束。

### K8s 容量与部分证据

既有有效数值不变；未知 capacity/allocatable 的 `cpu_m`、`memory_bytes` 及相应 human 字段为 null，不伪造 0。该变化只影响原先异常或无法可靠解释的输入，必须在兼容说明中明确。

requests/limits 已知合计和 pod_count 仍保留；无效 allocatable 对应 ratio 为 null。capacity 仅供展示，capacity 缺失本身不推翻基于有效 allocatable 与完整 Pod 扫描所得的超配判断，但整体取证仍 incomplete。指标缺失也不得抹掉已知 requests 超配结论。unsupported Pod resource model 继续返回 unknown，不在本轮补全新模型。

### Pod 日志范围

依然只采一个所选问题容器，不扩大 RBAC。当前 error finding 的 code 在 `CrashLoopBackOff/ImagePullBackOff/ErrImagePull/OOMKilled` 中时才触发原有日志范围。Failed/OOMKilled 不再受 `issue=phase=Failed` 的字符串影响。

`read_log_excerpt(..., prefer_previous: bool = True)` 新增内部关键字参数，旧调用默认行为保持。prefer_previous=false 时按 current→previous 尝试；true 时按 previous→current。保留已有错误分类、禁止重试权限/认证/超时/繁忙、16384 bytes/4000 chars/脱敏和部分证据语义。

### 明细字段

- get_node_detail 新增 `pods_sample_returned`、`pods_sample_truncated`，样本上限保持 20。
- get_node_resource_usage 新增 `pod_breakdown_returned`、`pod_breakdown_truncated`，明细上限保持 50。
- returned 等于实际数组长度；truncated 等于已扫描活跃 Pod 数大于该上限。分页预算截断通过既有 complete/warnings 表达，不虚构已知全集大小。
- 存在明细截断时 complete=false，并提示缩小范围或用 list_node_pods 分页；资源审计在计算完已知汇总和 overcommit 后再标记明细截断，不能把完整扫描已确认的超配改成 unknown。

## 交付分层与停止条件

1. **代码收尾，可独立完成**：H-C1/H-C2/K-C1/K-C2/K-C3 回归通过；契约、构建、独立安装和当前 head 的 CI 均通过；独立审查无未处理 P0/P1。新发现的 P2 若影响本轮行为，修复或在交付表逐项说明，不能只写“全部关闭”。
2. **实机验收，条件性执行**：用户明确提供隔离 SSH/集群目标和只读权限后执行；记录平台、服务端/metrics-server/SDK 版本、权限与证据。无基础设施时标“未验证”，不阻塞本地代码交付，也不宣称实机完成。
3. **后续事项，明确延期**：多人认证与 target_id/凭据隔离；SSH 复用/OS 或跨工具缓存；Pod-level/resize 完整支持；探测全局并发池；强隔离进程终止；生产负载/长稳；依赖漏洞扫描。漏洞扫描属于上线前独立验收，未运行不能写“依赖安全通过”。

## 文档与历史状态

新增本轮 `docs/validation/2026-11-01-closure.md`，用 mock、loopback、real 三列记录；实施前不得预填通过。它是本轮状态入口，链接原报告和旧关闭记录。

旧报告和旧计划保留历史正文，只加带日期的当前状态链接。旧关闭记录末尾“PR 尚未合并”等描述应标为当时状态；本地 Git 已有合并提交，可以记录本地已知事实，远程当前 CI 必须重新获取，不能推断。

## 计划、评审与实施边界

主计划协调两个独立子计划；先 Host，后 K8s，再整体交付。顺序实施避免同时改动兼容契约，末尾做一次独立审查。默认建议在当前会话实施，执行方法由用户决定。

设计和计划经用户审阅后已获准实施；执行状态以本轮验证记录为准。推送、PR、合并和发布仍各自遵守当时授权，不把实施授权当成发布授权。实施复用当前 checkout，分支为 codex/astromcp-closure。

2026-11-01：已依 Superpowers brainstorming 的 builder check 要求，由 fresh-context reviewer 只读检查设计与相关源码，反馈 `nothing important left`。这只说明设计无需猜测关键需求，不代表代码实现或运行验收已完成。
