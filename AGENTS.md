# AstroMCP development

This uv workspace contains two independent src-layout distributions: `astromcp-host` and `astromcp-k8s`. Keep their dependencies, settings and client lifetimes separate. Root contract tests protect all 13 tool names, required parameters and default values in the default configuration, plus the K8s resource and prompts.

Use `uv sync --locked --all-packages --all-extras --dev`. Run `uv run --frozen --all-packages --all-extras ruff check .`, `ruff format --check .`, and `python -m pytest -q` through the same uv prefix. Build both packages with `uv build --package astromcp-host` / `astromcp-k8s`; `scripts/verify_packages.py` checks independent wheel installations.

Keep SDK/SSH/process work behind injectable instance adapters. Pure resource algorithms and parsers must not import MCP registration. Preserve bounded waits, output budgets, partial evidence and worker leases after cancellation. Do not add mutation tools, arbitrary shell execution, credentials or local generated files. Public paths are checked by `scripts/verify_public_files.py`.

Documentation is maintained in the root README, `docs/configuration.md`, `docs/compatibility.md`, and `docs/validation/`. Distinguish mock, loopback and real infrastructure evidence. Member instructions add subsystem constraints. Historical plans describe migration steps; the current src packages are the runtime implementation.
