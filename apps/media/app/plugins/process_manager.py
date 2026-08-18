"""Supervises provider plugin subprocesses over Unix domain sockets.

See docs/02-architecture.md "Tooling reuse, not reinvention" for why this
is hand-rolled instead of circus/supervisord/s6.
"""

import asyncio
import contextlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

HEALTH_CHECK_TIMEOUT_SECONDS = 2.0
GRACEFUL_STOP_TIMEOUT_SECONDS = 5.0
RESTART_BACKOFF_BASE_SECONDS = 1.0
RESTART_BACKOFF_MAX_SECONDS = 30.0

# Linux's sockaddr_un.sun_path is 108 bytes including the NUL terminator;
# other platforms are similar or stricter. Checked with a safety margin,
# not the exact limit -- the point is failing fast and clearly, not
# reproducing the kernel's exact bookkeeping.
MAX_SOCKET_PATH_BYTES = 100


class PluginStartError(RuntimeError):
    """Raised when a plugin process never becomes healthy after spawning."""


@dataclass
class _PluginProcess:
    plugin_id: str
    command: list[str]
    socket_path: Path
    env: dict[str, str]
    process: asyncio.subprocess.Process
    stopping: bool = False
    restart_count: int = 0
    supervise_task: asyncio.Task | None = field(default=None, repr=False)
    output_tasks: list[asyncio.Task] = field(default_factory=list, repr=False)


def _minimal_env(extra: dict[str, str] | None) -> dict[str, str]:
    """The environment a plugin subprocess gets: PATH (to find its own
    interpreter/binaries) plus whatever the caller explicitly allowlists --
    never the parent's full environ.

    This is what actually enforces "no access to ... the master encryption
    key" from docs/02-architecture.md's isolation model: `env=None` in
    `asyncio.create_subprocess_exec` means *inherit everything*, which
    would hand every plugin `DATABASE_URL` and `UMEDIA_MASTER_KEY` whether
    it needs them or not.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        # Bare PATH and no LANG → Python uses the ascii codec for paths.
        # macOS screenshot names include U+202F; PutObject then 400s.
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUTF8": "1",
    }
    if extra:
        env.update(extra)
    return env


def _health_client(socket_path: Path) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(uds=str(socket_path))
    return httpx.AsyncClient(transport=transport, timeout=HEALTH_CHECK_TIMEOUT_SECONDS)


class PluginProcessManager:
    """Spawns, health-checks, and restarts provider plugin subprocesses.

    One instance per running core process; `plugin_id` is the manifest's
    `id` (or, for a plugin fronting multiple remote types like `rclone`,
    the plugin process's own id -- not the per-remote-type manifest id,
    see docs/03-provider-system.md).
    """

    def __init__(self, socket_dir: Path) -> None:
        self._socket_dir = socket_dir
        self._socket_dir.mkdir(parents=True, exist_ok=True)
        self._plugins: dict[str, _PluginProcess] = {}

    def socket_path(self, plugin_id: str) -> Path:
        """Where this plugin's Unix socket will be (or is)."""
        return self._socket_dir / f"{plugin_id}.sock"

    def running_plugin_ids(self) -> list[str]:
        return list(self._plugins)

    def pid(self, plugin_id: str) -> int | None:
        """The OS pid of the current process backing this plugin, if any."""
        entry = self._plugins.get(plugin_id)
        return entry.process.pid if entry else None

    async def start(
        self,
        plugin_id: str,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        healthy_timeout: float = 10.0,
    ) -> None:
        """Spawn a plugin process and wait for it to report healthy.

        `command`'s argv gets the socket path appended as its final
        argument -- every plugin (real or fake) reads it from there, see
        tests/fixtures/fake_plugin.py. `env` is an *allowlist* on top of a
        bare `PATH` -- the plugin does not inherit this process's full
        environment (see `_minimal_env`); pass whatever the plugin
        actually needs (e.g. a local plugin's storage root) explicitly.
        """
        if plugin_id in self._plugins:
            raise ValueError(f"Plugin '{plugin_id}' is already running")

        socket_path = self.socket_path(plugin_id)
        path_bytes = len(str(socket_path).encode())
        if path_bytes > MAX_SOCKET_PATH_BYTES:
            # Fail immediately and clearly instead of a plugin silently
            # never binding its socket and this timing out 10s later --
            # found (and originally debugged) the hard way, see
            # server/config.py's plugin_socket_dir default.
            raise PluginStartError(
                f"Plugin '{plugin_id}' socket path is {path_bytes} bytes, "
                f"over the ~{MAX_SOCKET_PATH_BYTES}-byte Unix socket limit: "
                f"{socket_path}. Point plugin_socket_dir at something "
                f"shorter (e.g. /run/umedia/plugins).",
            )

        plugin_env = _minimal_env(env)
        socket_path.unlink(missing_ok=True)
        process = await self._spawn(command, socket_path, plugin_env)
        entry = _PluginProcess(
            plugin_id=plugin_id,
            command=command,
            socket_path=socket_path,
            env=plugin_env,
            process=process,
        )
        self._plugins[plugin_id] = entry
        self._start_output_drain(entry)
        entry.supervise_task = asyncio.create_task(self._supervise(entry))

        try:
            async with asyncio.timeout(healthy_timeout):
                await self._wait_until_healthy(socket_path)
        except TimeoutError as error:
            await self.stop(plugin_id)
            raise PluginStartError(
                f"Plugin '{plugin_id}' did not become healthy within "
                f"{healthy_timeout}s",
            ) from error

    @staticmethod
    async def _spawn(
        command: list[str],
        socket_path: Path,
        env: dict[str, str],
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *command,
            str(socket_path),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def _supervise(self, entry: _PluginProcess) -> None:
        """Restart-with-backoff loop; runs for the plugin's whole lifetime."""
        while True:
            returncode = await entry.process.wait()
            if entry.stopping:
                return
            logger.warning(
                "Plugin '%s' exited (code %s); restarting",
                entry.plugin_id,
                returncode,
            )
            delay = min(
                RESTART_BACKOFF_BASE_SECONDS * (2**entry.restart_count),
                RESTART_BACKOFF_MAX_SECONDS,
            )
            entry.restart_count += 1
            await asyncio.sleep(delay)
            if entry.stopping:
                return
            entry.socket_path.unlink(missing_ok=True)
            entry.process = await self._spawn(
                entry.command, entry.socket_path, entry.env,
            )
            self._start_output_drain(entry)

    def _start_output_drain(self, entry: _PluginProcess) -> None:
        """Consume the child's stdout/stderr into our logger.

        Required, not cosmetic: an unread OS pipe fills its buffer and
        blocks the child's next write() once full, effectively hanging a
        plugin the first time it logs enough -- see
        docs/02-architecture.md "Correctness & consistency" #1 (never
        block on a plugin).
        """
        entry.output_tasks = [
            asyncio.create_task(
                self._drain_stream(entry.plugin_id, entry.process.stdout, "stdout"),
            ),
            asyncio.create_task(
                self._drain_stream(entry.plugin_id, entry.process.stderr, "stderr"),
            ),
        ]

    @staticmethod
    async def _drain_stream(
        plugin_id: str,
        stream: asyncio.StreamReader | None,
        stream_name: str,
    ) -> None:
        if stream is None:
            return
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                line = await stream.readline()
                if not line:
                    return
                logger.info(
                    "[plugin:%s:%s] %s",
                    plugin_id,
                    stream_name,
                    line.decode(errors="replace").rstrip(),
                )

    async def _wait_until_healthy(self, socket_path: Path) -> None:
        """Poll `GET /health` until it's 200. Caller wraps this in
        `asyncio.timeout()` -- see `start()` -- rather than this method
        taking a `timeout` parameter itself."""
        async with _health_client(socket_path) as client:
            while True:
                if socket_path.exists():
                    with contextlib.suppress(httpx.HTTPError):
                        response = await client.get("http://plugin/health")
                        if response.status_code == 200:
                            return
                await asyncio.sleep(0.05)

    async def is_healthy(self, plugin_id: str) -> bool:
        """Whether the plugin currently answers `GET /health` with 200."""
        entry = self._plugins.get(plugin_id)
        if entry is None:
            return False
        try:
            async with _health_client(entry.socket_path) as client:
                response = await client.get("http://plugin/health")
                return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def stop(self, plugin_id: str) -> None:
        """Stop a plugin gracefully (SIGTERM, then SIGKILL after a grace
        period) and clean up its socket. No-op if it isn't running."""
        entry = self._plugins.pop(plugin_id, None)
        if entry is None:
            return
        entry.stopping = True
        if entry.process.returncode is None:
            entry.process.terminate()
            try:
                await asyncio.wait_for(
                    entry.process.wait(),
                    timeout=GRACEFUL_STOP_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                entry.process.kill()
                await entry.process.wait()
        if entry.supervise_task is not None:
            entry.supervise_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await entry.supervise_task
        for task in entry.output_tasks:
            task.cancel()
        for task in entry.output_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        entry.socket_path.unlink(missing_ok=True)

    async def stop_all(self) -> None:
        """Stop every running plugin -- called from the app's shutdown."""
        for plugin_id in list(self._plugins):
            await self.stop(plugin_id)
