# Kubernetes package guidance

Follow the root AGENTS.md. Runtime code is in `src/k8s_check_mcp`; `mcp_server.py` is the compatibility SSE entrypoint. Keep Kubernetes SDK I/O, timeout and pagination in `client.py`, rate control in `limiter.py`, pure resource/diagnostic rules in their modules, orchestration in `inspector.py`, and MCP registration in `server.py`.

The service is read-only: no cluster mutation, eviction, arbitrary command, report/notification service or PV/PVC lifecycle management. Preserve seven tools, one resource and two prompts. Unknown metrics/resources must stay visible as incomplete/unknown. Keep Pod raw-field preservation at the SDK boundary and do not expand RBAC implicitly.

Run `uv run --frozen --all-packages --all-extras python -m pytest k8s-check-mcp/tests tests/contracts -q` from root. Default tests use fake SDK or local MCP transports. Real-cluster evidence must name the tested versions and permissions separately. Maintain features/setup in this directory's README.md and detailed defaults/semantics in configuration.md and compatibility.md; shared development commands live in the root CONTRIBUTING.md. Keep the root README as a brief MCP catalog and avoid duplicating defaults here.
