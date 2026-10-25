# AstroMCP 工程基线与交付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans（推荐本会话顺序实施）或 superpowers:subagent-driven-development 按任务实施。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在一个 GitHub 仓库内交付可独立安装、启动、测试的两个 MCP 包，完整落实既有审查的修复范围。

**Architecture:** 保留两个原目录，使用 uv workspace 管理依赖与共同检查。先保护原实现并修复行为，再各自提取 src 包；总计划负责基线、兼容契约、CI 与交付，两个子计划负责各自业务。

**Tech Stack:** Python >=3.10、FastMCP 3.x、Kubernetes Python Client、Paramiko、psutil、Pydantic 2、uv、setuptools、pytest/pytest-asyncio/pytest-timeout、Ruff。

**Spec:** `docs/superpowers/specs/2026-10-25-astromcp-design.md`，待用户批准。计划是完整可执行草案，不表示设计已经批准。

## Global Constraints

- 用户已选择同仓库两个独立 MCP 包；13 tools、1 resource、2 prompts 的名称保留。
- Python 最低版本保持 3.10；本机默认用现有 3.11；CI 首先测试 3.10 和 3.12。
- 新 CLI 默认 stdio；旧 K8s 脚本默认 SSE、127.0.0.1:8080；HTTP/SSE 非 loopback 启动拒绝。
- 所有数值预算、配置优先级、错误码、输出字段与兼容变化以设计第 4–7 节为准。
- 保持只读能力；无真实生产凭据、无集群变更；不引入第三个共享业务包。
- 本地提交按任务；首次公开推送前展示内容清单；不 force push、不自动合并、不发布 PyPI。

## Review Focus

1. 工作区的 .codegraph/.claude/凭据不能进入公开提交——M1、M5 的清单测试负责。
2. 真实 FastMCP 装饰器后的注册、Schema 与 stdio 输出不能被纯函数单测代替——M2、M3 负责。
3. wheel 离开工作区后仍能启动，且不需安装另一包——M3 的干净环境测试负责。
4. 底层同步工作未结束时，取消不能释放并发名额并制造无界后台线程——H2、K1 负责。
5. 未做真实 SSH/集群测试不能写成“生产验证通过”——M4、M5 的证据与报告负责。

## 任务依赖与交付顺序

```text
M1 原始基线 → M2 可复现依赖与契约基线
  → H1 → H2 → H3 → H4 → H5（主机包可独立交付）
  → K1 → K2 → K3 → K4 → K5 → K6（K8s 包可独立交付）
  → M3 包与协议总体验收 → M4 CI/文档 → M5 独立审查/交付
```

子计划：[主机 MCP](2026-10-25-astromcp-host.md)、[K8s MCP](2026-10-25-astromcp-k8s.md)。共 16 个可验收任务，不以“一次重写两个程序”交付。

## 最终文件责任

- 根 pyproject.toml/uv.lock：workspace、开发依赖、pytest marker 与 Ruff 规则。
- 两个目录下的 pyproject.toml：运行依赖、独立构建配置、各自命令入口。
- tests/contracts：清单、参数兼容、传输与构建验收，不存放业务单测。
- scripts/verify_inventory.py：读取并对比 MCP 元数据；scripts/verify_public_files.py：检查允许公开的文件清单。
- .github/workflows/ci.yml：Windows/Linux × Python 3.10/3.12；不含部署密钥。
- docs/configuration.md、compatibility.md、validation/：配置、变更和验证证据。

### Task M1: 建立可回滚原始基线

**Files:** Create `.gitignore`、`tests/contracts/source_baseline.json`、`scripts/verify_public_files.py`、`docs/validation/2026-10-25-baseline.md`；记录已有两份源码和审查文档。

**Interfaces:** Produces `verify_public_files(paths: list[str]) -> list[str]`（返回违规路径）；baseline JSON 记录源路径、SHA256、工具名、Resource/Prompt 名与原参数默认值，供 M2/M3 使用。

- [ ] Step 1：在测试中传入 `.codegraph/codegraph.db`、`.env`、`id_rsa`、`.claude/settings.local.json`、`__pycache__/x.pyc`，断言清单拒绝；`src/a.py`、`docs/design.md` 可通过。记录源码 SHA256：K8s `eae21beee71a45c96d151b35dfb433c9d293c1d9d4bac8dff0eeeb7f6a5ff85e`，host `020e25264cc2262e723ed1baa746464273d3c62d57c953841c955e490a9cc472`；执行时如已变化，先更新证据而非覆盖用户改动。
- [ ] Step 2：运行 `python scripts/verify_public_files.py --self-test`，实现前应失败/文件不存在；同时重新只读查询远程 main/提交状态。若远程已有内容，先对齐历史，不重建覆盖。
- [ ] Step 3：实现标准库检查器和忽略规则。完成源文件内容与示例敏感值检查，保留真实语义、排除本地生成物。确认不在其他 Git 仓库后初始化本地 main，添加用户给定 origin；不克隆覆盖工作区。缺 Git 作者身份时只记录待配置，不自行虚构。
- [ ] Step 4：运行上述 self-test、`git diff --cached --check` 与 `python scripts/verify_public_files.py --staged`；必须无违规路径。仅显式 stage 原源码、README/审查、计划文档与本任务文件，不使用未审阅的 `git add .`。
- [ ] Step 5：提交 `chore: record reviewed AstroMCP baseline`，记录基线 SHA；创建本地工作分支 `feat/astromcp-foundation`。本任务不向空远程推送原始未修复实现。

### Task M2: 可复现环境和真实 MCP 契约基线

**Files:** Create 根与两个成员的 `pyproject.toml`、`uv.lock`、`tests/contracts/conftest.py`、`tests/contracts/test_registration.py`、`tests/contracts/test_input_compatibility.py`、`tests/contracts/baseline_inventory.json`、`scripts/verify_inventory.py`。

**Interfaces:** `load_server(kind: Literal['host','k8s']) -> FastMCP` 是唯一过渡加载器，先 importlib 加载原文件并注册 sys.modules，H5/K6 后改用正式 factory；`read_inventory(server: FastMCP) -> dict` 用真实内存 Client 读取协议元数据。两个 member 初始为 uv virtual project（`tool.uv.package=false`），各自声明运行依赖；H5/K6 再转换成可构建包。

- [ ] Step 1：编写契约测试，断言 host 工具集合精确为 `check_host_status/check_disk_usage/check_mem/check_cpu/check_tcp/check_service_port`，K8s 为 `list_nodes/get_node_detail/get_node_resource_usage/find_problem_nodes/list_node_pods/diagnose_pod_issues/health_check`；Resource 为 `k8s://cluster/summary`，Prompts 两个原名。断言 required/default 与 M1 原始清单一致，允许后续仅增加 optional 参数。
- [ ] Step 2：在当前环境运行 `python -m pytest tests/contracts/test_registration.py -q`，预期缺依赖或导入失败，记录这一基线，不把缺依赖当业务缺陷。
- [ ] Step 3：设置根 workspace members 为两个现有目录。host 声明 fastmcp 3.x/paramiko/psutil/pydantic；K8s 声明 fastmcp 3.x/kubernetes/pydantic，PyYAML 放入 yaml extra。使用 `uv lock`、`uv sync --all-packages --all-extras --dev` 解析已兼容的具体版本；根 dev 加 pytest-timeout（默认 15 秒、thread 模式）以防 Windows 受限环境事件循环无限等待。项目级 Ruff 先使用 E/F/I/UP，按真实必要例外配置，不直接继承用户全局规则。
- [ ] Step 4：运行 `uv run --frozen python -m pytest tests/contracts -q`；真实 FastMCP Client 的 tools/list/resources/list/prompts/list 必须通过，不能去掉装饰器替代。记录实际版本与是否受沙箱进程/loopback 限制；若限制导致失败，明确报告并解决，不伪造 PASS。基线格式输出写为 fixture。
- [ ] Step 5：提交 `test: establish reproducible MCP contract baseline`。此时 virtual project 尚无正式 CLI，直接启动缺陷由 H1 修复，wheel 验收属于 M3。

### Task M3: 两包独立安装与协议总体验收

**Files:** Create `tests/contracts/test_packaging.py`、`test_transports.py`、`test_launch_policy.py`、`scripts/smoke_installed.py`；Modify `tests/contracts/conftest.py`、`baseline_inventory.json` 及两包构建配置。

**Interfaces:** Consumes H5/K6 的 `create_server()` 与命令入口；`smoke_installed.py --kind host|k8s --transport stdio|http|sse` 通过真实 Client 列工具并调用 fake 注入场景，stdout 仅协议数据。

- [ ] Step 1：测试精确断言 `astromcp-host`、`astromcp-k8s` 入口存在；各自 wheel 独立安装在两个临时环境（工作目录为临时目录）仍可启动。记录工具 Schema 仅允许 optional 增量；验证原 host 数字字段、K8s items/cluster/issue 保留，新增 complete/warnings/request_id。测试无配置非 loopback 启动返回非零、loopback 正常、旧脚本入口与新 CLI 的默认传输符合设计。
- [ ] Step 2：运行 `uv run --frozen python -m pytest tests/contracts/test_packaging.py tests/contracts/test_transports.py tests/contracts/test_launch_policy.py -q`；未接好入口或包内导入时应失败；网络测试用本机临时端口而非固定 8080/8081，子进程最多 30 秒、finally 回收。
- [ ] Step 3：完成两包入口和 wheel 包含清单，去掉对 workspace 文件路径的依赖；stdlib 日志走 stderr。用内存 Client 验证 fake gateway/executor 调用；用子进程 stdio/loopback HTTP/SSE 验证真实握手/关闭，不连接外部集群。旧脚本在 editable/已安装环境作为薄入口，未安装时给明确安装指令，不修改 sys.path 偷渡 src。
- [ ] Step 4：运行 `uv build --package astromcp-host` 和 `uv build --package astromcp-k8s`，再运行本任务契约测试及 `uv run --frozen python -m pytest -q`；所有正常模式通过，无遗留子进程。wheel 独立环境需从锁定依赖导出安装，再 `pip install --no-deps wheel`，避免冒烟时意外升级到未验证版本。
- [ ] Step 5：提交 `test: verify independent packages and MCP transports`。

### Task M4: 配置文档、CI 与真实验证边界

**Files:** Create `.github/workflows/ci.yml`、根 `README.md`、根 `AGENTS.md`、`docs/configuration.md`、`docs/compatibility.md`、`docs/validation/2026-10-25-validation.md`、`examples/{k8s-config,host-config,mcp-clients}.json`、`examples/k8s-readonly-rbac.yaml`；Modify 两包 README、AGENTS/CLAUDE、K8s requirements.txt。

**Interfaces:** CI 命令与本地完全一致；examples 使用 TEST_ONLY 占位值，不放真实目标/密码。requirements.txt 作为从已验证锁导出的兼容安装入口，文档指定生成命令而非手工维护第二份版本清单。

- [ ] Step 1：增加文档一致性测试 `tests/contracts/test_examples.py`：配置示例能被 load_settings 读取；示例工具名全部真实存在；README 命令入口与发行包名一致；known_hosts、loopback 变化与分页语义有明确迁移说明。
- [ ] Step 2：运行 `uv run --frozen python -m pytest tests/contracts/test_examples.py -q`，缺文档或无效例子预期失败。
- [ ] Step 3：编写 CI matrix：ubuntu-latest/windows-latest × Python 3.10/3.12，各 job timeout-minutes=15；先 locked sync，再 Ruff、全测试、双 wheel 构建，上传测试和 wheel artifacts，不自动发布。更新重复漂移的指引。真实联调标记 integration，默认不要求外部服务，报告分列 mock、loopback 协议和真实主机/集群证据。
- [ ] Step 4：运行 `uv run --frozen ruff check .`、`uv run --frozen ruff format --check .`、`uv run --frozen python -m pytest -q`、双 wheel 构建。若无测试主机或集群，把平台矩阵标为“解析/模拟通过、实机未验证”，不以 skip 冒充通过。公开 CI 要 push 才能运行，尚未 push 时记录“配置完成、远程待运行”。
- [ ] Step 5：提交 `ci: document and verify AstroMCP packages`。

### Task M5: 独立审查、问题关闭与可审阅交付

**Files:** Create `docs/validation/review-closure.md`；Modify `MCP代码审查与优化建议.md`（保留历史审查，附指向关闭记录的链接，不删除旧事实）。

**Interfaces:** 关闭记录每行含审查 ID、最终文件、测试名、执行结果、限制、提交 SHA；这些与各子计划闭环。

- [ ] Step 1：用 R1–R9、K1–K9、部署、依赖、文档作为固定集合，测试或检查每项有任务、证据与状态；未实测的事项不能标已完成验证。扫描最终 `git diff --check`、待公开文件清单与例子中的敏感值。
- [ ] Step 2：运行一次全分支独立审查（执行方式由用户确认；主代理顺序实施也需结束时独立 reviewer），重点检查权限边界、取消后线程、SDK 模型字段丢失、资源算法、Schema 兼容。按真实失败修复并补相应测试，不为无关风格重复重跑所有平台。
- [ ] Step 3：完成 review-closure 与最终测试结果。核对没有未经批准的许可证/自动部署/PyPI 发布。用 `git log` 和 staged 清单展示原始基线、任务提交和当前 diff。若计划执行中扩大首版范围，先回到设计确认。
- [ ] Step 4：运行 `python scripts/verify_public_files.py --tracked`、`git status --short`，确认工作区文件与交付说明一致。待用户审阅后首次 push 工作分支并准备 draft PR；如果远程仍无 main，先用已审阅的基线建立 main，再推工作分支，保留任务历史、不 force push；若 main 已存在则按最新历史对齐。不请求他人 review、不发送外部消息。
- [ ] Step 5：本地提交 `docs: record AstroMCP review closure`；若创建 PR，使用具体标题与验证说明，并在当前 chat attach PR。远程 CI 未运行/未通过时不宣布完成生产验证，不自动 merge。

## 审查发现到任务的追踪

| 发现 | 任务 | 必须验证的结果 |
| --- | --- | --- |
| R1 启动 | H1/M3 | 旧/新入口、tools/list |
| R2 注入、R3 host key | H1 | 换行拒绝、空格单参数、陌生/变化密钥拒绝 |
| R4 SSH 挂起 | H2 | 双流消费、deadline、输出上限、取消清理 |
| R5 本地阻塞、R6 mac CPU、R7 管道假成功 | H3 | heartbeat、user+sys、采集失败非零 |
| R8 IPv6、R9 ping/输入 | H4 | IPv4/IPv6、DNS/closed/timeout、kill/wait |
| K1 metrics N²、K6 incomplete、K9 分页 | K2 | 每扫描一次、无证据可见、有界页 |
| K2 timeout、K7 配置 | K1 | socket/排队预算、非法配置拒绝 |
| K3 Pod 漏报 | K3 | init、Ready、Pending、正常 init |
| K4 资源、K5 建议 | K4 | sidecar/overhead/unbounded/不盲目驱逐 |
| K8 Events/log | K5 | UID、最新、API 字节限制、权限降级 |
| 组织性 | H5/K6 | 单向依赖、薄 server、无 legacy 副本 |
| 部署边界/工程配套 | H1/K1/M1–M5 | loopback、已锁依赖、公开清单、诚实验证 |

## 本计划自查

- [x] 两个子系统独立计划、接口和顺序明确。
- [x] 18 个审查 ID 均有任务与验收映射；同一任务中允许修复同一边界的多项问题。
- [x] 所有未知环境、远程初始状态、真实平台验证与发布都未写成已完成。
- [x] 构建检查只在包化完成后要求；M2 的 virtual project 是明确过渡状态。
- [ ] 用户批准设计与计划，并选择执行方式后，才开始 M1。
