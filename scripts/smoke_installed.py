"""Real MCP smoke test: local subprocesses and loopback only."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


async def check_client(client, kind):
    async with client:
        assert len(await client.list_tools()) == (6 if kind == "host" else 7)
        if kind == "host":
            result = await client.call_tool("check_mem", {})
            assert result.data["ok"] and "total_gb" in result.data
        else:
            assert len(await client.list_resources()) == 1
            assert len(await client.list_prompts()) == 2
            prompt = await client.get_prompt("node_triage_prompt", {"node_name": "test-node"})
            assert "get_node_detail" in str(prompt)
            result = await client.call_tool("health_check", {})
            assert not result.data["ok"] and result.data["error_code"] == "CONFIG_ERROR"


async def smoke(kind: str, transport: str = "stdio"):
    module = "resource_check_mcp" if kind == "host" else "k8s_check_mcp"
    with tempfile.TemporaryDirectory(prefix="astromcp-smoke-") as directory:
        env = {k: v for k, v in os.environ.items() if not k.startswith("MCP_")}
        env.update(MCP_K8S_KUBECONFIG=str(Path(directory)/"missing-kubeconfig"),
                   MCP_K8S_IN_CLUSTER="false", PYTHONUTF8="1", FASTMCP_SHOW_CLI_BANNER="false")
        args = ["-m", module, "--transport", transport]
        log = Path(directory)/"server.log"
        if transport == "stdio":
            connection = StdioTransport(sys.executable, args=args, env=env, cwd=directory,
                                        keep_alive=False, log_file=log)
            try:
                await check_client(Client(connection, timeout=20, init_timeout=20), kind)
            except Exception as exc:
                raise RuntimeError(log.read_text(encoding="utf-8", errors="replace")[-4000:]) from exc
            return
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with log.open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen([sys.executable, *args, "--port", str(port)], cwd=directory,
                                        env=env, stdout=subprocess.DEVNULL, stderr=stderr)
            try:
                end = time.monotonic()+20
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(log.read_text(encoding="utf-8", errors="replace")[-4000:])
                    try:
                        _, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), .2)
                        writer.close()
                        await writer.wait_closed()
                        break
                    except (ConnectionError, OSError, TimeoutError, asyncio.TimeoutError):
                        if time.monotonic() >= end:
                            raise TimeoutError("Local MCP server did not start")
                        await asyncio.sleep(.1)
                suffix = "/sse" if transport == "sse" else "/mcp"
                await check_client(Client(f"http://127.0.0.1:{port}{suffix}", timeout=10, init_timeout=10), kind)
            finally:
                # Windows venv python.exe may be a launcher owning a Python child.
                # Terminating only that launcher leaves the server and log handle alive.
                if os.name == "nt" and process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                elif process.poll() is None:
                    process.terminate()
                try:
                    await asyncio.to_thread(process.wait, timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("host", "k8s"), required=True)
    parser.add_argument("--transport", choices=("stdio", "http", "sse"), default="stdio")
    parser.add_argument("--isolated", action="store_true")
    args = parser.parse_args()
    if args.isolated:
        other = "k8s_check_mcp" if args.kind == "host" else "resource_check_mcp"
        assert importlib.util.find_spec(other) is None, "Other workspace package leaked into environment"
    asyncio.run(smoke(args.kind, args.transport))
    print(f"{args.kind} {args.transport}: PASS")


if __name__ == "__main__":
    main()
