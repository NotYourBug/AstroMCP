"""Build and smoke-test each wheel in a separate environment with locked dependencies."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Use an already populated uv cache")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    network = ["--offline"] if args.offline else []
    with tempfile.TemporaryDirectory(prefix="astromcp-packages-") as directory:
        temporary = Path(directory)
        for kind in ("host", "k8s"):
            folder = temporary / kind
            interpreter = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            requirements = temporary / (kind + "-requirements.txt")
            commands = [
                ["uv", "build", "--package", "astromcp-" + kind, *network],
                [
                    "uv",
                    "export",
                    "--frozen",
                    "--package",
                    "astromcp-" + kind,
                    "--all-extras",
                    "--no-dev",
                    "--no-emit-workspace",
                    "--no-hashes",
                    "--output-file",
                    str(requirements),
                ],
                ["uv", "venv", str(folder), "--python", sys.executable],
                ["uv", "pip", "sync", *network, "--python", str(interpreter), str(requirements)],
                [
                    "uv",
                    "pip",
                    "install",
                    *network,
                    "--python",
                    str(interpreter),
                    "--no-deps",
                    str(root / "dist" / ("astromcp_" + kind + "-0.1.0-py3-none-any.whl")),
                ],
            ]
            for command in commands:
                subprocess.run(command, cwd=root, check=True, timeout=180)
            for transport in ("stdio", "http", "sse"):
                subprocess.run(
                    [
                        str(interpreter),
                        str(root / "scripts/smoke_installed.py"),
                        "--kind",
                        kind,
                        "--transport",
                        transport,
                        "--isolated",
                    ],
                    cwd=temporary,
                    check=True,
                    timeout=35,
                )
            print(kind + " independent installation and all transports: PASS", flush=True)


if __name__ == "__main__":
    main()
