"""Check paths before committing this public repository; never read credentials."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import PurePosixPath


def verify_public_files(paths: list[str]) -> list[str]:
    blocked_dirs = {'.venv', '.codegraph', '.claude', '__pycache__', '.superpowers'}
    violations = []
    for raw in paths:
        path = PurePosixPath(raw.replace('\\', '/'))
        name = path.name.lower()
        if (
            any(part in blocked_dirs for part in path.parts)
            or (name.startswith('.env') and name != '.env.example')
            or name.startswith(('id_rsa', 'id_ed25519'))
            or path.suffix.lower() in {'.pem', '.key', '.pyc', '.pyo'}
            or name == 'settings.local.json'
        ):
            violations.append(raw)
    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--staged', action='store_true')
    parser.add_argument('--tracked', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        bad = ['.codegraph/codegraph.db', '.env', 'id_rsa', '.claude/settings.local.json', '__pycache__/x.pyc']
        assert verify_public_files(bad) == bad
        assert not verify_public_files(['src/a.py', 'docs/design.md', '.env.example'])
        print('public-file self-test: PASS')
        return
    command = ['git', 'diff', '--cached', '--name-only', '-z'] if args.staged else ['git', 'ls-files', '-z']
    paths = subprocess.check_output(command).decode('utf-8').strip('\0').split('\0')
    bad = verify_public_files(paths)
    for path in bad:
        print(f'Forbidden public file: {path}')
    if bad:
        raise SystemExit(1)
    print(f'public-file check: PASS ({len(paths)} paths)')


if __name__ == '__main__':
    main()

