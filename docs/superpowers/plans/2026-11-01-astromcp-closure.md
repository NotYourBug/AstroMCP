# AstroMCP 项目收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复本轮四项缺陷及明细截断提示，交付可验证、范围明确的两个个人本地 MCP 包。

**Architecture:** 保留 uv workspace 和两个独立包，按 Host → K8s → 整体验收顺序推进。两个子计划分别形成可测试的交付，不进行新一轮架构改造。

**Tech Stack:** Python >=3.10、现有锁定 FastMCP/Kubernetes/Paramiko/psutil/Pydantic、uv、pytest、Ruff、setuptools、GitHub CI。

**Spec:** [收尾设计提案](../specs/2026-11-01-astromcp-closure-design.md)。状态：待用户审阅；所有执行复选框尚未完成。本次计划编制不等于实现、提交或发布。

## Global Constraints

- 遵守根和两个成员 AGENTS.md，以及设计中全部契约/安全/生命周期约束。
- 不扩大为多人服务，不增加变更工具、任意 shell、凭据文件或第三个共享包。
- 当前 129 passed 和六种独立安装验收仅为基线，不预填修复后通过。
- 默认复用合适 checkout；如需新分支用 codex/astromcp-closure。执行开始核对工作区，保留用户改动。
- 实机条件不具备时记录未验证；不得把 mock/loopback 写成真实基础设施证据。
- 小步提交需按执行授权；推送/PR/合并/版本发布不包含在本次计划请求中。

## Review Focus

1. 有效输入的 Schema 和主要数值回退：C2 的 contracts 和安装验收。
2. 过期、取消后 worker lease 和错误分类：Host H1/H2。
3. 未知容量、指标缺失、部分扫描、明细截断的相互作用：K8s K1/K3。
4. Failed/OOM、恢复历史及日志拒绝的组合：K8s K2。
5. 历史报告“已关闭”不能掩盖本轮未修或未验证项：C1/C3 的状态矩阵。

## 执行顺序与范围分层

| 顺序 | 任务 | 出口 |
| --- | --- | --- |
| 1 | C1：固定追踪与证据入口 | 所有发现有归属，未提前标完成 |
| 2 | [Host 子计划](2026-11-01-astromcp-host-closure.md)：H1、H2 | 两项缺陷与取消回归通过 |
| 3 | [K8s 子计划](2026-11-01-astromcp-k8s-closure.md)：K1、K2、K3 | 空容量、日志、明细语义通过 |
| 4 | C2：独立审查、整体本地验收 | 当前修复提交的测试、打包和协议证据 |
| 5 | C3：文档、CI、交付清单 | 代码完成/实机未验证/延期三类明确 |
| 条件性 | C4：隔离基础设施验收 | 单独标实机完成，不冒充默认门槛 |

本轮必做为 C1–C3 和 H1/H2/K1/K2/K3；C4 依赖明确的测试基础设施授权。没有基础设施也能达到“本地代码收尾完成”，不能达到“生产验证完成”。

### Task C1：建立本轮追踪入口

**Files:** 新增 `docs/validation/2026-11-01-closure.md`；只在 `MCP代码审查与优化建议.md`、`docs/validation/review-closure.md`、旧 master plan 的顶部加当前状态链接或历史说明。

**Interfaces:** 本轮验证文档含 `ID/问题/实施任务/回归用例/提交/验证类别/状态/剩余边界` 表；状态只用“待实施/已修复待验收/本地验收通过/实机验收通过/延期/未验证”。这是文档约定，不新增运行时模块。

- [ ] 核对当前 HEAD、工作区及两包指令；若基线变化，先判断五个修复目标是否仍存在，避免覆盖他人修复。沿用本轮可复现步骤而非只引用关闭记录。
- [ ] 建立 H-C1/H-C2/K-C1/K-C2/K-C3 五行，关联两个子计划；本会话已有检查放“基线”，新回归一律标待实施。
- [ ] 将历史 R1–R9、K1–K9 关联到旧关闭记录和新遗留项，特别明确 K4 支持边界、K9/B9 明细截断、R4/K2 线程限制；不重写历史发现正文。
- [ ] 增加延期表：多人认证/target_id、连接复用/缓存、新资源模型、探测全局池、强杀线程替代架构、生产负载长稳、漏洞扫描；记录重新启动工作的触发条件：用途改变、明确版本需求、测量显示瓶颈或上线要求。
- [ ] 用 Git diff 检查只添加事实状态和链接；按提交授权执行 `docs: establish AstroMCP closure tracking`。

### Task C2：独立审查与整体本地验收

**Files:** 两包修改文件、`tests/contracts/` 现有契约、`scripts/verify_packages.py` 等既有检查器；更新本轮验证记录。不为已有命令新增重复包装脚本。

**Interfaces:** 消费两个子计划的结果和现有检查入口；输出真实日志摘要、执行环境、提交 SHA、失败与修复记录。

- [ ] 两个子计划完成后，邀请一个 fresh-context reviewer 只读审查整个本轮差异，重点看 Review Focus。不能只审每个文件，必须检查 H1/H2、K1/K3 的交互。处理全部 P0/P1；剩余 P2 逐项修复或说明延期理由与影响。
- [ ] 修复 reviewer 指出的实际行为问题，新增有意义的失败回归，重跑受影响包；不做无关重构。评审新发现影响本轮定义的正确性时不得用“P2”直接跳过。
- [ ] 在仓库根按顺序执行：

```sh
uv sync --locked --all-packages --all-extras --dev
uv run --frozen --all-packages --all-extras ruff check .
uv run --frozen --all-packages --all-extras ruff format --check .
uv run --frozen --all-packages --all-extras python -m pytest -q
uv build --package astromcp-host
uv build --package astromcp-k8s
uv run --frozen --all-packages --all-extras python scripts/verify_packages.py
uv run --frozen --all-packages --all-extras python scripts/verify_inventory.py
uv run --frozen --all-packages --all-extras python scripts/verify_public_files.py --tracked
git diff --check
```

- [ ] 预期：全测试通过（数量由实际新增参数化案例决定，不预设为 129）；Ruff 两项通过；13 tools/1 resource/2 prompts 的名称与 required/default 不回退；双 wheel/sdist、两个临时环境的 stdio/HTTP/SSE 六种测试全部通过，且各包不能导入另一包。
- [ ] 环境限制导致无法执行的检查写明“未执行/受阻”，解决可解决的问题；不能将无凭据、沙箱 socketpair 失败或 mock 通过当作产品验收通过。uv cache/本机 Python 权限问题属于环境处理，不修改项目依赖规避。
- [ ] 记录 Python/OS/依赖锁/提交 SHA 与 mock、loopback 两类证据。检查构建产物不进入已跟踪文件；按提交授权记录验收提交，不重复运行无关检查。

### Task C3：交付状态与文档闭环

**Files:** 两包的 `README.md/configuration.md/compatibility.md`、`CONTRIBUTING.md`（仅确有命令变化时）、本轮验证记录及历史入口。根 README 不扩写功能说明。

- [ ] 检查 H1/H2/K1/K2/K3 的实际字段与文档一致，特别是 nullable 容量、已知超配与 incomplete 并存、TCP 清理 warning、日志次序、明细字段。只补实际变化，不重复复制默认配置表。
- [ ] 修正旧关闭记录“PR 尚未合并”等状态误读：保留当时文字，增加本轮已核对的本地 Git 事实。远程 PR/CI 只有读取当前结果后才能更新；不要拿历史链接当成功证据。
- [ ] 如已有授权的远程工作分支，获取并等待该修复 head 的 Ubuntu/Windows × Python 3.10/3.12 CI；失败须定位产品/环境/测试原因。没有推送授权则准备好具体提交与验证摘要，将远程 CI 标“待推送后验证”，本地完成与远程完成分开。
- [ ] 进行依赖风险记录：未扫描就保留“未验证”；若执行依赖漏洞扫描，优先使用现有可用工具或临时工具环境，记录工具/数据库日期、锁定版本与适用性，不顺手升级所有依赖或引入持久开发依赖。
- [ ] 最终交付清单包括：本轮五项状态、原报告映射、全部本地检查、当前 head CI 状态、实机边界、延期与触发条件、独立 reviewer 结论、变更文件清单。全部指向可审查事实，不写笼统“生产就绪”。
- [ ] 显式检查新增文件与公开路径，按授权提交文档；只有用户另行授权时才推送、建/更新 PR、合并或发布。到此可以宣告本轮本地代码收尾完成；远程 CI 未完成时必须同时说明。

### Task C4：条件性的真实基础设施验收

**Files:** 本轮验证记录，必要时在 `docs/validation/` 新增按实际日期命名的实机记录；不存凭据、真实 kubeconfig 或敏感原始输出。

- [ ] 先确认用户明确提供的隔离目标、只读身份、允许范围及系统/集群版本；缺失时保持“未验证”，继续完成不依赖它的 C1–C3。
- [ ] SSH：在明确的平台上验证 known_hosts 接受/未知/变更拒绝、CPU/内存/磁盘/TCP 真实输出、路径含空格、权限不足、超时/取消和连接回收。只有实际测过的平台才能标通过。
- [ ] K8s：记录服务端、metrics-server、SDK 版本；只读验证正常和空容量、缺 metrics、Events/log 403、当前 OOM/CrashLoop 的可控测试 fixture、分页与明细截断。测试工作负载由授权人员预先配置，MCP 不创建或变更集群对象。
- [ ] 有性能验收需求时先约定请求量、时长、错误率、P95、内存/线程/连接上限，再运行；不临时发明“生产容量”。无量化目标的短跑只记观察结果。
- [ ] 对每个真实场景记录通过/失败/未验证及脱敏证据；发生产品缺陷则开独立修复项和回归，不将 C4 无限扩大为本轮新功能工程。

## 自检与执行交接

计划编制自检：五个修复目标都有唯一实施任务；Host 与 K8s 没有共享新接口；新结果字段、日志内部参数及 null 语义已固定；评审关注点都指定了回归用例；未写产品函数体或预填实现完成。

推荐在当前会话顺序实施两个子计划，整体末尾独立审查；也可由用户选择逐任务 subagent 实施和复核。先审阅设计和计划，再按选定方式执行。本次仅交付文档草案。
