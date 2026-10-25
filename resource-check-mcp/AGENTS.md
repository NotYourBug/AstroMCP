# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project overview

A single-file FastMCP server (`resource-check.-mcp.py`) that exposes SRE/operations monitoring tools to LLMs. Each tool supports both local inspection (via `psutil`/`socket`) and remote inspection over SSH (via `paramiko`).

## Commands

This project has no build step, tests, or dependency manifest. To run the server:

```bash
python resource-check.-mcp.py
```

Dependencies (install manually, no lock file exists):
- `fastmcp` — MCP server framework
- `paramiko` — SSH client
- `psutil` — local system metrics
- `pydantic` — tool parameter validation (shipped with FastMCP)

## Architecture

### Helper layer

- `run_remote_command(host, port, username, password, key_path, command)` — executes a command over SSH via `paramiko`, wrapped with `asyncio.to_thread` to avoid blocking. Supports password auth, explicit key paths, ssh-agent, and default key lookup (`~/.ssh/id_rsa`). 5s connect timeout, 10s command timeout.
- `detect_remote_os(...)` — probes a remote host by trying `uname` (Linux/Mac) then `cmd.exe /c ver` (Windows). Returns `("linux"|"mac"|"windows", error)`.
- `is_local(host)` — returns `True` for `127.0.0.1`, `localhost`, `0.0.0.0`, `::1`.

### Tool pattern

Every monitoring tool follows the same dual-path pattern:
1. If `is_local(host)` → use `psutil` (or raw `socket` for port checks) directly.
2. Otherwise → call `detect_remote_os()` to pick OS-specific shell commands, then `run_remote_command()` to execute them, then parse the output.

### Tools exposed

| Tool | Local impl | Remote impl |
|---|---|---|
| `check_host_status` | `ping` subprocess | N/A (always pings from local) |
| `check_disk_usage` | `psutil.disk_usage` | `df -kP` (Unix) / PowerShell `Get-Volume` (Windows) |
| `check_mem` | `psutil.virtual_memory` | `free -b` (Linux) / `sysctl hw.memsize` (Mac) / `wmic OS` (Windows) |
| `check_cpu` | `psutil.cpu_percent` | `vmstat` (Linux) / `top` (Mac) / `wmic cpu` (Windows) |
| `check_tcp` | `psutil.net_connections` | `netstat -an \| grep tcp` (Unix) / `netstat -ano \| find "TCP"` (Windows) |
| `check_service_port` | `socket.connect_ex` | N/A (always connects from local) |

### Return types

- `check_host_status` and `check_service_port` return formatted strings with emoji indicators (✅/❌).
- All other tools return `dict` with numeric fields (`total_gb`, `used_gb`, `free_gb`, `percent`) plus a host identifier.
- On error, tools return `{"error": "..."}` rather than raising exceptions.
- Disk usage adds a `status` field: ⚠️ warning when >85%.

## Notes

- The `.codegraph/` directory contains a local CodeGraph database and its `.gitignore` — not part of the application.
- SSH auth is flexible: provide `password`, `key_path`, or rely on ssh-agent / default key discovery. `username` is required for all remote operations.
- The MCP server is named `"sre-ops-server"`.
