# Host package guidance

Follow the root AGENTS.md and current src package. `resource-check-mcp.py` is only a compatibility entrypoint; do not copy runtime logic into it. Keep SSH authentication/host key policy and worker leases in `ssh.py`, probes in `probes.py`, platform commands/parsers in `collectors/`, and protocol assembly in `server.py`.

Preserve six tool signatures and numeric result fields. Reject unsafe paths and never add arbitrary-command tools or AutoAddPolicy. Tests use injected executors/fake channels; real SSH tests require an explicitly configured test host and must be identified separately. Run package tests from the workspace root with `uv run --frozen --all-packages --all-extras python -m pytest resource-check-mcp/tests tests/contracts -q`.

The authoritative installation/configuration/compatibility references are the root README and docs/. Update those rather than maintaining duplicate defaults here.
