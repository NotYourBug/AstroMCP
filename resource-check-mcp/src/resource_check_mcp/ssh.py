from __future__ import annotations

import asyncio, threading, time
from concurrent.futures import ThreadPoolExecutor
import paramiko
from .config import HostSettings, load_settings
from .models import SSHConnection, CommandResult, HostOperationError

def create_ssh_client(settings: HostSettings):
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if settings.ssh_known_hosts:
        client.load_host_keys(settings.ssh_known_hosts)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return client


class SSHExecutor:
    """Bounded blocking workers; a cancelled waiter never frees a running worker's lease."""

    def __init__(self, settings: HostSettings, *, client_factory=None, clock=time.monotonic):
        self.settings, self.clock = settings, clock
        self.client_factory = client_factory or (lambda: create_ssh_client(settings))
        self._pool = ThreadPoolExecutor(max_workers=settings.ssh_max_in_flight, thread_name_prefix="astromcp-ssh")
        self._sem = threading.BoundedSemaphore(settings.ssh_max_in_flight)
        self._lock = threading.Lock()
        self._targets = {}
        self._active = {}
        self._closed = False

    async def execute(self, connection: SSHConnection, command: str, *, deadline: float) -> CommandResult:
        if not connection.username:
            return CommandResult(error_code="INVALID_INPUT", complete=False)
        key = (connection.host, connection.port, connection.username)
        queue_end = min(deadline, self.clock() + self.settings.queue_timeout_seconds)
        while not self._closed:
            if self.clock() >= queue_end:
                return CommandResult(error_code="BUSY", complete=False)
            if self._sem.acquire(blocking=False):
                with self._lock:
                    entry = self._targets.setdefault(key, [threading.BoundedSemaphore(self.settings.ssh_max_per_target), 0])
                    if entry[0].acquire(blocking=False):
                        entry[1] += 1
                        break
                    if entry[1] == 0:
                        self._targets.pop(key, None)
                self._sem.release()
            await asyncio.sleep(.005)
        else:
            return CommandResult(error_code="BUSY", complete=False)
        stop, holder = threading.Event(), []
        future = self._pool.submit(self._run, connection, command, deadline, stop, holder)
        with self._lock:
            self._active[future] = (stop, holder)

        def finished(done):
            with self._lock:
                self._active.pop(done, None)
                entry[1] -= 1
                if entry[1] == 0:
                    self._targets.pop(key, None)
            entry[0].release()
            self._sem.release()

        future.add_done_callback(finished)
        wrapped = asyncio.wrap_future(future)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), max(deadline-self.clock(), .001))
        except (TimeoutError, asyncio.TimeoutError):
            self._abort(stop, holder)
            return CommandResult(error_code="TIMEOUT", complete=False)
        except asyncio.CancelledError:
            self._abort(stop, holder)
            raise

    @staticmethod
    def _abort(stop, holder):
        stop.set()
        for client in holder:
            client.close()

    def _run(self, connection, command, deadline, stop, holder):
        client = channel = None
        out, err = bytearray(), bytearray()

        def remaining(end):
            value = end-self.clock()
            if stop.is_set() or value <= 0:
                raise TimeoutError
            return value

        try:
            client = self.client_factory()
            holder.append(client)
            timeout = min(remaining(deadline), self.settings.ssh_connect_timeout_seconds)
            client.connect(hostname=connection.host, port=connection.port, username=connection.username,
                           password=connection.password, key_filename=connection.key_path,
                           allow_agent=True, look_for_keys=True, timeout=timeout,
                           banner_timeout=timeout, auth_timeout=timeout)
            end = min(deadline, self.clock()+self.settings.ssh_command_timeout_seconds)
            stdin, stdout, _ = client.exec_command(command, timeout=remaining(end))
            stdin.close()
            channel = stdout.channel
            while True:
                remaining(end)
                for ready, recv, buffer in ((channel.recv_ready, channel.recv, out),
                                             (channel.recv_stderr_ready, channel.recv_stderr, err)):
                    if ready():
                        buffer.extend(recv(32768))
                        if len(out)+len(err) > self.settings.ssh_output_limit_bytes:
                            return CommandResult(error_code="OUTPUT_LIMIT", complete=False)
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    code = channel.recv_exit_status()
                    return CommandResult(code, out.decode("utf-8", errors="replace").strip(),
                                         err.decode("utf-8", errors="replace").strip())
                time.sleep(.005)
        except paramiko.BadHostKeyException:
            return CommandResult(error_code="HOST_KEY_CHANGED", complete=False)
        except paramiko.AuthenticationException:
            return CommandResult(error_code="AUTH_FAILED", complete=False)
        except (TimeoutError, asyncio.TimeoutError):
            return CommandResult(error_code="TIMEOUT", complete=False)
        except paramiko.SSHException as exc:
            code = "HOST_KEY_UNKNOWN" if "known_hosts" in str(exc) else "UNREACHABLE"
            return CommandResult(error_code=code, complete=False)
        except OSError:
            return CommandResult(error_code="UNREACHABLE", complete=False)
        finally:
            if channel is not None:
                channel.close()
            if client is not None:
                client.close()

    async def aclose(self):
        self._closed = True
        with self._lock:
            active = list(self._active.items())
        for _, (stop, holder) in active:
            self._abort(stop, holder)
        if active:
            await asyncio.wait([asyncio.wrap_future(f) for f, _ in active], timeout=1)
        self._pool.shutdown(wait=False, cancel_futures=True)


async def detect_remote_os(connection, executor, deadline):
    result = await executor.execute(connection, "uname", deadline=deadline)
    if result.error_code:
        raise HostOperationError(result.error_code, "SSH operating-system probe failed")
    if result.exit_code == 0:
        system = result.stdout.strip()
        if system == "Darwin": return "mac"
        if system == "Linux": return "linux"
        raise HostOperationError("COMMAND_FAILED", "Unsupported remote operating system")
    result = await executor.execute(connection, "cmd.exe /c ver", deadline=deadline)
    if result.error_code:
        raise HostOperationError(result.error_code, "SSH operating-system probe failed")
    if result.exit_code == 0: return "windows"
    raise HostOperationError("COMMAND_FAILED", "Cannot identify remote operating system")
