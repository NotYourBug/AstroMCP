from __future__ import annotations

from dataclasses import dataclass, field
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

_TOOL_DEADLINE = ContextVar("host_deadline", default=None)
_REQUEST_ID = ContextVar("host_request_id", default=None)

class HostOperationError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

@dataclass(frozen=True)
class SSHConnection:
    host: str
    port: int
    username: str
    password: str | None = field(default=None, repr=False)
    key_path: str | None = None


@dataclass
class CommandResult:
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error_code: str | None = None
    complete: bool = True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ok(**kwargs) -> dict:
    """构建成功结果，自动附加时间戳"""
    result = {"ok": True, "timestamp": _now_iso(), "complete": True, "warnings": [],
              "request_id": _REQUEST_ID.get() or str(uuid4())}
    result.update(kwargs)
    return result


def _err(host: str, error: str, **extra) -> dict:
    """构建错误结果，自动附加时间戳"""
    result = {"ok": False, "host": host, "error": error, "timestamp": _now_iso(),
              "complete": False, "warnings": [], "error_code": "COMMAND_FAILED",
              "request_id": _REQUEST_ID.get() or str(uuid4())}
    result.update(extra)
    return result

