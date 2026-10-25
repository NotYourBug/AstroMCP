# AstroMCP development

This uv workspace contains two independent src-layout distributions: `astromcp-host` and `astromcp-k8s`. Keep their dependencies, settings and client lifetimes separate. Root contract tests protect all 13 tool names, required parameters and default values in the default configuration, plus the K8s resource and prompts.

Use `uv sync --locked --all-packages --all-extras --dev`. Run `uv run --frozen --all-packages --all-extras ruff check .`, `ruff format --check .`, and `python -m pytest -q` through the same uv prefix. Build both packages with `uv build --package astromcp-host` / `astromcp-k8s`; `scripts/verify_packages.py` checks independent wheel installations.

Keep SDK/SSH/process work behind injectable instance adapters. Pure resource algorithms and parsers must not import MCP registration. Preserve bounded waits, output budgets, partial evidence and worker leases after cancellation. Do not add mutation tools, arbitrary shell execution, credentials or local generated files. Public paths are checked by `scripts/verify_public_files.py`.

The root README is a brief personal-repository introduction and MCP catalog. Maintain features, setup, configuration and result semantics in each MCP directory's `README.md`, `configuration.md` and `compatibility.md`. Shared development commands live in `CONTRIBUTING.md`; `docs/configuration.md` and `docs/compatibility.md` are link indexes retained for compatibility. Keep validation evidence in `docs/validation/`, distinguishing mock, loopback and real infrastructure. Member instructions add subsystem constraints. Historical plans describe migration steps; the current src packages are the runtime implementation.
