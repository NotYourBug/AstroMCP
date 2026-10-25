from __future__ import annotations

import argparse, os, ipaddress
from collections.abc import Sequence
from .config import load_settings
from .server import create_server

def main(argv: Sequence[str] | None = None) -> None:
    settings = load_settings(os.environ)
    mcp = create_server(settings)
    parser = argparse.ArgumentParser(description="AstroMCP host inspection (trusted local operator)")
    parser.add_argument("--transport", choices=("stdio", "http", "sse"), default=settings.transport)
    parser.add_argument("--host", default=settings.bind_host)
    parser.add_argument("--port", type=int, default=settings.bind_port)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    if args.transport != "stdio":
        if args.host != "localhost" and not ipaddress.ip_address(args.host).is_loopback:
            raise ValueError("Only loopback HTTP/SSE is supported; remote authentication is not configured")
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
