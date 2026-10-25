# AstroMCP 验证记录 · 2026-10-25

本记录区分实现回归、本机协议和真实基础设施。原始基线 `50f4b40`，M3 验收提交 `3bc04ff`，执行分支 `feat/astromcp-foundation`；最终关闭与审查结果见 [review-closure](review-closure.md)。

## 本地环境与命令

Windows，Python 3.11.9；锁定 FastMCP 3.4.11、Kubernetes client 37.0.1、Paramiko 5.1.0、psutil 7.2.2、Pydantic 2.14.1。版本代表本次实际环境，完整跨 Python 依赖标记见 `uv.lock`。

使用根 README 中的 locked sync、Ruff、全测试、双包构建及 `scripts/verify_packages.py` 命令。M3 全套 **100 passed**；独立 wheel 环境两包各通过 stdio/HTTP/SSE 握手、列工具与安全调用，且不能导入另一包。M4 全套 **107 passed**，Ruff check / format、双 wheel/sdist 构建及 13 工具清单验证通过；M5 最终结果在关闭记录更新。

Windows 受限沙箱内的 asyncio socketpair accept 阻塞已通过堆栈定位。异步与协议测试在允许本机 socket/进程的普通权限环境执行，没有降低测试断言或接入真实服务。曾发现虚拟环境 launcher 导致日志句柄回收失败，已修正为 Windows 测试进程树回收并重跑通过。

## 证据矩阵

| 范围 | 证据 | 状态与边界 |
| --- | --- | --- |
| 工具契约 | 内存 FastMCP Client，真实装饰器、13 tools/1 resource/2 prompts、required/default | 通过；默认配置，允许可选参数增量 |
| 两包独立安装 | 临时 venv + 锁定依赖 + pip --no-deps wheel，cwd 离开仓库 | 两包各 3 种传输通过；不依赖对方包 |
| 本机协议 | stdio 子进程、HTTP/SSE loopback 临时端口、finally 清理 | 通过；Client/server 同锁版本 |
| 本机 psutil | Windows 实际内存采集、CPU/磁盘/连接枚举模拟与响应性测试 | 内存实际通过；其余以单测边界为准，不声明实机全覆盖 |
| SSH 执行与取消 | fake channels/clients：双流、deadline、输出限额、并发、取消、host key | 模拟通过；未连接真实 SSH 主机 |
| Linux/macOS/Windows 采集 | 原始命令输出 fixture/JSON、恶意路径与退出码 | 解析通过；远程 OS、shell、工具版本未实测 |
| K8s gateway | fake SDK，timeouts/retries、分页、metrics 调用次数、403 降级 | 模拟通过；无真实 API server |
| K8s 资源与诊断 | Decimal/sidecar/init/overhead/limit/resize/Pod-level、Ready/Pending/OOM | fixture 通过；不支持模型明确 unknown |
| K8s Events/log | UID、时间排序、API 字节限额、截断、常见脱敏 | 模拟通过；不能保证识别任意业务敏感值 |
| K8s 凭据与健康调用 | 不存在的临时 kubeconfig，协议中返回 CONFIG_ERROR | 通过；未调用默认用户集群 |
| Ubuntu/Windows × Python 3.10/3.12 | `.github/workflows/ci.yml` | 配置完成，远程未运行；本机仅 3.11 |
| 真集群与平台兼容矩阵 | 尚无测试基础设施/凭据 | 未验证，无 skip 冒充通过 |
| 负载、长稳、漏洞扫描 | 本轮未执行 | 预算和模拟并发不等于生产容量结论 |

## 后续实机验收

在隔离测试主机核对 known_hosts 拒绝/接受、各平台命令和权限；在隔离集群以示例 RBAC 核对 Node/Pod/metrics/events/log 的允许与拒绝路径，记录 Kubernetes 服务端、metrics-server、SDK 和平台版本。再测试有量化目标的负载、取消、长稳和内存增长。团队远程部署须先单独完成认证授权设计。

没有自动发布、远程合并或生产变更；CI 是否通过以仓库实际运行结果为准。
