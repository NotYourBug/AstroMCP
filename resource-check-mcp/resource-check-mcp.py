"""Compatibility entrypoint; install with uv sync --all-packages first."""
try:
    from resource_check_mcp.__main__ import main
except ModuleNotFoundError as exc:
    raise SystemExit("Install AstroMCP first: uv sync --all-packages --dev") from exc

if __name__ == "__main__":
    main()
